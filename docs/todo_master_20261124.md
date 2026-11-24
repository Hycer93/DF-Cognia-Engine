# 记忆系统主待办台账（2026-11-24 立档）

> **本文件为系统主待办台账基准**：后续新增/完成任务均以此文档为基准更新。
> 编号规则：#N 统一待办编号，一经分配不复用。优先级 P0>P1>P2。
> 来源：`review_20261121_system_status.md` 主台账（#8~#33）+ `audit_memory_chain_20261123_E4E5E9.md`
> + `disposal_20261124_B_dup_A_drift.md` + 近期治理审计。
> 归档原则：已完成项保留历史号移入 G 区；未完成项按性质分 A~F 组。

---

## 编号计数器（发号登记区）

> **发号规则**：新增待办先在此登记取号，再写入对应分组。一经分配不复用。

- **已用最大号**：#46（含 #43 新重复专项/#44 detail护栏/#45 watchlist余2条/#46 demote语义对齐）
- **下一个可用号**：#47
- **断号说明**：
  - #32 未使用（保留不复用，历史重编遗留）
  - #1~#4 / #6 / #9 早期立项重编时废弃（无存活条目）
  - #A/#B/#C 已并入 #8/#15/#16（不复用）
  - **#31 编号冲突重编**：git 历史曾用 #31 指"对话证据库链"（已完成），违反不复用规则；2026-11-24 将该完成套重编为 **#42**，#31 权威归还"存量situational双active治理"（未做，B区）
- **F 系编号**：F00/F10/F20/F30/F40/F50/F60 为技能链路序号，非待办号，独立体系

---

## A. P1 核心缺口（有实际价值、未做）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **#30** | 根治入库三道闸（全貌）。**2026-11-24 已切 on 收尾**（`exec_30_switch_on_20261124.md`）：此前台账「待立项」严重过时——实际 P0/P1/P2/P3 四道闸 git commit-1~6 全部落地（`0edbf79/5b9ae9a/ec297df/a9d4514/a255b68/79d1e40`），段一~段六评审 + 段七第一阶段 dry-run 已完成。四道闸：P0 语义查重(`_p0_equiv_check`) / P1 derived 保留 detail / P2 跨池告警(`_cross_pool_warn`) / P3 E4 三指标。切 on 依据：影子重放 n=280 中 8/280 真同义零假阳 + 6 类对抗梯度全过 + dry-run 26/32 would_skip。`setx DF_SUMMARY_EQUIV_DEDUPE on` 已生效（注册表=on，新进程 MODE=on，阈值 0.95），on 实弹 3/3 正确（真重复拦/无关不拦/同主题不同事实不拦），回退秒级（`setx ... dry-run/off`）。回归全过。备份 `.bak_30on_20261124_142258` | ✅ 完成(切 on 上线) | 台账:224 + exec_30_switch_on |
| **#24** | Summary 推理核对表 Q3：改"信息保真"审视（方法论/元认知产出是否被引用进 final_answer） | 📋 待立项（待 #23） | 台账:218 |
| **#25** | 主链 Prompt 合并 P-A：13 次调用→4-5 次 | 📋 待立项（放 Q1 分流后做） | 台账:219 |

