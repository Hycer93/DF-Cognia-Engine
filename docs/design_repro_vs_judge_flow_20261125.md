# 记忆入库判定流程：三级可复现（repro）+ 五问判定 关系与规则

> 生成 2026-11-25。区分"专利设想的判定单元"与"系统实际实现"，钉死两者关系与缺口。
> 相关文件：`memory/memory_organize.py`(repro)、`memory/memory_judge.py`(五问)、`memory/memory_hook.py`(入库入口)、`memory/schema.py`(枚举)

---

## 一、总流程图

```
                        业务/会话结束触发 Jw
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │ judge_and_store(candidates)   │  ← 五问判定（LLM/桩）
                 │  memory_judge.py:371          │
                 └──────────────────────────────┘
                                │
                    _judge_call() 五问判定
                    Q1新认知→Q2本体→Q3归类→Q4血缘→Q5分池
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              verdict=store           verdict=hold / discard
                    │                       │
                    │                  hold→写 pending 池，等 review_holds 复评
                    │                  discard→只记日志（不算积压）
                    ▼
        ┌──────────────────────────────┐
        │ memory_hook.add(repro=...)    │  ← repro 从候选带下来（多数是 None）
        │  memory_hook.py:26            │
        └──────────────────────────────┘
                        │
                atomic_store(tags)
                        │
        repro = tags["repro"] or judge_repro(detail, tags)
                        │  ← None 时走三级查表（memory_organize.py:87）
                        ▼
        ┌──────────────────────────────┐
        │ 三层占用（layers 字典）        │  决定 summary/detail/source 存多少
        │  derived_only 标记（repro==derived 时内嵌 dfcp_tag）
        └──────────────────────────────┘
                        │
                        ▼
                   写入 memories.jsonl
                        │
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
   classify_and_link  mount_topic   distill_all
   (血缘建边)        (主题锚挂载)    (经验聚类)
```

**核心：五问在前（判"存不存/归哪类"），repro 在后（判"存多少/哪层"）。串行接力，不是并行协同。**

---

## 二、三级可复现判定（repro）—— 规则查表

### 2.1 代码（`memory/memory_organize.py:87`）

```python
def judge_repro(content: str, tags: dict) -> str:
    b = tags.get("behavior", "")
    if b in ("B00", "Fp", "Fq", "Fr"):
        return "derived"                        # ①
    if tags.get("affect_tags"):
        return "flash"                          # ②
    if tags.get("domain", "").startswith("005"):
        return "flash"                          # ③
    return "situational"                        # ④ 兜底
```

### 2.2 查表规则（按优先级，命中即返回）

| 序 | 条件 | 判定 | 理由 |
|---|---|---|---|
| ① | `behavior ∈ {B00, Fp, Fq, Fr}` | **derived（一级 完全可复现）** | 量化计算类骨架，确定性算法可重算 |
| ② | `affect_tags` 非空 | **flash（三级 不可复现）** | 情感交互体验，个体独有 |
| ③ | `domain` 以 `005` 开头 | **flash（三级 不可复现）** | 认知科学域，个体判断/教训/思考方式 |
| ④ | 以上都不命中 | **situational（二级 条件可复现）** | 兜底默认 |

### 2.2b 四分支成因 + 数据实证（2026-11-25 核实）

这四条分支**不是同时设计的四档规则，而是四个不同业务成熟度的产物拼在一起**（"时间胶囊"本质）：

