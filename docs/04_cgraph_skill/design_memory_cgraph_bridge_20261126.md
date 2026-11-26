# 记忆系统 ↔ 元核思维 联动方案（三问 + 三级 + 原文溯源）

- 日期：2026-11-26
- 状态：**已裁决，待实施**（本文只出方案，未改码）
- 涉及子系统：`cgraph/`（元核思维/思维框架图引擎）、`memory/`（记忆治理体系）
- 用户确认口径：
  - “三级记忆” = `schema.REPRO_TIERS`（flash / situational / derived），决定存储预算
  - 本轮范围 = 只出方案 + 三项裁决定稿；实施按 §4 顺序（A+C 同批 → 回归冒烟 → 再上 B）

---

## 0. 一句话结论

联动目前是**半通**：思维跑完确实能把绿档卡写进记忆库（`cgraph/green_card_writer.py`），
但存在两处断层——①标签写死（不论走哪个节点都记成 `Ht/P/L2`）、②绕过五问 judge（绿档直插 SQL）。
另外“原文入库”通道已现成（`memory_hook` session 模式），缺的只是与记忆卡的双向指针。本方案给三段改造设计。

---

## 1. 现状测绘（证据）

### 1.1 cgraph → memory 的桥（已存在）
| 位置 | 行为 |
|---|---|
| `cgraph/engine.py:114-124` | 每轮把 `save_chat` / `save_flash` 写入 **ChromaDB**（`cgraph/memory.py`，向量库，与 memory/ 无关） |
| `cgraph/executor.py:96,219` | `_gate_and_commit()` → `commit_green_cards()` 绿档落库 |
| `cgraph/green_card_writer.py:79,111,172-183` | import `memory.memory_organize`，双写 `core_memory_card` 表 + `memories.jsonl` |

> **注意**：存在**两套并行记忆**——`cgraph/memory.py`（ChromaDB，引擎上下文注入用）与 `memory/`（SQLite+JSONL，正式治理库）。二者零互通。本方案动的是后者（正式库），ChromaDB 那套维持不动。

### 1.2 断层①：绿档标签写死
`green_card_writer.py:_sync_committed_to_jsonl()` 硬编码：
```python
"dfcp_tag": {"domain": "001-003", "behavior": "Ht", "relation": "P", "memory_class": None},
"confidence": "L2",
```
→ 思维产出记进库，但**丢失“哪个节点 / 什么置信度”的元信息**，后续任何按 behavior/domain 的分析全失真。

**根因**：`_gate_and_commit(user_input, node_outputs, walked)` 调 `commit_green_cards(result, gate, user_input)` 时，
**没有把 `walked`（实际路径）和 `WalkResult.repro_hints` 传进去**，writer 拿不到真实节点码，只能写死。

### 1.3 断层②：绕过五问 judge
- 五问定义在 `memory/memory_judge.py`（Q1 新认知 / Q2 本体 / Q3 归类 / Q4 血缘 / Q5 分池）。
- 唯一调用方：`memory_hook.py:354` `judge_and_store()`。
- 绿档卡走 `commit_green_cards` → 直接 SQL INSERT + append_jsonl，**完全不经 Q1–Q5**。

### 1.4 三级（REPRO_TIERS）现状
`schema.py:75-79`：
```
flash       三级不可复现 → summary+detail+source 全量永久
situational 二级条件可复现 → summary+detail；source 只存受限入口
derived     一级完全可复现 → 仅 summary；source=检索路径指针
```
- 判定器：`memory_organize.judge_repro(content, tags)`（规则源），或上游 `add(repro=...)` 显式覆盖（`repro_source=manual`）。
- 绿档当前写死 `repro="situational"`（`green_card_writer.py:110` 附近），也是写死项之一。

### 1.5 三级 × 思维节点的天然映射（本方案的核心 insight）
`executor.py` 已试点让节点自报 repro 增量（`repro_hints`，`PILOT_REPRO_NODES=("Ht","Jw")`，2026-11-25 dry-run，只收集不入库）。
这说明**思维节点本来就有“可复现性”语义**，只是被上层丢掉。三级记忆与思维的正确挂法是：

