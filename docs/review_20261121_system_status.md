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
- **状态**：待办

#### P2-4 检索权重标定 + IDF/词序
- **证据**：`memory_query.py:661-663` 打分 `sem*0.30 + kw*0.30` 硬编码，`W_*` 在 `constants.py` 标 `[先验-业务设定]` 无标定报告；keyword 为朴素 n-gram 子串 + 1-gram Jaccard，无 IDF、无词序。
- **方案**：补权重标定对照实验文档；迭代引入 IDF 与词序特征。
- **状态**：待办

#### P2-6 memory_judge Q4 血缘检测 O(N) 全库扫描优化
- **证据**：`memory/memory_judge.py:113-128`（桩）每次 judge 都 `load_memories()` 全库遍历算 2-gram 重叠；LLM 化后此预计算保留作 bloodline_hint，扫描仍在。
- **方案**：按 domain/behavior 预聚合血缘 gram 快照，或加缓存；先评估样本量级再设计。
- **状态**：待办（P1-1 明确不并入）

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

---

## 三、执行顺序

1. **P0-1、P0-2**（先动手，止血）
2. P1-1（准入门面）
3. P2-1 ~ P2-5（按迭代节奏）

每条完成后在标题下追加 `✅ 完成 commit <hash>`。