| 分支 | 表面规则 | 真实覆盖 | 成因 | 状态 |
|---|---|---|---|---|
| **① 量化码→derived** | B00/Fp/Fq/Fr | 326 条中**仅 1 条**(Fp) | 为"元核原子化"预留的前瞻钩子——cgraph 14 个 skill **无一对应量化骨架**（只有 weakest_link 挂 G02），且量化码极少被当 behavior 用。**两头空：无 skill 供给 + 标签侧也少用** | 🟡 前瞻空转 |
| **② 情感→flash** | affect_tags 非空 | **0 条**（全库 affect_tags 全空） | 为"AI 经历/性格/情感三树"业务预留的钩子——`design_memory_v02.md:11` 原文"感性树有标签≈必为三级"。情感性格业务**搁浅**，affect_tags 只是占位字段(`v3_schema.py:30`) | 🔴 完全死码 |
| **③ 005域→flash** | domain 前缀005 | **142 条** | **当前唯一判定主力**。005=认知科学域，装"个体认知过程记录"（个体依附性=不可复现） | 🟢 唯一主力 |
| **④ 兜底→situational** | 其余全部 | 138 条 | 本该是"模糊带交 LLM 判定单元"的位置（见 2.6），实际直接兜底。**混合情况被迫暂定2级** | 🟡 被迫兜底 |

**关键修正：005 域不是"经验类"。** 142 条 memory_class 分布：`skill_refine 57` > `strategy_evolve 35` > `scar_case 19` > `key_pact 15` > `relation_milestone 6` > `null 10`。真正"经验(scar_case)"只占 19 条；主力是方法改进与路线裁决。005 的正确定性是"**个体认知混合桶**"，判三级的理由是"个体依附=不可复现"，不是"经验"。

### 2.3 依赖的枚举语义

**四个量化骨架（①）：**

| 骨架码 | 语义 |
|---|---|
| `B00` | 量级判定 |
| `Fp` | G15 量化 |
| `Fq` | G16 出清 |
| `Fr` | G17 低响应 |

**005 认知科学域（③），含子域：**
```
005 认知科学(元协作)
 ├ 001 设计裁决
 ├ 002 用户洞察
 ├ 003 记忆自我管理
 ├ 004 元思域
 ├ 005 失误与纠错
 ├ 006 方案攻防与专利评审
 ├ 007 世界模型与因果实验
 └ 008 自我锚定与利害结算
```

### 2.4 三级对应的存储策略（`layers` 字典 + 专利原意）

| repro | 专利原名 | 专利存储策略 | 系统实现 |
|---|---|---|---|
| `derived` | 一级 完全可复现 | 仅记录检索路径/规则/入口，不存本体 | 仅 summary + dfcp_tag.`derived_only`=True；**detail 物理保留但业务读取过滤**（#30 P1 偏离） |
| `situational` | 二级 条件可复现 | 压缩存摘要，不存完整原文 | summary + detail；source 只存受限入口 |
| `flash` | 三级 不可复现 | 全量永久保存，不降级不淘汰 | summary + detail + source 全量永久 |

### 2.5 原始设计意图（`docs/design_memory_v02.md:11`，2026-11-06 定案）

**原文一句话钉死全部设计：**

> ② 可复现性判定（**行为树编码预筛短路：计算/检索类节点直接判一级；感性树有标签≈必为三级；只对模糊带调判定单元**）

拆解设计意图 = **两层判定**：

```
第一层：编码预筛短路（快、免费、不看内容）
   ├─ 计算/检索类骨架(B00/Fp/Fq/Fr) → derived（短路）
   ├─ 感性树有标签(affect_tags)      → flash（短路）
   └─ 都没命中（模糊带）              → 交第二层
第二层：判定单元（慢、要 LLM/检索验证）
   └─ 对模糊带真正做语义可复现性判定
```

**「判断1 量化码→derived」的设计逻辑**：不是判"内容是否可复现"，而是判"**产生这条记忆的思维方式是否可复现**"。量化/计算类骨架（风险量化Fp/出清Fq/低响应Fr/量级B00）的产出本质是"算出来的结论"，输入相同则确定性算法可重算 → 判完全可复现 → 只存计算规则/检索路径，不占永久空间。这与专利 L57"可通过确定性算法…稳定重复获取"完全对齐。

### 2.6 实现 vs 设计的落差（核心缺口）

原始设计是"预筛短路 + 模糊带交判定单元"两层。**实现只做了第一层，第二层（判定单元）从未落地**：