| 节点产出类型 | 建议 repro | 理由 |
|---|---|---|
| 确定性聚合（Bc/Ch/Ht/Fp/Fq 有公式） | `derived` | Python 可重算出同一结论 → 只存摘要+指针 |
| 条件可复现（Cf/Cg/Di/Dj/Dk/Eo 依赖情境） | `situational` | 需摘要+详情，源只留受限入口 |
| 一次性踩坑/观测（green 卡常见） | `flash` | 不可复现 → 全量永久 |

> 即：**repro 不再由 writer 写死，而由“产出节点码”推导**，直接复用节点已有的 repro_hints 语义。

---

## 2. 改造设计（三段，彼此可独立落地）

### 改造 A：绿档标签动态化（最小闭环，建议先做）

**目标**：`dfcp_tag` / `confidence` / `repro` 来自真实路径，不再写死。

**a) 传参打通**（`cgraph/executor.py`）
```python
# 现状
self._gate_and_commit(user_input, node_outputs, walked)
def _gate_and_commit(self, user_input, node_outputs, walked):
    ...
    commit_green_cards(result, gate, user_input)

# 改为（多传 walked + repro_hints）
def _gate_and_commit(self, user_input, node_outputs, walked, repro_hints=None):
    ...
    commit_green_cards(result, gate, user_input, walked=walked, repro_hints=repro_hints or {})
```
`run()` 调用处补 `repro_hints=repro_hints`（此时 `repro_hints` 已在 run 内组装好，见 `executor.py:118-193`）。

**b) writer 推导标签**（`cgraph/green_card_writer.py`）
新增纯函数 `_derive_tag(card, walked, repro_hints)`：
```python
# behavior：node_Ch 前缀剥离得到节点码 Ch；解析失败回落 behavior="Ht" + 审计日志，绝不阻断落卡
# domain：按节点码查 NODE_DOMAIN 表（新增小常量表，见下）
# confidence：green 卡固定 L2 起步；若 gate_result.confidence≥0.7 且 repro=derived → 升 L3
# repro：优先 repro_hints[node]["repro_hint"]；缺则按 §1.5 表由节点码推导；再缺 → situational
# relation：默认 "P"（前驱），与原行为一致（改 relation 语义面大，本轮不动）
```
新增常量（放 `green_card_writer.py` 顶部，或更合宜地放 `memory/schema.py` 旁）。
下列映射已逐项跑过 `schema.validate_domain()` **全部 PASS**（2026-11-26 核验）：

```python
NODE_DOMAIN = {
    "Aa": "005-004",  # 任务空间扫描   → 认知科学-元思域
    "Bb": "005-004",  # 信息源头意图   → 认知科学-元思域
    "Bc": "005-006",  # 最弱环节定律   → 认知科学-方案攻防与专利评审
    "Cf": "005-004",  # 三步认知法     → 认知科学-元思域
    "Cg": "004-002",  # 利益者全景图   → 组织行为学-跨部门沟通
    "Ch": "005-001",  # 多维状态机校验 → 认知科学-设计裁决
    "Di": "005-004",  # 第一因溯源链   → 认知科学-元思域
    "Dj": "005-002",  # 逆向第一因     → 认知科学-用户洞察
    "Dk": "005-008",  # 直觉可验证锚定 → 认知科学-自我锚定与利害结算
    "Eo": "005-004",  # 上下文等价性   → 认知科学-元思域
    "Fp": "005-006",  # 动态风险量化   → 认知科学-方案攻防与专利评审
    "Fq": "005-001",  # 周期性出清     → 认知科学-设计裁决
    "Ht": "005-001",  # 决策整合       → 认知科学-设计裁决
    "Jw": "005-003",  # 经验进化闭环   → 认知科学-记忆自我管理
}
```
> 注意：`validate_domain` 要求 **2 段**（域-子域），单段如 `004`/`003` 会被拒（“编码段数错误”）。
> 上表已规避该坑。实现若新增节点，务必先过 `validate_domain` 再入表。

**c) 落库前预检 + fallback 兜底（用户 2026-11-26 裁决，必做）**
写库前对推导出的 tags 调 `schema.validate_memory_tags()`；不合法 → 回落默认值 + 落 `green_card_tag_fallback` 审计（**不阻断落库**）。
**节点码解析失败的兜底口径（硬约束）**：`evidence_step` 解析不出合法节点码 → 自动回落 `behavior="Ht"` + 写审计日志，**不阻断落卡**（宁可用旧默认也不能漏卡）。