## B. P2 记忆内容迭代（线B 深水区）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **F30** | F系推演层正向演化（F00锚定→K条分支发散→推进到T） | ⏸ **未编码**，F系 F20✅/F40✅ 中间断档 | `docs/formula_future_sim_v2_1.md` |
| **#8 / F60** | forward_check 第二环深度正演（第一环 n-gram 字面已实装，第二环待立项，名义闭环实质只验字面） | ⏸ 待立项 | 台账:228 |
| **#43** | 候选重复卡专项。**2026-11-24 甄别完成**（`dryrun_43_dup_candidate_20261124.md`）：台账记"18 对"为 #31 估算失真；summary 口径 sim≥0.84 实际 **5 对 + 1 对边界(0.804)**。**6 对全部已由 #29 于 2026-11-23 完成 tier=precedent 降级、superseded_by 已赋值**，仅 status 仍 active（属 status-tier 混叠态）；#43 **不再重复执行降级**，混叠统一交 **#46** 存量回填。个案 `9b48551b`(runtime) asset_class 异常需单独修正为 corpus（并入 #46）。detail 口径 96 对全模板占位符噪音，不动 | 🟡 甄别完成，处置移交 #46 | 本次甄别 |
| **#46** | demote() 语义对齐 + 37 张混叠态回填。**2026-11-24 已执行完成**（`exec_46_demote_status_realign_20261124.md`）：①`demote()` L529 + `_sync_demote_to_db` L482 同步写 `status='superseded'`；②新增回归 `e46_demote_status_regression.py`（10 用例防复现）；③37 张 `status=active&tier=precedent&superseded_by非空` 回填 status→superseded；④个案 `9b48551b` asset runtime→corpus（其余 26 张专利卡 asset 不动）；⑤jsonl 同步 37+1 行。校验混叠态=0，全套回归 53 PASS/0 FAIL，DB==jsonl。备份 `*.bak_46_20261124_130210` | ✅ 完成 | 本次 #43 预演发现 |
| **#16** | topic_layer 决断（开启 or 删除）：556 行默认关闭悬空资产 | 📋 待裁决 | 台账:230 |
| **#13** | 记忆卡版本链 + diff | ⏸ 暂缓但**前置已解除可评估** | 台账:231 |
| **#15** | 桩—LLM 判定一致性对照回归 | 📋 待裁决 | 台账:229 |

## C. P2 性能/架构（#21 改进方案衍生）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **#26** | 异步归档 P-B（记忆闸门移出阻塞路径） | 📋 待立项 | 台账:220 |
| **#27** | 分类任务切轻量小模型 P-C（延迟收益最大） | 📋 待立项 | 台账:221 |
| **#28** | 加深分支并行 P-D | 📋 待立项 | 台账:222 |

## D. P2 reward/赫布调制（依赖链）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **#17** | 用户回执→reward 映射工具 P2-A | 📋 待立项 | 台账:232 |
| **#18** | Hebbian 两路 reward 加权融合 P2-B（**依赖 #17**） | 📋 待立项 | 台账:233 |
| **#19** | Executor 节点 activation 输出契约 P2-C（依赖 #17/#18） | 📋 待立项 | 台账:234 |

