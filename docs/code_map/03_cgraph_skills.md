# 03 cgraph 技能类图
> 生成日期 2026-11-25 | 覆盖技能 13 个 | 全部源码逐行实读（非推断）。
> 技能体系定性：**「Python 薄壳 + Prompt 内核」的原子思维技能家族**。每个技能 = 一次受刚性框架约束的 LLM 语义调用：权限（state 白名单）、schema 校验、菜单/终止判定、审计、降级兜底全部由 Python 侧代码承载，LLM 只做语义工作且输出受固定 JSON Schema 约束。技能不做事实裁决、不触发记忆提案、不读写 DFCP 卡片（dfcp_link=无，skill_f_reason 例外见基类注释预留）。菜单（next_options）与终止判定（terminate_suggest）永远取自邻接表 + Python 条件，模型不可生成。

---

## 一、技能基类契约（atomic_skill.py）

文件：`cgraph/atomic_skill.py`（317 行，3 个顶层结构）

### 1.1 三个顶层构件
| 构件 | 作用 |
|---|---|
| `ThinkState`（dataclass） | 单次思考任务的内存态（不落盘，5 分钟内闭环）。`read(key)/write(key,value)` 支持 `flags.xxx` 嵌套键。字段：goal / goal_anchored / ref_material / matrix_knowledge / collab_synthesize_override / sub_questions / summary / trail / flags{confidence,sensitive,conflict} / steps，以及 13 个技能独写的动态输出键（weakest_link, hypotheses, selection_rationale, final_deliverable, alternatives_appendix, source_intents, topology, gaps, bd_matrix, unaddressed, deduction_chains, evidence_net, isolated_evidence, contradiction_pairs, gs_view）。 |
| `BaseAtomicSkill`（基类） | 承载全部刚性框架，子类只写语义。 |
| `SkillResult`（dataclass） | 统一输出：`skill_id / result(dict) / next_options(list) / terminate_suggest(bool) / secs`。 |

另有两个共享工具：`extract_json()`（剥 markdown fence + 首尾大括号 + strict/strict=False 两级容错 json.loads，失败返回 None 交调用方降级）和 `_ref_material_block(state, skill)`（P-C4 参考素材消费侧阈值分流：≤8000 字符完整直塞 prompt；>8000 走 `ref_retrieval.retrieve()` 只拉 top-3 片段；注入失败=无素材，不劣于现状）。

### 1.2 子类必须实现什么
- **类属性 `skill_id`**（必填，且必须等于邻接表 `data/atomic_skills_adjacency.json` 的 entries key，否则 `__init__` 直接 KeyError）。
- **`build_prompt(state) -> list[dict]`**：只做语义组装，读 state 必须用 `self._in(state, key)`。
- **`parse_result(raw, state) -> dict`**：LLM 原文 → result dict，强校验 + 清洗 + 降级。
- **`decide_terminate(state, result) -> bool`**（可选覆写）：纯 Python 终止判定；默认恒 False。
- **类属性 `STATE_OUTPUT_MAP`**（可选）：state_outputs 键 → result 字段名的映射，控制回写哪个 result 字段进哪个 state 键。

### 1.3 run() 生命周期（基类六步，子类不碰）
```
run(state) 内部:
① build_prompt → llm.chat → parse_result（任一异常 → _degraded，技能失败不炸链路）
② schema 补齐: result 缺邻接表 result_fields 声明的字段 → 补空串（脏数据不向下流转）
③ state_outputs 白名单写回（经 STATE_OUTPUT_MAP 或同名键 → state.write）
④ decide_terminate 纯 Python 判定（模型永不参与）
⑤ 审计: _audit_log → sqlite audit_topic_log 表 op='atomic_skill:<skill_id>'（独立归属，不进 E7 晨报；审计失败只 stderr 告警不阻断）
⑥ token 埋点: 读 llm.last_usage 写 token_in/token_out（纯观测，失败静默）
返回 SkillResult(next_options=邻接表声明, terminate_suggest=④结果)
```

### 1.4 权限模型
`_in(state, key)` 只放行邻接表 `state_inputs` 声明的键，越权读抛 `PermissionError`。写回只走 `state_outputs` 白名单。邻接表经 `eval/experiment/adjacency_validator.assert_loadable` 双触发校验（加载时 + 校验器调用时）。