**验收**：造 3 条不同路径的假 gate_list（含非 Ht 节点），断言落库后的 `dfcp_tag.behavior` 等于真实节点码。

---

### 改造 B：思维产出入库过五问 judge（中风险，需回归）

**用户裁决（2026-11-26）**：接受思维收尾多一次 LLM 调用；**优先原版 LLM judge，不用纯规则桩**。
理由：五问本身是带语义判断的治理关口，纯规则桩会阉割 Q1/Q4 这类语义校验能力，与整套记忆治理的设计初衷冲突。

**目标**：绿档/思维产出与人工 `add()` 走**同一道 Q1–Q5 关**，避免“思维写卡免检”的口子。

**设计要点**
1. writer 不再直接 INSERT，改为把绿档卡组装成 `judge_and_store()` 的 `candidates` 格式：
   ```python
   candidates = [{
       "summary": card["gist"], "detail": _green_detail(x),
       "source": f"memory_gate; session {day}", "event_ts": ts,
       "domain": tag["domain"], "behavior": tag["behavior"],
       "relation": tag["relation"], "confidence": tag["confidence"],
       "asset_class": "runtime", "repro": tag["repro"], "memory_class": None,
   } for ...]
   report = judge_and_store(candidates, context={"trigger": "cgraph_gate", "session_id": day})
   ```
2. `judge_and_store` 返回 store/hold/discard 三态：
   - `store` → 走原 `atomic_store`（与人工一致）
   - `hold` → 进 `store_pending.jsonl`（人闸/夜间 E9 复审，**不丢**）
   - `discard` → 记 `judge_log.jsonl`
3. **开关 `memory.cgraph_gate_via_judge`（默认 `false`），分三段推进（硬约束）**：
   - **① shadow 并行采集**：同一份卡**同时**走「旧直写」+「judge」两条路，**只对比结果、不改变入库行为**（入库仍走旧直写）。此阶段产出观测日志即可。
   - **② shadow 观测指标**：通过率、hold 占比、discard 占比、单次 judge 耗时、失败率。
   - **③ 灰度切 true 的条件**：连续稳定跑够样本、指标波动收敛、**无大规模误 hold / 误丢**，方可切 `true`（切换后入库才真正改走 judge 判定）。
4. **异常路由（硬约束）**：judge 调用异常（超时、报错、畸形返回）**统一路由到 `hold`**，写入 `judge_log`，**绝不丢弃思维产出的绿卡**。

**风险**：judge 依赖 LLM，绿档当前是零 LLM 直写；接 judge 后思维收尾多一次 LLM 调用（延迟 + 可失败）。
**缓解**：judge 失败 → `hold`（`memory_judge.py` 已实现“异常→hold 不丢”，与本裁决一致）。shadow 阶段不改入库行为，量化评估对思维主链尾延迟影响后再切。

**验收**：
- shadow 阶段：同 3 条假 gate_list，入库行为与改前完全一致（零回归）；同时 judge 对比日志可读。
- 切 true 后：产出经 judge_log 可回溯；注入 mock 超时 → 断言落 hold 而非丢弃。

---

### 改造 C：原文双向溯源（补齐，低风险）

**目标**：用户要求“最后将原文保存进记忆系统”——通道已现成，本改造只做**指针闭环**。

**现状**（已可用，无需新建）：
- `memory_hook.py cmd="session"` → `memory_gate.store_dialog()` 存原文进 `raw_corpus(kind=session_dialog)`（simhash 去重）
- `dialog_evidence_store.py` 存逐条消息 `core_dialogue_log` + 整会话 JSON `core_raw_dialog_session`
- `aggregate_session(ref_archive_fragment=<inst_id>)` 已能把会话挂到某条记忆的 inst_id 上
- 反向查询：`memory_query.cards_from_dialog(dialog_key)` 能从对话反查产生的卡

**缺的**：思维产出的绿档卡，`source_ref` 目前是 `"memory_gate; raw锚: session {day}; user_input=..."`（字符串拼接），
**不是可解析的 `sqlite:...#tree.address` 格式**，导致 `fetch_by_ref()` 无法从卡跳回原文全文。

