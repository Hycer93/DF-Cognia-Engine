# 05 eval/audit 代码地图

> **生成日期**: 2026-11-25
> **文件数**: 57 个 .py
> **总行数**: 约 9363 行（不含 __pycache__）
> **定性**: 以回归测试为主干(36个)，覆盖 #7~#46 各阶段的正确性验证；辅以巡检对账(4)、一次性修复/回填(9)、专项审计(7)、评分工具(3)。几乎全部带沙箱隔离或只读纪律，**没有会无差别修改生产数据的脚本**。

---

## 一、分类清单表

| 文件名 | 行数 | 分类 | 一句话作用 | 数据源 | 产出 |
|--------|------|------|-----------|--------|------|
| `e30_pool_regression.py` | 142 | 回归 | #30 A组：R1查重池加载口径/deleted/precedent不误拦/full_hash | 临时SQLite | PASS/FAIL清单 |
| `e30_p0_regression.py` | 288 | 回归 | #30 B组：P0语义查重开关on/off/dry-run/8空壳/反例 | 临时SQLite | PASS/FAIL清单 |
| `e30_p1_r4_regression.py` | 110 | 回归 | #30 C组：P1保留detail标记 / R4 read_card_detail分流 | 临时SQLite | PASS/FAIL清单 |
| `e30_p2_p3_regression.py` | 150 | 回归 | #30 D组：P2跨簇告警 / P3 E4指标采集 | 临时SQLite | PASS/FAIL清单 |
| `e30_smoke.py` | 116 | 回归 | #30 S1-S6：dry-run冒烟(池加载/向量化/单条比对/gate_log字段) | 临时SQLite | PASS/FAIL清单 |
| `e30_nonregression.py` | 90 | 回归 | #30 E组：正常non-derived逻辑不变/路径B逐行不变 | 临时SQLite | PASS/FAIL清单 |
| `e31_dialog_evidence_regression.py` | 173 | 回归 | #31：碎片追加去重/PreCompact聚合双写/悬空碎片兜底 | 临时SQLite | PASS/FAIL清单 |
| `dialog_trace_regression.py` | 243 | 回归 | #12：transcript解析/h通道落库/降级通路/session分支/E9断流 | 临时沙箱DB | PASS/FAIL清单 |
| `dialog_frag_unit_regression.py` | 109 | 回归 | 碎片判定口径统一为UTF-8字节≤20，防字符/字节分歧误归档 | corpus_store.db | PASS/FAIL清单 + JSON |
| `attitude_regression.py` | 225 | 红线 | #11态度元标签：三型落库/查询汇总/LLM解析/judge端到端/stub零副作用 | 临时沙箱DB | PASS/FAIL清单 |
| `collab_forward_check_regression.py` | 151 | 回归 | #8 forward_check四枚举白盒(PASS/PASS_WITH_GAPS/FAIL/NO_CONSENSUS) | 内存mock | PASS/FAIL清单 |
| `confidence_read_source_regression.py` | 98 | 回归 | 置信度读源一致(sqlite vs jsonl集合相等) | 主库+内存 | PASS/FAIL清单 + JSON |
| `connect_timeout_regression.py` | 66 | 回归 | P2-5 AST扫描所有sqlite3.connect()是否显式含timeout=参数 | 全项目AST | 违规清单 |
| `dl_route_regression.py` | 57 | 回归 | ComplexityRouter Dl域判定(heldout野生3条+E1漏触代表8条) | 内存mock heldout | PASS/FAIL清单 |
| `executor_split_equivalence.py` | 160 | 回归 | P2-2 exec.py巨型函数拆分组分前后行为等价(mock走链对比) | mock LLM桩 | PASS/FAIL清单 |
| `f20_weight_regression.py` | 259 | 回归 | F20权重层静态断言(公式性质+常量契约，不依赖DB快照) | 内存mock | PASS/FAIL清单 |
| `f30_regression.py` | 294 | 回归 | F30正向演化层(T30-1~T30-13)，假edges/score/mc/topics/card fn | 内存mock | PASS/FAIL清单 |
| `f40_promotion_gate_regression.py` | 258 | 回归 | F40晋升门槛②消融收敛+③沙箱稳定性+④降级分支矩阵集成 | fixture DB+mock | 审计目录产物 |
| `json_utils_regression.py` | 66 | 回归 | cgraph/json_utils.extract_json_obj边界行为(nested/字符串内花括号) | 纯函数 | PASS/FAIL清单 + JSON |
| `memory_judge_llm_regression.py` | 144 | 回归 | LLM判定路径+fallback(L1~L6六场景：正常/畸形/异常/漏答/非法/关开关) | mock CodeChatLLM | PASS/FAIL清单 + JSON |
| `memory_judge_llm_smoke.py` | 61 | 冒烟 | 真实LLM调用冒烟(glms-5.3-flash, 1~2条样本，手动) | CodeChatLLM真调 | verdict解析结果 |
| `memory_judge_stub_regression.py` | 150 | 回归 | 规则桩判定矩阵固化(+baseline/--compare对照模式) | 内存mock | baseline/compare JSON |
| `paradigm_read_regress.py` | 158 | 回归 | 范式读取/筛选/evidence_pass排序/calibration_pending过滤与E7对齐 | 主库SQL | PASS/FAIL清单 |
| `pending_writeback_regression.py` | 278 | 回归 | #7 pending原子写回(原子落盘/跨进程锁/转正幂等/退避重试) | 临时沙箱DB+jsonl | PASS/FAIL清单 |
| `retrieval_weight_doc_sync_regression.py` | 75 | 回归 | P2-4检索权重文档值↔代码常量一致性 | 设计文档+constants | PASS/FAIL清单 |
| `schema_migrate_regression.py` | 105 | 回归 | schema版本迁移(enum清洗/审计留档/幂等/asset_class白名单) | 临时/主库 | PASS/FAIL清单 + JSON |
| `topic_layer_regression.py` | 290 | 回归 | Topic锚层六规约验收(建锚/挂载/冲突/解耦/Guardrail) | 临时SQLite | PASS/FAIL清单 |
| `shadow_regress.py` | 352 | 回归 | 全链路影子回归(jsonl旧路 vs sqlite新路 monkeypatch打分) | 主库corpus_store.db | diff报告 |
| `hebbian_wording_regression.py` | 63 | 回归 | P2-3 hebbian文档"如实化"(非神经网络/阈值无消融/衰减计数器) | AST扫描docstring | PASS/FAIL清单 |
| `sim_41_demote_equiv.py` | 85 | 回归 | #41 demote新旧逻辑等价(tmp隔离库对比) | tmp隔离库 | 等价性结论 |
| `sim_41_unified_update.py` | 261 | 回归 | #41 update_card_fields统一封装+full-sync修复(tmp隔离库) | tmp隔离库 | dual_write_errors.jsonl |
| `test_memory_vs_human.py` | 185 | 专项 | 记忆系统vs人脑存取不足六个维度对照(D1~D6) | 主库corpus_store.db | 对照报告 |
| `threshold_hit_audit.py` | 160 | 审计 | #20 Phase2分域四档阈值命中率+临界带分析 | relation_graph.jsonl | 档位统计+临界带 |
| `governance_reconcile.py` | 232 | 对账 | 旧治理池jsonl↔新库治理表逐队列对账(E7依赖口径14d老化/引用性提及) | jsonl + core_*表 | 对账差异报告 |
| `audit30_dryrun_observe.py` | 106 | 观测 | #30段六dry-run灰度观测(影子重放池采集命中分布/分数/慢调用/E4指标) | tmp复制库+gate_log | 观测汇总 |
| `audit30_phase7_check.py` | 120 | 观测 | #30段七18:05 nightly后观测汇总(只读, 切on决策数据支撑) | gate_log + JSONL | 文本报告 |
| `demote_superseded_shells.py` | 165 | 一次性 | #29同事件多版本消重(demote空壳→precedent+supserseded_by回填) | corpus_store.db | --apply写库 |
| `backfill_jsonl_from_db.py` | 143 | 一次性 | #20 DB→jsonl回补(只补不删, 75条active卡遗漏) | corpus_store.db + jsonl | 追加到memories.jsonl |
| `memory_consistency_fix.py` | 181 | 一次性 | #14存量不一致修复(降級卡db为空补插/仅db存在确认/仅jsonl不补) | corpus_store.db + jsonl | --apply写库 |
| `scan_37_event_ts_dryrun.py` | 168 | 一次性 | #37 event_ts回填dry-run扫描A1/A2/B三批推导依据 | corpus_store.db | JSON方案清单 |
| `scan_37_event_ts_dryrun_v2.py` | 168 | 一次性 | #37 event_ts回填dry-run v2甄别"日期≠事件日"的可推导项 | corpus_store.db | 安全写入清单 |
| `backfill_37_event_ts.py` | 131 | 一次性 | #37 event_ts回填执行器(只写strong+summary可安全推导的20张) | corpus_store.db + jsonl | --apply写库 |
| `gen_dryrun_34_batch2.py` | 138 | 一次性 | #34第二批class回填dry-run报告生成(只读) | corpus_store.db + class_backfill_pending | markdown报告 |
| `backfill_34_memory_class.py` | 170 | 一次性 | #34 memory_class回填dry-run方案生成器(+--apply写库DB+jsonl+清pending) | corpus_store.db + jsonl + pending | CLASS_MAP方案 |
| `backfill_46_status_realign.py` | 181 | 一次性 | #46 status混叠态修复(active+tier=precedent+superseded_by非空→superseded) | corpus_store.db + jsonl | --apply写库 |
| `pii_residue_check.py` | 23 | 合规 | #20 PII残留抽查近7天raw_corpus(body手机号/邮箱/身份证) | corpus_store.db(raw_corpus) | 命中报告(应为0) |
| `e11_blind20.py` | 149 | 专项 | f_reason 20样本盲评(schema硬约束+LLM盲评≥17/20合格) | 主会话goal | experiment目录产物 |
| `e3_sft_audit.py` | 128 | 专项 | E3 SFT七池蒸馏质检v1(占位符/结构病/长度/重复/血统/坏JSON) | 七池JSONL | e3_sft_audit.json |
| `e3_sft_audit_v2.py` | 148 | 专项 | E3 SFT七池蒸馏质检v2(按每池schema定制体检项, 已退役出nightly) | 七池JSONL | 审计报告 |
| `judge_all.py` | 86 | 工具 | 全量裁判：逐题score_one+断点续跑(verdict.jsonl) | ab_result.jsonl | verdict.jsonl |
| `score_one.py` | 57 | 工具 | 逐题裁判(一条记录AB盲评, 判胜者+四维分数) | AB样本 | 单条verdict |
| `score.py` | 220 | 工具 | LLM裁判盲评(A/B两组ABTest全量盲评, 胜率+分差分布) | ab_result_*.jsonl | ab_judge_final.json |
| `e44_detail_guard_regression.py` | 107 | 回归 | #44 detail质量护栏(_detail_quality_issue检测/atomic_store警告不拒收/绿档去模板化) | 临时SQLite | PASS/FAIL清单 |
| `e46_demote_status_regression.py` | 138 | 回归 | #46 demote语义对齐(regression防混叠态复现: jsonl+DB status/tier/superseded_by) | 临时SQLite | PASS/FAIL清单 |
| `e7_gate_regression.py` | 166 | 回归 | E7自治闸门(boundary案例: 豁免卡/冲突卡/零锚卡/老化到期卡两分支断言) | 临时SQLite | PASS/FAIL清单 |
| `f20_ablation_convergence.py` | 357 | 专项 | F20门槛②消融收敛考核器(β×λ_debt 9点网格, machine_converged布尔) | DB+embedding+mock | ablation_f40/产物 |
| `f20_coverage_stability.py` | 422 | 专项 | F20门槛③边覆盖率稳定性考核器(5场景扰动flip=0 Jaccard≥0.6) | DB+embedding+tmp沙箱 | stability_summary.json |

