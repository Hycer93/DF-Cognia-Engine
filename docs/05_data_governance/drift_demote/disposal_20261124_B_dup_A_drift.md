# 2026-11-24 处置报告（问题B + 问题A + gate根因）

> **执行状态：✅ 已执行完成（2026-11-24 11:08）**
> 备份：`corpus_store.db.bak_dedup5_20261124_104505` / `memories.jsonl.bak_reexport_20261124_110827`

## 执行结果摘要

| 项目 | 结果 |
|---|---|
| 问题B 3张 superseded | ✅ 3fae1e01→94f77455 / 13a2aa61→9963caa5 / b34c33a6→9963caa5 |
| status 分布 | active 317 / superseded 3 / deleted 2 |
| 问题A 重导出 | ✅ 322 条，inst_id 全集一致 |
| 漂移字段对齐 DB | ✅ status/superseded_by/detail/memory_class 全对齐 |
| 7 审计字段 | ✅ 全部条数无丢失（126/20/66/4/29/322/43） |
| 回归 | ✅ e30_p0 17PASS / e30_p2_p3 13PASS |
| 文档注释 | ✅ memory_organize.py L30 已更新数据源优先级 |

---

# 一、问题B：memory_gate 同会话重复立卡

## 1.1 关键修正：不是 4 张，是 **5 张**

原以为是 2 组 4 张，实际 audit 日志揭示全貌：
- **vhal 组 2 张**：`94f77455`(13:25) / `3fae1e01`(13:27)
- **dumpsys 组 3 张**：`9963caa5`(13:25) / `13a2aa61`(13:27) / `b34c33a6`(13:29)

`13a2aa61` 是此前漏掉的第 5 张（dumpsys 第 2 个副本，evidence=node_Dk）。

## 1.2 完整性判据失效，改用"首次立卡"判据

用户要求"保留 detail 更完整那张"。但实测：**5 张卡 detail 全部 58 字、tags 全相同**，仅 summary 措辞和 evidence node 不同。detail/tags 无法区分完整性。

改判据：**保留每组最早首次立卡（首现），其余标记 superseded**。理由：13:25:29 那批是首次提取，后续 13:27/13:29 是 LLM 重复提取的漂移副本。

| 组 | 保留（首次） | 标 superseded | superseded_by 指向 |
|---|---|---|---|
| vhal | `94f77455`(13:25,node_Ch) | `3fae1e01`(13:27,node_Ch) | → 94f77455 |
| dumpsys | `9963caa5`(13:25,node_Bd) | `13a2aa61`(13:27,node_Dk) | → 9963caa5 |
| dumpsys | ↑ | `b34c33a6`(13:29,node_Di) | → 9963caa5 |

> 注意：dumpsys 3 副本 evidence node 各不相同（Bd/Dk/Di），是**不同素材步**被提取成同一知识点的多副本。保留 node_Bd（首现）。

## 1.3 处置动作（不物理删除，版本溯源）

```
UPDATE core_memory_card SET status='superseded', superseded_by='<保留卡inst_id>' 
WHERE inst_id IN ('3fae1e01...', '13a2aa61...', 'b34c33a6...')
```
- `superseded` 是 schema 合法值（v3_schema.py:42 `active|superseded|merged`）
- 不删除、不改 detail，只降权 + 留版本链
- 副作用：`load_active_pool()` 排除 superseded，查重池正确收缩 3 张

---

# 二、问题A：DB↔jsonl 字段漂移 → 合并式重导出

## 2.1 漂移实况（dry-run 统计）

DB 322 张 = 旧 jsonl 322 张，**inst_id 全集一致**，但字段值漂移：

| 字段 | 漂移条数 | 说明 |
|---|---|---|
| status | 2 | DB=deleted，jsonl=None |
| superseded_by | 10 | DB 有值，jsonl 全 None |
| detail | **97** | DB 是更新版（含`[账本关联]`），jsonl 是旧快照（含`[judge标注]`） |
| tier | 1 | |
| memory_class | **129** | DB 已回填分类，jsonl 滞后 |

## 2.2 关键约束：jsonl 有 7 个独有审计字段不能丢

jsonl 有、DB 无的字段（**合并时必须保留**）：

| 字段 | 有值条数 | 性质 |
|---|---|---|
| `code_changelog` | 126 | append-only 变更审计，**不可丢** |
| `provenance` | 66 | 溯源 |
| `verified_via` | 43 | 验证途径 |
| `superseded_reason` | 29 | 降级原因 |
| `corpus_snapshot_date` | 20 | |
| `superseded_at` | 4 | |
| `unclassified_cycles` | 322 | 全量有值 |

**结论：不能"DB 直接覆盖 jsonl"，必须合并**——以 DB 22 字段为准 + 保留 jsonl 这 7 个独有字段。

## 2.3 合并逻辑（已验证）