```python
def judge_repro(content, tags):
    if b in ("B00","Fp","Fq","Fr"): return "derived"   # 预筛①
    if tags.get("affect_tags"): return "flash"          # 预筛②
    if domain.startswith("005"): return "flash"         # 预筛③
    return "situational"                                 # ← 本该"调判定单元"，实际直接兜底
```

**最后一行 `return "situational"` 就是缺口**：原文"只对模糊带调判定单元"，那句没实现，直接硬给 situational。导致"预筛短路"这个**快捷路径被当成了完整判定**——所有走不到短路条件的卡一律兜底，而非被真正判定。

### 2.7 判定机制落地细节

- **触发位置**：不是独立钩子，而是 `atomic_store()` 第 5 道工序（`memory_organize.py:285`）
- **钩子形态**：`repro = tags.get("repro") or judge_repro(detail, tags)` —— **上游显式传 repro 时整个判定被 `or` 短路跳过，零校验**
- **上游传 repro 的三条路径**：CLI `--repro`(默认None) / 五问 `add(repro=c["repro"])` / 代码调用
- **判定后落地**：`layers[repro]` 决定三层占用；`repro==derived` 时 dfcp_tag 内嵌 `derived_only:True`，读取经 `read_card_detail(purpose)` 在 search/reasoning/display 时返回 None（物理全存+逻辑过滤）

### 2.8 已知缺陷（4 条）

1. **`content` 参数是死的**：形参有 `content`（实参传 `detail`），函数体一次没读。判定只看 `behavior/affect_tags/domain` 三标签。准确表述：**函数内不看语义，语义判断被前移到上游"打标签"环节，下游无任何基于内容的纠错**。
2. **判定单元缺失**：设计第二层"模糊带交 LLM 判定单元"未实现，短路层独自工作，退化。
3. **两套判定并存、口径不自洽**：`or` 短路导致上游显式传的 repro 绕过所有规则。实测 38 条 `derived` 是 behavior=Ht、12 条 `flash` 非005无affect——均显式传、规则自洽不了。
4. **一级 detail 物理存**：`layers["derived"]` 现为 `detail:True`（#30 P1 刻意改动），与专利"不存储信息本体"字面偏离（靠 derived_only 标记 + 读取过滤模拟隔离）。

> 另：二级→索引频次降级（专利 L73）未实现，grep 零命中。

### 2.9 Ht(G22 决策整合) 与 repro 的关系 —— 2026-11-25 核查定案

> 本节为 38 条 `Ht+derived` 的溯源结论，**同时作废本文档早期"把 Ht 补进 judge_repro 规则"的错误建议**。

#### Ht 的职责（权威定义：`data/mind_graph.json` 的 Ht 节点 + `schema.py:55`）

```
code: Ht   label: G22 决策整合   keyword: 整合   priority: P6
trigger: 重组完成 + G24全局拓扑构建完成
output: 最终决策结论 + 完整方案矩阵
五步：目标锚定 → 目的校验 → 光谱系数展开(穷举方案矩阵) → 加权评估 → 决策输出
```

**Ht = "做最终决定"这个思维动作**，产自决策整合过程。它在系统里有三重角色：

| 层面 | Ht 是什么 |
|---|---|
| 元核思维管线 | G22 节点，最终决策前的整合步骤 |
| 记忆 behavior 标签 | 标记"这条记忆产自决策整合过程"（"XX终裁/定稿"类） |
| memory_class 软先验 | `strategy_evolve→(Jw,Ht)`、`key_pact→(Ht,Iv)`，仅可疑提示不拦截（`memory_judge.py:225`） |

#### 关键实证：Ht 与 repro 正交（三分天下）

实测**全库 115 条 Ht 卡**的 repro 分布：

```
flash 39 / situational 38 / derived 38   ← 几乎均等三分
Ht 的 domain 分布：001-003(32) / 002-002(30) / 005-002(16) / 005-003(10) / ...
```