## E. 日常治理细活（低优先级，按周清）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **#34** | memory_class 分类回填收尾。**2026-11-24 已完成 50 张回填**：① 第一批高置信 30 张（commit `c6b9b45`，`exec_34_memory_class_20261124.md`）；② 第二批 A 组中置信 20 张（`exec_34_batch2_medium_20261124.md`，`--only all --apply`），分类分布 strategy_evolve×6/skill_refine×5/scar_case×4/relation_milestone×3/key_pact×2。复核推翻低质 pending 提案（behavior_prior/feature_words 自动推不准）。**完成校验**：active NULL **56→6**（剩余 6 张=B组维持 null）、非法值 0、DB==jsonl(323条)、回归 69 PASS/0 FAIL、零物理删除。**B 组 6 张维持 null**（季报扫描/晨报计数/对冲跟踪/金价复盘×2/拓普复核=单次记录）。备份 `.bak_34*_20261124_*`。**剩余未评审中置信条目继续挂起待人工逐条审**（B 组 null 决策待后续统一）| ✅ 已完成 50 张，B组6张挂起 | `audit_memory_chain:29-33` + dryrun_34 + exec_34 + exec_34_batch2 |
| **#44** | detail 质量护栏。**2026-11-24 已执行完成**（`exec_44_detail_guard_20261124.md`）：①`memory_organize._detail_quality_issue()` 黑名单检测（empty/green_template/ascii_placeholder/numeric_only，不用字数硬门槛避免误伤合法短详情）；②`atomic_store()` 编码校验后只警告不拒收（脏 detail 记 gate_log `warn_detail_quality`，仍 stored）；③`green_card_writer._green_detail()` 替代两处硬编码模板（不再写"绿档自动立卡(...)"占位）。新增回归 `e44_detail_guard_regression.py`（16 用例）。全套回归 69 PASS/0 FAIL。零 DB 数据变更，历史 29 空/20 短/19 模板不自动清理 | ✅ 完成 | 本次 #36 拆出 |
| **#45** | watchlist 余 2 条继续跟踪：w-949ef95d3b54(33条detail瘦身,未达K=3)、w-52e4c6ddc6b3(EEA架构考古,待找全称) | ⏳ 观察中 | 本次 #36 裁决 |
| **#37** | event_ts 空值回填。**2026-11-24 已执行 20 张**（`exec_37_event_ts_20261124.md`，`backfill_37_event_ts.py --tiers strong,summary --apply`）：仅写日期即本卡事件日的项（strong session/会话 19 + summary 正文 1）。**关键甄别**：旧报告 A1=22/A2=3 机械正则未辨语义，剔除 9 张误判——op 4（quarantine-restore/watchlist-promote 操作日，证据：0b272d92 source=11-23 但兄弟卡 7ee56837 ts=11-08）、date 3（专项扫描回填 11-13 标注日）、refdate 2（aa1d785b75 引用专利①/ ddd691156d 引用 G22）；填入=伪造业务时间污染时序+E4，故留空。**校验**：event_ts 空值 **91→71**，20/20 落地，仅改 event_ts，DB==jsonl(323)，回归 69 PASS。备份 `.bak_37_20261124_135259`。**剩余 71 张**：op/date/refdate 9 张待人工研判（不自动处理）；**B 批 62 张无线索永久挂起**（留空优于伪造）| 🟡 20 张完成，71 张留空 | `audit_memory_chain:§5` + dryrun_37_a1a2 + exec_37 |
| **#38** | P0 语义查重继续攒样本（目标 clean_skip_equiv ≥8~10 再评估切 on，当前 clean=1） | ⏳ dry-run 进行中 | `audit_memory_chain:70-71` |
| **#39** | 组8 corpus 锚点 detail 回填（降级后 anchor detail 也空，独立证据回填） | 🔲 挂起 | `review_29:25-26` |
| **#40** | 其它写入入口 jsonl 同步巡检。**2026-11-24 已完成**（`dryrun_40_write_entry_scan.md`）：扫描 8 处触及 core_memory_card 的写入位置，3 条业务主路径已双向同步（atomic_store/demote/green_card_writer）；缺口=schema_migrate UPDATE 不同步、nightly 兜底只补新增不修字段漂移 | ✅ 完成 | `dryrun_40_write_entry_scan` |
| **#41** | memory_organize 更新路径 jsonl 回写缺陷。**2026-11-24 模拟方案验证完成，暂不落地生产代码**（`dryrun_41_unified_update_sim.md`，模拟脚本 `eval/audit/sim_41_*`，commit 已归档）：update_card_fields 统一封装 + backfill --full-sync + demote 等价性 全部 tmp 隔离用例通过；当前生产 DB-jsonl 漂移=0，属预防性加固非线上故障。**防护约束**：①新增 core_memory_card UPDATE 逻辑优先复用 demote()/atomic_store()，禁止裸 UPDATE；②所有 eval/audit 回填脚本 --apply 结束必须执行 `backfill_jsonl_from_db.py --full-sync` 全量对齐。**触发落地条件**：新增大量主表 UPDATE 业务逻辑 或 真实出现 DB-jsonl 漂移故障，二者任一发生再拿出整套方案落地 | ⏸ 暂缓，方案归档冻结 | #40 巡检发现 |

## F. 已暂缓（有触发条件，现在不动）

| 事项 | 暂缓条件 | 出处 |
|---|---|---|
| Week2 换语义向量基座 | C3/C5 判据现基于 char-2gram 经验值，换向量需重标定 | `design_memory_v02:272` |
| F20 守卫基线批量刷新 | 等图谱治理、簇构成大规模更新后统一刷 | `review_20261121:184-186` |
| 冲突评审积压 | 待元思域对撞流水线 | `design_memory_v02:282` |
| Memory_judge Q4 血缘 O(N) 优化（S1~S4） | 调研已完成（预聚合省 91.4%），代码优化未排期 | `review_20261121:120-135` |
| 检索权重标定实验（批1~批4） | 设计已交付，实标定未跑 | `review_20261121:103-118` |

## G. 已完成归档（勿重做）

