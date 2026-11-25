# 06 eval/core + eval/experiment 代码地图

> 生成日期: 2026-11-25 | 基于全量源码阅读(28/28 文件)
> eval/core: 13 个 .py, 3289 行 | eval/experiment: 15 个 .py, 3474 行
> 调度入口: `scripts/nightly_run.py` STEPS 注册表(Windows schtasks 定时夜跑)

## 一、eval/core 巡检体系总览表

| 文件 | E编号 | 行数 | 检什么指标 | 数据源 | 产出 | 调度方式 |
|---|---|---|---|---|---|---|
| e0_topic_gate_guard.py | E0 | 136 | TOPIC_LAYER_ON 开关断层(写守卫卡→断言挂锚/落hold→自清理) | topic_layer/memory_hook/hold池/jsonl/db | stdout PASS/FAIL, 无落盘 | nightly STEPS(硬FAIL阻断) |
| e1_probe_trigger.py | E1 | 186 | 探针触发率: 正例命中/负例误触/detection_hint 结构命中 | data/probe/*.jsonl (123条) + cgraph Engine 全链 | logs/e1_probe_result.jsonl | **已注释停跑**(NO_MEMORY=1 手动), 晨报仍读历史结果 |
| e2_gate_calibration.py | E2 | 258 | 反问门 match_eff 标定扫描+双门分层评估+固化回归(gate_regression.jsonl) | memory_query.ask / probe | logs/e2_gate_calibration.json | nightly STEPS |
| e4_maintenance_patrol.py | E4 | 585 | DB↔jsonl漂移/多主题/000超期/隔离区/冲突队列/实体悬空/event_ts空值/watchlist/别名脏词/占位卡/等价拦截增量/记忆健康度四维 | memories.jsonl + 各 jsonl + corpus_store.db | logs/e4_maintenance.json | nightly STEPS |
| e5_consolidation.py | E5 | 116 | (非巡检)机器可安全自动整理: 四档补挂+经验蒸馏+class/behavior回填提案+置信度动态扫描 | memory_organize/relations/distill | logs/e5_consolidation.json | nightly STEPS |
| e6_mind_graph_patrol.py | E6 | 160 | 思维图: 边饱和/主干坍缩/死边/定义-执行缺口/备份diff | data/mind_graph.json + backups | logs/e6_mind_graph_patrol.json | nightly STEPS(快照函数被夜跑前调) |
| e7_nightly_governance.py | E7 | 500 | P1 hold池三路分流/P2 高频簇混入/P2b cue区分度复检/P3 范式标定/P4 证据回写 | corpus_store.db (core_hold_pool/topic/paradigm表) | logs/e7_governance.json | nightly STEPS(支持 --rollback 与 E7_EMERGENCY_STOP 文件开关) |
| e9_dialog_governance.py | E9 | 344 | 对话归档主通道: 高价值复跑判定→store_pending提案 / transcript(applog.log)兜底脱敏入库 / miss_case / h通道断流 / 碎片72h聚合 | raw_corpus(session_dialog) + applog.log + gate_log | logs/e9_dialog_governance.json + store_pending 追加 | nightly STEPS 尾部 |
| e10_semantic_outlier_patrol.py | E10 | 160 | 语义离群: bge embedding 簇质心+μ-kσ 动态阈值检出脏卡(只告警) | corpus_store.db(topic_links+card) + 本地 bge-small-zh | logs/e10_semantic_audit.json (+state增量) | nightly STEPS(增量, --full 手动) |
| e11_atomic_skill_smoke.py | E11 | 323 | 七原子技能冒烟: f_reason/memory_gate/Dk/En/Bd/G24/Gs 非降级+schema+红线断言 | cgraph 技能本体 + 真实 LLM | logs/e11_atomic_smoke.json | **已注释停跑**(LLM成本, 手动 NO_MEMORY=1) |
| e12_router_smoke.py | E12 | 333 | skill_router 冒烟: G1/G2/G3 任务分型 + S1-S4 素材通道(直塞/检索/截断) + N1-N3 后置告警链 + Jaccard观测埋点 | cgraph router/executor + 专利原文 + db audit_topic_log | logs/e12_router_smoke.json | **已注释停跑**(手动) |
| e_hold_review.py | E-hold | 78 | pending hold 池夜间复审: memory_judge.review_holds() 接入主链(证据补齐→转正/维持/丢弃) | memory_judge (store_pending) | logs/e_hold_review.json | nightly STEPS |
| patrol_dialog_fragments.py | 碎片巡检 | 110 | session_dialog ≤20字节碎片占比(字节口径铁律), 超阈值15%告警 | corpus_store.db raw_corpus | stdout(+patrol_last_run.json 周频标记) | nightly 每日调度但脚本内周频锁, 本周首次才全量 |

E 编号缺口: E3(一次性审计)移 eval/audit/ 归档退役; E8(影子对话演习)移 eval/drill/ 按需手动。主链=纯本地无人值守内务闭环。

## 二、eval/core 逐文件详解

### e0_topic_gate_guard.py — E0 开关守卫探针
- 起源: 2026-11-16 发现 TOPIC_LAYER_ON 未启用导致业务静默断层。
- 流程: ①断言 topic_enabled() → ②memory_hook.add 写守卫卡(合法白名单编码) → ③断言 report['topic'] 存在 → ④断言零锚卡落 topic_hold_pool.jsonl → ⑥三侧自清理(hold jsonl + db 三表 + memories.jsonl)。
- 哲学: 断层变硬 FAIL, 拒绝静默漂移。退出码 0/1, 失败即 nightly FAIL。
- 坑点: 双写清理必须 db+jsonl 两侧都清, 否则残留旧行污染下轮断言。

### e1_probe_trigger.py — E1 探针触发率回归
- 123 探针(Aa/Bc/Dl 三骨架 × trigger/no_trigger + held-out)跑 cgraph Engine 全链。
- 每条结果强制携带判定环境三元组(llm_backend/llm_model/graph_file)+config 快照(P0-1 证据链, 防跨天归因断裂)。
- 断点续跑 + 3 天时效守卫(mtime 超期强制重跑, 防旧缓存冒充回归)。
- 当前状态: STEPS 中已注释(停跑, LLM 成本), 晨报 e1_summary() 仍展示历史 jsonl。

### e2_gate_calibration.py — E2 反问门标定
- 双职责: (1) match_eff 阈值 0.08~0.30 扫描 + F1 最优点 → 只出报告不改阈值, 改值留人晨裁; (2) 固化回归用例 gate_regression.jsonl(POS/NEG/CONDITIONAL/time_window_hit 四类)——双门/锚点逻辑任何改动后须全绿。
- 关键函数: run_gate_regression / build_positive_queries(corpus 目标带 corpus 检索, 11-09 尸检修正) / sweep 循环。

### e4_maintenance_patrol.py — E4 夜间维护巡逻(只读只报警)
- 原则: 机器不私自处理数据——所有项只计数+报警, 处置归人。
- 检查项编号连续扩展(0~11): 0 DB↔jsonl 对账(漂移非零报警) / 1 多主题混杂+粗口径嫌疑 / 2 000超期 / 3 隔离区按原因分组 / 4 冲突评审队列 / 5 经验split_pending / 6 实体悬空+runtime未挂靠 / 7 event_ts空值存量 / 8 watchlist / 9 别名健康(含设施③a: count_active_pending 区分活跃22 vs 终态209, 修正误报) / 9b dfcp_tag核心编码缺失 / 10 质量门(registry版本锚+占位卡+fragile) / 10d #30语义等价拦截增量告警(seen 水位机制) / 11 _memory_health 四维(检索/冲突级联/去重/归档)。
- 阈值统一收拢 calibration/nightly_calib.toml, 代码内不出现判定数字。
- 长度 585 行, 是 core 最重巡检; #30 P3 测试隔离: 若 memory_organize.MEM_DIR 被重定向, gate_log 路径跟随。

### e5_consolidation.py — E5 自动整理(唯一的"写"步)
- 与 E4 分工: E4 只读报警, E5 做机器可安全自动的幂等软层操作(只追加不删除, 不碰原子层主记录)。
- 四操作: classify_and_link 补挂 / distill_all 经验聚类 / memory_class + behavior 回填提案(E5批量提案+judge确认, 禁止机器直写主库) / confidence_dynamics_scan 只读统计。
- corpus 教材、multi_topic 冻结、保护清单在函数内部跳过。

### e6_mind_graph_patrol.py — E6 思维图巡逻
- 检查: 边权饱和(硬上限/软上限) / 贪心首跳 top3 集中度(主干坍缩) / 死边 / DECLARED_EXECUTORS 定义-执行缺口(当前清单已空, Jw 钩子已落地) / 与最近备份的 diff。
- snapshot_for_nightly(): 夜跑前 E1 调用, 当日快照不覆盖。
- 起源: Aa→Eo 顶 2.0 主干坍缩事故靠 E1 事后暴露, 损失一轮 8h 夜跑。

### e7_nightly_governance.py — E7 夜间治理(唯一可直写 verdict 的治理步)
- 四环节 P1-P4 + 紧急停机 + 回滚:
  - P1 hold 池复审: classify_hold 单入口三路分流——auto(release/keep, 直写 verdict, resolved_by='e7-auto') vs manual(强制人工: 零锚命中/多锚歧义/cluster_conflict)。豁免 user-retain-* 不重复提案。审计字段契约固定(card_id/summary/check_type/rule_triggered/state/last_judge/expire_at/evidence/next_action)。
  - P2 高频簇巡检: inspect_freq='high' 簇逐卡核锚词, p2_exempt 豁免, 可疑混入只上交不踢。
  - P2b cue 区分度复检: 全量复检 active 锚 cue 判别力(只告警不删改, 锚属元配置变更必须人闸)。
  - P3 范式两表标定: calibration_pending 复审 / candidate 满 3 独立实证 propose_promote(不直接升) / active 超 90d propose_recalibrate。
  - P4 证据回写: maintenance_cases 同型 paradigm_hint ≥3 写 audit_paradigm_evidence(outcome='proposed')。
- 红线: 不删数据只降权/上交; 幂等; ID 层 32 位全 id 等值查询零 LIKE。
- E7_EMERGENCY_STOP 触发态文件一键停 P1/P2 自动动作; `--rollback <card|all> [--since]` 只回滚 e7-auto 的动作, 人工裁决永不被机器回滚。

### e9_dialog_governance.py — E9 对话归档主通道
- 归档侧主通道(会话收尾信号不可靠, 夜间固定时序是主力): 当日 session_dialog 复跑 should_archive_highvalue → 高价值提案送 store_pending(**红线: 绝不直写 core_memory_card**)。
- transcript 兜底: 只读扫 applog.log 提取用户提交原文 → PII 脱敏(_sanitize_text) → simhash 当日去重 → t 通道入 raw_corpus(复本隔离+来源指针)。路径收拢 config/paths.json(优先级 env > paths.json > 默认), 探测失败打 e9_transcript_offline 告警。
- 优先级规约: 旁路true+同hash跳过 / 旁路false+夜间true补提 / 旁路true+夜间false打风险标送E7复审。
- 附加: miss_case 晨报列示 / probe 假阴性疑点(E7/F系关键词但判定false)攒 scar_case / #12 h 通道(PreCompact)断流审计 / #31 悬空碎片 72h 兜底聚合。

### e10_semantic_outlier_patrol.py — E10 语义离群
- 独立 E10 不并入 E7(故障隔离, embedding 异常不拖垮 E7)。
- 算法: 每簇 summary 优先(detail 补) embedding → 质心余弦距离 → μ-kσ 动态阈值 + 0.30 绝对下限。per-topic k 可配(patent 簇 2.5)。小簇 <5 卡跳过。
- 全程只读只告警; 向量不持久化。核心捕获对象: 引用性提及假阳性(正文仅举例引用簇关键词, cue 统计合格但语义离群)。

### e11_atomic_skill_smoke.py — E11 原子技能冒烟
- 七技能各固定冒烟样例(专利审查真题同源语境, 链路连贯 f_reason→Dk→En→Bd→G24): 断言非降级 + schema 合法 + 红线(f_reason 无 plausibility 档 / memory_gate 绿档类目封闭 观测/测试结果/踩坑 / Dk 链≤4步 / Gs view≤3100字)。
- 只调技能 run() 不调编排器 commit_green_cards——夜间不产生绿卡写入。
- 现已从 STEPS 注释掉(手动跑), 与 E12 一起因 LLM 成本降频。

### e12_router_smoke.py — E12 编排组件冒烟
- 三组样本: G1/G2(应触发 f_reason)/G3 负例(warn 裁量不 FAIL); S1-S4 素材通道(≤8000 直塞 / >8000 检索 / >200K 截断 / CIPP 6401字直塞)——词表机械断言拒绝 LLM judge; N1-N3 后置告警链(sufficiency:alert 落 audit_topic_log, retrieval_dependent 哨兵)。
- 尾部 P0-1 观测埋点: 近 60 条 gate 摘要 bigram-Jaccard 分布(幂等阈值 0.6 决策素材, 先观测不调参)。

### e_hold_review.py — E-hold 复审步
- #7 收尾: memory_judge.review_holds() 之前全仓零调用点(D1 致命缺陷), 挂进 nightly 形成"E9 提案入池 → 夜间消费转正"闭环。
- 锁契约: 抢锁失败返回 skipped, 告警但不阻断(退出 0); 执行异常才退 1。
- 幂等: 无新证据的 hold 原样保留, reviews 追加审计记录非业务漂移。

### patrol_dialog_fragments.py — 碎片巡检(周频)
- 口径铁律: 碎片 = UTF-8 字节数 ≤20(length(cast(body as blob))), 禁用 length(body)(字符尺, 曾把 552 条中文短指令误判)。
- nightly 每天调度, 脚本内 ISO 周频锁: 本周首次全量+阈值判定(15%), 其余静默 exit 0。

## 三、eval/experiment 实验体系总览表

| 文件 | 行数 | 实验类型 | 开关变量 | 影响链路 | 状态 |
|---|---|---|---|---|---|
| ab_test.py | 82 | AB 对照(裸模型 vs 图路由) | 无(手动跑) | cgraph Engine 全链 | 活(手动工具) |
| adjacency_validator.py | 202 | 结构校验器(非实验,守卫) | — | 原子技能邻接表加载 | **活(被 cgraph 生产调用)** |
| deepen_experiment.py | 45 | 探索实验(DEEPEN 自主加深激活) | 无 | cgraph Engine | 一次性/历史 |
| e_x_multi_skill_probe.py | 344 | 探索脚手架(固定路线串技能) | --tasks/--route | 四+二原子技能链(零DB写) | 活(按需) |
| f20_nightly_guard.py | 213 | 守卫(回归哨兵) | profile=baseline 固定 | F系 minloop 五场景 | **活(nightly STEPS 挂主链尾)** |
| f20_weight.py | 343 | 权重层(库模块,非脚本实验) | BETA_PROFILES 三档 | F10 候选分支打分 | **活(被 minloop/F30 生产调用)** |
| f30_forward.py | 305 | 正向演化状态机(库模块) | enable_f30/enable_llm_epilog | F系推演 K 分支推进 | **活(被 minloop 生产调用)** |
| future_sim_minloop.py | 633 | F系最小闭环冒烟(F00→F10→F40) | CONFIG 块(raw_bridge/sensitive/K/beta...) | F系全链只读推演 | 活(F20守卫底层引擎) |
| hltm_shadow_trial.py | 197 | shadow 影子试验(热度加权灰度) | TRIAL_WINDOW 30d/14d | memory_query recency(仅影子替换) | 活(结论:零增量, 待复跑) |
| intent_alignment_probe.py | 376 | 观测实验(意图稀释假设检验) | --live/--no-llm | data/trajectories 离线分析 | 一次性/结论沉淀 |
| p26_judge_q4_profiling.py | 108 | 性能 profile(调研) | 无 | memory_judge._bloodline O(C×N×G) | 一次性调研 |
| p27_q1_continue_ab_eval.py | 142 | AB 影响评估(改动前) | 无 | memory_judge._stub_one Q1 分支 | 一次性评估 |
| smoke_router_23.py | 80 | 冒烟(#23 复杂度路由) | 无 | ComplexityRouter.route() | 历史冒烟(旧图路由) |
| test_raw_bridge_stage1.py | 100 | 冒烟测试(阶段1 raw桥) | raw_bridge_enabled | _edges_from/raw_drill | 活(阶段1回归) |
| verify_ratio_ablation.py | 304 | 消融实验(#21 Hebbian 边权) | EPS/DECAY/四策略组 | mind_graph.json 边权(离线拷贝) | 一次性/结论沉淀 |

## 四、eval/experiment 逐文件详解

### ab_test.py
同一问题双路: A=裸模型单轮直答(LLMClient), B=图路由全流程(Engine.run, 会触发赫布更新——这是有意观测项)。产物 logs/ab_result_*.jsonl 供人工盲评或 eval/score.py LLM 裁判。

### adjacency_validator.py — 邻接表守卫(注意: 是生产组件)
七项校验: schema 完整 / 孤岛 / 单向死端 / 幽灵后继 / 终止可达(BFS) / 自环 / state_inputs-outputs 与 ThinkState 字段漂移。后置收尾步豁免(skill_memory_gate/skill_sufficiency_check, 编排器触发不在主链图内)。校验失败拒绝加载(assert_loadable 抛 RuntimeError)——被 cgraph/atomic_skill.py load_table 调用, 是**生产加载链的一部分**, 虽住在 experiment 目录。日志 logs/adjacency_guard.log。

### deepen_experiment.py
3 道隐藏疑点题, 观察强化引导后模型是否主动输出 [[DEEPEN:节点码]]。一次性实验。

### e_x_multi_skill_probe.py — 多技能集成脚手架
硬编码固定路线消费邻接表(零 LLM 分支决策), 真实 CodeChatLLM(agent_free=True)跑 9 任务(T1-T4 + C1-C5 语料真题: 断档事故/挂载质量/命名幻觉/F系硬伤/建仓分歧)。结构性自动观测(degraded/schema缺字段/菜单异常/state上下文退化), 语义问题留人工标注区。零业务 DB 写, 只落 data/experiment/multi_skill_probe_<ts>/。用途: 人工区分 prompt bug vs 范式待迭代, 挑 Mock 回归用例。

### f20_nightly_guard.py — F20 基线守卫
五场景(锚查询+goal 前缀+基线判定+top-k 短id位次), subprocess 调 future_sim_minloop --profile baseline。判定: 只看链路判定翻转与座次颠倒, 权重数值浮动合法(90d 半衰天然衰减)。退出码恒 0(experiment 层不允许非零退出惊扰 core 主链), 翻转只写 guard_latest.json 供晨报高亮。A-2 数据漂移归因: 对比前后快照 goal 卡边数/topic归属, 区分"数据漂移"vs"权重异常"。基线位次来源: factor_fresh 连续 age 修正 + inst_id 定序, DB 行序偶然序不可作基线。

### f20_weight.py — F20 权重层(生产库模块)
公式 w=(1+δ_step)^β·C(conf)·F(fresh)·R(edge)·D(density), β 三档写死(0.6/1.0/1.6)。
- 关键修复史: factor_fresh 只认 event_ts 不回退 ts(#37 ts 污染: 批量导入让旧事件卡 ts≈2026), 缺失给中性 0.7; factor_edge 修正 rel 编码不一致 bug(裸 relation 词曾被误判 topic 弱边); factor_density 债务软惩罚 D_FLOOR=0.3 硬下限(分支只压低永不消失, 红线纪律②)。
- attribute_failure 失败归因四枚举(goal_isolated/sensitive_blocked/edge_coverage_gap/step_exhausted); degrade_path_candidates 门槛④降级分支(仅审计素材, 不自动晋升)。

### f30_forward.py — F30 正向演化(生产库模块)
方案C 混合: 内层纯确定性图推进(LLM 零参与状态转移), 外层可选 LLM 终态叙述(不回流 path/state/w, 关闭后字节级等价)。FwdEvolver 状态机五态(active/discarded/frozen/reached_T/dead), 贪心同权按 inst_id 字典序(修 max 字典序反序 bug, T30-3)。raw 叶子落点即停(raw_leaf)。测试依赖注入(edges_fn/score_fn 等全可桩)。复用 _edges_from 边规则与 F40 100% 对齐。

### future_sim_minloop.py — F 系最小闭环引擎
F00 冻结快照(DFCP 检索锚定, 全程不复检) → F10 分支发散(δ: conflict=-0.8/簇内贴线/簇外偏离; 硬边豁免 prune 裁剪——conflict 恒 0.20<0.3 若不豁免则矛盾分支数学上永远出不来) → F20 重排 → F30 推进 → F40 逆向回溯(BFS, 高敏簇过滤, 大簇 topic 横穿裁剪只留入簇首跳, raw 边反向跳过) → 正演校验三独立指标(可达/相交/步数偏差)。
阶段1 raw 桥: 摘要卡→raw_corpus 原文, 标题清洗+2-gram Jaccard(阈值 0.4, 全文匹配会把专利连成一片——原型实测过的坑); raw 节点是叶子铁律(is_raw_node 含'.'快判, _edges_from 直接返回 []); raw_drill() 是公共出口(查专利详情/发明人)。主入口被 f20_nightly_guard subprocess 调用, --out 传相对段。

### hltm_shadow_trial.py — HLTM 灰度
影子替换 memory_query._recency_table(30d/14d vs 现行 7d), _CitedLogGuard 跑完回滚 cited_log(零副作用)。8 固定样本。已得结论: 当前数据形态下 top5 排序零增量(热门卡同步抬升), 真正发挥场景是引用间隔 7~30d 的长热记忆, 待跨月数据复跑。

### intent_alignment_probe.py — #22 意图稀释假设检验
方案A embedding(bge)节点产出 vs query 余弦分趋势; 方案B LLM 分层抽样校验。数据源 data/trajectories thought_chain。关键结论(已沉淀): 节点贴合度低由"节点职责离字面query的距离"决定而非链路漂移——低分集中在元认知/方法论节点(G10-G14/G21), 与位置无关; "缩短链路治意图稀释"未获数据支持, #23 分流价值只在省 token, #24 兜底方向应为"保证低贴合节点价值被汇入"。

### p26 / p27 — judge 专项
p26: 量化 _bloodline O(C×N×G)(每候选重复 load_memories+全库 grams 重算), 为预聚合 gram 快照方案提供依据。p27: Q1 discard 分支补 continue 的影响评估——遍历 store_pending+judge_log hold 快照, A/B 对比判定, 全部为 hold→discard 转变, 只出报告不改代码(用户裁决: 严禁提前改代码必先评估)。

### smoke_router_23.py — #23 复杂度路由冒烟
旧图(MindGraph.json + ComplexityRouter)简单/中/复杂三档分型+路径长度校验, 只验路由行为不评质量, router_log 落盘。属旧 cgraph 图路由时代的产物(与 E1 同代), 原子技能邻接表架构后为历史参考。

### test_raw_bridge_stage1.py — 阶段1 raw 桥回归
六约束: raw 边能建(P28) / raw 是叶子 / 开关可回滚(置 False 边全消失) / raw_drill 端到端(抽出发明人=黄艺超) / F40 路径不含 raw 节点。只读库。

### verify_ratio_ablation.py — #21 Hebbian 消融
E1 翻转可行性(集中负反馈几轮改道) / E2 四组对照(off/full/random/utility_only, 26查询×5轮) / E3 翻盘阈值(gap/ε)。结论已沉淀: 主路径由结构权重(~1.83 vs 旁支0.3-0.5)硬锁, 四组均无法改变 Aa→Jw 主路径; verify_ratio 信号非噪声(比 random 稳定数倍)但只在旁支层微调; 离线无法证明"改善", 需真 LLM 盲评闭环。

## 五、巡检-实验-治理关系图

```
                      ┌─ schtasks 定时 ─┐
                      ▼                │
  scripts/nightly_run.py (STEPS 注册表, run_step 带超时分级)
  │
  ├─ 硬FAIL守卫层: E0(开关断层) ──失败→ 晨报FAIL, 不静默
  ├─ 标定/回归层: E2(反问门标定+固化回归)
  ├─ 只读巡检层: E4(记忆库11项) / E6(思维图) / E10(语义离群) / 碎片巡检(周频)
  │      │ 只报警                    │
  │      ▼                          ▼
  │  人闸晨报终审  ◄─── logs/*.json + stdout 明细
  ├─ 治理/提案层(机器提案, 终裁=人):
  │      E5(自动整理+回填提案) → judge 确认 → 主库
  │      E7(hold分流/范式标定, e7-auto 直写可 --rollback)
  │      E9(对话归档提案→store_pending) → E-hold(夜间消费转正)  ←─闭环─┘
  ├─ experiment 挂主链: F20 基线守卫(退出码恒0, 只写 guard_latest.json)
  │
  └─ (停跑保留): E1/E11/E12 —— LLM成本降频, 手动触发

  实验层内部 F 系栈:
  future_sim_minloop (F00→F10→F40 引擎, 只读库)
       ├─ f20_weight   (权重层, score_branch 排序)   ← minloop 导入
       ├─ f30_forward  (正向演化状态机)              ← minloop 导入
       └─ f20_nightly_guard (五场景基线哨兵) ──翻转→晨报高亮(不阻塞)
            └─ 数据漂移归因: 区分库数据变动 vs 权重故障

  发现→实验→修复回路示例:
  E1 暴露 Aa→Eo 坍缩 → E6 主干巡检固化 → verify_ratio_ablation 消融证伪
  "Hebbian改善路径" → 主张未获支持, 收敛为守卫+观测;
  E4 alias_pending 误报231 → 设施③a count_active_pending → 真积压22;
  E9 transcript 碎片污染 → patrol_dialog_fragments 周频防护(字节口径)。
```

## 六、给未来 AI 的避坑要点

1. **E7 是唯一能直写 verdict 的巡检步**，但只在 P1 auto 路径、resolved_by='e7-auto'、且 --rollback 只回滚机器动作不碰人工裁决。改 E7 分流逻辑前必读 classify_hold 头部写死的用户裁决①-⑤。
2. **E4/E6/E10 是纯只读**，任何"顺手自动修一下"都违反"机器不私自处理数据"红线（2026-11-09 风险审查定案）；E5 是唯一写步，且只写软层+提案，禁直写主库。
3. **E9 绝不直写 core_memory_card**；applog.log 三硬规约（只读/复本隔离/来源指针）写死在代码不可配置。transcript 路径优先级 env > paths.json > 默认。
4. **计数口径坑**：alias_pending 须用 count_active_pending（终态历史不计积压）；session_dialog 碎片必须字节数（length(cast(body as blob))）非字符数；E1 结果文件 3 天即陈旧须重跑。
5. **阈值全在 calibration/nightly_calib.toml**（E4/E6），改阈值改 toml 留 changelog，代码内搜不到判定数字是正常的。
6. **experiment 目录里住着三个生产组件**：f20_weight / f30_forward 被 minloop 生产调用，adjacency_validator 被 cgraph 邻接表加载链调用——别当"实验脚本"误删误改。F20 守卫退出码恒 0 是规约。
7. **E1/E11/E12 已从 STEPS 注释停跑**（LLM 成本），晨报仍消费其历史 jsonl；E3 移 eval/audit 退役、E8 移 eval/drill 按需手动。E 编号续编不重排。
8. **raw 叶子铁律**（阶段1）：raw_corpus 节点是叶子不再扩散；标题级 2-gram Jaccard 阈值 0.4，绝不用全文（专利全文匹配会连成一片）。F40 反向搜索永远不走 raw 边。
9. **f20 factor_fresh 只认 event_ts**：回退 ts 会把考古卡判最新（#37 实证），缺失给中性 0.7。
10. **minloop 快照/输出覆盖坑**：f20_nightly_guard 跑五场景须先把 snapshot json 挪独立目录（minloop 同名覆盖）；--out 参数只传 logs/ 之后的相对段。
11. **测试隔离**：memory_organize.MEM_DIR 被回归测试重定向时，E4 健康度段 gate_log 路径跟随；e_hold_review/e5 设 NO_MEMORY=1。
12. **退出码语义分级**：E0/E11/E12 FAIL=1 硬阻断进晨报；e_hold_review 锁冲突=0；碎片巡检周频静默=0；F20 守卫恒 0；adjacency_validator 0/1/2（表损坏=2）。
13. **不确定标注**：smoke_router_23/verify_ratio_ablation/intent_alignment_probe/p26/p27/hltm 为一次性或历史实验，结论已沉淀进设计文档，代码保留供复跑验证；deepen_experiment 为早期探索，价值存疑。E6 的 DECLARED_EXECUTORS 清单当前为空（Jw 钩子已落地），属待清理陈旧结构。

（本地图基于 2026-11-25 全量源码阅读生成；调度关系以 scripts/nightly_run.py STEPS 注释行为准，其中 E1/E11/E12 处于注释停跑状态。）