# 系统现状评审与整改台账（2026-11-21）

> 评审范围：`cgraph/`（推理）与 `memory/`（记忆）两模块。
> 评审方式：逐文件读码 + 行号取证，不含臆测；代码无法判断的效果类结论明确标注。
> 本文件兼作整改 issue 台账，按 P0→P1→P2 排序，完成后在对应条目标注 commit。
> **后续系统综合评审（2026-11-22，全批闭环后基于 148 py / 2.5万行权威扫描）见附件 `docs/review_system_20261122.md`。**

---

## 一、总体判断

**骨架很真、血肉有虚。**

- **真的部分**：路由刚性约束（fallback≤1、单技能≤2、四通道退出）、素材通道截断、测试覆盖（`test_skill_router.py` 40+ 用例）、`corpus_registry` 行级聚合锚、`memory_query` 两段式排序——这些都是踩过真实 bug 收敛出来的，注释里有 E8 首跑 / P39/P40 的修复痕迹。
- **虚的部分**：两个"智能核心"当前是桩——**记忆的 judge 是长度判断（`memory_judge.py:57-140` 注释自认"规则桩"），推理的赫布是加减计数器（`hebbian.py`）**。文档措辞（"四档判定""带权思维图学习"）高于代码实质。

分期策略本身合理（LLM 判定留了接口位），问题是"Week2 换基座"已拖延数周，桩实现仍在当生产用。

---

## 二、整改台账

### 🔴 P0 — 线上单点故障 / 潜伏数据损坏

#### P0-1 JSON 解析脆弱（链路崩溃根来源）
- **证据**：`cgraph/executor.py:431-451 _parse_node_json` 与 `cgraph/router.py:88-99` 均用 `t.find("{")` / `t.rfind("}")` 抠 JSON。
- **风险**：LLM 输出含嵌套 JSON 或字符串/注释内的花括号时，切片边界错，`json.loads` 失败 → 整条走链降级为纯文本，结构化信号（analysis/deepen）全丢。
- **方案**：引入括号计数扫描器（正确处理字符串字面量与转义），零外部依赖（项目无 requirements.txt，`json_repair` 未安装，故不引外部包）；抽为共用工具函数，executor 与 router 共用；补回归用例（嵌套/转义/多对象/纯文本）。
- **方案落地**：新增 `cgraph/json_utils.py`（括号计数扫描器，零依赖）；executor/router 切换；`eval/audit/json_utils_regression.py` 10/10 通过；`test_skill_router.py` 63 项全绿。
- **状态**：✅ 完成 commit 00579b4

#### P0-2 confidence_dynamics 双读不一致（数据漂移）
- **证据**：`memory/confidence_dynamics.py:32-38 _load_memories()` 永远读 `memories.jsonl`；而 `memory_query.py` 有 `_MEMORY_SOURCE` 在 sqlite/jsonl 间切换。
- **风险**：主库切到 sqlite 后，置信度升降级对着过期 jsonl 做动作，与主库漂移。
- **方案**：`_load_memories()` 复用 `memory_query.load_memories()` 同源读取；新增 `eval/audit/confidence_read_source_regression.py` 一致性回归（3/3 通过）。
- **实测漂移**：改造前 jsonl 245 条 vs sqlite active 314 条，差 69 条（即 69 条记忆的置信度动态更新从未生效）。
- **状态**：✅ 完成 commit 4fa285b

### 🟠 P1 — 准入门面桩转正

#### P1-1 memory_judge 规则桩接 LLM
- **证据**：`memory/memory_judge.py:57-140` 五问全为 if/else 长度检查 + 硬编码特征词匹配（Q1 `len(summary)<8`、Q2 `len(detail)<30`、Q4 全库 2-gram 重叠 `<6`）；注释自标"规则桩…Week2 换基座"。
- **风险**：系统的准入闸门靠长度判断守，且 Q4 每次全库 `load_memories()` O(N) 扫描。
- **方案**：接入 LLM 判定主路径（`llm_codechat.py` / glm-5.3-flash / agent_free=True），规则桩降级为强制 fallback；Q4 全库扫描本次保留，另立 P2-6。
- **设计文档**：`docs/design_memory_judge_llm_fallback.md`（v1.0 方案已出，待确认后编码）
- **状态**：✅ 完成 commit 5dfc6c6
- **代码**：`memory/memory_judge.py` 拆四组件 `_judge_call`(调度)/`_judge_call_llm`/`_judge_call_stub`/`_stub_one`，`_bloodline` 共用 Q4 预计算；`judge_log.jsonl` 强制写 `judge_source`(llm/stub)
- **回归**：`memory_judge_stub_regression.py` 7/7（--compare 基线一致，桩字节等价）；`memory_judge_llm_regression.py` 7/7（L1~L6 mock）；`test_skill_router.py` 63 项全绿
- **开关**：`JUDGE_USE_LLM=0` 纯桩；`JUDGE_LLM_MODEL` 可覆盖模型
- **冒烟**：`memory_judge_llm_smoke.py`（手动，不进 nightly）

### 🟡 P2 — 中长期技术债（不阻断当前冒烟/A/B）

