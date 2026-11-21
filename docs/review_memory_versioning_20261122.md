# 记忆版本链 / 对话追溯 能力缺口分析（2026-11-22）

> 数据源：本轮对 `memory/` 与 `cgraph/` 的真实代码扫描（含文件行号证据），摒弃旧快照。
> 配套台账：`docs/review_20261121_system_status.md`；系统综合评审：`docs/review_system_20261122.md`。
> 关联立项：#11 态度回执（P1）、#12 对话追溯（P0）、#13 记忆版本链（P2 暂缓）。

---

## 0. 系统定位前提

本系统定位是 **AI 思考/推理的记忆底座**，不是通用聊天记录后台。
因此过程性能力（对话原文追溯、版本演化、主观态度）一律视为 **审计/排错辅助**，与「推演用事实记忆」严格隔离。

**共同设计铁律（#11/#12/#13 共享）**：
- **主记忆卡 `core_memory_card` 只存推演用的干净事实**，不塞过程/态度/历史版本，防止膨胀与推演输入污染。
- **过程、态度、历史对话、版本快照** 一律放附属表/附属日志，仅供审计、排错、人工审阅。
- **机器推演逻辑不消费附属数据**（不进 F20 权重、不进检索打分、不自动改置信/存活）。

---

## 1. 对话到记忆实际怎么走（事实）

### 1.1 对话落库（raw_corpus）

- 每轮对话是 **新 id**：`dialog.2026-11-22.h0015`（下一轮 `h0016`），**不同 id**。
- `raw_corpus` 的 `version` 递增只在 **同 id 反复 upsert** 时生效（`corpus_store.py:141-173` `upsert_corpus`）。对话段永远新 id → **对话迭代在 raw_corpus 里串不成版本链，每轮都是独立 version 1**。
- 三条写入通道：
  | 通道 | 内容 | 时机 | 去向 |
  |---|---|---|---|
  | h（PreCompact hook） | 用户+回复混在段文本里 | 每轮会话结束 | raw_corpus(kind=session_dialog) |
  | t（applog 兜底，E9） | **仅用户原文** | 每晚 E9 | raw_corpus(kind=session_dialog) |
  | Chroma `chat` 集合 | 用户+助手配对 | Engine.run 实时 | ChromaDB（独立向量库） |

### 1.2 记忆卡落库（core_memory_card）

- `full_hash` UNIQUE **精确拦截**（`memory_organize.py:112-118`）：内容完全相同 → 第二条直接拒（只存一份）；内容有改动 → hash 不同 → **存成两张互不相干的卡**。
- `core_memory_card` **无 `version` 字段**。`superseded_by` 指向「校准单 ID」（外部治理决定），是 **降级链**（`demote()` `memory_organize.py:291-340`），**不是版本链**。
- `entity_id`：共时相似度聚类（"这些卡讲同一主题"），**非历时演化**。
- `origin_ids`（拆分来源）/ `derived_from`（蒸馏来源）：**任务血缘**，非 V1→V2 时间链。

---

## 2. #12 对话会话归组 & 历史追溯（P0 立项，轻量版）

### 现状缺口（真实隐患）
1. **无 session 概念**：对话段散在 raw_corpus，靠日期+通道尾号组织，同一会话多轮被拆成多个段，**无法归组回一个连贯对话**。
2. **按 dialog_seq 范围查无封装**：需手写 SQL 解析 id 序号。
3. **模型回复在 t 通道永久丢失**：PreCompact hook 一旦异常，当天模型回复不可恢复（t 通道只补用户原话）。
4. **无对话级追溯接口**：`memory_query.ask` 只查记忆卡不查对话；`trace_cited` 是记忆引用流水非对话追溯。
5. 碎片（≤20 字节）与 simhash 当日重复段 **硬丢弃**，不入任何表。

### 为什么是底线能力
- **排错刚需**：记忆内容错/不该生成却生成/该生成没生成时，现在只能手工翻库定位「哪一轮对话催生」。工程排错、审计的底线，非产品炫技。
- **真实隐患**：PreCompact hook 异常 → 模型回复永久丢失，线上不能假设「钩子永不坏」。

### 做什么（轻量底线，不动记忆写入核心）
- ✅ **session_id 透传标记**：对话入库增加 session 标识（不重构存储，仅加字段/标记）。
- ✅ **高层查询封装**：`按 session / 按日期` 获取对话片段（raw_corpus 数据本就在库，只缺封装函数）。
- ✅ **断流审计告警**：h 通道长时间无新对话入库 → 告警"可能模型回复有丢失风险"。
- ✅ **source_ref 反向弱关联**：记忆卡来源可反查对应对话 id（弱关联即可，不强外键）。

### 明确不做（重、收益有限）
- ❌ 不做完美结构化「用户/助手分字段存储」（保留原始文本，不强行重构）。
- ❌ 不做对话 diff、修改编辑等产品化能力。

### 定性
**当排错审计工具做，不是给终端用户看的聊天历史功能。**

### ✅ 实施完成（2026-11-22，方案一：根治 h 通道断流）