### 1.5 注册与调用机制
- **注册**：`cgraph/router_atomic.py` 的 `SKILL_REGISTRY` dict（skill_id → 类），全部 13 技能在此显式注册。executor 补跑 terminate 时会临时注入/恢复该 registry。
- **调用**：`cgraph/executor.py` 的 `run_atomic()` 是唯一入口 → 实例化 `RouterAtomic` → 循环：`_exec_skill`（从 registry 实例化 `SkillX(llm, adjacency).run(state)`）→ `_clean_menu`（剔除非法项 + synthesize 门控）→ `_route_llm`（LLM 从菜单选下一技能，失败回退首个非 terminate 项）→ 直至 skill_terminate 执行。
- **后置触发**：executor 在链路退出后（POST_EXIT_HINT 同 memory_gate 先例）补跑 `SkillSufficiencyCheck` 与 `SkillMemoryGate`，不占 steps、不进菜单。
- **例外**：`skill_sufficiency_check` 和 `skill_memory_gate` **不在 SKILL_REGISTRY**（memory_gate 甚至不是 BaseAtomicSkill 子类，是独立 Python 件，不进邻接表 entries、不走 run 五步生命周期——见 router_atomic.py 头注）。sufficiency 在邻接表中有条目（state_inputs 声明齐全，`self._in` 才不抛错）。

---

## 二、技能总览表

| 技能类 | 代号/源流 | 文件 | 行数 | 职责 | 触发 | 输入(state_inputs) | 输出(state_outputs) | 依赖 |
|---|---|---|---|---|---|---|---|---|
| SkillAnchorTarget | 试点①/标准入口 | skill_anchor_target.py | 113 | 原始提问收敛成锚定目标+任务类型分类+歧义识别+素材剥离 | 链路恒首步 | goal | goal_anchored, ref_material, flags.confidence, flags.sensitive | 无上游 |
| SkillScopeScan | 试点②/原Aa·G00 | skill_scope_scan.py | 140 | 任务空间平铺拆解:子问题/关键参数/领域 | anchor 后 | goal_anchored, flags | sub_questions, summary, flags.scan_confidence | anchor |
| SkillSourceIntent | 试点③/原Bb·G01 | skill_source_intent.py | 184 | 外部信息源意图画像(谁放的/时机/诉求) | 深挖分支,router 选中 | goal_anchored, sub_questions, summary | source_intents, summary | scan |
| SkillWeakestLink | 试点④/原Bc·G02 | skill_weakest_link.py | 251 | 脆弱面扫描:最弱环节单点+各维风险分 | 深挖分支,router 选中 | goal_anchored, sub_questions, summary | weakest_link, summary | scan |
| SkillFReason | F20封装/F系推演 | skill_f_reason.py | 228 | 生成竞争性假设谱(假设-证据-反证-验证路径四元组,无档位无排序) | 深挖分支,router 选中 | goal_anchored, sub_questions, summary(,+weakest_link) | hypotheses, summary | scan/wl; matrix_knowledge 注入 |
| SkillSynthesize | P-skill-synthesize | skill_synthesize.py | 118 | 假设谱收敛+方案终稿:权衡理由/交付终稿/备选附录三段 | 仅 goal_type==scheme_design(router._clean_menu 门控) | goal_anchored, sub_questions, summary, hypotheses, ref_material | selection_rationale, final_deliverable, alternatives_appendix, summary | f_reason(读hypotheses) |
| SkillDkDeduction | P-skeleton/Dk·节点③ | skill_dk_deduction.py | 146 | 证据→前提→结论演绎链构建(全量织链,单链≤4步) | 主线,router 选中 | goal_anchored, hypotheses, weakest_link, summary | deduction_chains, summary | f_reason/wl |
| SkillEnEvidenceNet | P-skeleton/En·节点④ | skill_en_evidence_net.py | 164 | Dk链内证据两两横向关系(印证/矛盾/补充) | 主线,Dk 之后 | goal_anchored, deduction_chains, matrix_knowledge, ref_material, summary | evidence_net, isolated_evidence, contradiction_pairs, summary | dk; ref_material/matrix_knowledge 定位证据原文 |
| SkillBdCrosscheck | P-skeleton/Bd·节点② | skill_bd_crosscheck.py | 160 | 假设×薄弱点逐对判定关系(回应/竞争解释/无关) | 主线,Dk+En 之后 | goal_anchored, hypotheses, weakest_link, deduction_chains, evidence_net, summary | bd_matrix, unaddressed, summary | f_reason/wl/dk/en |
| SkillG24Topology | P-skeleton/G24·节点① | skill_g24_topology.py | 171 | 决策整合前全局回视:四图投影(纯Python)+子问题遗漏检测(LLM)+gaps三源合并 | 主线,Bd 之后 | goal_anchored, sub_questions, hypotheses, weakest_link, unaddressed, deduction_chains, summary | topology, gaps, summary | 全家族(trail/matrix_knowledge 只读投影) |
| SkillGsReview | P-skeleton/Gs·节点⑤ | skill_gs_review.py | 125 | 长链过程检查点:漂移告警+resume_view 压缩视图 | 邻接表软挂全技能,router 自主选路;steps<4 跳过 | goal_anchored, sub_questions, trail, summary, steps | gs_view, summary | trail(只读) |
| SkillTerminate | 链路出口 | skill_terminate.py | 160 | 收敛+整体汇编+质量自检(纯Python零LLM) | 显式菜单选择/路由收敛 | goal, goal_anchored, summary, trail | [] (零回写) | trail(每步自报summary) |
| SkillSufficiencyCheck | P-C5 后置参谋 | skill_sufficiency_check.py | 337 | 结论↔证据机械对账+可疑时LLM疑点复核;只告警不裁决不拦截 | executor 后置(POST_EXIT),不进菜单 | goal, goal_anchored, sub_questions, summary, trail, ref_material | [] (R15零回写) | trail/ref_material; ref_retrieval(BM25) |

