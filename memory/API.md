# DFCP 记忆系统 AI 接入契约（API.md）

> 面向对象：**接入本记忆系统的其他 AI 模型/Agent**（非人类用户）。
> 原则：只写契约不写原理；禁止事项写成硬红线而非建议。
> 变更纪律：管线/接口任何变更，随提交同步本文档（工程 CLAUDE.md 已挂钩）。
> 工作目录：本工程根（`cognitive_engine_v3`）；所有命令相对此根执行。

---

## 0. 一分钟概览

```
读记忆  memory_gate.py probe → (true) memory_hook.py query
存对话  memory_gate.py dialog --file <md> --session <id>
存卡片  memory_hook.py add …（严格字段契约，见 §3）
治理    E1-E9 管线自动跑，接入 AI 不执行、不干预
终审    人闸（晨报），机器只提案——接入 AI 无裁决权
```

---

## 1. 读侧（每次会话开工）

### 1.1 深度检索门

```bash
python memory/memory_gate.py probe --topic "<当前任务意图>"
```

输出 JSON：`{"deep_retrieve": bool, "query_hint": [str], "reason": str}`

- `deep_retrieve=true` → 按 `query_hint` 逐条执行 1.2
- `deep_retrieve=false` → 跳过检索，直接开工（轻量基线记忆已在会话上下文，无需再拉）
- **失败/超时语义：保守按 true 处理**。读侧假阴性（漏读关键记忆）的代价远高于多查一次。

### 1.2 检索

```bash
python memory/memory_hook.py query "<query_hint 中的词>" --top 5
# 可选过滤: --domain 002-003 --behavior Jw
```

输出带 `verdict` 字段。`verdict ∈ {uncertain, marginal_ask, marginal_hit}` 时表示证据不足，**如实向用户转述不确定**，禁止编造命中内容。

### 强制直通

`/force-mem-on`（用户指令）→ 无视 probe 结果直接深度检索。这是**用户专属指令**，接入 AI 不得自行调用。

---

## 2. 对话原文归档（写侧之一）

```bash
python memory/memory_gate.py dialog --file <utf-8 md> --session <会话标识>
```

- body 非空才会入库；simhash 与当日已有段比对去重。
- **`duplicate-referenced` 不是错误**：表示同源段已存在，返回 `refs` 指向原段 id，引用即可，不得重复入库。
- 其他状态：`stored`（成功，返回 id）、`empty-skip`（空文件，静默跳过）。
- 触发时机：仅 PreCompact hook 或用户显式指令。**接入 AI 不得在每轮回复后自行调用**。

---

## 3. 记忆卡片写入（写侧之二）

```bash
python memory/memory_hook.py add \
  --domain <str> --behavior <str> --relation <str> \
  --confidence <L0-L4> --summary <str> [--detail <str>] \
  [--source <str>] [--asset-class <str>] \
  [--repro <flash|situational|derived>] [--event-ts <ISO>]
```

### 字段契约（踩坑固化，违者入隔离区）

| 字段 | 合法值 | 已知陷阱 |
|---|---|---|
| `confidence` | L0/L1/L2/L3/L4 五级枚举 | 与 repro 正交：confidence 管推理，repro 管存储 |
| `repro` | **{flash, situational, derived}** | `verdict` 是非法值（历史事故：报 stored 实际静默进隔离区）。合法枚举见 `memory/schema.py:69` |
| `detail` | 建议必带完整上下文 | 薄 detail 会被 judge Q2"定义本体不足"拦截（held），捞回需完整重写候选而非补投 |
| `event-ts` | ISO 格式 | 缺省=时间未知，不是"现在" |

### 写入红线

- **禁止直写 `core_memory_card` 表/库文件**。唯一合法入口就是本节 `add` 命令（经 judge 管道）。
- 提案类写入只能进 `data/memory/store_pending.jsonl`，由人闸晨报终审放行。

---

## 4. 管线与治理（接入 AI 只读不动）

### E/T 编号账本（编号=时间戳历史，退役不回收不重排）

| 编号 | 层 | 职责一句话 |
|---|---|---|
| E1 | core | 探针触发 |
| E2 | core | 门校准 |
| E4 | core | 维护巡逻 |
| E5 | core | 软层整理 |
| E6 | core | 思维图巡逻 |
| E7 | core | 夜间治理（提案/hold 复审/范式标定，只提案） |
| E9 | core | 对话治理（归档主通道 + transcript 兜底，只提案） |
| T1 | core | trace 月度归档 |
| E3 | audit | 已退役（缺号记录） |
| E8 | drill | 演习（独立调用，不在夜间主链） |
| F 系 | experiment | 未来推演实验（晋升门槛另见 README） |

调度：`scripts/nightly_run.py` 每日主链（schtasks 18:00/18:05）。**接入 AI 不得改调度、不得手动触发主链**（用户指令除外）。

### 数据文件读写权限

| 文件 | 权限 |
|---|---|
| `data/memory/gate_log.jsonl` | 追加（系统写）；接入 AI 只读 |
| `data/memory/gate_miss_case.jsonl` | 同上 |
| `data/memory/store_pending.jsonl` | 系统提案追加；接入 AI **只读**（放行=人闸） |
| `C:/Users/uidq0280/AppData/Roaming/DSV CodeChat Desktop/logs/applog.log` | **绝对只读**（平台 transcript；三规约见 E9 源码注释） |
| `data/memory/dialog_last_segment.md` | 会话 AI 在 compact 前覆写（工程 CLAUDE.md 纪律 2） |

---

## 5. 决策权边界（最重要）

1. **人闸终审**：E7/E9 晨报提案的放行/否决 = 人类用户专属。接入 AI 不得代行、不得批量自动放行。
2. **即时入库仅两类合法触发**：用户显式说"入库/记下来"或 `/force-archive`。接入 AI 不得扩大解释（"我觉得重要"不构成合法触发）。
3. **读侧可自主**（probe/query 接入 AI 可自由调用）；**写侧受限**（add 可用但过 judge；dialog 仅 hook/用户触发；治理全禁）。
4. 对管线产物的异议 → 记入 `gate_miss_case.jsonl` 等审计通道或报告用户，**禁止绕过管道直接修库**。

---

## 6. 排错速查

| 症状 | 含义 | 正确动作 |
|---|---|---|
| judge 报 stored 但卡片查不到 | 字段非法被静默隔离（历史上 repro=verdict） | 检查 §3 枚举，改合法值重投 |
| `duplicate-referenced` | 同源段已存在 | 引用 refs，不重投 |
| probe 超时/异常 | 判定失败 | 保守按 true 走检索 |
| cue_anchors 相关操作异常 | 双层序列化坑 | 传 list，勿预 dumps 成字符串 |