**改法（用户 2026-11-26 裁决，硬约束）**
1. writer 落库时，`source_ref` 采用规范化指针：`session:<session_id>#card:<inst_id>`。
2. **同步扩展 `memory_query._parse_source_ref()`** 解析器，兼容新指针格式。
3. **存量旧字符串格式做兼容保留**：解析器**优先识别新指针格式**；识别失败时**降级为旧文本模式 + 告警日志**，不破坏老卡溯源。
4. 若同会话已有 `raw_corpus` 行（`store_dialog` 写的），在 `aggregate_session(ref_archive_fragment=inst_id)` 里回填该卡的 `inst_id`，形成 `session → card → 原文` 三向可达。
5. 新增只读校验脚本 `scripts/verify_memory_origin_link.py`：**必须同时覆盖旧格式、新格式两种 case**，断言 `fetch_by_ref(source_ref)` 能取回非空原文。

**验收**：抽 5 张思维产出的绿档卡，全部能 `fetch_by_ref` 回溯到原对话文本；脚本对旧格式、新格式各跑一组均 PASS。

---

## 3. 三级记忆 × 五问 × 原文 的整体闭环（改造后）

```
元核思维跑一轮
  ├─ 走链产出 node_outputs + walked + repro_hints
  ├─ skill_memory_gate → gate_list（绿/黄/红）
  └─ commit_green_cards
        ├─[A] _derive_tag: 由 walked 节点码推导 domain/behavior/confidence/repro
        ├─[B] judge_and_store: Q1新认知→Q2本体→Q3归类→Q4血缘→Q5分池
        │      store → atomic_store（三级 repro 决定存多少）
        │      hold  → store_pending（人闸/E9 复审）
        │      discard → judge_log
        └─[C] source_ref 规范化为可解析指针 → 可 fetch_by_ref 回原文
```

- **五问挂钩点**：改造 B（入库判定关）。
- **三级挂钩点**：改造 A（repro 由节点码推导，替代写死 situational）。
- **原文挂钩点**：改造 C（source_ref 指针闭环 + aggregate_session 回填）。

---

## 4. 落地顺序与风险（用户 2026-11-26 裁决：顺序不变）

| 序 | 改造 | 风险 | 依赖 |
|---|---|---|---|
| 1 | A 标签动态化 + C 原文溯源（同批次） | 低（纯映射+回落+审计；source_ref 格式+扩解析器+校验脚本） | NODE_DOMAIN 已全过 validate_domain（14项）；需扩 `_parse_source_ref` 兼容新旧两种 |
| 2 | B 五问挂钩 | 中（引入 LLM 延迟/失败面） | shadow 开关 `memory.cgraph_gate_via_judge`（默认 false） |

> **A + C 同批次提交**；稳定回归、冒烟跑完之后，再单独上 B（五问 judge 接入，带 shadow 开关）。

## 5. 不改动的部分（明确边界）
- `cgraph/memory.py` 的 ChromaDB 三集合：维持并行存在，**继续隔离，不与正式 memory 库互通**。
- `memory/trace_archive.py`：trace 流水归档，与对话原文无关，不动。
- **`relation` 语义：本轮全部不改，维持默认 `P`**（改 relation 会牵动血缘图谱判定）。

## 6. 用户裁决定稿（2026-11-26）

| # | 事项 | 裁决 |
|---|---|---|
| 1 | NODE_DOMAIN 映射 | ✅ 14 条全部采纳；**Bc 归 `005-006`（方案攻防与专利评审）**——它是最弱环节/风险校验节点，不属于通用元思域。上线保留 fallback：解析失败自动回落 `Ht` + 日志审计，不阻断落卡 |
| 2 | 改造 B 五问接入 | ✅ 接受多一次 LLM 调用，**用原版 LLM judge 不用纯规则桩**（纯桩会阉割 Q1/Q4 语义校验）。开关默认 false，先 shadow 并行（同卡双跑，只对比不改入库），观测通过率/hold 占比/discard 占比/耗时/失败率；指标收敛且无大规模误 hold/误丢再灰度切 true；judge 异常统一路由 hold + 写 judge_log，绝不丢弃绿卡 |
| 3 | source_ref 格式 | ✅ 采用 `session:<session_id>#card:<inst_id>`；**同步扩展 `_parse_source_ref`** 兼容新格式；旧字符串格式保留，解析器优先识别新指针、失败则降级旧文本模式 + 告警，不破坏老卡溯源；`verify_memory_origin_link.py` 必须覆盖新旧两种 case |
| 4 | 落地顺序 | ✅ A、C 同批 → 回归冒烟 → 再单独上 B（带 shadow） |
| 5 | 边界备忘 | ✅ 本轮不改 relation（维持 P）；ChromaDB 那套 cgraph 本地向量记忆继续隔离，不与正式 memory 库互通 |