（skill_memory_gate 不属 BaseAtomicSkill 家族，本文档不展开，仅在此标注其存在于 executor 后置位。）

---

## 三、技能协作流程图

```
用户原始提问
      │
      ▼
[skill_anchor_target] ── goal_type==simple_query 且无歧义且conf≥0.9 ──► [skill_terminate] (直收敛)
      │ goal_anchored / goal_type / flags
      ▼
[skill_scope_scan] ── sub_questions 空且 scan_conf≥0.9 ──► [skill_terminate] (单点闭合)
      │ sub_questions
      ▼ (深挖分支, router 按菜单+收益成本选路)
┌──────────────┬──────────────────┬───────────────┐
│              │                  │               │
[skill_source_intent]   [skill_weakest_link]   [skill_f_reason]
   source_intents          weakest_link ──────► hypotheses
                                              (wl 作语境注入,非硬依赖)
                                                │
                    ┌───────────────────────────┘
                    ▼
              [skill_synthesize]  (仅 scheme_design; menu门控剔除其余)
                    │ selection_rationale / final_deliverable / alternatives_appendix
                    ▼
   ════════ P-skeleton 主线: f_reason → Dk → En → Bd → G24 → terminate ════════
                    │
                    ▼
              [skill_dk_deduction]   deduction_chains (含 unsupported_premises)
                    │
                    ▼
              [skill_en_evidence_net] evidence_net / isolated_evidence / contradiction_pairs
                    │
                    ▼
              [skill_bd_crosscheck]  bd_matrix / unaddressed
                    │
                    ▼
              [skill_g24_topology]   topology / gaps(=uncovered+unaddressed+unsupported 三源合并唯一出口)
                    │  gaps 含 reflow_allowed 标记 ──► 可回流 f_reason 补推演(每子问题≤1次,router层计数)
                    ▼
              [skill_terminate]  final_output / termination_reason / steps_used / coverage_check
                    │
                    ▼ (executor 后置 POST_EXIT, 不占steps不进菜单)
              [skill_sufficiency_check]  sufficiency_report / alerts / coverage_metrics (零回写)
              [skill_memory_gate]        (独立件, 非 BaseAtomicSkill)

  [skill_gs_review] 软挂全链任意位置(steps≥4), gs_view 检查点:
        resume_view 注入后续prompt + 供 terminate 汇编; drift_warnings→terminate 独立"过程漂移"章节
```

**产出→消费关系明细**：
- `weakest_link`：wl 写 → f_reason(语境注入)/Dk(织链对象)/En(间接)/Bd(配对侧)/G24(投影+unaddressed 判据)/sufficiency(wl_addressed 指标)
- `hypotheses`：f_reason 写 → synthesize/Dk/Bd/G24/sufficiency
- `deduction_chains`：Dk 写 → En(证据引用集合来源)/Bd(背景)/G24(unsupported 合并)
- `evidence_net`：En 写 → Bd(背景)/G24(投影)
- `unaddressed`：Bd 写 → G24(合并进 gaps)
- `gaps`：G24 独写，系统**唯一对外缺口出口**；contradiction_pairs/drift_warnings 严禁写入(留 terminate 独立章节)
- `gs_view`：Gs 写(每次覆盖=最新检查点) → terminate 汇编复用