**实证根因**（非推测）：h 通道（PreCompact hook）**已长期静默断流**——1508 条 session_dialog 中仅 1 条来自 h，1505 条来自 t 通道（E9 每晚 applog 兜底）。根因是旧机制依赖「主 AI 每次 compact 前手动覆写 `data/memory/dialog_last_segment.md`」这一**脆弱协作契约**，主 AI 未写 → 文件空 → `≤20 字节` 拦截 → 静默断流，模型回复永久丢失。gate_log 铁证：11-21 两次 hook 触发均 `too_short` 被拦。

**根除方案**：PreCompact hook stdin 自带 `{session_id, transcript_path, hook_event_name}`（已从 agent-pipe guard.js 生产实现确认契约），直接解析 transcript JSONL 拿本轮完整对话，**不再依赖主 AI 手写 md**。

**改动清单**（4 处代码 + 1 回归）：
1. `memory/memory_gate.py`：提取 `store_dialog(body, session, source_tag)` 可复用函数（原 dialog 分支逻辑内联在 main，抽出供 CLI+hook 共用，去重/取号/落库口径不变）。
2. `memory/memory_hook.py`：
   - 新增 `_read_stdin_payload()` / `_extract_text_from_content()` / `_parse_transcript()` / `_session_dialog_from_hook()`；
   - `session` 子命令：识别 hook payload（含 session_id/transcript_path）→ 解析 transcript 落库（title=session_id, source_ref 标 `precompact-stdin-transcript`）；transcript 缺失/异常 → **降级回 dialog_last_segment.md 旧通路**（向后兼容，日志记录 channel）；候选 judge 管道照跑。
3. `memory/corpus_store.py`：新增 `list_dialog(session_id=None, tree_vol=None, with_body=False)` 只读查询封装。
4. `memory/memory_query.py`：新增 `cards_from_dialog(dialog_key)` 反向查找（内存遍历 source_ref，兼容 3 种真实格式：`#dialog-id` / `session=<id>` / `raw锚: session <日期>`；弱关联不强外键）。与 `fetch_by_ref`(卡→对话) 互补成双向。
5. `eval/core/e9_dialog_governance.py`：新增 `_check_h_channel_stalled(N)`，h 通道近 N 天（默认3）无记录 → 打印 `[e9][h_channel_stalled]` 告警进晨报，report 增 `h_channel` 字段。
6. 回归：`eval/audit/dialog_trace_regression.py` **27/27 全绿**（解析/落库/降级/查询/反查/告警 6 类，全沙箱隔离），既有 pending 回归 18/18 无破坏，真实库零污染（320 卡不变）。

**明确边界（不可修复项）**：只对**新产生会话**生效；历史存量 t 通道老数据无法复活（旧数据仍只有用户原文，无模型回复）。transcript 格式假设 = Claude Code 标准 `{type, message:{role,content}, timestamp}`，与 agent-pipe 生产 guard.js 同源。

---

## 3. #13 记忆卡版本链 + diff（P2 暂缓，带触发条件）

### 为什么不现在做
1. **触碰记忆写入核心，风险高**：要改 `full_hash` 唯一拦截、新增 version 快照、改 `atomic_store`。这是 #7 刚加固稳定的可靠写入链路，刚稳就动核心，一旦引入 bug → 记忆重复/脏数据/损坏。
2. **现实需求不迫切**：记忆卡变更来源主要是 ①夜间复审 hold 转正 ②人工 `demote()` 降级作废。现有 `demote()`（降级原因+时间+指向校准单）+ 各类 jsonl 审计日志 **已可完成人工审计**，虽非优雅 V1/V2/V3 链，但安全够用。
3. **成本收益不对等**：完整版本链+diff 工作量大，短期解决的是「体验优雅度」而非阻塞性 bug。

### 启动触发条件（满足才考虑）
- 出现「同一条记忆高频迭代修改」的规模化场景；
- 现有降级+审计日志的人工追溯已变得非常痛苦；
- 记忆写入链路经过一段时间充分稳定之后。

### 现阶段
保持现状：`demote()` 降级标记 + 各类审计 jsonl 做追溯，够用、安全，虽不漂亮。

### 未来若要做的缺失清单（记录备查）
1. 放开 `full_hash` 刚性拦截（允许同内容多版追踪）；
2. 引入 `version` 递增 + `content_hash`（仿 raw_corpus）；
3. 记忆变更时旧版快照写 `iteration_history` 表；
4. `difflib.unified_diff` 做 summary/detail 差异；
5. `list_versions(inst_id)` + `diff_versions(inst_id, v1, v2)` 接口。

---

## 4. 整体优先级排序

| 优先级 | 编号 | 任务 | 定性 |
|---|---|---|---|
| **P0** | #12 | 对话追溯轻量版（session 归组+查询封装+断流告警+source_ref 弱关联） | 审计排错底线，改动小风险可控 |
| **P1** | #11 | 态度回执附属记录（方案 A，只存不自动消费） | 主观反馈留存，人审参考 |
| **P2** | #13 | 记忆卡版本链+diff | 暂缓，等触发条件 |

---

## 5. 铁律重申

> **千万不要为实现追溯，把过程性数据塞进主记忆卡，会污染推演输入。**
> 主卡只存推演事实；过程/态度/对话/版本全进附属存储；机器推演不消费附属数据。