#### P2-1 schema 版本与迁移
- **进展**：✅ 完成 commit 400f2fe
- **证据**：`memory/schema.py` 硬编码 dict 白名单；`corpus_store.py` 仅 `CREATE TABLE IF NOT EXISTS`，无 `schema_version` 表、无 migration 脚本；僵尸表 `persona_anchor` 仍在 SCHEMA 但 `add_anchor()` 已写 `core_persona_anchor`（`corpus_store.py:231-245`），`get_anchors()` 用 `LIKE '[已迁范式]%'` 过滤旧数据。
- **方案**：新增 `schema_version` 元表 + migration 脚本；清理僵尸定义；枚举变更留迁移路径，避免历史 jsonl 旧值静默拒收。
- **落地**：新增 `memory/schema_migrate.py`（schema_version 元表 + 迁移注册表 + dry-run/status）；
  迁移 0001 枚举规范化清洗 38 条非法 memory_class（observation→NULL）+ 43 条浮点 confidence（→L 档），
  明细写 `audit_schema_migrate`；迁移 0002 标记 persona_anchor 废弃并从 `corpus_store.SCHEMA` 移除定义（不 DROP 数据）。
  新增 `schema.ASSET_CLASS_ENUM` 校验；修复脏数据写入源 `cgraph/executor.py:548-555` 的
  commit_green_cards（observation/0.6/observation → runtime/L2/None）。
- **回归**：`schema_migrate_regression.py` 7/7；路由器 63 项、judge 桩矩阵 7/7、置信度读源 3/3 全绿；integrity ok
- **状态**：✅ 完成

#### P2-2 executor 巨型函数拆分 + 移除生产 assert
- **证据**：`executor.py:165-288 run_atomic` ~120 行混入 ref_material 切片/audit 写库/matrix 注入/terminate 补跑 4 个关注点；`commit_green_cards`（~80 行 DB 逻辑 + Jaccard 去重 + 审计双写）嵌在 executor；`engine.py:64` 用 `assert decision.is_valid` 做生产校验（`python -O` 下消失）。
- **方案**：`commit_green_cards` 移出 executor 独立模块；`run_atomic` 按关注点拆分；`assert` 改显式异常。
- **落地**：
  - `commit_green_cards`（~84 行 DB 逻辑）迁出至新模块 `cgraph/green_card_writer.py`，
    executor 侧以 `from cgraph.green_card_writer import commit_green_cards` 重导出，
    调用面（含单测 monkey-patch `EX.commit_green_cards`）零变化；SQL/阈值/幂等语义原样迁移。
  - `run()` 111→38 行：抽出 `_walk_chain`（主链循环，62 行）、`_run_jw_sidebranch`、
    `_run_summary`。
  - `run_atomic()` 127→32 行：抽出 `_split_ref_material`（P-C4 入口分隔符通道）、
    `_load_matrix_feed`（P-matrix-feed）、`_post_exit_terminate_and_sufficiency`（P-C5 后置）。
  - 消除重复：`run`/`run_atomic` 共用的 gate+commit 收尾收敛为 `_gate_and_commit`。
  - 生产 `assert` 改显式异常：`engine.py:64`（路由缺保底节点 → ValueError）；
    `trace_archive.py` 两处数据守恒断言（归档对账 / 删除守恒 → RuntimeError，
    `-O` 下不再静默跳过继续删主库）。
  - executor.py 583→544 行；R17 源码断言同步改读 green_card_writer 并断言 executor 重导出。
- **回归**：新增 `eval/audit/executor_split_equivalence.py` 13/13（主链/Jw/汇总链路、
  汇总降级、ref_material 四分支、gate+commit 复用恰一次、重导出同源）；路由器 63 项、
  judge 桩矩阵 7/7（--compare 一致）、connect_timeout、schema 迁移 7/7、置信度读源 3/3、
  碎片口径 7/7、trace 归档 replay PASS 全绿
- **状态**：✅ 完成

#### P2-3 hebbian 措辞如实化
- **证据**：`hebbian.py` 实为 `weight += 0.05` / `weight *= 0.995`，初始权重来自 `graph.py:23-24` P0-P8 主观优先级，`neg` 阈值 0.3 无消融支撑，无显著性检验。
- **方案**：文档/注释去掉"图学习"虚高描述，如实写"带衰减计数器"；显著性统计留后续迭代。
- **落地**：
  - `cgraph/hebbian.py` 模块 docstring 重写：明示「不是神经网络/图学习，无梯度/无反向传播/
    无损失函数」，如实描述 `weight += eps` / `weight *= decay` 累加-衰减计数规则，
    标注初始权重来自 P0-P8 主观优先级、"0.3 阈值无消融实验支撑、无显著性检验"、
    "慢尺度蒸馏仅统计胜率、未实现蒸馏"。
  - `HebbianUpdater` 类 docstring 补「命名沿用历史，实现本质是带全局衰减的加权计数器」。
  - `cgraph/graph.py` 模块 docstring 边权说明同步为「带衰减权重计数器（非图学习）」。
  - **零逻辑改动**：eps=0.05 / decay=0.995 / min_w=0.05 默认值不变。
  - 新增 `eval/audit/hebbian_wording_regression.py` 固化"文档不得虚高"断言 8 项。
- **回归**：hebbian_wording_regression 8/8（含实现默认值未变校验）
- **状态**：✅ 完成