---

## 四、逐技能详解

### 4.1 SkillAnchorTarget（试点①，标准入口）
- **职责**：原始用户提问 → 结构化锚定目标；五类 goal_type 分类；剥离参考素材原文；识别歧义。
- **触发**：链路恒首步。
- **关键方法**：`build_prompt` 只喂原始 goal；`parse_result` 清洗——非法 goal_type 枚举→unknown_ambiguous、confidence 夹取 0-1、anchored_goal 为空=最严重降级；R4 覆盖防御：入口已写非空 ref_material 时代码层直接丢弃 LLM 版本（prompt 软约束+代码硬约束双层）。
- **输出结构**：anchored_goal / ref_material / goal_type(simple_query|analysis|scheme_design|review|unknown_ambiguous) / ambiguity_note / summary / confidence / sensitive。
- **decide_terminate**：simple_query + 无歧义 + conf≥0.9（CONF_TERMINATE_MIN）+ 非降级 才建议终止；低置信强制走向扫描（风险1过度收敛兜底）。
- **依赖/被依赖**：无上游；被 scan 及全家族消费 goal_anchored/goal_type。

### 4.2 SkillScopeScan（试点②，原 Aa/G00）
- **职责**：锚定目标平铺成任务空间：子问题清单/关键参数/涉及领域。
- **触发**：anchor 之后。v1 只收编"任务空间扫描"；参数溯源/遗留回溯移出。
- **关键方法**：子问题逐项清洗（q 非空、type 非法→analytical、纯字符串容错）；MAX_SUB_QUESTIONS=8 硬上限截断+note；全空产出（无子问题无参数无领域且无 summary）→ 降级。
- **输出结构**：sub_questions[{q,type:factual|analytical|design|verification}] / key_parameters / domains_touched(自由文本不映射topic_id) / summary / confidence / ambiguity_note。
- **decide_terminate**：sub_questions 空 AND 非降级 AND scan_confidence≥0.9。
- **细节**：scan_confidence 写独立键 flags.scan_confidence，绝不覆盖 flags.confidence（两阶段置信分开存，责任链可审计）。

### 4.3 SkillSourceIntent（试点③，原 Bb/G01）
- **职责**：外部信息源画像：source/actor/timing_motive/is_inference，无 trust 字段（可信度打分 behavior-3 移出，属事实裁决类）。
- **触发**：深挖分支，router 选中。
- **关键方法**：MAX_SOURCES=6 截断；**防脑补核心**：推测条目(is_inference=true)数量不得超过显式线索条目，超限逐条截光+note；applicable=false（无信源任务）=合法非降级；声称适用但清洗后全空=降级（O2 修复，summary 文本不可豁免）。
- **decide_terminate**：恒 False（深挖分支无自身终止逻辑）。

### 4.4 SkillWeakestLink（试点④，原 Bc/G02）
- **职责**：脆弱面扫描：最弱环节单点 + 各维 0-1 健壮分 + 五档风险映射。
- **触发**：深挖分支，router 选中。
- **关键方法**（F20 范式：LLM 只给原始分，聚合全 Python）：
  - `_clean_scores/_clean_point/_strip_remedy`：分数夹取；**风险2红线**——正则剔除"建议/应该…修复/改进/排期"类处置文本并 note；
  - 评分自洽校验：weakest_point.score 必须等于 min(dimension_scores)，偏差>0.05 强制修正+note，微差静默对齐；scores 空/全非法→降级；
  - weakest_point 缺失时由 min 维度构造（单点语义不可为空）；others 次级列表 MAX_OTHERS=4 截断；risk_level 由 RISK_LEVELS 阈值表（<0.2 critical / <0.4 high / <0.6 medium / <0.8 low / 其余 negligible）纯 Python 映射。
- **输出结构**：weakest_point{location,dimension,score,evidence}(state 键名 weakest_link，单点 dict)/ others / dimension_scores / risk_level / applicable / …。
- **matrix_knowledge 注入**：仅本技能与 f_reason 可读消费（P-matrix-feed 契约）。