---

## 二、回归测试族（36个）

### 2.1 共性特征

- **统一接口模式**：几乎所有回归脚本使用相同的 `check(name, cond, detail)` 辅助函数 + `PASS/FAIL = [], []` 列表收集结果，最后打印计数并 return exit_code(0=全过, 1=有失败)。
- **退出码约定**：`sys.exit(0)` 表示全部通过，`sys.exit(1)` 表示有断言失败。这便于 CI 串联和 shell `$?` 判断。
- **输出路径**：多数在 `logs/<script_name>.json` 写结构化结果，部分写 stdout 文本报告。
- **沙箱纪律**：绝大多数采用 `tempfile.mkdtemp()` + `shutil.copy` 复制主库到临时库，再 `os.environ["MEM_DIR"]` 或直接 monkeypatch 指向临时路径，保证 `不碰主库`。**只有少数合规/审计脚本是只读直连主库**。

### 2.2 入口与运行方式

```bash
# 大部分脚本无需环境变量即可运行
python eval/audit/<script_name>.py

# dl_route_regression.py 需要 NO_MEMORY=1 跳过完整初始化
NO_MEMORY=1 python eval/audit/dl_route_regression.py

# memory_judge_llm_smoke.py 为手动冒烟(不进nightly), 消耗真实配额
python eval/audit/memory_judge_llm_smoke.py

# e11_blind20.py 需 UTF8 解码
python -X utf8 eval/audit/e11_blind20.py
```

