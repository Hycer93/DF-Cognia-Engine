# memory_judge 规则桩转正方案（LLM 五问判定 + 规则桩 fallback）

> 版本：v1.0（2026-11-21）
> 状态：**方案待确认，未编码**
> 对应台账：`docs/review_20261121_system_status.md` P1-1
> 用户裁决要点：LLM 走 `llm_codechat.py`（glm-5.3-flash，`agent_free=True`）；规则桩保留为强制 fallback；
> Q4 全库 O(N) 扫描本次不动，另立 P2-6 待办。

---

## 一、现状梳理（`memory/memory_judge.py` 逐段）

### 1.1 文件结构（277 行，5 个函数/入口）

| 行号 | 符号 | 职责 |
|---|---|---|
| 1-30 | 模块 docstring | 五问契约、三态输出语义、健壮性四隐患、职责边界 |
| 32-44 | 常量 | `JUDGE_BATCH_LIMIT=8`、`HOLD_MAX_AGE_DAYS=14`、`STORE_PENDING_JSONL`、`JUDGE_LOG_JSONL` |
| 50-54 | `_load_pending()` | 读 `store_pending.jsonl` |
| **57-140** | **`_judge_call(candidates, context)`** | **唯一判定入口（当前=规则桩），返回 `{idx: verdict_dict}`** |
| 143-212 | `judge_and_store(candidates, context)` | Jw 钩子入口：批量截断 → 调 `_judge_call` → 三态分流落库 + 审计 |
| 214-277 | `review_holds(context)` | hold 二次复审（第六问），转正/维持/丢弃 |

### 1.2 `_judge_call` 桩实现逐问现状（关键：这是要转正的部分）

```
Q1（行 72-74）   summary 空 或 len<8              → discard
Q2（行 76-81）   detail 空 或 len<30              → hold   (evidence_gap: detail 过薄)
Q3（行 87-111）  memory_class 白名单外字符串      → hold
                 memory_class=None + 特征词命中    → 放行 + class_missing_suspect 提示
                 behavior 与类先验不符             → 放行 + class_behavior_mismatch_suspect 提示
Q4（行 113-128） 全库 load_memories() 2-gram 重叠
                 best_overlap==0                   → hold   (evidence_gap: 缺血缘互指)
                 单条 overlap>=6                   → conflict_suspect=True（不拦截）
Q5（行 130-137） skill_refine 且含个体指涉词      → hold   (evidence_gap: 知识层/经验层存疑)
其余                                              → store
```

**桩的本质问题（已在评审确认）**：
- Q1/Q2 是纯长度判断，不判"是否真有新认知"；
- Q3 的 `_FEATURES` 是硬编码特征词 `in summary` 匹配；
- Q4 每次全库扫描（本次不动，见 P2-6）；
- Q5 只对 `skill_refine` + 4 个个体词做判断，语义层完全缺失。

### 1.3 必须保持不变的部分（用户明确约束）

| 项 | 说明 |
|---|---|
| 三态输出契约 | `store` / `hold` / `discard` 语义与分流动作一字不改（`judge_and_store` 行 191-211） |
| `judge_log.jsonl` 全量审计 | 每条候选一条 `op:"judge"` 快照，含 reasons/evidence_gap/snapshot，append-only |
| hold 二次复审 | `review_holds()` 完全复用，第六问逻辑不动 |
| 批量上限与截断 | `JUDGE_BATCH_LIMIT=8` + `batch_truncated` 日志 |
| 异常不丢不硬 discard | 判定失败 → 全部 hold + `judge_failure` 标记 |
| `memory_hook.add` 唯一落库口 | judge 不直接写库 |

### 1.4 `_judge_call` 返回契约（下游依赖，不可破）

```python
{
  int_idx: {
    "verdict": "store" | "hold" | "discard",
    "memory_class": str | None,
    "conflict_suspect": bool,
    "reasons": [str, ...],          # 五问逐条理由，进审计日志
    "evidence_gap": str | None,     # hold 专用，写入 pending 的 hold_reason
  }
}
```

---

## 二、LLM Prompt 设计

### 2.1 调用形态

```python
from cgraph.llm_codechat import CodeChatLLM
llm = CodeChatLLM(model="glm-5.3-flash", agent_free=True, timeout=120)
resp = llm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": user_payload}])
```

- **后端**：`llm_codechat.py`，`TEST_MODEL="glm-5.3-flash"`（与原子技能/协同冒烟/A/B 基线一致）
- **`agent_free=True`**：去 `task_id` + system 并入首条 user，走纯文本直返路径，保证输出纯 JSON、不触发服务端工具编排（`llm_codechat.py:104-120` 已实测）
- **超时**：120s（单批 ≤8 条，单条判定应远快于此；超时即 fallback）
- **返回 `None`**（请求异常，`llm_codechat.py:142`）→ 视同失败走 fallback

### 2.2 System Prompt（草案）