---

## 7. 实施记录（A+C 已落地，2026-11-26）

### 7.1 改动文件
| 文件 | 改动 | 行数 |
|---|---|---|
| `cgraph/green_card_writer.py` | 新增 NODE_DOMAIN(14)、DERIVED_NODES、`_node_code`/`_log_tag_fallback`/`_derive_tag`/`_make_source_ref`；`commit_green_cards` 增参 walked/repro_hints/session_id；INSERT 与 `_sync_committed_to_jsonl` 改用推导标签 + 新 source_ref | +171 |
| `cgraph/executor.py` | `import os`；`_gate_and_commit` 增 repro_hints 参数，透传 walked/repro_hints，session_id 从 `DF_SESSION_ID` 环境变量取 | +13 |
| `memory/memory_query.py` | `_parse_source_ref` 扩展：优先识别 `session:<id>#card:<inst>` 新指针（经 `list_dialog(session_id=)` 反查树地址）；旧 sqlite 格式不变；无法解析降级 None | +27 |
| `scripts/verify_memory_cgraph_bridge.py` | 新增冒烟验收（34 断言，3 组） | 新文件 |

### 7.2 关键实现决策
1. **fallback 保守回落**：节点码解析失败 → behavior=Ht，但 `repro` **不沿 Ht 的 derived 属性**，强制回落 `situational`（来源未知的弱卡不冒认"可确定性重算"），`repro_source=fallback`。这是编码时发现的语义坑，已验证修复。
2. **session_id 来源**：executor 侧无会话上下文，从 `DF_SESSION_ID` 环境变量取；缺省则 source_ref 退化旧文本锚 `memory_gate; raw锚: session <day>; ...`（解析器已兼容降级），不破坏老卡溯源。
3. **`memory` 名称抢占坑**：`cgraph/memory.py` 会在 cgraph 单独入 sys.path 时抢占 `memory` 名称，导致 writer 内部 `from memory import memory_organize` 静默失败。生产从根目录运行不受影响（命名空间包正常）。测试脚本已锁定只加仓库根。

### 7.3 验收结果（2026-11-26）
- 冒烟 `verify_memory_cgraph_bridge.py`：**34 PASS / 0 FAIL**
  - 组1 标签推导（Ch/Di/Bc/hint覆盖/fallback/14项validate_domain）
  - 组2 source_ref（新指针构造+解析、旧 sqlite、旧文本锚降级、空/None 安全、无匹配返回 None）
  - 组3 端到端假 gate_list（临时库落库 3 卡，behavior 含真实 Ch/Di + 回落 Ht；Ch repro=derived+conf=L3；jsonl 同步一致）
- 既有回归：`test_skill_memory_gate.py` 25 PASS / 0 FAIL；`smoke_test.py` 全通。
- 生产库未污染：`memories.jsonl` 仍 333 条、mtime 未变、无测试特征串。

### 7.4 下一步
A+C 已就绪，待提交。提交并回归冒烟通过后，再启动 **改造 B**（五问 judge 接入，带 `memory.cgraph_gate_via_judge` shadow 开关）。

---

## 8. 改造 B 实施记录（五问 judge 接入，2026-11-26）

### 8.1 改动文件
| 文件 | 改动 |
|---|---|
| `cgraph/green_card_writer.py` | 新增开关 `_cgraph_gate_via_judge()`、候选构造 `_cards_to_candidates()`、shadow 观测 `_shadow_judge()`、judge 真判定 `_judge_gate_commit()`；`commit_green_cards` 改为「true→judge 判定入库（提前 return，不双写）/ false→原直写+末尾 shadow 观测」互斥分派 |
| `config/config.example.yaml` | 新增 `memory.cgraph_gate_via_judge: false`（含注释；环境变量 `CGRAPH_GATE_VIA_JUDGE` 覆盖） |
| `scripts/verify_memory_cgraph_bridge.py` | 追加组4（20 断言）：开关默认 false、候选构造、shadow 观测/5项指标、shadow 异常、judge 异常路由 hold、true 模式不双写 |