#### P2-4 检索权重标定 + IDF/词序
- **证据**：`memory_query.py:661-663` 打分 `sem*0.30 + kw*0.30` 硬编码，`W_*` 在 `constants.py` 标 `[先验-业务设定]` 无标定报告；keyword 为朴素 n-gram 子串 + 1-gram Jaccard，无 IDF、无词序。
- **方案**：补权重标定对照实验文档；迭代引入 IDF 与词序特征。
- **落地**：
  - 新增 `docs/design_retrieval_weight_calibration.md`（设计交付）：
    §1 现状盘点（打分公式两段式、常量标定状态表——仅门控阈值已实测标定，
    sem/kw 硬编码 0.30 无独立常量、bias 三项仍先验）、§2 实验设计（评测集固化 +
    R1~R5 单因素对照矩阵 + 执行顺序 + 脚本规格 + 改值人闸纪律）、
    §3 分批迭代路线（批1测量→批2 kw特征 IDF/词序→批3 bias→批4回归固化）、
    §4 风险（不过拟合小样本、fw/IDF 隔离、夜间只测不改、不引入黑盒）。
  - kw 短板如实记录：无 IDF（gram 等权，泛词稀释召回）、无词序（集合交集不可区分短语顺序）。
  - 新增 `eval/audit/retrieval_weight_doc_sync_regression.py`：固化"文档记录的权重值
    == constants.py 实际值"（8 项常量对照）+ 状态标注不虚高。
  - **本轮不改线上权重常量**（用户裁决"产文档为主，可迭代引入"）；实标定实验为批1起后续迭代。
- **回归**：retrieval_weight_doc_sync_regression 12/12
- **状态**：✅ 完成（设计交付；实标定实验列为后续迭代）

#### P2-6 memory_judge Q4 血缘检测 O(N) 全库扫描优化
- **证据**：`memory/memory_judge.py:113-128`（桩）每次 judge 都 `load_memories()` 全库遍历算 2-gram 重叠；LLM 化后此预计算保留作 bloodline_hint，扫描仍在。
- **方案**：按 domain/behavior 预聚合血缘 gram 快照，或加缓存；先评估样本量级再设计。
- **落地（调研，不改代码）**：
  - 实测脚本 `eval/experiment/p26_judge_q4_profiling.py`（产物 `logs/p26_judge_q4_profiling.json`）：
    当前库 N=245/高置信 230；单次全库 grams 重算 1.89ms；**候选数近线性放大**
    （C=1/5/10/20 → 5.14/27.93/52.91/105.13ms，放大 1→20.45×）。
  - 调研文档 `docs/design_judge_q4_perf_20261121.md`：
    复杂度实为 **O(C × N × G)**（每条候选重复 `load_memories()` + 全库 `grams()` 重算）；
    预聚合快照方案实测 **grams 重算省 91.4%**（构建一次 2.49ms，复用扫描 0.16ms vs 1.89ms），
    候选 C 条总开销由 `C×5.14ms` 降到 `2.49 + C×0.16ms`（C=20 约 5.7ms vs 105ms）；
    建议 S1~S4 落地步骤（签名加可选 snapshot 参数、批次入口构建一次透传、
    结果等价回归），**不引入持久化快照**（N≈245 规模下进程内一次构建已足够）。
  - 结论：问题真实但当前非瓶颈（百毫秒级）；按用户裁决"先调研不直接写代码"，
    代码改动留待后续迭代评审。
- **状态**：✅ 完成（调研交付；代码优化列为后续迭代 S1~S4）

#### P2-5 SQLite 连接超时 + 去静默吞错
- **证据**：`corpus_store.py:102 _conn()` 无 `timeout` 参数（默认可能永久阻塞）；`memory_gate.py:78-79` gate_log 写失败 `except OSError: pass`；`memory_query.py:760-761` cited_log 写失败静默容忍（此项为有意设计，保留但加日志）。
- **方案**：所有连接补 `timeout`；无条件 `pass` 改告警日志。
- **落地**：
  - `timeout=5` 全量补齐：AST codemod `scripts/p25_add_connect_timeout.py` 一次性处理
    42 个文件 / 80 处调用（合计 82 处，含原有 2 处）。注：`sqlite3.connect` 默认 timeout
    本就是 5.0s，本次为显式化/统一/可审计，运行时行为等价。
  - 高危静默吞错改 stderr 留痕（7 处）：`memory_gate._log`、`cgraph/atomic_skill._audit_log`、
    `cgraph/executor` entry_hit/entry_fallback 审计、`memory/memory_organize` dual_write 错误日志、
    `eval/core/e7_nightly_governance._log`、`eval/core/e0_topic_gate_guard` 清理、
    `memory/memory_query._governance_families` registry stat。
  - 评估后**不改**：`cgraph/matrix_knowledge` 3 处（docstring 契约「异常恒返回 []」，空矩阵为常态
    降级，告警会刷屏）；`e12_router_smoke` 两处（测试脚本，可选样本缺失属预期）；
    `llm.py`/`router_atomic.py` 返回默认值项（非 silent failure，语义即降级）。
  - 新增永久校验 `eval/audit/connect_timeout_regression.py`（AST 断言无 timeout 嫌疑清零）。
