# FCLe-Y 上线灰度方案

> 状态：方案 Y 盲测已通过（5题×2轮 depth 0漂移，详见 `fc_le_blindtest_result_20261125.md`）。
> 本文档定义灰度接入的开关设计、观测指标、回滚条件与压测方法。

---

## 一、背景与前置结论

方案 Y 验证结论：
- Router 语义分类头（轻 LLM）判定稳定：5 题 × 2 轮，depth/路径 **0 漂移**，难度判断 5/5 吻合预期。
- 所有业务节点不再单独起子 LLM：一次 run 的 LLM 调用从 `len(path)+2` 次降到 `1 次 Router + 1 次主 AI 推理`。
- Y2 路由缓存兜底高频重复查询：同题二次命中 **0.0015s**（vs 首调 48s）。

当前状态：**eval 环境验证通过，进入灰度接入评估**。

---

## 二、灰度开关设计

### 2.1 开关落点（基于现有配置范式）

工程现状：
- 无统一 feature flag 系统，现有范式为 `config/config.yaml` 的 `pipeline.mode` 字符串 + `NO_MEMORY=1` 环境变量。
- 生产入口：`cgraph/engine.py:81` `Engine.run(user_input, utility)`。

**新增开关**（在 `config/config.yaml` 增加，最小侵入）：

```yaml
pipeline:
  mode: single
  workers: 4
  # FCLe-Y 灰度开关（2026-11-25）
  # off       = 老管线（逐节点 LLM），完全原行为
  # shadow    = 影子对照：老管线正常产出 + 旁路跑 FCLe-Y 骨架，只记日志不影响用户
  # on        = FCLe-Y 生效：Router 一次 LLM + 骨架给主 AI 推理
  fcle_mode: "off"
  fcle_cache: true        # Y2 缓存（仅 fcle_mode=on/shadow 生效，logs/.fc_le_router_cache.json）
```

### 2.2 三档语义

| 档位 | 用户感知 | 老管线 | FCLe-Y | 用途 |
|---|---|---|---|---|
| `off` | 不变 | 全量跑 | 不跑 | 默认；回滚目标 |
| `shadow` | 不变（仍老管线） | 全量跑 | 旁路跑骨架，仅记日志 | 灰度对照压测，零风险 |
| `on` | 变（FCLe 产出） | 不跑 | 全量跑 | 正式切换 |

### 2.3 接入点（改动面最小化）

`Engine.run`（engine.py:81）在 ①路由后插入分派：

```python
fcle_mode = self.cfg.get("pipeline", {}).get("fcle_mode", "off")
if fcle_mode == "shadow":
    self._run_fcle_shadow(user_input, decision)   # 旁路，异常吞掉不影响主链
elif fcle_mode == "on":
    return self._run_fcle(user_input, decision)    # FCLe 主链
# off 或老逻辑照旧
```

- `shadow`：老管线跑完后，旁路调 `run_skeleton.build_skeleton(..., router_llm=self.llm, use_cache=cfg)`，把骨架 + 耗时 + depth 写 `logs/fcle_shadow.jsonl`。**不写记忆、不改图、不影响返回**。
- `on`：调 `build_skeleton` 拿骨架 → 主 AI 推理（由调用方 CodeChat 主模型承接，非 engine 内起 LLM）→ 产出按原 `WalkResult` 结构回填，确保赫布链路（engine.py:109 `hebbian.apply`）与记忆归档照旧。

> ⚠ 关键约束：`on` 档下 engine 自身**不再逐节点调 `self.llm.chat`**（executor.py:142 等），这是省时间的关键。主 AI 推理部分在生产由 CodeChat 主模型承接（MCP 框架注入模式），engine 只负责选路+赫布+归档。

---

## 三、观测指标

### 3.1 影子期（shadow）必看

| 指标 | 采集点 | 期望 |
|---|---|---|
| depth 一致率 | 老管线 `decision.complexity` vs FCLe Router depth | ≥90% |
| 路径差异分布 | 老 `decision.path` vs FCLe `path` | 记录差异题，人工抽查合理性 |
| Router 耗时 | build_skeleton `elapsed_ms_tool` | 基线：41-50s（首调）/150-178s（后续负载） |
| 骨架生成异常 | shadow 日志 error 字段 | 0 |
| 主链未被污染 | 用户返回答案 vs 纯老管线答案 | 逐字节一致（shadow 不得影响主链） |

### 3.2 正式期（on）必看

| 指标 | 采集点 | 期望 |
|---|---|---|
| 单轮总耗时 | 端到端 wall time | 显著低于老管线（老管线 len(path)+2 次 LLM × ~30-200s） |
| LLM 调用次数 | 计数器 | `2`（Router 1 + 主 AI 1），不随 path 增长 |
| 缓存命中率 | `cache_hit` 字段 | 高频重复查询场景应偏高 |
| 赫布回填链路 | `logs/edge_log.jsonl` 新增行 | 每轮正常写入（见 §5） |
| 赫布权重漂移 | mind_graph edge weight | 在 min/max 区间内合理演化，无异常塌陷 |
| 结论质量 | 人工抽检 | 对齐老管线（参照 A1 三题已合格基线） |
| verify_note 比率可解析 | `re.search("比率 ([\d.]+)")` | 非空率 ≥ 老管线，否则赫布跳过率上升 |

