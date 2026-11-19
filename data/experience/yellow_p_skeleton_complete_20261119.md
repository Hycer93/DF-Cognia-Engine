# 黄档卡：P-skeleton-complete 本轮归档（2026-11-19）

commit 链：510b59d → a8b86fc → 85ddb9a → 3ba7a1d → 339aeb8 → ebc7a61 → 1956153 → 85b5b05

## 一句话结论

五技能（Dk/En/Bd/G24/Gs）设计冻结→编码→E11 冒烟 7/7→round6 A/B 复测**覆盖律首次达标**
（双样本 B 侧 5/5=裸模型），骨架链路在真实任务上完整跑通；短板：软挂模式下
En/Bd/Gs 自然触发率 0，骨架能力是"可选消费"而非必经。

## 关键工程发现（本轮最重要产出）

**软挂邻接表模式下 En/Bd/Gs 属于可选消费技能**：router 在收益/成本权衡后优先
直接 terminate（anchor 样本 Dk 后直达 terminate；ethics 样本 Dk→G24→terminate），
两样本 En/Bd/Gs 触发率均为 0。这不是 bug，是设计预期的观测结果；但说明仅靠
next_options 软挂不能保证证据网/交叉校验被执行——骨架能力不会自动兑现，
价值兑现依赖 router 认为值得。

## 量化结果（详见 data/experiment/p_skeleton_round6_rerun_20261119/VERDICT_rerun.md）

| 指标 | anchor_patent | ethics_patent |
|---|---|---|
| 覆盖律（5维审查轴） | B 5/5 = A 5/5 | B 5/5 = A 5/5 |
| B 侧步数/降级/fallback | 10步 / 0 / 0 | 12步 / 0 / 0 |
| Dk 织链 | 5链20步, 16步白名单引用(80%) | 5链20步, 3步留空如实标记 |
| G24 gaps | (router 未选 G24) | 8条实义: 4 uncovered + 4 unsupported |
| 耗时 | B 576s vs A 185s | B 616s vs A 75.5s |

对比 round6 基线（管线覆盖 1/5、假设只有一句话无证据）：三级跳。
Dk 解决"假设缺证据支撑"历史痛点；G24 产出无噪声实义缺口（含交付完成度检查）。

## 未收口项（后台增量，非阻塞）

1. ethics_patent 人工逐维对照 → 深度底 50% 验收基线正式定档
2. 攒 ≥5 份专利 A/B 样本 → En/Bd/Gs 触发率统计
3. 样本充足后七段评审二选一：方案X 调 next_options gain/cost 文案（不碰内核）
   vs 方案Y router 层强制触发钩子（Dk 后强制给 En/Bd 候选）
4. 暂不开启回流补推演（f_reason 二次调用）

## 状态

骨架代码稳定可随时重启迭代；本轮收工，转运维债务闭环为最高优先级。