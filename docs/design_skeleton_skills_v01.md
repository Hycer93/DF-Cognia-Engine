# P-skeleton-complete 段一：技能设计草案 v0.4（节点①②③已冻结）

> 输入：docs/design_skeleton_complete_v01.md（侦察三输入）。
> 规约：__planned__ 占位通道——一次只聊一个节点，达成共识后落地该节点
> entries 条目，再开下一个。本文档随节点评审逐节增补冻结。
>
> 评审顺序：①G24_topology（✅冻结）→ ②Bd（✅冻结）→ ③Dk（✅冻结）→ ④En（评审中）→ ⑤Gs

## 节点①：skill_g24_topology（G24 全局拓扑构建）【2026-11-19 用户逐项裁决冻结】

### 定位与角色

专利定义：在最终决策前（G22/terminate 前）强制调用，动态构建四张全局
结构图，构成"全局视野"。v3 语义：**决策整合前的强制全局回视步骤**——
不是新信息生产者，而是已有链路信息的结构化投影 + 遗漏检测器。

关键裁决点（待用户定）：G24 在 v3 的价值主张是**遗漏检测**——四图把
"哪些子问题没被任何假设/证据回应"显式化，直击案卷第 4 条根因
（上下文传递弱联系）。信息本身不新增（trail/state/matrix 已有），
新增的是**结构化全局视野 + 遗漏清单**。

### 邻接表 entry 草案

```json
"skill_g24_topology": {
  "role": "G24全局拓扑构建(P-skeleton-complete): terminate前强制调用; 四图投影+遗漏检测, 不生产新信息只结构化全局视野",
  "state_inputs": ["goal_anchored", "sub_questions", "summary", "weakest_link",
                   "hypotheses", "trail", "matrix_knowledge"],
  "state_outputs": ["topology", "gaps"],
  "result_fields": ["skeleton_family", "tool_topology", "knowledge_map",
                    "path_status", "uncovered_subquestions", "summary",
                    "confidence", "applicable"],
  "next_options": [
    {"skill": "skill_terminate", "gain": "全局视野已固化, 汇总可引用gaps补漏", "cost": "低"},
    {"skill": "skill_f_reason", "gain": "对uncovered子问题补推演假设谱", "cost": "高"}
  ],
  "terminate_suggest": "恒False(工具型节点无自身终止逻辑)",
  "dfcp_link": "无(纯思考技能, 不读记忆库; 未来接入须重走七段)"
}
```

### ThinkState 新增字段（2 个）

```python
topology: object = None    # 四图快照 dict(skeleton_family/tool_topology/knowledge_map/path_status)
gaps: list = None          # 遗漏清单(uncovered_subquestions: [{q, why_missed, suggest_next}])
```

### 四图 → prompt 组装映射（build_prompt 语义）

| 图 | 数据源（state 侧） | 注入形式 |
|---|---|---|
| 骨架族谱图 | trail（skill_id 序列 + 各步 summary） | 已执行技能清单+每步一句话摘要 |
| 工具资源拓扑图 | 静态：SKILL_REGISTRY + 邻接表 next_options | 本轮**未触发**技能清单（对照出跳过项） |
| 知识资产关联图 | matrix_knowledge 注入记录 + ref_material 命中摘要 | 本轮命中的知识卡 id+tech_name 列表 |
| 推理路径状态图 | state 全字段非空判定 + steps + 各步 _degraded 标记 | 字段填充状态表（完成/空缺/降级） |

四图**前三张可纯 Python 生成**（数据都在 state/trail，无需 LLM 推理），
LLM 只负责第四步：对照四图产出 `uncovered_subquestions`（哪些子问题
未被 hypotheses/weakest_link 回应）+ summary。

### 裁决记录（2026-11-19 用户逐项裁决，三项全裁）

**裁决1——混合执行架构 ✅**：四图投影全部纯 Python 实现（确定性、零
LLM 开销）；仅遗漏语义检测交 LLM。理由：四张图是审计基底不能飘，投影
交 LLM 会引入随机抖动；LLM 只做"子问题有没有被回应"这个模糊语义判断。
LLM 降级兜底：gaps 置空 + `_degraded` 标记，四图照常输出，链路不崩。

**裁决2——邻接表软挂，不做路由器强制 ✅**：挂 f_reason、synthesize 的
next_options，作为 terminate 前可选一站。不改动 router 调度内核，改动面
最小、兼容现有全部回归。观测到位（E11/E12 样本 + 路由选 G24 触发率/
收益统计）后，在 Gs 节点评审阶段再议是否升级 router 强制触发，不提前
锁死硬约束。