- **回归**：connect_timeout_regression 82/82 带 timeout；路由器 63 项、judge 桩矩阵 7/7
  （--compare 基线一致）、置信度读源 3/3、schema 迁移 7/7 全绿；运行时冒烟 OK
- **状态**：✅ 完成

#### P2-7 memory_judge Q1 分支补 continue（discard 被 Q4 覆写修复）
- **证据**：`_stub_one` Q1 分支置 `verdict="discard"` 后未 `continue`/`return`，继续执行到 Q4；
  孤条（零血缘）时 Q4 把 verdict 覆写为 `hold`。P1-1 桩转正按"字节等价"红线原样保留。
- **方案**：Q1 分支补提前返回，使 discard 不被 Q4 覆盖。**属语义行为变更**（改判定结果 +
  judge_log 历史分布），须前置 A/B 影响评估，评估通过才编码（用户裁决：严禁提前动手）。
- **落地（S1~S5 已执行，commit `981a032`）**：
  - S1 `_stub_one` Q1 fail 分支改 `return {"verdict": "discard", ...}` 提前返回，
    Q1 一票否决生效；Q2~Q5 及其余桩逻辑零改动；docstring 同步为 P2-7 说明。
  - S2 重建桩基线 `logs/memory_judge_stub_baseline.json`（md5 `173e8b0ae6544c134574f7ec265bc1be`）。
    顺带修复脚本缺陷：`_run_stub` 强制 `JUDGE_USE_LLM=0`，否则矩阵随 LLM 路径漂移、基线对照失效。
  - S3 回归全绿：stub `--compare` 8/8 与新基线一致；`memory_judge_llm_regression` 7/7；
    `test_skill_router` 63 OK；8 个库 `PRAGMA integrity_check` 全 ok；另
    schema_migrate/connect_timeout/executor_split/hebbian_wording/retrieval_weight_doc_sync 全 PASS。
  - 新增用例 `Q1_discard_not_overwritten_by_q4`：summary="置信度五级"（len=5 触发 Q1，
    与库内 L0 条目 2-gram 重叠 4，Q4 本会放行），断言仍为 discard，专测 Q1 early-return 阻断覆写。
  - 边界遵守：仅改规则桩 `_stub_one`；LLM 提示词、五问契约文档、`review_holds()` 均未动。
- **状态**：✅ 完成（commit `981a032`；影响评估 22 条变化 1 条 4.55%，hold 池 13 条零影响）
- **风险备注**：行为方向为收紧（hold→discard），不影响既有 hold 池；历史 judge_log 分布有 1 条口径变化。

#### F20 权重层守卫告警（#5 主线，2026-11-21 结案：保留现状不重定基线）

- **现象**：`logs/f20/guard_latest.json`（2026-11-21 21:34）`flipped=true, rank_shifted=true`。
- **根因（A-1 溯源已钉死，非权重/代码 bug，是两处正向图谱治理变更）**：
  1. **explore1**：`f7a20eb7` 于 2026-11-20 17:43:31 `method='final-review'` 语义正确重归类，
     由 `topic_reflection_trace` → `topic_atomic_metacore`；与 `04353a35` 失去 topic 交集，
     切断 F40 topic 弱边路径 → `step_exhausted`（PASS→FAIL）。
  2. **smoke/explore2**：11-16~11-19 期间簇债务/连通性演化，D 因子打分使 top-3 位次由
     `f4198bf7`(D=0.300，被压至 floor) 漂移至 `58707552`(D=0.419)。
- **处置裁决（用户 2026-11-21）**：**暂不重定守卫基线**，`f20_nightly_guard.py` 的 SCENARIOS
  预期判定与 expected_top 维持原值，夜间守卫继续告警。待后续完成图谱治理、大规模更新
  topic 归属/簇构成后，统一批量刷新整套 F20 基线，不零散单点改。
- **归档审计产物**（日后批量重定基线参考素材）：
  - `logs/f20/guard_audit_report_20261121.md`（新旧对比报告）
  - `logs/f20/guard_audit_20261121_run.json`（真实运行快照）
  - 备份 `logs/f20/guard_latest.before_audit.json`（审计前基线，diff 确认未改）
- **约束遵守**：未改任何 F20 代码/常量/守卫配置；回归脚本维持现状。
- **状态**：✅ 结案（保留告警，台账标记；相关副线 A-2/A-3/A-5 已落地 commit `dd8fdfd`）

---

## 三、执行顺序

1. **P0-1、P0-2**（先动手，止血）
2. P1-1（准入门面）
3. P2-1 ~ P2-5（按迭代节奏）

每条完成后在标题下追加 `✅ 完成 commit <hash>`。

---

## 四、主线队列（统一编号总表，2026-11-22 重编）

> **编号规则**：#N 为统一待办编号，一经分配不复用。优先级 P0>P1>P2。已完成项保留历史号归档在表末。
> 本次重编变更：#A 并入 #8（同为 F60 第二环）；原 §七 一致性修复编号为 #14；原 #B→#15、#C→#16；#11 复盘承接的 P2-A/B/C 登记为 #17/#18/#19（详见 `docs/review_11_attitude_20261122.md`）；记忆系统评估复盘立项 #20（P1，详见 `docs/review_20_observability_20261122.md`）；cgraph 可验证性评审立项 #21（P1，详见 `docs/review_21_cgraph_verifiability_20261122.md`）。

