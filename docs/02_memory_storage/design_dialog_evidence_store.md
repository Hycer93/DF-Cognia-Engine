# 对话原始证据库设计（#31 · dialog_evidence_store）

> 日期：2026-11-23
> 决策：用户拍板 C 方案（逐条 + 会话快照双写） + 写入时机方案B（PreCompact为主 + 碎片兜底）
> 落地 commit：见 git log（memory/v3_schema.py、memory/dialog_evidence_store.py、memory/memory_hook.py、eval/core/e9_dialog_governance.py、eval/audit/e31_dialog_evidence_regression.py）

---

## 1. 背景与动机

现状（调研实证）：
- `raw_corpus(kind=session_dialog)` 1508 行，**92% 是 ≤100B 碎片**（如「C盘没空间了」），最长 6972B 还是专利文档回填——**没有任何一次完整多轮对话被留存**；
- 现有 `_parse_transcript`（memory_hook.py）把 transcript JSONL **扁平化成 `【用户】/【模型】` 文本**：丢弃 tool_use/tool_result、丢弃 timestamp、丢弃 block 结构；
- 平台**只有 PreCompact 一个 hook，无 SessionEnd**；
- 历史 h 通道已断流：1508 条对话中 1505 条来自 applog 兜底（t 通道），模型回复长期丢失。

结论：需要一张**未加工的原始会话证据表**，作为故障复现/离线仿真的真相源。

---

## 2. 三张表职责边界（严格切分，禁止混用）

| 表 | 粒度 | 内容 | 用途 | 运行时记忆流水线 |
|---|---|---|---|---|
| `core_dialogue_log`（已有空表，本次激活） | 逐条消息 | role/turn/content/ts | SQL 检索、关键词筛查、FTS 检索、统计 | 不读（仅排错查询） |
| `core_raw_dialog_session`（新建） | 整会话 | transcript 原始 JSON 对象数组（**原样，零裁剪**） | 权威证据快照、故障复现、离线重跑、评测导出 | **禁止读取** |
| `_tmp_dialog_fragment`（新建，内部临时） | 单条消息碎片 | msg_raw 原始 JSON | 崩溃兜底 + 巡检聚合载体 | 不读 |

**红线**：
1. `core_raw_dialog_session.messages` **绝不调用 `_parse_transcript`**，直接存 transcript 原始对象（保留 tool/ts/block）。
2. 业务记忆压缩流水线继续用 `session_dialog_archive_fragment`/`raw_corpus`，**不读证据库两表**。
3. 证据库只做存储与检索，**不做任何内容改写/摘要/过滤**。

---

## 3. 表结构

### 3.1 core_raw_dialog_session（权威快照）
```sql
session_id           TEXT PRIMARY KEY,
start_ts, end_ts     TEXT,                 -- 起止时间(优先取消息原始 timestamp)
model_version        TEXT,                 -- 复现关键
meta                 TEXT,                 -- JSON 环境/入参/上下文标签
messages             TEXT,                 -- JSON 数组: transcript 原始对象, 原样
msg_count            INTEGER DEFAULT 0,
terminate_reason     TEXT,                 -- precompact_normal|timer_orphan_collect|process_crash
ref_archive_fragment TEXT,                 -- → session_dialog_archive_fragment.id (证据链)
archived_offload     TEXT,                 -- 冷热分离: >90d 正文导出外部路径, messages置NULL时填
created_at, updated_at TEXT
```

### 3.2 _tmp_dialog_fragment（兜底碎片）
```sql
frag_id INTEGER PK AUTOINCREMENT,
session_id TEXT, seq INTEGER, msg_hash TEXT,  -- 幂等去重
msg_raw TEXT NOT NULL,                        -- 单条原始 JSON
captured_ts TEXT, consumed INTEGER DEFAULT 0  -- 0待聚合 1已聚合(留作去重锚)
```

### 3.3 core_dialogue_log（已有，激活）
复用 v3_schema.py 既有 DDL + FTS 索引 `fts_dialogue`，本次开始写入。

---

## 4. 写入时机（决策2 = 方案B）