**裁决3——a+b 双通路全开 + 循环防护 ✅**：next_options 同时透出
skill_terminate 与 skill_f_reason，router 自主选路。硬防护：**单个子问题
最多回流补推演 1 次**，trail 打标记计数。选 terminate：uncovered 列表
带进汇总诚实披露缺口（可信底线）；选 f_reason：只挑未补过的子问题生成
假设谱补齐覆盖。

### 锁定契约（用户补充，冻结）

1. 四图只读 state 已有数据，**绝不主动拉取 ref_material、不新增外部
   检索**——恪守"只投影、不生产新证据"定位
2. `state_outputs` 的 topology、gaps 字段**仅 G24 写入**，其他技能只读
   禁止覆盖
3. 冒烟用例 smoke_g24_topology 按草案落地，纳入 E11 回归集

### 红线（对齐既有技能家族）

- 只投影不裁决：不评价假设对错，只陈述覆盖状态
- 不写记忆库（dfcp_link=无，同 f_reason/wl 家族）
- uncovered 判定只陈列不给结论（裁决权在人/terminate）
- 遗漏检测 LLM 失败 → gaps=空 + 降级标记，四图投影仍可用（半本地架构
  的天然容错：LLM 挂了图还在）

### 冒烟（E11 追加 smoke_g24_topology）

构造带 8 子问题但只 4 假设的 state → 断言：四图四键全出、
uncovered_subquestions 含未回应子问题、非降级、gaps 写回 state。

---
（节点④En 评审中，草案见下节）

## 节点④：skill_en_evidence_net（En 证据网）【草案 v0，待评审】

### 定位与角色

En = 多源证据交叉关联网。与 Dk 的分工：Dk 织**单条结论的纵向链**
（假设→前提→证据），En 织**证据之间的横向网**（同一证据被几条链引用/
多条证据是否互相印证或矛盾/孤立证据有哪些）。round6 病根"A 胜证据密度"
的直接对策——裸模型赢在把一堆对比文件编织成密实证据网，管线缺这个
横向编织层。

产出：`evidence_net`——节点=素材来源（ref_material 段/matrix 卡），
边=印证/矛盾/补充关系；`isolated_evidence` = 无任何边的孤立证据。

### 邻接表 entry 草案

```json
"skill_en_evidence_net": {
  "role": "En证据网(P-skeleton-complete): 多源证据横向交叉关联(印证/矛盾/补充); 只织网不裁决",
  "state_inputs": ["goal_anchored", "hypotheses", "deduction_chains",
                   "ref_material", "matrix_knowledge", "summary"],
  "state_outputs": ["evidence_net", "summary"],
  "result_fields": ["net", "isolated_evidence", "contradiction_pairs",
                    "applicable", "summary", "confidence"],
  "next_options": [
    {"skill": "skill_g24_topology", "gain": "网完整性并入全局视野", "cost": "低"},
    {"skill": "skill_terminate", "gain": "网就绪直接收敛", "cost": "零"}
  ],
  "terminate_suggest": "恒False",
  "dfcp_link": "无"
}
```

### ThinkState 新增字段

```python
evidence_net: list = None      # [{src_a, src_b, relation: "印证|矛盾|补充", note}]
isolated_evidence: list = None # 引用计数=0 的证据源(纯Python派生)
```

### 执行架构（混合范式第四例）

- 边关系判定走 LLM（两两证据对的语义关系，模糊判断）
- `isolated_evidence` 与引用计数纯 Python 派生（统计 Dk 链 + Bd 矩阵 +
  hypotheses 的 evidence_ref 出现次数，零次=孤立）
- 前置守卫：ref_material 与 matrix_knowledge 全空 → applicable=false
  跳过
- `contradiction_pairs`（矛盾对）是 terminate 汇编的高价值输入——
  汇总时"证据冲突未决"必须披露

### 裁决点（待用户）

1. **挂接位置**：Dk→En→Bd→G24（网在链后、交叉前——Bd 判 relation 时
   连证据横向关系也能看到）？还是 Bd→En（交叉后再织网，网只服务
   汇总）？【建议：前者，与 Dk 同理——信息越全判定越扎实；但多一站
   前置，链路变长】
2. **两两配对成本控制**：证据源 n 条则配对 n(n-1)/2 次 LLM 判定，10 条
   素材=45 对。方案：(a) 单次 LLM 调用批量判全部对（一次 prompt 全网，
   靠 JSON 输出）；(b) 只判 Dk 链已引用的证据对（未进链的素材不织网）；
   (c) 硬上限（如只取引用频次 top-K 证据织网）。【建议：(a) 单次批量
   ——一次调用内部并行判定，成本可控且覆盖全】
