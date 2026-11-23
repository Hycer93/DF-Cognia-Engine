# 记忆链巡检存档（E4/E5/E9）— 2026-11-23

> 触发背景：2026-11-23 18:05 nightly 因 E1 探针（推理类）卡在内网 LLM 长耗时，
> 用户裁决停掉全部推理脚本，专攻记忆链。本文件固定记录手动按链跑 E4→回补→E5→E9 的结果，
> 作为后续 P0 等价去重（dry-run）攒样本的**基线对照**，避免多轮数据混淆。

- 执行时间：2026-11-23 19:07 ~ 19:08
- 执行方式：`NO_MEMORY=1 DF_SUMMARY_EQUIV_DEDUPE=dry-run` 手动逐段跑（不走 nightly 全链）
- 跑序：E4 维护巡逻 → DB→jsonl 增量回补 → E5 自动整理 → E9 对话治理

---

## 1. 回补成果（漂移归零）

| 项 | 值 |
|---|---|
| 回补前 DB↔jsonl 漂移 | **+75**（db=323 vs jsonl=248） |
| 回补后 | **drift=0**，jsonl=323(unique 323) = DB 全量 323 |
| append 条数 | 75 |
| 备份 | `data/memory/memories.jsonl.bak_20261123_190742` |
| 红线遵守 | 不删任何行、不改 DB；7 组同事件多版本原样并存（留待 superseded_by 任务） |

结论：迁移漂移已归零，双侧对齐 323 条，具备切换读源前提（本轮不切）。

---

## 2. E5 记忆自动整理

| 项 | 值 |
|---|---|
| ok | True |
| 补挂分类 | ok 26 / candidates 43（conflict 4, skipped 0） |
| distill 归纳 | created 6, updated 0, untouched 5, atom_total 213 |
| 库态 after | memories=**323**, graph_edges=**262**, experiences=**28** |

---

## 3. E9 对话治理

| 项 | 值 |
|---|---|
| 扫描段 | 50 |
| 高价值 / 提案 | 0 / 0 |
| 重复跳过 | 2（旁路已提，夜间不重复） |
| transcript 兜底 | scanned 79 / **stored 17** / dup_skipped 45 / too_short 17 |
| **h 通道** | **h_records=5**，t_records=67，days=3，**stalled=False** |
| 悬空碎片（#31 证据库） | **orphan_sessions=0, collected=0** |

关键判读：
- **h 通道近 3 天 5 条** —— #31 接线修复（`--file` 不再遮蔽 stdin payload）生效后，PreCompact
  hook 真实会话开始入库，h 通道**不再 stalled**（此前长期断流）。
- **evidence_orphan=0** —— 对话证据库三表（core_raw_dialog_session / core_dialogue_log /
  _tmp_dialog_fragment）碎片兜底链路稳定，无悬空碎片丢数据。

---

## 4. P0 等价去重 dry-run 指标（本轮基线）

环境：`DF_SUMMARY_EQUIV_DEDUPE=dry-run`（影子统计，不真拦截）

| 指标 | 值 | 来源 |
|---|---|---|
| gate_dedup_hits（门控查重命中） | **5** | E4 health.dedup |
| skip_equiv_count（语义等价跳过） | **1** | E4 health.dedup |
| p0_equiv_dry（P0 等价 dry-run 触发） | **1** | E4 health.retrieve.op_dist |
| p0_slow（P0 慢路径） | **1** | E4 health.retrieve.op_dist |
| simhash_bearing（携带 simhash 卡） | 17 | E4 health.dedup |
| active_same_summary_multi | 2 | E4 health.dedup |

> 本轮即 P0 dry-run 的**基线**。当前 skip_equiv 仅 1 例，样本不足以判断误伤/误拦/慢路径
> 稳定性。目标：后续多轮攒到 **skip_equiv ≥ 8~10** 再做切 on 安全性评估。

---

## 5. E4 四项报警清单与状态

| # | 报警 | 状态 |
|---|---|---|
| 1 | DB↔jsonl 漂移 +75 | ✅ **本轮已修复归零**（见 §1） |
| 2 | 隔离区近7天新增 4 条校验拦截（其他:3 / 枚举非法值:1） | 🔲 待人工复核（优先） |
| 3 | watchlist 3 条在池（0 条仍 watching 未推进） | 🔲 待人工裁决转正/关闭 |
| 4 | event_ts 空值存量 39 条待回填 | 🔲 存量修缮（最低优先级，不动观测窗口） |