### 2.3 #30 回归家族（6个，e30_*）

编号体系为 **字母组+序号**，对应 PR/commit 不同改动面：

| 脚本 | 组别 | 覆盖用例 | 功能点 |
|------|------|---------|--------|
| `e30_pool_regression.py` | A组 | 1-4 | load_active_pool口径、deleted/precedent不误拦、new_hash全新口径 |
| `e30_p0_regression.py` | B组 | 5-9 | 8空壳召回、PAIR6反例、off零行为、dry-run只记不拦、on真拦 |
| `e30_p1_r4_regression.py` | C组 | 10-12 | derived保留detail/标记、read_card_detail分流、非derived不标记 |
| `e30_p2_p3_regression.py` | D组 | 13-14 | P2跨簇告警、P3 E4指标采集 |
| `e30_smoke.py` | S1-S6 | - | dry-run冒烟(池加载/向量化/单条比对/PAIR6反例/gate_log字段/冷启动计时) |
| `e30_nonregression.py` | E组 | 15-16 | normal non-derived逻辑不变、路径B逐行不变 |

**共同特点**：全部复制 `corpus_store.db` 到 tempdir → 重写 `MEM_DIR` → reload `memory_organize` 切换开关态(三态: on/off/dry-run)。每个脚本约 90-290 行，规模适中。