### 4.5 SkillFReason（F20 封装，F 系推演原子化）
- **职责**：对关键不确定点生成竞争性假设谱，每条四元组 {hypothesis, supporting, contradicting, test_next}——**无 plausibility 档位、无优先级排序**（分歧①B 案软围栏：杜绝预结论诱导人闸的橡皮图章风险）。
- **触发**：深挖分支。与 wl 分工：wl 向内评估已知方案脆弱点；f_reason 向外生成可能性候选空间。
- **关键方法**：
  - P1快修：try 读 weakest_link（旧邻接表未声明时 PermissionError 视同不存在，向后兼容），非空则注入为"交叉验证语境"；
  - `_clean_evidence`（分歧⑦）：证据字段宽容（str→[str]），假设本体严格（本体空才整条丢弃）；
  - 空谱三态（分歧⑤）：applicable=true+清洗后空→降级；applicable=false+空→合法非降级；true+有效条目→正常；
  - MAX_HYPOTHESES=5 **按输出顺序截断**（排序即裁决倾向）；applicable=false 反向强制清空 hypotheses。
- **输出结构**：hypotheses / applicable / coverage_note / summary / confidence / ambiguity_note（清洗丢弃/截断原因并入）。
- **被依赖**：synthesize、Dk、Bd、G24、sufficiency 的直接上游。其 next_options 不含 weakest_link（分歧④B 案：假连通）。

### 4.6 SkillSynthesize（P-skill-synthesize，2026-11-18）
- **职责**：假设谱收敛+方案终稿，一次 LLM 调用三段输出：selection_rationale（引用 H1/Hn 编号权衡）/ final_deliverable（达到可写入交底书完成度的推演）/ alternatives_appendix（未选假设完整保留+保留理由）。
- **触发**：**仅 goal_type==scheme_design**；router._clean_menu 从 trail 内 anchor 的 result_fields 读 goal_type，非 scheme_design 一律剔除本技能（analysis/review/simple_query 链路字节级回归）。#9 collab 旁路：state.collab_synthesize_override=True 可绕过门控（仅 collab_pipeline 多路协同场景注入）。
- **关键方法**：假设谱全量陈列并 H1..Hn 编号；final_deliverable 缺失即 result 打 degraded 标记（final_deliverable_missing / empty_output），**绝不修改 state.hypotheses**，router 据标记强制 next=terminate，链路回退旧行为。
- **decide_terminate**：恒 False（生成类不做终止决策）。
- **v0 边界**：不注入 matrix_knowledge（最小面）。

### 4.7 SkillDkDeduction（P-skeleton Dk，节点③）
- **职责**：为全部 hypotheses 与 weakest_link 条目织"证据→前提→结论"可追溯链——只织链不裁决不生成新假设。
- **触发**：主线 f_reason 之后；前置守卫：hypotheses 与 weakest_link 两者全空 → applicable=false 跳过（非降级，前置链未走常态）。
- **关键方法**：LLM 拆解步骤链；**unsupported_premises 纯 Python 派生**（evidence_ref 为空的前提逐链内嵌+汇总）；单链 MAX_CHAIN_STEPS=4 硬截断（截断打标 note）；非法条目/空 target 丢弃计数；chains 非列表→降级。
- **输出结构**：chains[{target_ref, steps[{premise,evidence_ref,note}], verdict_ready, unsupported_premises}]（state 键 deduction_chains）/ applicable / unsupported_premises / summary / confidence。
- **被依赖**：En（证据引用集合唯一来源）、Bd（背景）、G24（unsupported 合并进 gaps）。

### 4.8 SkillEnEvidenceNet（P-skeleton En，节点④）
- **职责**：多源证据横向交叉关联（印证/矛盾/补充），只织网不裁决证据真伪。
- **触发**：Dk 之后；前置守卫：Dk 链引用集合为空 → applicable=false 跳过。
- **关键方法**：`_refs_from_chains` 纯 Python 提取 Dk 全部非空 evidence_ref 及计数（配对集合与孤立检出共同数据源）；证据原文上下文定位（matrix 卡按 matrix_id 对齐 / ref_material 内 find 抽片段）；net 清洗：白名单引用对 + 三档关系 + (a,b)/(b,a) 去重；**contradiction_pairs 纯 Python 从 net 关系筛出**（比 LLM 单独输出更稳）；isolated_evidence = 引用集合中不进任何 net 边的 ref。
- **输出结构**：net[{src_a,src_b,relation:印证|矛盾|补充,note}] / isolated_evidence / contradiction_pairs / applicable / …。
- **关键约束（裁决3）**：contradiction_pairs **不进 G24.gaps**——terminate 汇编独立"证据冲突说明"章节（事实冲突≠覆盖缺口）。