E4 其它健康度：检索 gate=26 miss=0.0 | 冲突 q=103(待0) | 待归档 13(超14d=0) | hold 最长 12 天。
Phase2：冲突级联分布={} | 去重触发 5（simhash 卡 17）| 降级率 0.1169（29/248）。

---

## 6. 后续执行顺序（用户 2026-11-23 拍板）

1. ✅ 存档本轮结果（本文件）
2. ⏳ **P0 维持 dry-run 继续攒样本**，不切 on；目标 skip_equiv ≥ 8~10 再评估安全性
3. 🔲 人工复核隔离区 4 条（先拉原始 transcript/记忆卡，确认正常拦截→归档标记；误拦→单开 ticket）
4. 🔲 watchlist 3 条逐条评审（转正 / 关闭，留决策记录）
5. 🔲 event_ts 空值 39 条分批回填（低优先级后台脚本，观测期不大改存量）

> 约束：第 3~5 项按低干扰、不阻塞主链路节奏分批处理，不抢占 P0 观测窗口。

---

## 补充：19:31-19:32 重复手动记忆链重跑（存量复跑，无新真实会话流入）

> 说明：仅回放已有 323 条存量记忆，**无新的真实对话进入 PreCompact hook 管道，P0 统计指标不会上涨**。

| 阶段 | 结果 |
|---|---|
| E4 维护巡逻 | 5 项报警，较上轮新增 2 项：冲突积压 3、runtime 未跑四档 17 |
| DB→jsonl backfill | 0 补，drift 保持 0，双侧持续对齐 323 条 |
| E5 自动整理 | 0/17 补挂（幂等，上轮已完成全部可补挂项） |
| E9 对话治理 | 扫描 67 段，兜底入库 2 段；h 通道=5 正常；orphan_sessions=0 |

### P0 dry-run 累计指标（无新增）

> 重要提示：**重复执行调度/脚本不会凭空产生等价命中；必须真实新会话产生语义等价记忆写入才会新增统计**

| 指标 | 累计值 |
|---|---|
| gate_dedup_hits | 5 |
| skip_equiv_count | 1 |
| p0_equiv_dry | 1 |
| p0_slow | 1 |

daily_trend 佐证：2026-11-23 gate=18, store=10, dedup=4；今日有真实会话流入，但仅发生 1 次语义等价观测。

### E4 报警更新

原有：
1. DB↔jsonl 漂移：✅ 已修复归零
2. 隔离区近 7 天 4 条拦截：🔲 待人工复核
3. watchlist 3 条在池：🔲 待人工裁决
4. event_ts 空值 39 条待回填：🔲 存量修缮（最低优先级）

**本轮新增报警：**
5. 冲突队列积压：3 条
6. runtime 未跑四档判定：17 条

> 新增两项属于治理类告警，不阻塞 P0-dry-run 样本采集观测窗口，归入后续人工治理任务。

### 修正后真实样本累积路径

1. 维持环境变量 `DF_SUMMARY_EQUIV_DEDUPE=dry-run` 不变；
2. 依赖**日常真实对话持续流入 h 通道 PreCompact hook** 自然累积 `skip_equiv_count`；定时脚本重复执行存量回放不会增长该指标；
3. 触发评估条件：`audit30_phase7_check.py` 输出 `skip_equiv_count ≥ 8~10`；
4. 到达阈值后：先完成全部 E4 人工复核任务，再评估是否切 `on`。

### 校验脚本口径确认（2026-11-23 19:37）

`eval/audit/audit30_phase7_check.py` 已加 §④「样本量门槛判定」：
- 当前输出：`skip_equiv_count=1 < 8 → 继续累积真实会话样本，尚未到达切 on 评估条件（还差 7 例）`；
- ≥8 时输出：`✅ 已达切 on 评估门槛 → 先完成全部 E4 人工复核，再评估切 on`。
- 目的：防止后续误判，把重复跑脚本当成样本增长。后续查看进展固定命令：
  `python eval/audit/audit30_phase7_check.py`

### 轻量调度脚本认知修正

> 核实：仓库中**当前不存在** `run_memory_only_nightly.py`（如后续新增轻量记忆链调度脚本，须明确认知：）
> **它只消费已经产生的会话，不能生成新的 P0 观测样本；样本来源唯一是真实业务对话。**