### 待办（按优先级）

| 编号 | 任务 | 优先级 | 状态 | 说明 / 依赖 |
|---|---|---|---|---|
| **#21** | cgraph 可验证性：verify_ratio 消融 + A/B 结论沉淀 | **P1** | 🔄 **部分完成**（离线消融✓，质量盲评待做） | ✅ 已完成：离线四组消融（`eval/experiment/verify_ratio_ablation.py`）+ A/B 耗时汇总 + README 措辞校准。**核心结论：主链免疫边权学习，仅旁支温和微调，"改善"未经质量验证**；详见 `docs/review_21_verify_ratio_ablation_result_20261122.md`。⏳ 待做：`eval/audit/score.py` 真 LLM 盲评 33 样本出胜率 |
| **#20** | 记忆系统可观测性 + 合规 | **P1** | 📋 待立项 | 补检索成功率/miss_case趋势/误归档率/hold池规模/冲突判定计数/simhash触发/热区扫描指标并入E4晨报；全链路流程图；applog脱敏；**先指标后收敛，禁无据大砍规则**；详见 `docs/review_20_observability_20261122.md` |
| **#12** | 对话会话归组 & 历史追溯（轻量版） | **P0** | ✅ **完成** | 根治 h 通道断流（实证：1508段仅1条h,全靠E9兜底）；stdin transcript 解析落库+降级md+list_dialog+cards_from_dialog+E9断流告警；回归 27/27；详见 §六 |
| **#11** | 回执态度元标签子通路（方案A） | **P1** | ✅ **完成** | `memory_attitude.py` 附属 JSONL + judge LLM 通路接入 attitude 解析；stub 零副作用、主表零改动；回归 22/22；详见 §五 |
| **#8** | forward_check 第二环深度正演（复用 minloop.verify） | **P2** | ⏸ 第一环已实装+回归，第二环待立项 | 第一环静态版 `collab_pipeline.py:407` + 回归 15/15；第二环补齐推理正演自证闭环（原 #A 并入）；详见 `docs/review_8_forward_check_20261122.md` |
| **#15** | 桩—LLM 判定一致性对照回归 | **P2** | 📋 待裁决 | 抓入库闸门降级行为漂移；纯 eval/audit 不改生产，成本低收益高；原 #B |
| **#16** | topic_layer 决断（开启 or 删除） | **P2** | 📋 待裁决 | 消除 556 行默认关闭悬空资产；原 #C |
| **#13** | 记忆卡版本链 + diff | **P2** | ⏸ 暂缓（**前置已解除，可评估**） | 触碰 #7 刚加固写入核心；#14 已收敛单源真相，**评估条件已满足**；触发条件见 §六 |
| **#17** | 用户回执→reward 映射工具（P2-A） | **P2** | 📋 待立项 | 按 `target_memory_id`+时间窗读回执映射 0-1 分；带开关；**无 trail_id 概念**（修正复盘前提）；Hebbian 不直读 JSONL；详见 `docs/review_11_attitude_20261122.md` §六 |
| **#18** | Hebbian 两路 reward 加权融合（P2-B） | **P2** | 📋 待立项 | `HebbianUpdater.apply` 显式加权 `w1*verify_ratio+w2*attitude_reward`，非覆盖；无回执回退旧逻辑；保留计数器基线可切；依赖 #17 |
| **#19** | Executor 节点 activation 输出契约（P2-C） | **P2** | 📋 待立项 | 每原子节点输出 `activation: float 0~1`，为调制赫布离线仿真备输入；只加字段不改路由/打分；调制赫布 Stage1 缺口 |

### 已完成（历史归档）

| 编号 | 任务 | 状态 | 说明 |
|---|---|---|---|
| #5 | F20 权重层 | ✅ 已结案 | 守卫告警保留不重定基线（见上 F20 子项）；副线 A-2/A-3/A-5 已落地 `dd8fdfd` |
| #7 | pending 队列可靠写回（原"sqlite 分片"） | ✅ **完成** | 放弃物理分片；A-1/A-2/A-4/A-5 核心 `6d5a25a`+`431ce52`；A-3 调度接入 + A-6 WAL 显式化 `6880f63` |
| #10 | 门槛②③④ | ✅ **完成** | 口径冻结 `7c6e074`；S2-S4 业务 `82a4ee8`；S5 总回归 `75f8fda`；人工评审签字归档 `docs/artifacts/ablation_summary_signed_20261122.json`（reviewer uidq0280） |
| #14 | 记忆一致性修复 | ✅ **完成** | demote sup DB 同步根因修复 + 存量对账；sup 29/29、补插 4/4、cards 316→320、sup非空 2→31、integrity ok；`4b97a63` |

### #7 进展明细

- **任务重定义（用户 2026-11-21 裁决）**：放弃物理分片（33MB 无依据），改为 pending 队列可靠写回。
- **已落地（A-1/A-2/A-4/A-5）**：
  - A-1 `_atomic_write_jsonl`：temp+fsync+os.replace 原子覆盖写；`append_jsonl` 增 fsync 可选参数。
  - A-2 `_PendingLock`：lockfile(pid+超时过期)；`review_holds` 入口抢锁，抢不到返 skipped 不阻塞。
  - A-4 `_write_card_to_db`：BEGIN IMMEDIATE + busy_timeout=5000 + 指数退避 3 次重试，仅落卡路径。
  - A-5 `eval/audit/pending_writeback_regression.py`：6 类 16 用例全绿（原子/锁/幂等/重试/fsync）。