```python
entry = dict(db_row)                    # DB 22 字段为准（含 status/superseded_by/detail/memory_class）
entry['dfcp_tag'] = json.loads(...)     # JSON 串转 dict
entry['protected'] = bool(...)
for k in ['code_changelog','provenance','verified_via','superseded_reason',
          'corpus_snapshot_date','superseded_at','unclassified_cycles']:
    if k in old_jsonl[inst_id]: entry[k] = old_jsonl[inst_id][k]   # 保留审计字段
```

抽样核对 3 个漂移样本（2a836a7f / 9b48551b / 63dfe4d8）：status/superseded_by **全部对齐 DB** ✅

## 2.4 执行计划

1. 备份旧 `memories.jsonl` → `.bak_reexport_<ts>`
2. 按上述合并逻辑，DB 全量 322 张重导出 → 新 jsonl
3. 校验：inst_id 全集不变(322)、status/superseded_by/detail 漂移样本对齐 DB、7 个审计字段有值条数不变
4. 更新注释文档：明确 "统计巡检优先读 DB；jsonl 仅审计存档"

---

# 三、memory_gate 重复立卡根因（审计日志实证）

## 3.1 触发序列（audit_topic_log 铁证）

```
13:24:03-04  skill_memory_gate 触发 17 次（多分支并行扫描）
13:25:29     memory_commit:green written=2  → 9963caa5 + 94f77455
13:27:23     memory_commit:green written=2  → 3fae1e01 + 13a2aa61   ← 重复！
13:29:26     memory_commit:green written=1  → b34c33a6 (跳过 vhal semidup)
13:31:55     memory_commit:green written=0  → 全跳过（semidup 生效）
```

## 3.2 根因（三重叠加）

1. **同会话多次 run_atomic**：该 session 在 13:24-13:32 期间触发了多轮 memory_gate（13:24 达 17 次），每次对同一 trail 重新盘点。
2. **bigram-Jaccard 阈值 0.6 过低**：`green_card_writer.py` 用 Jaccard>0.6 判定半重复，但 LLM 措辞漂移（"未部署"→"未预置"、"真值判据"→"权威判据"）导致 Jaccard 分数未达拦截线。
3. **无消息级去重游标**：memory_gate 没有 "last_processed / 已处理消息" 机制，每次 run_atomic 都从零扫同一 trail。

## 3.3 注意：system 有半重复拦截，但阈值太松

13:29:26/13:31:55 日志显示 `skipped: ['semidup:...']` —— **Jaccard 机制确实在工作**，只是 0.6 阈值拦不住 0.7-0.9 的措辞漂移。到 13:31:55 才靠累积已立卡把 Jaccard 拉高到能拦。

## 3.4 修复方向（本次不改码，仅待办）

- `commit_green_cards` 的半重复阈值从 bigram-Jaccard 0.6 升级为 embedding 语义 ≥0.95（复用 P0 能力）
- 或加"同 session 同 node 已立卡"的硬去重
- **本次只做数据层 superseded 标记，不动 gate 业务代码**

---

# 四、待办新增

1. **[只读分析已完成] memory_gate 短时间同输入重复立卡根因** → 结论：多轮 run_atomic + Jaccard 0.6 太松 + 无消息游标。后续排期修复。
2. **底层缺陷：记忆更新只写 DB、不同步 jsonl** → 本次以重导出修复存量漂移；管线层面的"更新即回写"需后续迭代（memory_organize.py 更新路径不回写 jsonl）。

---

# 五、执行记录（已完成）

## 5.1 问题B（已执行）
```
UPDATE core_memory_card SET status='superseded', superseded_by=?
  3fae1e01... → 94f7745503444112
  13a2aa61... → 9963caa5fd3044b4
  b34c33a6... → 9963caa5fd3044b4
```
- 验证：3 张 status=superseded，superseded_by 正确指向首版；不删除，evidence-node 信息留在原卡可回溯。
- status 分布：active 317 / superseded 3 / deleted 2。

## 5.2 问题A（已执行）
- 备份 `memories.jsonl.bak_reexport_20261124_110827`
- 合并式重导出 322 条（DB 业务字段真值 + 保留 jsonl 7 审计字段）
- 校验全过：
  - inst_id：DB 322 = 新 jsonl 322 = 旧 jsonl 322，集合完全一致
  - 漂移字段（status/superseded_by/detail/memory_class）全对齐 DB
  - 7 审计字段条数无丢失：code_changelog 126 / corpus_snapshot_date 20 / provenance 66 / superseded_at 4 / superseded_reason 29 / unclassified_cycles 322 / verified_via 43

## 5.3 文档注释（已更新）
`memory/organize.py` 存储布局注释（L30 区）改为：
> DB core_memory_card = 唯一真相；业务计算/统计/巡检一律优先读 DB；memories.jsonl = 审计导出快照，不得作为统计数据源。

## 5.4 gate 根因（仅留存结论，未改码）
见 §三。修复方向（Jaccard 0.6→embedding 0.95 / 加 session-node 硬去重）保留在待办池。
