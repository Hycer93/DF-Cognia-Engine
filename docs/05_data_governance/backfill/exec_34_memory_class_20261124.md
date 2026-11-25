# #34 memory_class 分类回填（第一批·高置信）—— 执行归档报告

- 日期：2026-11-24 13:34
- 性质：**已执行第一批（仅高置信 30 张）**，零物理删除
- 数据：执行前 active NULL 56 张 → 执行后 26 张（减少 30）

---

## 一、数据刷新（#46 后与旧盘点报告差异）

`dryrun_34_memory_class_20261124.md`（12:42 盘点）记 36 真待回填 + 21 无提案。**但 #46 已改 37 张 status**，实测刷新：

| 项 | 旧报告 | 实测(13:30) |
|---|---|---|
| 真待回填（NULL+有提案） | 36 | **22**（21 active + 1 superseded）|
| 无提案 active NULL | 21 | **34** |
| **active NULL 合计** | ~57 | **56** |

**关键发现：pending 提案质量差**。`behavior_prior` / `feature_words` 自动推的分类大量不准，抽样：
- 「非农金价复盘」→ 提案 skill_refine（实为投资记录，应 null）
- 「双暮暮对峙裁决」→ 提案 strategy_evolve/scar_case（实为顶层裁决）
- 「P-C4素材通道」→ 提案 scar_case（实为工程实现 skill_refine）

故本轮回填**不直接采用 pending 提案**，改用人工逐条复核的分类映射。

---

## 二、执行范围（用户决策：仅高置信）

人工复核 56 张 → 三档：
- 🟢 **高置信 30 张**：语义明确，本轮执行
- 🟡 中置信 26 张：有判断成分，**待用户逐条审**（未执行）
- null 保留 6 张：投资临时记录/临时观测，判为普通记录

本轮仅写 **高置信 30 张**。

---

## 三、执行明细（30 张）

脚本：`eval/audit/backfill_34_memory_class.py`（CLASS_MAP 内置复核映射，dry-run/--apply 双模式）
命令：`--only hi --apply`

**备份**：`corpus_store.db.bak_34_20261124_133430` / `memories.jsonl.bak_34_20261124_133430`

分类分布（30 张内）：
- scar_case（踩坑复盘）：踩坑类天然归此，如 vhal_test未部署、dumpsys回读不可信、未验证假设叠数字结论等
- skill_refine（工程实现）：P-C1~C6 专利管线、检索 2gram→BM25、F系门槛核对、九层骨架验证等
- strategy_evolve（架构/专利裁决）：脑区映射V1.0、双暮暮裁决、星型拓扑编号、九层骨架本体等
- relation_milestone（认知里程碑）：'我们一体'人设定调等

---

## 四、校验结果

| 项 | 结果 |
|---|---|
| active NULL 变化 | 56 → **26**（减 30 ✅）|
| 非法 class 值 | **0** ✅ |
| DB == jsonl | **完全一致** ✅ |
| memory_class 分布 | skill_refine 134 / strategy_evolve 61 / scar_case 38 / NULL 31 / key_pact 30 / relation_milestone 29 |
| 零物理删除 | ✅ 仅 UPDATE memory_class 字段 |

> 注：全库 NULL 31 = active NULL 26 + superseded NULL 5（superseded 低优先，本轮不动）。

---

## 五、回归（全 PASS）

e7_gate 13 / e30_p0 17 / e30_p2_p3 13 / e46 10 / e44 16 = **69 PASS / 0 FAIL**

---

## 六、待后续（第二批）

**26 张中置信 + 6 张 null 保留** 待用户逐条审。中置信典型：
- 「黄金ETF对冲决策/后续跟踪」「拓普定价复核」→ 投资决策，是 strategy_evolve 还是 null？
- 「E7晨报held计数」→ 临时观测数字，倾向 null
- 「脑区映射/专利矩阵架构映射」→ strategy_evolve vs 其他
- 「复活指南放置位置」→ key_pact vs 其他

用户确认后再执行第二批。

---

## 七、约束确认
- ✅ 零物理删除（仅 UPDATE memory_class）
- ✅ 备份齐全（DB + jsonl，bak_34_20261124_133430）
- ✅ 未采用低质 pending 自动提案，人工复核后回填
- ✅ 全套回归 69 PASS / 0 FAIL
- ⏳ 第二批（中置信 26 + null 6）待用户逐条审