- **收尾落地（2026-11-22，commit `6880f63`）**：
  - A-3 调度接入：新建 `eval/core/e_hold_review.py`（CLI 包装 `review_holds`，锁冲突 skipped 退出 0 不阻断夜跑、异常退出 1 触发晨报）；`scripts/nightly_run.py` STEPS 在 E9 后插入 "E-hold复审"(60min)。
  - A-6 WAL 显式化：`memory/corpus_store.py:_conn()` 显式 `PRAGMA journal_mode=WAL` + `wal_autocheckpoint=1000`（幂等切换，兼容 delete 模式存量库；只读场景 try/except 降级）。
  - A-5 回归扩至 18 用例：新增 `case_e_hold_review_cli`（锁冲突 skipped+退出0 / 正常 退出0+报告落盘）全绿。
  - 验证：journal_mode=wal / autocheckpoint=1000 / integrity ok；真实库零污染（316 卡 / 245 memories / 13 pending）。
- **约束**：业务判断逻辑零改动（hold/转正/丢弃逐字不变）；真实 pending 池未改动（diff 确认）。

### #10 进展明细

- **口径冻结（2026-11-22 用户裁决，路线 A）**：`docs/design_f40_threshold_234.md`。
  - D1 门槛②消融收敛：β∈[0.6,1.0,1.6] × λ_debt∈[0.25,0.5,0.75] 9 点网格；相邻命中率变化≤5% 且 top-3 Jaccard≥0.7；机器达标 + 人工签字（`human_accept/reviewer/review_ts`）。
  - D2 门槛③边覆盖率：debt_ratio 作代理；沙箱临时库扰动，判定翻转=0 且 top-3 Jaccard≥0.6。
  - D4 门槛④降级分支：`degraded` 标记 + `failure_reason` + `low_confidence_candidates` top-k；仅 step_exhausted/goal_isolated 触发。
  - D5/D6 默认：本期不做 `promote_check()` 门面；新建独立 `eval/audit/f40_promotion_gate_regression.py`。
- **红线**：新增代码全落 `eval/*`；`core/cgraph/memory` 只读；`f20_weight.py` 仅末尾新增 `degrade_path_candidates()`，不改既有逻辑；③全沙箱零真实库写入。
- **执行（S1-S5）**：
  - S1 文档 ✅ `7c6e074`（口径冻结 + 台账）
  - S2 `eval/audit/f20_ablation_convergence.py` ✅ `82a4ee8`（含 `_real_sandbox()` 修复）
  - S3 `eval/audit/f20_coverage_stability.py` ✅ `82a4ee8`
  - S4 `eval/experiment/f20_weight.degrade_path_candidates` ✅ `82a4ee8`（纯新增 103 行）
  - S5 `eval/audit/f40_promotion_gate_regression.py` ✅ `75f8fda`
- **验证**：S2 selftest 4/4 + 真实沙箱零写；S3 selftest 5/5 + 真实 PASS；S4 四枚举矩阵全绿 + 存量 20/20；S5 selftest 12/12 + 真实全绿 + 库指纹零改动。
- **⚠️ 事故与修复（2026-11-22）**：S2 真实模式曾因 `memory_query.ask()` 隐性回写 `trace_cited` 污染真实库 134 条（11-22）；已备份 `logs/rollback_trace_cited_20261122.jsonl` 并从库删除（40373→40239），新增 `_real_sandbox()` 彻底隔离，复验零写入。
- **遗留（非编码）**：人工评审签字流程（`ablation_summary.json` 的 `human_accept/reviewer/review_ts`），属后期实验评审动作。
- **人工评审签字（2026-11-22 完成）**：
  - 真实模式产出 `data/experiment/ablation_f40/20261122_011922/ablation_summary.json`（machine_converged=True）。
  - 审阅结论：12 条相邻边全 pass，Jaccard 恒 1.0；`top3_order_identical` 于 λ_debt 0.25→0.5 三条边（smoke/explore2）出现位次轮转，核对为同集合纯顺序旋转（`[58707552,87f66cc1,f1577a38]` ↔ `[87f66cc1,f1577a38,58707552]`），集合稳定，判定可接受。
  - 签字归档：`docs/artifacts/ablation_summary_signed_20261122.json`（human_accept=true, reviewer=uidq0280, review_ts=2026-11-22T01:20:31，含 review_note）；稳定性产物同步归档 `docs/artifacts/stability_summary_20261122.json`（flip_total=0, min_jaccard=1.0）。

**运维红线**：`memory_query.ask()` 每次检索向 `trace_cited` 回写引用统计（隐性写副作用）。今后所有 eval 层仿真/消融脚本，凡调用 `future_sim_minloop.f00_anchor` / `memory_query.ask`，**必须跑在 SQLite 沙箱副本内，禁止直连真实库**。

每条完成后在标题下追加 `✅ 完成 commit <hash>`。

## 五、下一批立项（#11 回执态度元标签子通路，方案A）

> 2026-11-22 用户方案分析确认：值得做，但**不混入当前批次**，记为下一独立立项，优先方案 A 最小轻量版。

