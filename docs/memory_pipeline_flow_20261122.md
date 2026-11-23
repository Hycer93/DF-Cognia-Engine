# 记忆系统全链路流程图（#20 Phase2 收尾）

> 生成日期：2026-11-22
> 目的：把"记忆写入/检索/维护"三条链路画成可读流程，配套 #20 观测层定位 E4 指标落点。
> 性质：纯文档，只描述现状，不改任何规则。

---

## 一、写入链路（对话 → 卡片入库）

```
用户对话/输入
   │
   ▼
┌─────────────┐
│  gate 闸门   │  memory_gate：决定这条输入要不要进记忆
│ (memory_gate)│  ├─ 太短/闲聊 → dialog_too_short 丢弃
└──────┬──────┘  ├─ simhash 去重命中 → dialog_dedup 丢弃
       │         └─ 通过 → 进 judge
       ▼
┌─────────────┐
│  judge 判定  │  LLM 判定是否值得记忆（judge_failure → 进 store_pending hold 池）
└──────┬──────┘
       │ 通过
       ▼
┌─────────────┐
│ relations   │  四档同源判定（纯 embedding，无 LLM）：
│ 关系判定     │   equivalent(高重合) / augment(增补) / conflict(分叉) / new
└──────┬──────┘  ├─ 前置闸门：topic 不同→跳过 / 同entity→折叠 / 占位→停 summary 层
       │         ├─ 三级级联：summary → detail → source_ref 逐层下钻
       │         └─ 写 relation_graph.jsonl【观测落点：cascade_layer】
       ▼
┌─────────────┐
│ organize    │  落库到 core_memory_card（SQLite，记忆卡唯一真相主写）
│ 落库+整理    │  ├─ 写入由 cgraph/green_card_writer.py 机械执行（memory_gate 绿档清单）
└──────┬──────┘  ├─ demote 降级：tier→precedent + superseded_by【观测：demote_rate】
       │         └─ 写入 memories.jsonl（审计导出流，非主写）
       ▼
   core_memory_card (SQLite) ─── 记忆卡权威存储
       │
       └──▶ memories.jsonl（降级导出，靠 nightly「DB→jsonl增量回补」保持同步）

   ※ 另有一套素材表（memory/corpus_store.py）：raw_corpus/session_dialog/
     corpus_tags 等，存对话原文与语料，非记忆卡本体，E9 归档写这里。
```

**关键事实**：11-15 起 SQLite 是唯一主写源，`memories.jsonl` 只是审计导出。两者靠 nightly 回补步保持一致 → E4 的 `db_jsonl_drift` 监控此一致性。

---

## 二、检索链路（查询 → 召回）

```
用户查询
   │
   ▼
┌─────────────┐
│  cgraph 路由 │  ComplexityRouter：simple/medium/complex 三档
│ (router.py)  │  ├─ simple → 固定 MANDATORY_NODES(Aa,Eo,Jw) 3 节点
└──────┬──────┘  └─ medium/complex → 贪心路径(medium_steps=5/complex_steps=12)
       │
       ▼
   embedding 召回（本地 ONNX bge-small-zh）
       │
       ▼
┌─────────────┐
│ gate 检索闸  │  probe/judge 记录 → gate_log.jsonl【观测落点：daily_trend】
└──────┬──────┘  检索未命中 → gate_miss_case.jsonl（当前无数据，miss_rate=0）
       │
       ▼
   返回相关记忆卡
```

**关键事实**：`gate_log.jsonl` 是检索侧核心观测源，E4 的 `retrieve` 维度全从这里聚合。

---

## 三、维护链路（nightly 夜间无人值守闭环）

```
nightly_run.py  STEPS 顺序（每步独立子进程，分级超时）：
   │
   ├─ E0  topic锚开关守卫        —— 开关被关/管道断裂 → 硬 FAIL（不静默）
   ├─ E2  反问门标定扫描          —— 纯本地，标定 gate 阈值
   ├─ E1  探针触发率全链回归      —— LLM 探针（90min 超时）
   ├─ E4  记忆库维护巡逻  ★#20主战场 —— 只读报警 + health 四维聚合
   ├─ DB→jsonl增量回补   ★#20漂移修复 —— 只补不删，幂等
   ├─ E5  记忆自动整理            —— 幂等软层，只追加不删除
   ├─ E6  思维图巡逻              —— 只读报警
   ├─ E7  夜间治理                —— 提案不裁决，人闸门终审
   ├─ E10 语义离群巡检            —— 本地 bge 审"统计合格但语义错挂"脏卡
   ├─ E9  对话治理    ★脱敏落点    —— 归档主通道 + PII 脱敏 + transcript 兜底
   ├─ E-hold复审                  —— 消费 pending 池
   ├─ 碎片巡检(周频)              —— 对话碎片占比，超15%报警
   ├─ E11 原子技能冒烟            —— 真实 LLM 冒烟
   ├─ E12 路由器冒烟              —— 真实 LLM 冒烟
   ├─ T1  trace月度归档           —— 热窗30天，归档前对账守恒
   └─ F20 基线守卫(experiment)     —— 只跑 beta=1.0 守卫基线，吞错恒退0
```

**E4 在链条中的角色**：清晨第一个"体检报告"，只读聚合前面各步留下的日志 → 产出 `logs/e4_maintenance.json`（含 `health` 四维）→ 晨报高亮。它不修改任何数据，是所有观测指标的**汇流点**。

---

## 四、#20 观测指标 → 数据源 → E4 字段 对照表

| 指标 | 数据源 | E4 health 字段 | 采集方式 |
|---|---|---|---|
| 检索次数/miss率 | gate_log.jsonl | `retrieve.gate_total` / `miss_rate` | Counter 聚合 |
| 日级趋势 | gate_log.jsonl | `retrieve.daily_trend` | 按 ts 前10字符分天 |
| 冲突队列积压 | conflict_queue.jsonl | `conflict.queue_total` / `pending` | status 分组 |
| 冲突三级分级 | relation_graph.jsonl | `conflict.cascade_dist` | cascade_layer 分桶 |
| 去重触发 | gate_log.jsonl | `dedup.gate_dedup_hits` / `simhash_bearing` | op/simhash 计数 |
| hold 老化 | store_pending.jsonl | `archive.max_hold_age_days` / `over14d` | hold_ts 距今天数 |
| 校准降级率 | memories.jsonl | `archive.demote_rate` | superseded_by 非空占比 |
| DB↔jsonl 一致 | DB vs jsonl | `items.db_jsonl_drift` | 集合差 |

---

## 五、旁路通道

```
PreCompact hook ──▶ 对话压缩前抢救高价值内容（不依赖主链）
E9 transcript 兜底 ─▶ 只读扫 applog 提取当日用户提交 → simhash 去重 → 隔离入 raw_corpus
PII 脱敏 ────────────▶ E9 落 body 前正则替换（手机/身份证/邮箱/银行卡/密钥）
```

---

## 附：本文档不覆盖

- cgraph 思维图内部节点语义（见 mind_graph.json + E6 巡逻）
- 元思域冲突对撞机制（见 conflict_queue 消费侧）
- LLM 后端协议细节（见 cgraph/llm_codechat.py）

以上为 #20 观测范围内完整链路。所有指标均已落地 E4，无 LLM 依赖。