# P-skeleton-complete 段一：技能设计草案 v0.2（节点①已冻结）

> 输入：docs/design_skeleton_complete_v01.md（侦察三输入）。
> 规约：__planned__ 占位通道——一次只聊一个节点，达成共识后落地该节点
> entries 条目，再开下一个。本文档随节点评审逐节增补冻结。
>
> 评审顺序（用户可调）：①G24_topology（✅冻结）→ ②Bd（评审中）→ ③Dk → ④En → ⑤Gs

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
（节点②Bd 评审中，草案见下节）

## 节点②：skill_bd_crosscheck（Bd 双维交叉验证）【草案 v0，待评审】

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

### 裁决点（待用户）

1. **挂接位置**：f_reason 的 next_options（推演完立刻交叉）还是 G24 之后
   （全局视野里再看交叉）？【建议：f_reason→Bd→G24，交叉先行——gap
   发现越早补漏成本越低】
2. **与 G24 的关系**：Bd 的 unaddressed 直接写 state.unaddressed，G24
   遗漏检测时合并进 uncovered 清单？还是分开两字段独立上报？
   【建议：G24 合并读取，terminate 汇总统一披露，单一事实源】
3. **relation 三档**（回应/竞争解释/无关）是否够用？要不要加"部分回应"？