**结论：repro 不由 Ht 决定。** 同是 Ht，有的是 005 域→flash，有的兜底→situational，有的上游显式传→derived。

#### 38 条 Ht+derived 的真实来路（查实，非脏数据）

| 证据 | 内容 |
|---|---|
| 内容特征 | 全是"终裁/定稿/裁决/定案"类（`多主题拆分终裁`、`复现率门禁v0.1终裁冻结`、`F系推演公式v2定稿`、`E7双裁决定案`…） |
| memory_class | strategy_evolve 20 / key_pact 9 / relation_milestone 8 / skill_refine 1（全为结论性/规约性） |
| 写入机制 | `repro = tags.get("repro") or judge_repro(...)` 的 `or` 短路——上游显式传 derived，规则根本不执行 |
| 来源 | 最早批 `migrate_memory.py` 迁移带入；11-15~11-17 批系主 AI 会话入库时判断"结论有决策逻辑→可复现"显式指定 |
| 是否为脏数据 | ❌ **否**。内容语义上确实可复现，是有意覆盖，不是错误 |

#### 为什么"补 Ht 到规则"是错的（撤回早期建议）

若把 `Ht` 加入 `judge_repro` 的 `derived` 分支：**会误伤 77 条**（39 flash + 38 situational 的 Ht 卡）。Ht 只答"思维动作"，不答"可复现性"，二者正交——**不可规则化**。

**正确做法**：文档化（已写入 `memory_organize.py` 的 `judge_repro` docstring），不动规则。

---

## 三、记忆入库五问 —— 规则

### 3.1 触发入口

- Jw 钩子入口：`memory_hook.py:354` → `judge_and_store(candidates, context={"trigger":"hook"})`
- 触发类型 context.trigger：`decision | fix | session_end | manual | hook`
- 批量上限 `JUDGE_BATCH_LIMIT`，超限截断告警不静默丢

### 3.2 有两条判定路径（LLM 优先，桩兜底）

**路径 A：LLM 判定（`_JUDGE_SYSTEM_PROMPT`，memory_judge.py:50）**

```
你是记忆入库判定卡。对每条候选记忆回答五问，只输出 JSON，不要任何解释文字或 markdown 围栏。

五问（门槛链，任一不过即降级）：
Q1 新认知：这轮是否产生了新的、可复用的认知？占位/寒暄/重复表述 → 不算。
Q2 定义本体：是否有实质内容（具体做法/结论/证据），而非空壳标题？
Q3 归类：归入且仅归入以下五类之一，或 null（普通记录）：
   skill_refine（方法/工程改进）、scar_case（踩坑/失效教训）、
   strategy_evolve（路线/裁决演进）、relation_milestone（实体血缘打通）、
   key_pact（规约/契约/边界约束）。
Q4 血缘：与已有记忆是否有同源/实体链关系？candidates 里 bloodline_hint 给出本地预计算的
   best_overlap（2-gram 重叠数值）与 conflict_suspect 作辅助参考；数值证据以代码为准，
   你只做语义综合。孤条无血缘 → 降权；疑似高度同源重复/冲突 → conflict_suspect=true。
Q5 分池：知识层（可复现方法论，须去个体化）还是经验层（事故/裁决/里程碑，可含个体性）？

输出 JSON（严格）：
{"verdicts":[{"idx":0,"verdict":"store","memory_class":"scar_case","conflict_suspect":false,"reasons":["Q1...","Q2..."],"evidence_gap":null}]}
verdict 取值规则：五问全过=store；有可补缺口=hold（evidence_gap 写清缺什么）；无新认知=discard。
```

**路径 B：规则桩（`_stub_one`，memory_judge.py:193，JUDGE_USE_LLM=0 或 LLM 不可用时）**