### 4.9 SkillBdCrosscheck（P-skeleton Bd，节点②）
- **职责**：假设谱×薄弱点逐对判定回应关系；产关联矩阵+无回应薄弱点清单。只判关系，不评假设对错、不新增假设/薄弱点。
- **触发**：主线 Dk+En 之后（就近交叉，gap 提前暴露）；前置守卫：任一侧空 → applicable=false 跳过。
- **关键方法**：`_wl_locations` 提取薄弱点 location 唯一标识（wl 单 dict 契约，容错 list 形态）；cross_matrix 清洗：hyp/wl 标识白名单 + 三档关系（回应/竞争解释/无关，残缺覆盖写 note 不改档位）；背景压缩（Dk 只给 target+premise 骨架，En 只给边三元组，防上下文爆炸）；**unaddressed 纯 Python 派生**（无任何回应/竞争解释关系的薄弱点）。
- **输出结构**：cross_matrix（state 键 bd_matrix）/ unaddressed_points（state 键 unaddressed）/ applicable / …。
- **被依赖**：G24（unaddressed 合并进 gaps）。

### 4.10 SkillG24Topology（P-skeleton G24，节点①）
- **职责**：决策整合前全局回视——四图投影 + 子问题遗漏检测 + **gaps 三源合并（系统唯一对外缺口出口）**。
- **触发**：主线 Bd 之后（f_reason→Dk→En→Bd→G24→terminate）。
- **关键方法（混合架构裁决1）**：
  - `_project_four_maps` **纯 Python 先行**（审计基底不能飘，LLM 挂了图还在）：①骨架族谱图（trail 执行序列+每步理由）②工具资源拓扑图（邻接表全技能 vs trail 已执行→未触发技能集）③知识资产关联图（matrix_knowledge 命中卡）④推理路径状态图（核心字段存在性+步数）；
  - LLM 只做子问题遗漏检测（uncovered_subquestions）；LLM 挂 → uncovered 置空 + _degraded，Bd/Dk 来源缺口照常合并；
  - gaps 合并三类：G24 原生 uncovered_subquestions（带 reflow_allowed=True 回流标记）+ Bd.unaddressed + Dk.unsupported_premises。**契约红线：contradiction_pairs / drift_warnings 永不写入 gaps**；
  - 循环防护：回流补推演每子问题≤1 次，G24 只产标记，计数管控在 router 层 trail 打点；
  - 锁定契约1：四图只读 state 已有数据，绝不主动拉取 ref_material 新检索。
- **输出结构**：topology（四图快照）/ uncovered_subquestions / gaps[{gap_type: uncovered_subquestion|unaddressed_weakpoint|unsupported_premise, payload, reflow_allowed?}] / applicable / …。

### 4.11 SkillGsReview（P-skeleton Gs，节点⑤）
- **职责**：长链过程态轻量检查点（软挂全技能，router 自主选路）：①resume_view（结构化精简续链视图，解长链 trail 膨胀）②drift_warnings（主题/方向漂移告警）。只观测不纠偏，禁止修改 trail/state 推理结论。
- **触发**：steps<4 前置守卫跳过（短链不浪费轮次，非降级）。
- **关键方法**：trail 压缩注入（只取 skill_id+reason 骨架，锁死拒绝无节制复制）；resume_view 3000 字符 Python 硬截断；降级兜底=极简 resume_view 保留检查点标记；双消费：resume_view 注入后续技能 prompt + 落 state.gs_view 供 terminate 复用（每次执行覆盖=最新检查点）。
- **decide_terminate**：drift 非空倾向收敛（仅软性 suggest，终裁 router）。
- **裁决3**：drift_warnings=方向类风险，严禁进 G24.gaps（那是覆盖类缺口）——terminate 汇编独立"过程漂移"章节。

