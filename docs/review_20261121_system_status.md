# 系统现状评审与整改台账（2026-11-21）

> 评审范围：`cgraph/`（推理）与 `memory/`（记忆）两模块。
> 评审方式：逐文件读码 + 行号取证，不含臆测；代码无法判断的效果类结论明确标注。
> 本文件兼作整改 issue 台账，按 P0→P1→P2 排序，完成后在对应条目标注 commit。

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

## 四、主线队列（P0-P2 技术债闭环后）

| # | 任务 | 状态 | 说明 |
|---|---|---|---|
| #5 | F20 权重层 | ✅ 已结案 | 守卫告警保留不重定基线（见上 F20 子项）；副线 A-2/A-3/A-5 已落地 `dd8fdfd` |
| #7 | pending 队列可靠写回（原"sqlite 分片"） | 🚧 **进行中** | 重定义：放弃物理分片；A-1/A-2/A-4/A-5 已落地，A-3 调度后置 | 
| #10 | 门槛②③④ | 🚧 **已实现（编码落地）** | 口径冻结 `7c6e074`；S2-S4 业务 `82a4ee8`；S5 总回归 `75f8fda`；仅遗留人工评审签字 |
| #8 | forward_check | ⏸ 待启动（低优） | — |

### #7 进展明细

- **任务重定义（用户 2026-11-21 裁决）**：放弃物理分片（33MB 无依据），改为 pending 队列可靠写回。
- **已落地（A-1/A-2/A-4/A-5）**：
  - A-1 `_atomic_write_jsonl`：temp+fsync+os.replace 原子覆盖写；`append_jsonl` 增 fsync 可选参数。
  - A-2 `_PendingLock`：lockfile(pid+超时过期)；`review_holds` 入口抢锁，抢不到返 skipped 不阻塞。
  - A-4 `_write_card_to_db`：BEGIN IMMEDIATE + busy_timeout=5000 + 指数退避 3 次重试，仅落卡路径。
  - A-5 `eval/audit/pending_writeback_regression.py`：6 类 16 用例全绿（原子/锁/幂等/重试/fsync）。
- **待做**：A-3 接入 nightly 调度（须 A-1/A-2/A-4/A-5 就绪后）；A-6 WAL 显式化收尾。
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

**运维红线**：`memory_query.ask()` 每次检索向 `trace_cited` 回写引用统计（隐性写副作用）。今后所有 eval 层仿真/消融脚本，凡调用 `future_sim_minloop.f00_anchor` / `memory_query.ask`，**必须跑在 SQLite 沙箱副本内，禁止直连真实库**。

每条完成后在标题下追加 `✅ 完成 commit <hash>`。