### 8.2 关键实现决策
1. **true/false 互斥而非叠加**：初版把 judge 放在直写之后 → true 模式会「直写 + judge 再入库」双写。重构为 true 提前 return 走 `judge_and_store`，false 才走原直写；两分支不共存。
2. **shadow 复用 `_judge_call` 而非 `judge_and_store`**：`judge_and_store` 的 store 分支会真实 `memory_hook.add` 入库，shadow 绝不能碰。改用 `memory_judge._judge_call`（纯判定、零副作用），只写 `logs/cgraph_judge_shadow.jsonl` 对比日志。
3. **异常一律 hold + 不丢卡**：shadow 注入异常→`judge_failure=True` 且该条按 hold 计；true 模式 judge 异常由 `judge_and_store` 内部路由 hold + 写 pending（已验证 held=候选数、stored=0）。
4. **观测五项指标**：`pass_rate / hold_rate / discard_rate / elapsed_ms / judge_failure`（+ judge_source、逐条 verdict），全部落 shadow 日志。

### 8.3 验收结果（2026-11-26）
- 冒烟 `verify_memory_cgraph_bridge.py`：**54 PASS / 0 FAIL**（组1-3 A+C 34 + 组4 B 20）
- `skill_memory_gate` 回归：25 PASS / 0 FAIL
- `smoke_test`：全通（含 mock LLM JSON 降级为历史固有行为）
- 生产库零污染：`memories.jsonl` 333 条、mtime 13:09 未变、测试串 0（4f 用例 `memory_hook.add` 已打桩，未真入库）
- 开关默认 false：`_cgraph_gate_via_judge()` 返回 False

### 8.4 灰度切 true 的准入（编码阶段不做切换）
shadow 跑够样本 → 通过率/hold/discard 占比/耗时/失败率指标收敛 → 无大规模误 hold/误丢 → 方可设 `CGRAPH_GATE_VIA_JUDGE=1` 或改 config true。

---

## 9. Shadow 观测台账（改造B 灰度前必读）

### 9.1 观测工具
`scripts/analyze_cgraph_judge_shadow.py`（只读）：汇总 `logs/cgraph_judge_shadow.jsonl`，
输出五项指标均值/中位/极值 + 单卡裁决分布 + judge_source 分布 + 自动告警。

### 9.2 采集纪律（样本真实性）
- 采集 shadow 样本时**必须 `JUDGE_USE_LLM=1`**（默认即 1），剔除 stub 样本。
  stub（`JUDGE_USE_LLM=0`）的延迟、裁决分布全部失真，不能作为切 true 依据。
- 脚本会统计 `judge_source` 分布并告警 stub 占比，据此判断样本是否可用于评估。

### 9.3 ⚠️ 已知长尾风险（2026-11-26 实测，必须记入台账）
- **实测**：首轮 shadow 真调 LLM，单次 judge `elapsed_ms = 378370`（**约 378 秒**）。
- **风险定性**：judge 是同步调用，若切 true 后落在元核思维收尾主链，将严重阻塞主思维链。
- **切 true 的前置硬约束（二者至少其一）**：
  1. 给 judge 单独加**超时熔断 + 兜底 hold**（超时即路由 hold，不阻塞主链判死）；
  2. 或改为**异步 judge 链路**（judge 脱离主思维链同步路径，后台判定后再落库/pending）。
- **结论**：长尾未解决前，**禁止同步切 true**；此风险为灰度准入的否决项之一。

### 9.4 重点盯的两项指标
1. **`elapsed_ms.max`（长尾延迟）**：脚本直接展示 max；378s 级别不可接受为同步路径。
2. **discard 理由分布**：区分「合理 discard」（如空 summary/占位卡无新认知）与「误 discard」。
   需逐条看 verdicts[].reasons，不能只看 discard_rate 总量。

### 9.5 当前状态
- 开关 `memory.cgraph_gate_via_judge = false`（shadow 模式），保持不动。
- 尚未切 true；切 true 需：样本充分（真实 LLM）→ 五项指标收敛 → 无大范围误 hold/误丢 → 长尾熔断/异步方案就位。