### 4.12 SkillTerminate（链路出口）
- **职责**：三职责递进：终止（消费终态产出 final_output）/ 汇编（依据 trail 内每步自报 step_summary 拼全局图景）/ 质量自检（termination_reason + steps_used）。
- **触发**：显式菜单选择。**纯 Python 拼装，零 LLM**（分歧⑩B 案：build_prompt 直接 raise NotImplementedError；run 覆写基类不走 LLM 管线，但保留降级兜底/审计/schema 补齐）。
- **关键方法**：
  - run 开头断言 next_options 非空即 raise（防邻接表过校验后被热改）；
  - 汇编素材来自 **trail 携带的每步自报摘要**（分歧⑧C 案：state_inputs 只有 goal/goal_anchored/summary/trail 四键——terminate 对新增技能封闭，新技能只要输出 summary 契约即自动纳入，耦合方向反了会产生 O2 类静默漏编）；
  - termination_reason 只输出可观测两态 normal_stop|max_steps_hit（"谁决定停"不在感知域，强写即编造）；降级特殊语义=assembler_crash（全链路最高级失败），强制保留三核心字段；
  - coverage_check 只陈列触及/未触及客观事实，禁止"覆盖充分/不足"定性评价（分歧⑨）；
  - P-C4：长素材作汇编附件经 `_ref_material_block` 同口径分流；
  - 空链路直达 terminate 合法（simple_query 场景）。
- **输出结构**：final_output / termination_reason{verdict,detail} / steps_used / coverage_check。state_outputs=[]（T9 契约：出口零回写）。

### 4.13 SkillSufficiencyCheck（P-C5 后置参谋）
- **职责**：链路收口后对"结论↔证据"支撑关系机械对账，可疑时 LLM 疑点复核。**只告警不裁决不拦截**（R15：alerts 永不作任何自动机制的触发条件；R16：LLM 复核永为机械指标下游放大器，无机械疑点零 LLM 调用）。
- **触发**：executor 后置（POST_EXIT_HINT 同 memory_gate 先例），不占链路步数、不进 next_options 菜单、不写 steps_log，审计独立。
- **关键方法**：
  - `_mechanical_report`（纯 Python，8 指标）：subq_coverage（2-gram 重叠，对账对象排除 scan 自身防复读自命中）/ hypotheses_n / wl_addressed（wl↔假设谱 2-gram 交集，不随检索算法迁移）/ empty_evidence_fields / degraded_steps / retrieval_dependent（BM25 同源，与检索选段共用一把尺）/ material_unmapped / final_assembly_present（病灶A回归哨兵）；
  - `_mechanical_alerts`：候选阈值告警（THRESH_SUBQ_COVERAGE=0.6 等）；
  - 短链路/simple_query → applicable=false 合法跳过；无机械疑点 → 零 LLM；有疑点 → LLM 复核失败静默跳过+固定告警；
  - `_clean_review`：措辞违规过滤（"建议/应/需要/合格"开头），违规不丢弃——原文保留供人审；LLM 新发现强制 `[LLM新发现]` 打标。
- **输出结构**：sufficiency_report{applicable,llm_review,filtered_violations} / alerts / coverage_metrics / applicable / coverage_note / summary / confidence / ambiguity_note。**state_outputs=[]（零回写）**。
- **被依赖**：无人消费（终态告警材料，人审用）。

---

## 五、给未来 AI 的避坑要点

