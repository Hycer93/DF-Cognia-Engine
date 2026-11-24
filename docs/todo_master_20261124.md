# 记忆系统主待办台账（2026-11-24 立档）

> **本文件为系统主待办台账基准**：后续新增/完成任务均以此文档为基准更新。
> 编号规则：#N 统一待办编号，一经分配不复用。优先级 P0>P1>P2。
> 来源：`review_20261121_system_status.md` 主台账（#8~#33）+ `audit_memory_chain_20261123_E4E5E9.md`
> + `disposal_20261124_B_dup_A_drift.md` + 近期治理审计。
> 归档原则：已完成项保留历史号移入 G 区；未完成项按性质分 A~F 组。

---

## 编号计数器（发号登记区）

> **发号规则**：新增待办先在此登记取号，再写入对应分组。一经分配不复用。

- **已用最大号**：#42（含 E 区新发号 #34~#41 + 冲突重编 #42）
- **下一个可用号**：#43
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
| **#30** | 根治入库三道闸（全貌）：P0 summary 语义查重 / P1 derived 不再清 detail / P2 跨池弱匹配告警 / P3 E4 指标兜底 | 📋 待立项走七段。**#33 已落地路径 B（green_card_writer），路径 A（atomic_store 主流程）未做** | 台账:224 |
| **#24** | Summary 推理核对表 Q3：改"信息保真"审视（方法论/元认知产出是否被引用进 final_answer） | 📋 待立项（待 #23） | 台账:218 |
| **#25** | 主链 Prompt 合并 P-A：13 次调用→4-5 次 | 📋 待立项（放 Q1 分流后做） | 台账:219 |

## B. P2 记忆内容迭代（线B 深水区）

| 编号 | 事项 | 状态 | 出处 |
|---|---|---|---|
| **F30** | F系推演层正向演化（F00锚定→K条分支发散→推进到T） | ⏸ **未编码**，F系 F20✅/F40✅ 中间断档 | `docs/formula_future_sim_v2_1.md` |
| **#8 / F60** | forward_check 第二环深度正演（第一环 n-gram 字面已实装，第二环待立项，名义闭环实质只验字面） | ⏸ 待立项 | 台账:228 |
| **#31** | 存量 situational 双 active 多版本治理：8 对真实重复卡（detail 非空壳） | 📋 候选待评估 | 台账:225 |
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
| **#34** | memory_class 分类回填收尾（26条 ok 后仍剩 pending 候选待 LLM 裁定 + 冲突待人工裁决） | 🟡 部分完成 | `audit_memory_chain:29-33` |
| **#35** | 隔离区近 7 天新增 4 条校验拦截复核（其他3/枚举非法1） | 🔲 待人工复核（优先） | `audit_memory_chain:§5` |
| **#36** | watchlist 3 条人工裁决转正/关闭（留决策记录） | 🔲 待办 | `audit_memory_chain:§5` |
| **#37** | event_ts 空值 39 条分批回填 | 🔲 最低优先级 | `audit_memory_chain:§5` |
| **#38** | P0 语义查重继续攒样本（目标 clean_skip_equiv ≥8~10 再评估切 on，当前 clean=1） | ⏳ dry-run 进行中 | `audit_memory_chain:70-71` |
| **#39** | 组8 corpus 锚点 detail 回填（降级后 anchor detail 也空，独立证据回填） | 🔲 挂起 | `review_29:25-26` |
| **#40** | 其它写入入口 jsonl 同步巡检（#33 只修 gate 路径） | 🔲 待办 | `green_card_gate_concurrency_fix:92` |
| **#41** | 记忆更新只写 DB 不同步 jsonl（memory_organize 更新路径不回写） | 🔲 待办 | `disposal_20261124:141` |

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