| 问 | 桩口径（规则） | 不通过时 |
|---|---|---|
| **Q1 新认知** | summary 非空且长度 ≥ 8 | `discard`（一票否决，early return，不往下判） |
| **Q2 定义本体** | detail 非空且长度 ≥ 30 | `hold`（evidence_gap=detail 过薄） |
| **Q3 归类** | memory_class ∈ 五类枚举 或 None（null 合法） | `hold`（非法字符串）；null 时做内容特征词粗筛，命中打 Q3hint 但放行 |
| **Q4 血缘** | 与库内条目 2-gram 重叠 > 0 | `hold`（孤条零血缘，evidence_gap=缺血缘互指）；重叠高标 conflict_suspect 不拦截 |
| **Q5 分池** | memory_class≠skill_refine 直接过；=skill_refine 且 summary 含"我/用户/主人/厂长"→视为知识层含个体指涉 | `hold`（evidence_gap=知识层/经验层归属存疑） |

**路径 C：异常兜底**：`_judge_call` 抛异常 → 整批 `hold`（`hold_reason="judge_failure"`），不丢不强 discard。

### 3.3 五问输出 → 动作

| verdict | 动作 |
|---|---|
| `store` | 调 `memory_hook.add` 入库（免人确认，Jw 自主闭环） |
| `hold` | 写 `STORE_PENDING_JSONL` pending 池，等 `review_holds` 复评（证据补齐转正/仍不足维持/反向证据丢弃） |
| `discard` | 只记 judge_log，不算积压 |

`judge_source` 强制记录路径（`llm`/`stub`），供 A/B 口径审计。所有判定快照 append-only 落 `judge_log.jsonl`。

---

## 四、两者关系（精确结论）

### 4.1 是串行接力，不是并行协同

> 注意区分两个层面：**执行层面**是串行接力（五问先跑判 store，repro 后跑判档，见 4.1）；**语义层面**是两个正交维度（归类 vs 预算，见 4.3b）。二者不冲突——先跑的那个只是先拿到字段，并不决定另一个的取值。

| 维度 | 五问判定 | repro 三级判定 |
|---|---|---|
| 位置 | `judge_and_store`（入库闸门） | `atomic_store`（底层存储） |
| 顺序 | **先** | **后**（仅 store 后才走到） |
| 输入 | summary/detail/context（LLM 语义） | behavior/affect_tags/domain（三标签） |
| 输出 | verdict + memory_class | repro（derived/situational/flash） |
| 强弱 | LLM 语义判断（强） | 硬编码标签查表（弱） |

### 4.2 字段传递是一条"细线"

`judge_and_store` 调 `memory_hook.add`（L447）时：`repro=c.get("repro")`。
- 候选 `c` 带 repro → 原样传下去
- 候选 `c` 无 repro（多数情况）→ `add` 收到 None → `atomic_store` 里 `None or judge_repro(...)` 走查表

### 4.3 关键缺口：LLM 的语义判断力没传导到 repro

> 与 4.3b「正交」不矛盾：正交指两个维度**设计上应分开**（归类≠预算），这里说的缺口是**软联动本可更好**——Q5 的"知识层/经验层"语义上≈可复现性，LLM 判 Q5 时本可顺手给 repro 一个 `hint`，但没给。这是正交维度间的"可优化联动"缺失，不是"正交被打破"。

- 五问 Q5"知识层(可复现方法论)/经验层(事故裁决里程碑)"语义上**≈**可复现性
- 但 **Q5 不产出 repro 字段**，五问也不给 repro 任何建议值
- repro 仍由 `judge_repro` 三标签查表独立决定
- **结果**：五问跑了 LLM，判出 store；但 repro 还是靠 6 行 if 补的。两条线各算各的。

### 4.3b 两者的真实分工：正交的两个维度（2026-11-25 数据实证）

**一句话：五问归类（归哪类），三级定预算（可复现性→存多少），两者正交不互相决定。**

#### 交叉表实证（326 条真实数据 memory_class × repro）

| memory_class | flash | situational | derived |
|---|---|---|---|
| skill_refine | 57 | 69 | 16 |
| strategy_evolve | 21 | 23 | 23 |
| scar_case | 18 | 5 | 1 |
| key_pact | 15 | 6 | 11 |
| relation_milestone | 18 | 6 | 8 |
| null | 0 | 6 | 0 |