```
你是记忆入库判定卡。对每条候选记忆回答五问，只输出 JSON，不要任何解释文字、不要 markdown 围栏。

五问（门槛链，任一不过即降级）：
Q1 新认知：这轮是否产生了新的、可复用的认知？占位/寒暄/重复表述 → 不算。
Q2 定义本体：是否有实质内容（具体做法/结论/证据），而非空壳标题？
Q3 归类：归入且仅归入以下五类之一，或 null（普通记录）：
   skill_refine（方法/工程改进）、scar_case（踩坑/失效教训）、
   strategy_evolve（路线/裁决演进）、relation_milestone（实体血缘打通）、
   key_pact（规约/契约/边界约束）。
Q4 血缘：与已有记忆是否有同源/实体链关系？孤条无血缘 → 降权。
   若与库内条目疑似高度同源（可能重复/冲突）→ conflict_suspect=true。
Q5 分池：知识层（可复现方法论，须去个体化）还是经验层（事故/裁决/里程碑，可含个体性）？

输出 JSON（严格）：
{"verdicts":[
  {"idx":0,"verdict":"store|hold|discard","memory_class":"skill_refine|null",...
   "conflict_suspect":false,"reasons":["Q1...","Q2..."],"evidence_gap":null}
]}
verdict 规则：五问全过=store；有可补缺口=hold（evidence_gap 写清缺什么）；无新认知=discard。
```

### 2.3 User Payload（批量 ≤8 条）

```json
{
  "context": {"trigger": "fix|decision|session_end", "entity_hint": ["inst_id..."]},
  "candidates": [
    {"idx":0, "summary":"...", "detail":"...(截断至 800 字)", "memory_class":"...",
     "domain":"...", "behavior":"...", "relation":"...", "confidence":"...",
     "bloodline_hint": "Q4 本地预计算的 best_overlap 数值（辅助 LLM 判血缘）"}
  ]
}
```

> 注：Q4 的本地 2-gram 预计算**保留**（不删 `load_memories()` 扫描），作为 `bloodline_hint` 传给 LLM 辅助判断，既省 LLM 猜测又复用现有逻辑。性能优化待 P2-6。

### 2.4 输出 JSON Schema（解析后强校验）

| 字段 | 类型 | 校验 |
|---|---|---|
| `verdicts` | list | 必须存在且为 list |
| `idx` | int | 必须落在 `[0, len(candidates))` |
| `verdict` | str | ∈ {store, hold, discard}，否则该条 fallback |
| `memory_class` | str\|null | ∈ MEMORY_CLASS_ENUM 或 null，否则取候选原值 |
| `conflict_suspect` | bool | 非 bool 强转 `bool()` |
| `reasons` | list[str] | 缺失补 `["llm_no_reason"]` |
| `evidence_gap` | str\|null | hold 时缺失补默认串 |

**逐条粒度容错**：单条解析畸形只影响该条（fallback 到桩），不整批失败。

---

## 三、调用链路设计

### 3.1 入口与分支

```
judge_and_store(candidates, context)
  └─ 批量截断（不变）
  └─ _judge_call(candidates, context)      ← 改造这里
       ├─ [开关] JUDGE_USE_LLM==0 或 环境无 token → 直接走 _judge_call_stub（原桩逻辑原样搬迁）
       └─ _judge_call_llm(candidates, context)
            ├─ 构造 prompt/payload
            ├─ llm.chat(...)
            │    ├─ 返回 None（请求异常）      → raise/return None → 上层 fallback
            │    └─ 返回文本
            ├─ extract_json_obj(resp)（复用 P0-1 的 cgraph/json_utils）
            │    └─ None（解析畸形）           → fallback
            ├─ 逐条校验 schema
            │    ├─ 合法条 → 收进 out[i]
            │    └─ 非法条 → out[i] = _stub_one(c, i)（单条降级）
            └─ 缺失 idx（LLM 漏答）→ 该 idx 用 _stub_one 补齐
```

### 3.2 fallback 触发条件（任一命中即整体/单条降级）

| 触发条件 | 粒度 | 处理 |
|---|---|---|
| `JUDGE_USE_LLM=0` | 整体 | 全走桩 |
| `extract_token()` 抛异常（未登录/token 过期） | 整体 | 全走桩 + 记 `judge_llm_unavailable` |
| `llm.chat()` 返回 None（网络/超时/服务端异常） | 整体 | 全走桩 + 记日志 |
| `extract_json_obj` 返回 None（畸形输出） | 整体 | 全走桩 + 记日志 |
| 单条 verdict 非法 / idx 缺失 | **单条** | 仅该条 `_stub_one`，其余用 LLM 结果 |

> **关键纪律**：fallback 到桩**不改变三态语义**——桩走完后照常 store/hold/discard，`judge_and_store` 下游完全无感。这样夜间管线、影子回归、A/B 都拿到可用结果。

### 3.3 函数拆分（`_judge_call` 拆 3 个）