| 编号 | 事项 | 完成标记 |
|---|---|---|
| **#33** | memory_gate 并发去重 + jsonl 同步（BEGIN IMMEDIATE + session-node 兜底） | ✅ `4ee46c1`，dry-run 6进程无重复，43回归PASS |
| **#31** | 存量 situational 双active治理（8对）：PAIR1(5cb9bdf8→ac25fe3a)、PAIR7(d987b0cc→4d26fad6) superseded；PAIR2-5已闭环；PAIR6反例+PAIR8两张保留 | ✅ 2026-11-24，2张superseded，零物理删除，43回归PASS |
| **#35** | 隔离区近7天4条拦截复核：relation=R collab(已#32回收,不重复回收)+3条domain009冒烟测试 → 全部 verdict=discarded（原记录保留不删） | ✅ 2026-11-24，quarantine 35条无删 |
| **#36** | watchlist 3条裁决：MoE架构(w-8e37c92a)转正入库`da08b663`(005-002/Ht/C/strategy_evolve)；余2条继续观察 | ✅ 2026-11-24，从watchlist移除1条，43回归PASS |
| **#42** | 对话原始证据库链（快照+逐条双写+碎片兜底 / SessionEnd-Stop对接 / `--file`遮蔽stdin接线修复）。**⚠️历史误用编号#31，2026-11-24重编为#42**（#31 权威归"存量situational双active治理"，见B区） | ✅ `783dd27`+`ffdc438`+`10f16d1` |
| **#29** | 同事件多版本存量消重（8张derived空壳降precedent） | ✅ `e42c5b5` |
| **#14** | 记忆一致性修复（demote sup DB 同步根因 + 存量对账） | ✅ `4b97a63` |
| **#21** | cgraph 可验证性：verify_ratio 消融 + A/B 结论 | ✅ 完成（图路由略低于裸模型） |
| **#20** | 记忆可观测性 Phase1/Phase2 | ✅ 完成 |
| **#11(态度)** | 回执态度元标签子通路（memory_attitude.py） | ✅ 完成 |
| **#12** | 对话追溯轻量版 — 根治 h 通道断流 | ✅ `6b4606b` |
| **#10(门槛)** | F40 门槛②③④ 消融收敛/边覆盖率/失败溯源降级 | ✅ 完成（人工评审签字归档） |
| **#10/#11(口径)** | P0 dry-run origin 打标 + raw/clean 双统计 + add 入口 origin 通道 | ✅ **已完成**（`_p0_equiv_check` origin + `e4` clean过滤 + `memory_hook.add` origin 参数 + CLI `--origin`，回归10a~11c PASS，`96ab723`） |
| **#7** | pending 队列可靠写回 | ✅ `6d5a25a`+`431ce52`+`6880f63` |
| **#5** | F20 权重层 | ✅ 已结案 |

---

## 备注区：#10 / #11 实现要点（编码查阅用）

### #10：p0_equiv_dry 表 origin 字段
- 枚举：`business` | `script-reingest`
- 真实 h-channel 会话产生：`origin="business"`（默认值）
- 脚本重导入/批量恢复产生：`origin="script-reingest"`
- 落点：`_p0_equiv_check(summary, pool, origin="business")` 写入 p0_equiv_dry 日志；
  `atomic_store(..., origin="business")` 参数透传。

### #11：统计逻辑双口径
- `raw_skip_equiv_count`：原始全部计数（兼容回看历史日志）
- `clean_skip_equiv_count`：仅统计 `would_skip=true AND origin="business"`，**作为 P0 评估阈值依据**
- `audit30_phase7_check.py` 输出两套指标 + 门槛判定（clean ≥ 8 才可评估切 on）
- 回归用例 `e30_p0_regression.py` 用例 10a~11c 覆盖
- **约束**：不改动 P0 等价去重核心判定，维持 `DF_SUMMARY_EQUIV_DEDUPE=dry-run` 影子模式不变

### 现状（2026-11-24 审计）
- ✅ `_p0_equiv_check` origin 参数、`atomic_store` origin 参数已实现
- ✅ 回归 e30_p0 用例 10a~11c 全 PASS
- ✅ audit30 输出 raw/clean 双指标 + 门槛判定
- ✅ E4 `e4_maintenance_patrol.py:382-387` clean 过滤已实现（旧日志无 origin 保守计 business）
- ✅ 真实 gate_log：p0_equiv_dry 31 条 = 30 script-reingest + 1 business
- ⚠️ ~~**待收尾缺口**：`memory_hook.add()` 无 origin 参数通道~~ → ✅ **已修复(96ab723)**：add() 新增 `origin="business"` 参数并透传 atomic_store，CLI `add --origin` 可选 business|script-reingest。