3. **矛盾对消费**：contradiction_pairs 进 G24 gaps（作为"证据冲突未决"
   缺口）还是只进 terminate 汇编上下文（不是缺口是事实）？
   【建议：进汇编上下文不进 gaps——矛盾是事实披露不是覆盖缺口】

## 节点③：skill_dk_deduction（Dk 演绎链）【2026-11-19 用户逐项裁决冻结】

### 定位与角色

Dk = 证据→前提→结论的可追溯演绎链。案卷第 3 条（f_reason 假设深度不足）
与第 4 条（证据无累积区）的直接对策：round6 实测管线假设"有谱无肉"——
H1 公开不充分成立但 support 证据密度塌到 0。Dk 补的是**每条关键假设/
结论的演绎前提链**：从证据到结论走了哪几步、每步依据是什么，让
terminate 汇编时有链可织。

产出：`deduction_chains`——对 hypotheses（或 wl 薄弱点）逐条挂
`[{premise, evidence_ref, step_note}]` 链。

### 邻接表 entry 草案

```json
"skill_dk_deduction": {
  "role": "Dk演绎链(P-skeleton-complete): 关键结论的证据->前提->结论可追溯链构建; 只织链不裁决",
  "state_inputs": ["goal_anchored", "hypotheses", "weakest_link", "ref_material",
                   "matrix_knowledge", "summary"],
  "state_outputs": ["deduction_chains", "summary"],
  "result_fields": ["chains", "unsupported_premises", "applicable",
                    "summary", "confidence"],
  "next_options": [
    {"skill": "skill_bd_crosscheck", "gain": "链条与薄弱点交叉验证", "cost": "中"},
    {"skill": "skill_g24_topology", "gain": "链完整性并入全局视野", "cost": "低"},
    {"skill": "skill_terminate", "gain": "链就绪直接收敛", "cost": "零"}
  ],
  "terminate_suggest": "恒False",
  "dfcp_link": "无"
}
```

### ThinkState 新增字段

```python
deduction_chains: list = None  # [{target_ref(H1/wl位置), steps:[{premise, evidence_ref, note}], verdict_ready}]
```

### 执行架构（混合范式第三例）

- 链步生成走 LLM（从 ref_material/matrix_knowledge 中为每条假设找前提）
- `unsupported_premises` 纯 Python 派生：evidence_ref 为空或引用不存在的
  素材 id 的前提——确定性检出"空中楼阁"步骤
- 前置守卫：hypotheses 与 weakest_link 全空 → applicable=false 跳过
- evidence_ref 校验白名单：只允许引用 ref_material 命中段/matrix 卡 id
  /trail 中已出现的事实，禁止凭空引用（防幻觉引用）

### 裁决记录（2026-11-19 用户逐项裁决，三项全裁）

**裁决1——挂接顺序：f_reason→Dk→Bd→G24 ✅**：Dk 产出的证据-前提链是
Bd 做「回应/竞争解释/无关」判定的核心依据——Bd 看不到假设背后的演绎
步骤，语义判定只能浮在假设字面，容易误判；先织链再交叉，Bd 判定质量
扎实。保留兜底：Dk 跑完可直达 G24 或 terminate，路由灵活选路。

**裁决2——织链范围：全部 hypotheses + 单链 4 步截断 ✅**：只给 wl 关联
假设织链会丢独立衍生假设的证据溯源（审计盲区）；全量织链 + 4 步上限
锁死成本，平衡溯源完整性与 token 开销。附加契约：空证据引用的前提
直接标记进 unsupported_premises；evidence_ref 严格走白名单校验，禁止
凭空编造素材索引。

**裁决3——unsupported_premises 送 G24 合并进 gaps ✅**：对齐 Bd/G24
单事实源契约——state 保留 unsupported_premises 独立字段（观测/调试/
指标统计）；G24 汇总三类缺口（原生 uncovered_subquestions + Bd 的
unaddressed + Dk 的 unsupported_premises）；terminate 只读 G24.gaps
对外输出，报告统一、审计口径唯一。

### 锁定契约（用户补充，冻结）

1. Dk 定位：只编织推理链路，**不修改、不生成新假设、不做真假裁决**
2. 混合范式对齐家族规范：LLM 拆解构建 step 链，unsupported_premises
   纯 Python 派生检出；LLM 降级时 chains 置空 + `_degraded` 标记，
   链路继续不阻塞