1. **改技能必须同步改邻接表**。`data/atomic_skills_adjacency.json` 是权限/schema/菜单/终止的唯一事实源：state_inputs 漏声明 → `_in` 抛 PermissionError；state_outputs/result_fields 漏声明 → schema 补齐/写回失效；且改完必须过 `adjacency_validator.assert_loadable` 双触发校验（含自环检测、is_exit 识别）。ThinkState 的动态输出键已因校验器 G 检查全部显性化——新增输出键同样必须显性声明。
2. **三种"异常"语义严格三分，不可混淆**：`applicable=false`（语义不适用，合法正常路径）/ `_degraded`（解析或 LLM 异常，置空+标记但链路不阻塞）/ 前置守卫跳过（输入空，applicable=false 非降级）。试点③ O2 教训：声称适用但清洗后全空必须降级，且 summary 文本不可豁免。
3. **applicable 布尔顿化是家族统一模式**（str "true/yes/1" / bool / int，缺失非法回退 true），写新技能照抄同款，勿自造口径。
4. **菜单与终止判定永远不能交给 LLM**。next_options/terminate_suggest 全部取自邻接表+Python 条件函数（decide_terminate 覆写）——这是"漂移防线"的核心设计。gs_review 的 terminate_suggest 也只是软建议，终裁在 router。
5. **gaps 是系统唯一对外缺口出口，且有黑名单**：contradiction_pairs（En 产出）与 drift_warnings（Gs 产出）严禁写入 gaps——它们分别在 terminate 汇编为独立"证据冲突说明"/"过程漂移"章节。混淆"事实冲突"与"覆盖缺口"是本体系明确定性的错误。
6. **素材注入走 `_ref_material_block` 统一分流，禁止私自拼 ref_material 进 prompt**。阈值 REF_INLINE_THRESHOLD=8000（消费侧）与 REF_MAX_CHARS=200000（入口写入硬上限）是两套独立参数；`matrix_knowledge` 与 `ref_material` 物理隔离（独立分隔符头），仅 f_reason/weakest_link 可消费 matrix，sufficiency 判据零读取 matrix（R15a 源码断言把守）。ref_material 一旦写入链中禁改（R13 只读红线），anchor 的 R4 覆盖防御（入口优先丢弃 LLM 版本）同理。
7. **LLM 只做语义，凡是能 Python 算的必须 Python 算**（F20 范式家族惯例）：wl 的 min/risk_level 映射、Dk 的 unsupported_premises、En 的矛盾对与孤立证据、Bd 的 unaddressed、G24 的四图投影与 gaps 合并、terminate 全部汇编、sufficiency 的机械指标。给 LLM 加"帮忙算一下/评一下档"的活就是踩坑。
8. **sufficiency 的 BM25 判据与 ref_retrieval 检索选段强制同源**（共用 bm25_scores/_segments）。检索算法迁移时该处必须同步迁移，禁止两套打分标准；E12 N1 告警链 FAIL 时优先查 sufficiency 阈值，别动检索主逻辑。
9. **synthesize 的路由门控在 router._clean_menu，不在技能内**：仅 goal_type==scheme_design 放行（goal_type 从 trail 内 anchor 的 result_fields 读，fail-safe 宁可不进）。collab_synthesize_override 是唯一旁路且只由 collab_pipeline 注入——改普通任务链路时禁止触碰此门。
10. **terminate 覆写了 run()**：不走基类 LLM 管线。给它"加个 LLM 总结"类改动直接违反分歧⑩B 案定案（演进路径是先 C 案 Python 骨架+≤100字总括，禁止直接跳 A 案完整 LLM 自由汇编——放宽容易收紧难）。
11. **terminate 对新增技能封闭**：汇编素材只来自 trail 每步自报 step_summary（≤60 字，STEP_SUMMARY_MAX）。新技能只要 result 带 summary/step_summary 契约即自动纳入汇编；若让 terminate 依赖具体技能的业务键就是重蹈"耦合方向反转→O2 静默漏编"。
12. **审计红线**：全部技能审计落 `audit_topic_log`，op=`atomic_skill:<skill_id>`，独立归属**不进 E7 晨报**；只有思考产出结论并提案入库时，整条轨迹才作为提案附件过人闸。审计失败只 stderr 告警不阻断业务。
13. **executor 后置位技能不进 SKILL_REGISTRY/邻接表 entries**：sufficiency 由 executor 补跑（但需邻接表条目供 state_inputs 声明），memory_gate 是独立 Python 件。在 registry 里找它们会找不到。
14. **硬上限家族统一截断+note**：sub_questions 8 / sources 6 / hypotheses 5 / others 4 / chain steps 4 / resume_view 3000 字符 / step_summary 60 字符。截断必须记入 ambiguity_note/note，禁止静默截断。
15. **技能红线总纲**（atomic_skill.py 头注）：技能不做事实裁决、不触发任何记忆提案、不读写 DFCP 卡片（dfcp_link 声明除外，当前全部为"无"）。任何"顺手写个记忆/评个对错"的改动都破线。

**诚实标注（读码未找到/未展开的）**：
- `skill_memory_gate` 未在本次 13 文件清单内，仅从 router_atomic.py 头注与 executor.py L377-390 确认其存在与定位（独立件，非 BaseAtomicSkill）。
- 邻接表 `atomic_skills_adjacency.json` 本身未读（任务限定 src 技能文件），next_options 具体收益/成本文案以邻接表为准；router 的 `_c3_should_preempt`（C3 抢占 f_reason）与回流计数细节未逐行读 router_atomic.py 全文。
- `cgraph/matrix_knowledge.py` 的 `matrix_block()` 与 `cgraph/ref_retrieval.py` 的 BM25 实现未读，仅确认消费接口。