**这张表证明正交**：同一个"类"横跨三个"档"（如 skill_refine 既有 57 条 flash、也有 16 条 derived）；同一个"档"含多个"类"（situational 里 5 种类都有）。**若二者是从属关系（归类→决定档），应呈现"一类一档"的阶梯，而非满盘散布。**

#### 各管什么（精确分工）

| 维度 | 五问（memory_class） | 三级（repro） |
|---|---|---|
| 回答的问题 | 「这是什么类型的记忆？」 | 「这记忆怎么存、占多少预算？」 |
| 产出 | skill_refine / scar_case / strategy_evolve / key_pact / relation_milestone | flash / situational / derived |
| 类比 | 图书馆**按学科分类**（文学/理工/历史） | **按载体形式决定复本数**（精装全本/平装摘要/仅目录卡） |
| 决定 | 内容归属、怎么被检索/复用 | 存 summary/detail/source 各占多少 |

#### 关键澄清："可复现性 ≠ 重要程度"

**不能把三级理解成"重要程度"**——这正是专利 L9/L99 明确要反对的旧范式：

> 专利原文：「本发明摒弃传统基于信息**热度、访问频率**的管理逻辑」「现有方案…依据重要性决定记忆去留」

repro 的真实维度是「**可复现性**」，不是「重要性」：
- `derived`（一级）**不是"不重要"**，而是"可被确定性算法重算，存个计算规则/路径就够"——一条核心决策若可复现，也只存路径
- `flash`（三级）**不是"重要"**，而是"依附个体/场景、无法重算，必须全量永久保存"
- **可复现性 ≠ 重要性**：这正是专利的创新点；用"重要程度"理解 repro 等于把专利思想降级

#### 三句话总结

1. **五问判定** → 决定「这条记忆**归哪类**」（memory_class，管内容归属与复用方式）
2. **三级判定** → 决定「这条记忆**占多少存储预算**」（repro，管可复现性 → 存多少，**不是重要性**）
3. **两者正交**（交叉表已证）→ 同一类可跨三档，同一档可含多类，互不决定

> 补充真实状态：五问 97% 没走 LLM（judge_log llm 仅 3%），memory_class 多由上游直填或桩判；repro 是标签查表 + 两个未来钩子空转。**理论上两维度独立判定，实践中两维度的判定质量都不高。**

### 4.4 与专利的对照

| 专利设想（L79） | 系统实现 |
|---|---|
| "配置**判定单元**自动完成可复现性分类" | ❌ 无判定单元，是 `judge_repro` 6 行标签查表，`content` 参数死 |
| "判定单元可采用轻量化语言模型、规则引擎、在线检索验证模块" | ⚠️ 最接近的 LLM 判定是**五问**，但五问判的是"入库五问"，不是 repro 三级；Q5 二分≠三级 |
| "保护核心判定规则与分层存储逻辑" | ✅ 分层存储（layers 字典）实现了；判定规则是标签查表（简陋但存在） |

---

## 五、改进方向（待决）

若要让两者真正协同、对齐专利"判定单元"设想，候选方案：

1. **五问 Q5 输出 repro 建议值**：LLM 在 Q5 分池时顺带输出 `repro_hint`，`judge_and_store` 传 `add(repro=v["repro_hint"])`，让 LLM 语义判断直接驱动分级。
2. **`judge_repro` 接入 content 语义**：把 `content` 参数真正用起来（规则/LLM 混合），而非只看标签。
3. **补二级频次降级**：实现"situational 低频 → 降级索引条目"。
4. **一级 detail 物理不存**（可选）：回到专利原意，或明确保留现有"物理存+逻辑隔离"设计并记录偏离理由。

> 现状记录：以上均**未实施**。当前是"标签查表分级 + LLM 五问入库"两条平行线。