```
每轮 turn 完成
  └─ append_turn_fragment(session, msg_obj)  → _tmp_dialog_fragment (崩溃兜底)

PreCompact 触发（memory_hook._session_dialog_from_hook）
  ├─ store_dialog(...) → raw_corpus (原逻辑不变, 向后兼容)
  └─ _evidence_ingest_and_aggregate():
       ├─ ingest_transcript(session, transcript_path)  碎片落地(幂等)
       ├─ aggregate_session(session, ref=raw_corpus.id) 聚合
       │    ├─ → core_raw_dialog_session (快照, messages 原样数组)
       │    ├─ → core_dialogue_log (逐条, role/turn/content)
       │    ├─ 回填 ref_archive_fragment 证据链
       │    └─ 碎片 consumed=1
       └─ 全异常吞掉只报告, 绝不阻断主流程

后台巡检（E9 夜间治理挂载）
  └─ collect_orphan_sessions(max_age_hours=72) 收拢悬空碎片 → 聚合(terminate_reason=timer_orphan_collect)
```

注释：平台无 SessionEnd，故"每轮 turn 追加"依赖未来可用的 UserPromptSubmit/PostToolUse hook 或调用方显式调用；当前 PreCompact 主路径已保证压空前完整落地。

---

## 5. 证据链

```
_tmp_dialog_fragment (碎片, 崩遗锚)
      │ aggregate
      ▼
core_raw_dialog_session.session_id ──ref_archive_fragment──▶ session_dialog_archive_fragment.id
      │                                                              │ 加工
      └──session_id 关联──▶ core_dialogue_log                  core_raw_corpus
```

由一条原始会话可反查：它产出的压缩归档片段 → 正式语料。反之由语料可溯源到原始对话。

---

## 6. 冷热分离（防主库膨胀）

- >90 天会话：messages 大 JSON 导出外部 `data/memory/offload/<session_id>.jsonl`，主库 `messages` 置 NULL，`archived_offload` 填导出路径；
- 元信息（session_id/起止时间/msg_count/terminate_reason/ref）保留，删除正文不丢证据（可回读外部文件）。

> 生命周期执行器为后续增量项（当前只预留 `archived_offload` 字段，未实现自动导出）。

---

## 7. 回归覆盖（eval/audit/e31_dialog_evidence_regression.py，27/27 PASS）

- 用例1：碎片追加重放 + 幂等去重 + seq 递增
- 用例2：PreCompact 聚合双写（快照 + 逐条）+ 证据链写入
- 用例3：**元信息完整性**（tool_use/timestamp/type 未丢、start_ts 取原始时间）
- 用例4：逐条表 role 拆分 / turn 递增 / content 非空
- 用例5：聚合幂等（重跑 updated，逐条不翻倍）
- 用例6：悬空碎片巡检兜底聚合（terminate_reason=timer_orphan_collect）
- 用例7：空 session 安全（返回 empty 不抛异常）
- 用例8：三表存在性 + 快照表关键列齐全

---

## 8. 待后续项（低优先级）

- **存量补录**：历史会话优先保障新会话强制落库，存量回溯补录优先级低；
- **冷热导出执行器**：90 天自动导出 + 主库 messages 置 NULL 的定时任务；
- **turn 级实时捕获**：若平台后续提供 UserPromptSubmit/Stop hook，接入 append_turn_fragment 实现真正"每轮追加"。当前 PreCompact 主路径 + 72h 巡检兜底已覆盖崩溃场景。

---

## 9. SessionEnd/Stop 对接预留（已实现映射，仅缺平台侧配置）

平台当前只暴露 PreCompact 一个 hook，**无 SessionEnd/Stop 事件**。但代码已预留完整对接点：

1. **入口天然通用**：`memory_hook.py session` 子命令不绑定事件名，只要 stdin payload 含 `{session_id, transcript_path, hook_event_name}` 三字段之一即被识别处理（`_hook_keys` 判定）。未来平台加 Stop/SessionEnd 事件，**同一入口直接复用，无需改代码**。

2. **事件→terminate_reason 映射表**（`memory_hook._HOOK_EVENT_TO_REASON`）：
   ```python
   {"PreCompact": "precompact_normal", "Stop": "session_stop",
    "SessionEnd": "session_end", "SessionStart": "session_start"}
   ```
   `_evidence_ingest_and_aggregate(..., hook_event=payload.get("hook_event_name"))` 按事件名自动归类 terminate_reason；缺省按 `precompact_normal`（向后兼容）。

3. **对接动作（平台侧就绪后）**：仅在 `~/.codechat/settings.json` 增加 Stop/SessionEnd hook 配置，指向同一 `memory_hook.py session`，即自动生效——证据库会把每次会话结束的完整 transcript 落地，terminate_reason 正确记 `session_stop`/`session_end`。

> 回归用例8（8e/8f/8g）已覆盖映射正确性。这是"接口已对接、平台待开放"的预留状态。