---

## 四、回滚条件（硬触发，满足任一即回滚 `off`）

1. **结论质量崩塌**：人工抽检连续 5 题明显弱于老管线（裁决逻辑缺失/结论跑偏）。
2. **赫布链路断裂**：连续 10 轮 `logs/edge_log.jsonl` 无新增，或 `verify_note` 比率解析失败率 >50%。
3. **耗时倒挂**：FCLe 单轮总耗时 ≥ 老管线（说明省调用没省下，Router 慢或主 AI 推理变长）。
4. **骨架异常**：`build_skeleton` 抛错率 >1%，或路由 `is_valid=False`（缺保底节点）。
5. **记忆污染**：记忆库出现重复/错误归档（FCLe 与老管线双写导致）。

回滚动作：`config.yaml` 改 `fcle_mode: "off"`，重启即恢复老管线（零代码改动，随时切回）。mind_graph 若被污染，用 `data/mind_graph_backup_<date>.json` 还原。

---

## 五、赫布回填链路验证（首要前置）

调研结论（`cgraph/executor.py` + `hebbian.py` + `graph.py`）：

```
engine.py:109  fb = WalkFeedback(path=result.path, verify_ratio=ratio, utility)
               update = self.hebbian.apply(fb, self.graph_file, self.edge_log)
   ↓
hebbian.py:59  apply → verify_ratio<0.3 或 utility<0 → 负反馈
               → g.reinforce_path(path, success, epsilon, decay)
   ↓
graph.py:152   reinforce_path 遍历 path 相邻对 (a,b) → update_edge(a,b,success,epsilon)
   ↓
graph.py:146   update_edge 改 self.edges[(a,b)].weight（正/负）
               → global_decay(decay) 全图衰减
   ↓
hebbian.py:66  g.save(graph_file) 写回 mind_graph.json + logs/edge_log.jsonl
```

**FCLe `on` 档对接要点**：只要 `WalkResult.path` 正确反映实际经过的节点序列，`reinforce_path` 无需改造。即 FCLe 骨架给出的 `dec.path` 必须原样传入 `WalkResult.path`，赫布链路自动兼容。

**验证脚本**：`scripts/verify_fcle_hebbian.py`（见 §6.2）——跑一轮 FCLe，断言（a）`edge_log.jsonl` 新增 1 行；（b）`mind_graph.json` 对应边 `weight` 变化；（c）`success_count`/`failure_count` 递增。

---

## 六、压测与对照方法

### 6.1 对照压测（老管线 vs FCLe-Y）

测试语料：`logs/ab_result_20261113_001951.jsonl`（8 题带老管线存档答案/路径/耗时，2026-11-13 基线）。

> 注：mind_graph 在 2026-11-25 19:44 经赫布更新，与 11-13 基线路径可能有偏移——对照时**以"结论质量"为主，"路径一致"为辅**（同 A1 口径）。

脚本：`scripts/eval_fcle_pressure.py`（待建，见 §6.3）
- 对每道题各跑：老管线（`Engine().run`）+ FCLe-Y（`build_skeleton` + 主 AI 推理）
- 记录：总耗时、LLM 调用次数、depth、路径、结论、repro_hint
- 输出：`logs/fcle_pressure_<ts>.jsonl` + Markdown 对照表

### 6.2 赫布链路验证脚本（待建）

`scripts/verify_fcle_hebbian.py`：单轮 FCLe 后断言 edge_log 新增 + weight 变化 + 计数递增（见 §5）。

### 6.3 质量对照脚本（待建）

`scripts/eval_fcle_quality.py`：从 `data/origin_corpus3_raw.txt`（78KB 元核原始语料）或 `logs/ab_result_*.jsonl` 基线题中抽经典对话，跑 FCLe 骨架 + 主 AI 推理，与老管线结论对比质量（无元核骨架 vs 有元核骨架的推理质量差异）。

---

## 七、上线节奏

1. ✅ **盲测完成**（本文档前置）—— depth 0漂移。
2. **影子期**：`fcle_mode: "shadow"` 跑 N 天真实流量，看 §3.1 指标，重点确认主链零污染 + depth 一致率。
3. **赫布验证**：`verify_fcle_hebbian.py` 跑通，确认回填链路正常。
4. **对照压测**：`eval_fcle_pressure.py` 跑 8 题基线，确认提速且质量对齐。
5. **小流量 on**：`fcle_mode: "on"` 灰度少量请求，看 §3.2 指标。
6. **全量 on**：指标达标后正式切换，保留 `off` 开关随时回滚。

---

## 八、远期优化备忘

- Router 耗时波动是**上游 LLM 服务负载问题，非判定逻辑缺陷**（盲测 10 次裸跑 depth 0漂移已证）。
- 若后续要极致稳定：给 Router 单独做轻量分类头（小模型离线推理），属远期优化，非当前必须项。
- Y2 缓存（同题二次 0.0015s）已能兜底高频重复查询场景。