### 现状痛点
`memory_judge` 四出口（store/hold/discard/conflict）中，确认/认可类回执大多直接 `discard`——整条信息丢弃，「对被评价记忆的态度（认可/不认可）」永久丢失，无法回溯用户主观反馈（只能靠人翻原始对话日志）。

### 核心原则
- 回执消息**本体不生成新记忆**（仍走 memory_judge → 绝大多数 discard）；
- 仅提取「态度 + 目标记忆ID」作为**元标签**打在旧记忆上；
- **主判定主链路零改动**（不改 4 个 verdict 枚举、不改 hold 池 / review_holds）。

### 架构（方案A 最小轻量版）
- 新增独立模块 `memory/memory_attitude.py`，**仅 LLM 通路生效**；降级到规则桩 stub 时**直接关闭该子功能**（stub 不做语义识别，避免误识别）。
- LLM 额外输出结构化态度体：
  ```
  {"attitude_type": "approve|disapprove|neutral",
   "target_memory_id": "xxx", "comment": "...", "source_dialog_id": "..."}
  ```
- 存储：**不改 core_memory_card 主表**；新增附属表 `memory_attitude` 保存态度记录（附属观测元数据，非记忆本身；多条回执可对同一记忆叠加态度）。
- 使用：推演 / F20 权重 / 检索打分**默认不消费**态度标签；仅审计报表、晨报展示、人审参考。

### 红线（硬约束）
⚠️ 态度是**人主观反馈，只给人看**。机器**绝不**根据回执自动上调/下调记忆可信度、**绝不**自动降级/作废记忆。一旦放开自动消费，一句"我不同意"就会篡改事实权重，引入行为风险。

### 明确不踩的坑
- ❌ 不把"认可回执"变成新的 store 记忆（会膨胀出"我认可记忆A"碎片，态度噪声污染检索/推演）；
- ❌ 不修改现有 4 verdict 枚举（破坏全套回归/基线/hold 池）；
- ❌ 不在规则桩 stub 里硬写回执识别（文本特征易误判，语义识别归 LLM）。

### 方案 B（进阶，A 完成后再议）
在 A 基础上加**人闸开关**：人工审阅一批 attitude 记录后手动触发批量调整置信/打标签；机器永不自动改记忆状态。

### 边界案例
1. "好的，我认可【xxx】这个裁决" → 本体 discard；子通路记 `approve` 指向旧记忆；推演/权重不受影响；晨报/审计可见。
2. "这点我不同意【xxx】" → 本体 discard；附属表记 `disapprove`；**机器不自动降 confidence**，仅人审线索。
3. 故障降级到 stub → 子通路关闭，行为与现状 100% 一致，零副作用。

### 兼容性
P0-P2 / #5 / #7 / #10 现有代码、回归、基线**完全不用改**；新增表 + 独立模块；仅 LLM 主通路多跑一段解析；stub 兜底通路零改动；hold 池 / review_holds 不受影响。

### 回归计划（立项后）
mock LLM 输出测试解析 + 落表；**只读主库，不碰原有业务表**。

### 决策
- 采纳**方案 A**，作为下一独立立项；本批（P0-P2/#5/#7/#10/#8）已完成闭环，先维持现状。
- 现实取舍：此类回执占比不高但信息价值高；严格隔离「主观态度」与「客观记忆事实」。
- **若不做**：确认/认可回执全部 discard，态度信息永久丢失，无痕迹留存。

### ✅ 实施完成（2026-11-22，方案 A）

**改动清单**（1 新模块 + 1 处接入 + 1 回归）：
1. `memory/memory_attitude.py`（新）：`record_attitude` 落库 / `list_attitudes` 查询 / `summarize_attitudes` 汇总。存储=**独立 JSONL** `data/memory/memory_attitude.jsonl`（与 judge_log/gate_log 审计日志同构，**主库 core_memory_card 零改动、不建 sqlite 表**）。三型 approve/disapprove/neutral，非法类型/缺 target 防御性拒绝。多条回执可对同一记忆叠加。
2. `memory/memory_judge.py`：
   - `_JUDGE_SYSTEM_PROMPT` 增「附加可选字段 attitude」约定（**不改变 verdict 枚举**），target_memory_id 须取自 `context.entity_hint`。
   - `_judge_call_llm` 解析时若 verdict 对象含合法 `attitude`，挂 `_attitude` 键（仅搬运，不改 verdict）。
   - `judge_and_store` 主循环：`_source=="llm"` 且带 `_attitude` → `memory_attitude.record_attitude` 落库；失败只打 stderr 不阻断主判定。
3. `eval/audit/attitude_regression.py`（新）：**22/22 全绿**（落库/查询/LLM解析/端到端/stub零副作用/主表零改动 6 类，全沙箱）。

**红线守持验证**：stub 通路零 attitude（JUDGE_USE_LLM=0 时无记录）；态度落库不改 card 数/superseded_by 分布、不产新记忆；机器推演/F20/检索默认不消费 attitude。既有回归 judge 8/8、pending 18/18、dialog 27/27 全绿无破坏，真实库零污染（320 卡不变）。

## 六、记忆过程能力立项（#12/#13，2026-11-22 裁决）