3. 前置守卫：hypotheses、weakest_link **两者全空**时 applicable=false
   直接跳过
4. E11 新增 smoke_dk_deduction：构造带无证据前提的假设，断言链条生成
   正常、unsupported_premises 命中检出

## 节点②：skill_bd_crosscheck（Bd 双维交叉验证）【2026-11-19 用户逐项裁决冻结】

### 定位与角色

Bd = 假设谱（f_reason 产出，发散维）× 薄弱点定位（weakest_link 产出，
聚焦维）的双维交叉验证。P1 快修（ee5da74）已打通数据通路（f_reason
prompt 现注入 wl 结构化块），Bd 是其正式技能形态：**对每个假设，显式
判定它是否回应/竞争解释了 wl 定位的薄弱点**——把"注入语境"升级为
"逐假设交叉打分"。

产出：假设-薄弱点关联矩阵（哪条假设回应哪个薄弱点/无假设回应的薄弱点
= 新 gap，喂给 G24 的 gaps 通道）。

### 邻接表 entry 草案

```json
"skill_bd_crosscheck": {
  "role": "Bd双维交叉验证(P-skeleton-complete): 假设谱×薄弱点逐对判定回应关系; 产关联矩阵+无回应薄弱点清单",
  "state_inputs": ["goal_anchored", "hypotheses", "weakest_link", "summary"],
  "state_outputs": ["bd_matrix", "summary"],
  "result_fields": ["cross_matrix", "unaddressed_points", "applicable",
                    "summary", "confidence"],
  "next_options": [
    {"skill": "skill_g24_topology", "gain": "交叉结果并入全局视野", "cost": "低"},
    {"skill": "skill_terminate", "gain": "交叉完成直接收敛", "cost": "零"}
  ],
  "terminate_suggest": "恒False",
  "dfcp_link": "无"
}
```

### ThinkState 新增字段

```python
bd_matrix: list = None   # [{hypothesis_ref, wl_location, relation: "回应|竞争解释|无关", note}]
unaddressed: list = None # wl 定位但无任何假设回应的薄弱点
```

### 执行架构（对齐 G24 裁决1 混合范式）

- 关联判定走 LLM（语义模糊判断，同 G24 遗漏检测分工）
- `unaddressed` 纯 Python 派生：cross_matrix 中无任何"回应/竞争解释"
  关系的 wl 条目——确定性兜底
- 前置条件守卫：hypotheses 或 weakest_link 任一为空 → applicable=false
  直接跳过（不硬跑），不降级不报错

### 裁决记录（2026-11-19 用户逐项裁决，三项全裁）

**裁决1——挂接位置：f_reason→Bd→G24 ✅**：推演刚产出假设谱立刻与
weakest_link 就近交叉校验，gap 提前暴露交 G24 汇总；放 G24 后会退化成
事后复盘，丢失"提前补漏"时序价值，不符 Bd"双维互证"定位。
next_options 约束：**Bd 跑完优先流向 G24，保留直达 terminate 的旁路兜底**。

**裁决2——与 G24 关系：unaddressed 交 G24 合并，单事实源 ✅**：
- Bd 产「薄弱点维度缺口」（wl 定位、无假设承接）
- G24 汇总两类缺口：Bd 的 unaddressed + G24 自检的子问题 uncovered
- **terminate 只消费 G24 的 gaps 一套出口**，避免两套缺口打架、审计混淆
- 存储区分：state 保留 bd_matrix、unaddressed 独立字段（观测调试用）；
  仅合并视图送 G24 的 gaps

**裁决3——relation 维持三档 ✅**：回应/竞争解释/无关，不增"部分回应"
（第四档放大 LLM 语义模糊性、边界歧义、单测膨胀）。中间态处理：note
备注描述覆盖残缺，relation 主标签仍打"回应"。三值主分类规则稳定，便于
矩阵统计与自动化观测告警。

### 锁定契约（用户补充，冻结）

1. Bd 只做假设与薄弱点**关系判定**，不评判假设对错、不新增假设、不新增
   薄弱点
2. 前置守卫严格生效：hypotheses 为空 或 weakest_link 为空 →
   applicable=false 直接跳过，链路正常往下走
3. 沿用混合范式：LLM 输出 cross_matrix，unaddressed 由 Python 确定性
   派生；LLM 降级时 matrix 空、unaddressed 空、标记 `_degraded`，
   链路不阻断
4. E11 新增 smoke_bd_crosscheck：给定 wl 条目+若干假设，断言矩阵关系
   标签正确、unaddressed 正确检出无匹配薄弱点