### 2.4 功能回归覆盖矩阵

按功能域归纳：

| 功能域 | 涉及的回归脚本 | 核心断言 |
|--------|---------------|---------|
| **#30 查重/治理** | e30_pool, e30_p0, e30_p1_r4, e30_p2_p3, e30_smoke, e30_nonregression, shadow_regress, audit30_dryrun_observe, audit30_phase7_check | 查重池口径/语义查重/跨簇告警/E4指标/影子回放 |
| **#46 demote 对齐** | e46_demote_status_regression, backfill_46_status_realign | status/tier/superseded_by三元一致性 |
| **#44 detail 护栏** | e44_detail_guard_regression | _detail_quality_issue检测准确性 |
| **judgment/LPM** | memory_judge_llm_regression, memory_judge_llm_smoke, memory_judge_stub_regression, attitude_regression | LLM路径/fallback/stub六场景/态度三型 |
| **executor/lifecycle** | executor_split_equivalence, pending_writeback_regression, connect_timeout_regression | 函数拆分等价/原子写回/connect timeout |
| **F系列** | f20_weight, f30, f40_promotion_gate, f20_ablation_convergence, f20_coverage_stability | 权重公式/正向推演/晋升门槛三段 |
| **dialog/frag** | dialog_trace, dialog_frag_unit, e31_dialog_evidence | transcript解析/碎片口径/证据库 |
| **infra/misc** | json_utils, schema_migrate, confidence_read_source, heuristic_wording, retrieval_weight_doc_sync, paradigm_read, topic_layer, collab_forward_check, dl_route | JSON提取/schema迁移/读源一致/文档同步/范式读取/Topic锚/forward_check/router |
| **E7** | e7_gate_regression | 自治闸门边界案例 |
| **治理对账** | governance_reconcile | jsonl↔DB逐队列一致性 |