> 完整缺口分析（含文件行号证据）见 `docs/review_memory_versioning_20261122.md`。
> 定位：系统是 AI 推理记忆底座，非通用聊天记录后台；过程信息一律附属存储，不污染主卡。

**优先级排序（2026-11-22 用户裁决）**：P0 #12 → P1 #11 → P2 #13（暂缓）。

### #12 对话会话归组 & 历史追溯（P0，轻量版）

- **为什么**：排错刚需（记忆卡出问题时需定位"哪一轮对话催生"，现只能手工翻库）；t 通道缺模型回复，PreCompact hook 异常则当天回复永久丢失，线上不能假设钩子永不坏。
- **做什么（不动记忆写入核心）**：
  - session_id 透传标记（对话入库加标识，不重构存储）；
  - 高层查询封装（按 session / 按日期取对话片段，数据本就在 raw_corpus）；
  - h 通道断流审计告警（长时间无新对话入库 → 提示"模型回复有丢失风险"）；
  - source_ref 反向弱关联（记忆卡来源可反查对话 id，弱关联不强外键）。
- **不做**：结构化「用户/助手分字段存储」；对话 diff / 编辑等产品化能力。
- **定性**：排错审计工具，不是终端用户聊天历史功能。

### #13 记忆卡版本链 + diff（P2，暂缓）

- **不做的理由**：① 触碰记忆写入核心（改 full_hash 拦截 / 新增 version / 改 atomic_store），是 #7 刚加固稳定的链路，风险高；② 现 `demote()`（降级原因+时间+指向校准单）+ 各类审计 jsonl 已够人工审计；③ 完整版本链+diff 成本收益不对等，解决的是体验优雅度非阻塞 bug。
- **启动触发条件（满足才考虑）**：
  1. 出现「同一条记忆高频迭代修改」的规模化场景；
  2. 现有降级+审计日志人工追溯已变得非常痛苦；
  3. 记忆写入链路经一段时间充分稳定之后。
- **现状**：保持 `demote()` + 审计 jsonl 追溯，够用、安全（不漂亮）。
- **未来缺口清单**（备查）：放开 full_hash 拦截 / 加 version+content_hash / iteration_history 表 / difflib diff / list_versions+diff_versions 接口。

### 设计铁律（#11/#12/#13 共享，硬约束）

> **主记忆卡 `core_memory_card` 只存推演用干净事实**；过程、态度、历史对话、版本快照一律进附属表/附属日志，**机器推演逻辑不消费**（不进 F20 权重、不进检索打分、不自动改置信/存活）。**绝不因追溯需求把过程性数据塞进主卡污染推演输入。**

## 七、#14 记忆一致性修复（2026-11-22 已完成 ✅，commit `4b97a63`）

> 完整证据与明细清单见 `docs/review_memory_consistency_20261122.md`（阶段一：只读对账，零数据变更）。
> **编号 #14，优先级 P0，曾阻塞 #13；已于 2026-11-22 修复完成并解阻塞。**

**对账结论（阶段一）**：
- 唯一真相源已固化为 SQLite DB；**JSONL 已非完整镜像**（db 多 75 条近期新增，jsonl 多 4 条远古漏灌）。
- **`demote()` 半截同步缺陷**：`tier=precedent` 同步进 db，但溯源字段 `superseded_by` 只写 jsonl、db 永久为空（29 条专利卡全部丢失该指针，指向同一校准单 `44d69231...`）。
- **根因**：`memory/v3_migrate_batch1.py:63` 卡片 INSERT 22 参数，第 20 位（superseded_by）硬编码 `None`。

**#14 修复实施（阶段二）**：
1. **✅ 根因修复** `memory/memory_organize.py`：新增 `_sync_demote_to_db()`，`demote()` 落盘 jsonl 后逐条 `BEGIN IMMEDIATE` + `busy_timeout=5000` 同步 `UPDATE core_memory_card SET tier/superseded_by`，失败落 `dual_write_errors.jsonl` 留痕；返回值增 `db_synced`/`db_failed`。
   - 说明：db `core_memory_card` **无** superseded_reason/at 列（仅 superseded_by），故只同步 tier+superseded_by；reason/at 留 jsonl 侧（溯源可查）。
2. **✅ 存量对账** `eval/audit/memory_consistency_fix.py`：`--dry-run`（默认预览）/`--apply`（真执行，先 sqlite backup API 自动备份到 `data/memory/backup/`）双模式。
   - ① 29 条降级卡 sup 补写 db；② 4 条仅 jsonl 补插 db；③ 75 条仅 db **仅报告不处理**（决策：db 为唯一真相源，jsonl 退化为导出流，明示不再等价，不回写 jsonl）。
3. **✅ 双轨收敛**：读侧权威 `DF_MEMORY_SOURCE=sqlite`，写侧 demote 已同步 db，单源真相成立。

**真实库执行结果**：sup 补写 29/29、补插 4/4、cards 316→320、sup 非空 2→31、tier=precedent 保持 29、integrity ok、dry-run 复验清零（0/0）。备份 `corpus_store.db.bak_consistency_20261122_022906`。

**约束（已满足）**：
- ✅ 4 条缺失老数据统一批量补入，未单独临时改库。
- ✅ #13 前置阻塞已解除：#14 收敛单源真相，可进入 #13 评估。