```
_judge_call(candidates, context)        # 调度器：开关判断 + LLM 尝试 + fallback 编排
_judge_call_llm(candidates, context)    # LLM 路径：prompt→chat→parse→校验，失败返 None
_judge_call_stub(candidates, context)   # 现桩逻辑原样搬迁（零行为变更）
_stub_one(c, i)                         # 桩单条判定（供 LLM 单条降级复用，从 _judge_call_stub 抽出）
```

> 搬迁纪律：`_judge_call_stub` 内含 Q1-Q5 的所有判断**逐字保留**（含 Q4 全库扫描），保证 `JUDGE_USE_LLM=0` 时字节级行为等价。

---

## 四、不变部分（红线）

1. **三态输出**：`store`/`hold`/`discard` 语义、分流动作、`conflict_suspect` 打标进 detail —— 不动
2. **`judge_log.jsonl`**：每条候选仍写 `op:"judge"` 快照（含 reasons/evidence_gap/snapshot）—— 不动；LLM 路径额外加 `judge_source: "llm"|"stub"` 便于审计区分
3. **hold 二次复审** `review_holds()` —— 完全不动
4. **批量上限截断** —— 不动
5. **异常不丢不硬 discard** —— 不动（现有 `judge_and_store` 的 try/except 保留）
6. **`memory_hook.add` 唯一落库口** —— 不动

---

## 五、回归用例规划

### 5.1 新增：`eval/audit/memory_judge_llm_regression.py`（mock LLM）

| 用例 | mock 行为 | 断言 |
|---|---|---|
| L1 LLM 正常返回 | mock chat 返回合法 JSON（1 条 store） | verdict=store，`judge_source=llm`，落库 1 条 |
| L2 LLM 解析失败 | mock chat 返回 `"这不是JSON"` | 自动切桩，`judge_source=stub`，桩判定生效 |
| L3 LLM 调用异常 | mock chat 返回 None / 抛异常 | 自动切桩，不丢候选，report 正常 |
| L4 LLM 单条漏答 | 返回 2 条候选只答 1 条 | 缺的 idx 用桩补齐，总数守恒 |
| L5 LLM 单条 verdict 非法 | 返回 `verdict:"maybe"` | 该条降桩，其余用 LLM |
| L6 开关关闭 | `JUDGE_USE_LLM=0` | 全走桩，`judge_source=stub`，与现桩输出字节级等价 |

### 5.2 保留：存量桩用例

- `JUDGE_USE_LLM=0` 下跑现有夜间/影子回归，确认桩逻辑零变更
- 建议在 `eval/audit/` 新增 `memory_judge_stub_regression.py`：固化桩的 Q1-Q5 三态判定矩阵（8~10 条典型样本 × 期望 verdict），防搬迁时手滑改逻辑

### 5.3 回切开关

- `JUDGE_USE_LLM=0` → 纯桩模式（A/B 对照、离线回归用）
- 默认（未设置）→ LLM 优先 + 桩 fallback
- 建议再加 `JUDGE_LLM_MODEL` 可覆盖模型（默认 glm-5.3-flash）

### 5.4 真实 LLM 冒烟（可选，不进 nightly）

- 手动跑 `python -m eval.audit.memory_judge_llm_smoke`（真实调用 1~2 条样本），确认 token 可用 + JSON 守约。避免进 nightly 消耗配额。

---

## 六、待办登记

| 编号 | 内容 | 优先级 |
|---|---|---|
| P2-6 | memory_judge Q4 血缘检测 O(N) 全库扫描性能优化：按 domain/behavior 预聚合血缘 gram 快照，或加缓存；先评估样本量级再设计 | 🟡 P2 |

---

## 七、实施步骤（确认方案后）

1. 建 `eval/audit/memory_judge_stub_regression.py` 固化现桩行为（先测后改，防回归）
2. 抽 `_stub_one` + `_judge_call_stub`（原逻辑搬迁），跑 5.2 确认零差异
3. 写 `_judge_call_llm` + prompt
4. 改 `_judge_call` 为调度器（开关 + fallback）
5. 建 `eval/audit/memory_judge_llm_regression.py`（mock 6 用例）
6. 跑 `JUDGE_USE_LLM=0` 全量回归 + 默认模式 mock 回归
7. 提交（代码 + 两个回归脚本 + 本文档 P1-1 状态更新）

---

## 八、风险与权衡

| 风险 | 缓解 |
|---|---|
| LLM 抖动导致判定不稳定 | 规则桩兜底 + 逐条粒度假 + `judge_source` 审计可追溯 |
| glm-5.3-flash 不守 JSON | 已实测该模型守约（`llm_codechat.py:31-33` 夜跑裁决）；再加 `extract_json_obj` 容错 + 单条降级 |
| token 依赖本机登录态 | `extract_token()` 失败整体降桩，不阻断；夜间无登录态时自动退化为桩（行为等同现状） |
| 引入 LLM 后判定变慢 | 单批 ≤8 条一次调用；120s 超时；夜间管线本就异步 |
| 桩/LLM 判定口径漂移 | `judge_source` 标记 + A/B 对照实验（`JUDGE_USE_LLM=0` vs 默认）可比 |