### 2.5 特殊模式说明

- **--selftest 快速模式**：f20_ablation_convergence、f20_coverage_stability、f40_promotion_gate_regression 支持 `--selftest` 注入 fake provider，不依赖真实 DB/embedding，适合 CI。
- **--dry-run / --apply 双模式**：一次性修复脚本(scheme 类似) 默认只打印不改数据，`--apply` 才实际写库。这是用户安全红线。
- **AST扫描类**：`connect_timeout_regression.py` 使用 Python AST 遍历整个项目的 `*.connect(...)` 调用，检查 `timeout=` 参数存在性。这是最轻量但覆盖面最广的回归。
- **Mock/Stub类**：`dl_route_regression.py`、`f30_regression.py`、`collab_forward_check_regression.py` 等完全内存操作，不读DB、不调LLM，秒级完成。

---

## 三、巡检/审计/观测脚本（6个）

| 脚本 | 定位 | 检什么 | E编号 | 输出格式 |
|------|------|--------|-------|---------|
| `audit30_dryrun_observe.py` | 阶段六灰度观测 | 影子重放：命中分布/分数/慢调用/E4三指标/单条耗时 | #30 | JSON观测报告(写temp目录) |
| `audit30_phase7_check.py` | 阶段七夜间接口 | 18:05 nightly 后一键汇总gate_log P0观测 | #30 | 文本stdout报告(只读) |
| `threshold_hit_audit.py` | 阈值命中审计 | 分域四档阈值命中率+临界带(margin band)分析 | #20 Phase2 | 档位统计+临界带堆积 |
| `pii_residue_check.py` | PII合规核查 | 近7天raw_corpus body中手机号/邮箱/身份证残留 | E9脱敏 | 终端命中报告(目标: 0) |
| `governance_reconcile.py` | 治理队列对账 | jsonl旧池↔DB新表5队列逐项对账(行数/verdict/老化/Hold) | 全局 | 差异报告文本 |
| `e3_sft_audit_v2.py` | SFT质检(已退役) | 七池SFT资产逐项体检(按池schema定制) | E3 | 审计清单 |

### 3.1 关键发现

- **audit30_* 系列** 是 #30 分段上线期间的持续观测脚本，分别对应段六(灰度影子重放)和段七(nightly接口的阶段性验收)。产出用于 "切 on 决策" 的数据支撑。
- **governance_reconcile.py** 是最全面的对账脚本，覆盖 quarantine/conflict_queue/governor_pending/alias_pending/watchlist 五个队列的对齐，且关注 E7 依赖的关键口径（14天老化判定、引用性提及判定）。
- **pii_residue_check.py** 是唯一真正的"合规安检"脚本，用正则匹配手机号/邮箱/身份证号扫描 raw_corpus body，目标是0命中。

---

## 四、校准/治理/一次性脚本（13个）

### 4.1 回填/修复类（7个）

这些脚本遵循统一的 dry-run / --apply 安全模式，是项目历史中最容易踩坑的操作类型。

