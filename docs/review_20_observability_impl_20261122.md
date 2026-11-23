# #20 记忆系统可观测性 + 合规 — 实施记录（Phase 1 切片）

> 生成于 2026-11-22 | 状态：**Phase 1 首批落地完成（观测聚合 + 合规脱敏）**
> 立项：`docs/review_20_observability_20261122.md`
> 约束：只补观测层、不改治理规则；拿数据后再谈规则收敛（Phase 2）

---

## 一、调研结论（已有 vs 缺失）

`memory/` 子系统全貌：gate(写入闸门) → judge(判定) → relations/organize(冲突+落库) → corpus_store(SQLite) → query(检索)；旁路 hook(PreCompact) + E9(applog兜底) + E4(晨报巡检) + E5(冷热区)。

**已有**：E4 有规则巡检型指标（quarantine_by_cat / placeholder_cards / hit_domains / discard_rate_warning / hold_age_days / db_jsonl_drift）。
**缺失**：7 类业务效果指标基本全无结构化聚合（检索成功率 / miss_case 趋势 / 误归档率 / 冲突分级计数 / simhash 触发 / 热区扫描），只能人工看晨报。`gate_miss_case.jsonl` 为空置。

## 二、本次落地（两项，均可立即验证）

### 1. E4 新增「记忆健康度」聚合块（`e4_maintenance_patrol.py`）

新增 `_memory_health(mems)` + `report["health"]`，从既有 jsonl **只读聚合**，不改任何规则：

| 维度 | 指标 | 数据源 |
|---|---|---|
| 检索 | gate_total / deep_retrieve / forced / op 分布 / miss_cases / **miss_rate** | `gate_log.jsonl` + `gate_miss_case.jsonl` |
| 冲突 | queue_total / 状态分布 / **pending 积压** | `conflict_queue.jsonl` |
| 去重 | store_pending_total / **hold_reason 分布** | `store_pending.jsonl` |
| 归档 | hold_aged / **max_hold_age_days** / over14d | `store_pending.jsonl` hold_ts |

- 输出：`logs/e4_maintenance.json` 的 `health` 字段 + stdout 一行人读摘要。
- 实测（当前数据）：`检索gate=8 miss=0.0 | 冲突q=104(待0) | 待归档=13(超14d=0) | hold最长11天`。
- 设计：失败不阻断主 patrol（try/except 包住），`health.error` 可见。

### 2. E9 applog 脱敏（`e9_dialog_governance.py`）

新增 `_sanitize_text()`，在 transcript 通道**落 raw_corpus 副本前**清洗 PII：
- 手机号（11位）/ 身份证（18位含X）/ 邮箱 / 银行卡（16-19位）/ api_key|token|password 赋值。
- **合规边界**：只作用于进 DFCP 的副本正文；平台原文件（applog.log）**只读不变**（守规约①）；hook 通道正文不经此步（避免误伤）。

## 三、验证

- 脱敏单测：手机/邮箱/身份证/银行卡/密钥 5 类全部正确替换，正常文本不受影响。✅
- E4 全量跑通：`health` 块正确聚合真实数据，stdout 健康度行正常。✅
- 语法检查通过。✅

## 四、未做（Phase 2 / 需更多数据）

- [ ] miss_case 趋势**日级时间序列**：`gate_miss_case.jsonl` 当前无数据，需先积累。
- [ ] 误归档率（store 后 demote 比例）：需在 organize 侧加埋点。
- [ ] 冲突分级计数（三级判定分桶）：需在 memory_relations 判定分支加计数。
- [ ] simhash 触发计数聚合：gate_log 已有 `dialog_dedup` op，可扩为日聚合。
- [ ] 阈值来源与命中率统计（simhash≤6 / 90天热区 / verify_ratio<0.3）：需专门审计。
- [ ] 全链路运行流程图（文档形式）：未画。
- [ ] 规则收敛：**明确不做**，无数据前禁止删规则。

## 四·补（Phase 2 已落地 4 项，2026-11-22）

Phase 2 的"纯数据聚合"部分全部本地可做、不吃 LLM，已实施并验证：

1. **日级趋势** ✅：E4 `health.retrieve.daily_trend` —— gate_log 按天聚合 `{gate,store,dedup,miss}`，取近 7 天。当前样本少（3 天），随积累成形。
2. **误归档率** ✅：E4 `health.archive.demote_rate` —— `superseded_by` 非空（active→precedent 校准降级）占全库比。当前 29/320=**0.0906**。注意：这是"降级率"，非"误删率"（demote 零删除、可回溯）。
3. **冲突三级分级计数** ✅：`memory_relations.py` 级联处把 `_cascade_layer`/`_cascade_layers` 挂到 best 候选 → 写进 `relation_graph.jsonl` 图谱条目（新字段 `cascade_layer`/`cascade_layers`）→ E4 `health.conflict.cascade_dist` 聚合。1=summary 即判、2=下钻 detail、3=再下钻 source。端到端验证：summary=1.0/detail=0.971 → `layer=2` 正确落图谱。
4. **simhash/去重触发计数** ✅：E4 `health.dedup.gate_dedup_hits`（dialog_dedup op 计数）+ `simhash_bearing`（带 simhash 字段的写入数）。当前 1 次触发 / 3 张带 simhash。

**未做（仍是待环境/待数据）**：miss_case 日级（无数据源 miss_case 文件尚不存在）、阈值命中率专项审计、全链路流程图。

## 五、回滚
- E4：删 `_memory_health` 调用与函数即可，patrol 其余逻辑零改动。
- E9：`body = _sanitize_text(text)` 改回 `body = text` 即恢复。
- Phase2 分级：删 `memory_relations.py` 中 `_cascade_layer` 挂载 + graph_entry 的 `cascade_layer` 两行即可；旧图谱条目无该字段，读取侧已容错（`if ... is not None`）。