| 脚本 | 任务号 | 内容 | 日期标记 | 状态 |
|------|--------|------|---------|------|
| `backfill_jsonl_from_db.py` | #20 | DB→jsonl回补75条遗漏active卡 | 2026-11-22 | ✅ 应已执行 |
| `demote_superseded_shells.py` | #29 | 同事件多版本消重，8组空壳降级 | 2026-11-22 | ✅ 应已执行 |
| `memory_consistency_fix.py` | #14 | 降级卡db为空补插/仅db确认/仅jsonl不补 | 2026-11-22 | ✅ 应已执行 |
| `scan_37_event_ts_dryrun.py` | #37 | event_ts回填干扫A1/A2/B三批 | 2026-11-24 | 🟡 v1 → v2迭代 |
| `scan_37_event_ts_dryrun_v2.py` | #37 | event_ts回填干扫v2(甄别日期≠事件日) | 2026-11-24 | 🟡 最新版 |
| `backfill_37_event_ts.py` | #37 | event_ts回填执行器(强+summary可推导20张) | 2026-11-24 | ⏳ 待执行 |
| `backfill_34_memory_class.py` | #34 | memory_class回填方案(内置CLASS_MAP, --apply写库) | 2026-11-24 | ⏳ 待决策 |
| `gen_dryrun_34_batch2.py` | #34 | #34第二批dry-run报告生成 | 2026-11-24 | ✅ 只读 |
| `backfill_46_status_realign.py` | #46 | status混叠态修复37张卡+个案修正 | 2026-11-24 | ⏳ 待执行 |

**⚠️ 安全注意**：执行任何 `backfill_*` 前必须先确认其 `--dry-run` 输出符合预期，特别是 `backfill_34_memory_class.py` 同时修改 DB + jsonl + 清理 pending，影响面最大。

### 4.2 专项评估类（4个）

| 脚本 | 定位 | 特点 |
|------|------|------|
| `f20_ablation_convergence.py` | F20门槛②消融收敛 | 9点参数网格, 需DB+embedding, ~分钟级 |
| `f20_coverage_stability.py` | F20门槛③边覆盖稳定 | 5场景扰动测试, 最慢(422行, 多轮DB复制) |
| `f40_promotion_gate_regression.py` | F40晋升门槛总回归 | 集成②③④三段, 含selftest模式 |
| `test_memory_vs_human.py` | 记忆系统vs人脑对照 | 六个维度定量化短板清单, 只读探针 |
| `e11_blind20.py` | f_reason 20样本盲评 | schema硬约束+LLM盲评, 产experiment报告 |
| `sim_41_demote_equiv.py` | #41 demote等价验证 | tmp隔离库两套逻辑对比 |
| `sim_41_unified_update.py` | #41统一update验证 | tmp隔离库验证full-sync修复能力 |

### 4.3 废弃/已退役脚本

| 脚本 | 原因 |
|------|------|
| `e3_sft_audit.py` (v1) | 七池schema各异导致全量误报，已被v2替代 |
| `scan_37_event_ts_dryrun.py` (v1) | 被v2甄别增强替代 |

### 4.4 评分工具类（3个）

| 脚本 | 用途 | 输入 | 输出 |
|------|------|------|------|
| `score.py` | 全量ABTest盲评打分 | logs/ab_result_*.jsonl | logs/ab_judge_final.json |
| `score_one.py` | 逐题裁判(断点续跑) | ab_result.jsonl + index | logs/verdict.jsonl |
| `judge_all.py` | 全量裁判调度器 | 同上 | logs/verdict.jsonl |

这三个组成AB测评流水线：`judge_all.py` 负责逐条调用 `score_one.py` 逻辑 + 断点续跑；`score.py` 负责最终的全量AB盲评汇总。

---

## 五、给未来 AI 的避坑要点

### 5.1 数据库交互纪律

1. **所有回归脚本都不碰主库**：它们使用 `tempfile.mkdtemp()` + `shutil.copy("corpus_store.db", tmp)` 复制到临时目录后再操作。如果你要修改某个回归脚本让它读主库，先确认是否有必要。
2. **一次性修复脚本必须走 dry-run → --apply 两步**：`backfill_*`、`demote_*`、`*fix.py` 都遵循此模式。直接 `--apply` 可能产生不可逆后果。
3. **corpus_store.db 是唯一真相源**：`jsonl` 已退化为审计导出流(从11-15起)。读侧应优先查DB，写侧应通过 `memory_organize` 统一管理。
4. **`conn.row_factory = sqlite3.Row` 是惯例**：大部分脚本用到DB查询时都会设置此属性以获得列名访问。忽略则返回元组索引。

### 5.2 命名与编号约定

1. **eNNN_ 前缀 = Nightly实验相关**：e30/#30查重、e31/#31对话证据、e44/#44 detail护栏、e46/#46 demote、e7/#7挂闸。不是"紧急修复"的意思，而是"夜间实验第N项"。
2. **fNN_ 前缀 = 功能模块回归**：f20(F20权重)、f30(F30正向演化)、f40(F40晋升)。
3. **test_ 前缀 = 专项对照测试**：不是回归，是探索性探针(如 vs人脑)。
4. **audit_ 前缀 = 巡检/观测/对账**：定期产出报告供决策使用。
5. **日期标记在 docstring 第一行**：快速判断脚本时效性。例如 `(2026-11-21 P0-1)` 比 `(2026-11-15)` 更可能是活跃维护的。

### 5.3 Mock 注入模式

1. **f30/f40/f20_weight** 用假 fn injection (假 edges_fn/score_fn/mc_fn/topics_fn/card_fn)，不读真库不调真LLM。如果你想加新用例，保持这个模式。
2. **memory_judge_* 系列** 用 mock CodeChatLLM.chat 返回不同内容来覆盖各分支。
3. **dl_route_regression** 用 heldout 野生样本 + E1漏触代表的对抗检验类，完全内存操作。

### 5.4 环境依赖注意事项

1. **`NO_MEMORY=1`**：`dl_route_regression.py` 需要此环境变量跳过完整 memory 初始化。
2. **`TOPIC_LAYER_ON=1`**：`topic_layer_regression.py` 强制开启 Topic 层开关。
3. **`JUDGE_USE_LLM=0`**：关闭 LLM 判定走 stub 通路(`attitude_regression` 的第5断言)。
4. **`DF_MEMORY_SOURCE=jsonl`**：应急回切 jsonl 读源(`confidence_read_source_regression` 第3断言)。
5. **`CODECHAT_TOKEN`**：`memory_judge_llm_smoke.py` 需要本机 CodeChat 登录态。

### 5.5 常见陷阱

1. **不要混用 `.pyc`/`.py`**：`eval/audit/__pycache__/` 下的缓存可能与源代码不同步。修改后运行前建议 `find eval -name __pycache__ -exec rm -rf {} \;`。
2. **回归脚本间无依赖关系**：每个脚本都是独立的，没有"必须先跑A再跑B"的顺序约束。可以并行跑。
3. **`e3_sft_audit.py` 已被 `e3_sft_audit_v2.py` 替代**：v1 因七池 schema 各异而产生大量误报，已从 nightly 主链摘除。看历史记录时用 v2。
4. **`shadow_regress.py` 是最难调试的回归**：它同时跑了旧路(jsonl)和新路(sqlite monkeypatch)，diff 既可能反映数据迁移差异也可能反映逻辑变化。排查时需分别注释旧路/新路定位问题源。
5. **`governance_reconcile.py` 是当前最复杂的一页**：覆盖5个队列、4个对账维度、E7依赖口径，且涉及旧jsonl→新DB的映射。修改前先理解它的队列映射表。

### 5.6 缺失覆盖区域（TODO）

以下功能域在当前 audit/ 目录下**没有对应回归脚本**：
- **E5 夜间治理**：无独立 E5 回归脚本。
- **Cgraph 管线路由**：仅有 `dl_route_regression` 覆盖 Dl 域，Ao/Eo/Jw 等其他 router 域未见回归。
- **Memory Alias 状态区分**：设施③a 刚编码完成，尚未添加对应回归用例(当前只有 E4 patrol 的报告修正)。
- **Class Backfill 回填后验证**：`backfill_34_memory_class.py` 执行后缺少自动化的回填结果验证回归。

---

*文档结束。如需进一步了解某脚本的内部实现细节，请根据文件名在 `eval/audit/` 下直接阅读。*