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

---

## 补充：2026-11-23 22:36 基线快照（治理闭环后实测，含样本污染剔除）

> 背景：P2（151 条 class_backfill）/ P3（6 卡 governor）治理闭环后，实跑
> `DF_SUMMARY_EQUIV_DEDUPE=dry-run python eval/audit/audit30_phase7_check.py` 固化基线。
> **本轮实测 `skip_equiv_count=31`，但经逐条溯源，其中 29 条为当日脚本 re-ingest 污染，
> 真实业务同义样本贡献 = 0。** 以下是剔除污染后的干净口径。

### 一、原始输出（照抄 audit30，未清洗）

| 指标 | 值 |
|---|---|
| gate_log 总记录 | 59（op 分布：probe 2 / judge 1 / dialog_stored 13 / dialog_dedup 5 / dialog_too_short 4 / p0_slow 3 / **p0_equiv_dry 31**） |
| p0_equiv_dry（观测） | **31** |
| skip_equiv_count（`e4_maintenance_patrol` 口径） | **31**（= skip_equivalent_derived 0 + p0_equiv_dry 31） |
| dry 中 would_skip=true | **26 / 31** |
| p0_slow（慢调用） | **3**（max=4959ms，均值=2522ms） |
| p0_unavailable（降级） | 0 |
| 日期分布 | 2026-11-23 全部 31 条 |

### 二、31 条逐条溯源（关键：剔污染）

按精确秒分组：

| 时间戳 | 条数 | 性质 | would_skip |
|---|---|---|---|
| 18:05:07 | 1 | TOPIC_LAYER_ON 守卫探针测试卡 | false（sim 0.619，未达 0.95 阈值） |
| 20:32:01 | 1 | 阈值细化讨论（真实内容） | false（sim 0.819，未达阈值） |
| **22:17:27** | **26** | **fix_quarantine re-ingest 29 卡批量触发** | **26 true**（sim≈1.0，卡本身已在池） |
| **22:17:28** | **3** | 同批次尾部 | 3 false |

**结论：29 条 22:17 批次是污染源**——时间点精确匹配 `fix_quarantine_20261123.py`（mtime 22:17:01）执行 re-ingest 29 张隔离卡的时刻；每张卡调 `atomic_store` → P0 语义查重 → 在 dry-run 模式下各写一条 `p0_equiv_dry`。由于这些卡原本就在池里，故 `sim≈1.0 / would_skip=true`。

### 三、剔除污染后的干净基线

| 口径 | 值 | 说明 |
|---|---|---|
| 原始 skip_equiv_count | 31 | 含 29 条脚本回放污染 |
| 剔除 re-ingest 污染（-29） | **2** | 仅剩 18:05 / 20:32 两条零散实测 |
| 其中真实业务跨会话同义（would_skip=true） | **0** | 2 条零散均 would_skip=false（0.619 / 0.819，未达 0.95） |
| **真实业务有效样本** | **≈0** | 距目标 8~10 缺口很大 |

> **重要纠正**：此前文档（§4、19:31 补充节）记载的 "skip_equiv_count=1，重复重跑存量不会增长" 表述，
> 在 **re-ingest 一类脚本**下不成立——`fix_quarantine` 这类"重新入库已有卡"的操作会真实写入
> `p0_equiv_dry`，导致计数虚高。**判定标准应改为：只统计真实新会话流入 h 通道产生的 would_skip=true，
> 排除任何形式的存量卡 re-ingest 回放。**

### 四、算法口径缺陷记录（待修，本轮未改码）

`eval/core/e4_maintenance_patrol.py:379`：
```
skip_equiv_count = op_cnt.get("skip_equivalent_derived", 0) + op_cnt.get("p0_equiv_dry", 0)
```
**缺陷**：把 `would_skip=false` 的观测也计入（本轮 5 条 false 被计入）。真实"同义命中"应只算
`would_skip=true`。建议后续修：`p0_equiv_dry` 加 `origin` 字段（business / script-reingest）
并仅统计 `would_skip=true AND origin=business`。**本轮按用户要求只记录不改码，留作后续。**

### 五、E4 巡检快照（同步确认治理成果）

| 项 | 值 |
|---|---|
| total_memories | 353 |
| ok | False |
| skip_equiv_count | 31（见上，含污染） |
| cross_pool_warn | 0 |
| active_same_summary_multi | 29 |
| alerts 数 | 6 |

6 项报警：
1. 隔离区近7天新增 4 条校验拦截（其他3 / 枚举非法1）——拦截正常，非故障
2. 冲突评审积压 3 条（同源分叉待元思域对撞）
3. runtime 未跑四档判定 29 条——entity_timeline 挂靠需补 classify_and_link
4. event_ts 空值存量 82 条待回填
5. watchlist 3 条在池（0 仍 watching）
6. 语料版本漂移 patent_disclosures(hash_changed)——合法更新

### 六、治理池大盘点（2026-11-23 22:35 实测）

| 池子 | 待处理 | 总数 | 状态 |
|---|---|---|---|
| core_conflict_queue | **0** | 149 | ✅ dismissed141 + merged8，全结清 |
| core_quarantine | **0** | 43 | ✅ 28入库 + 15丢弃 |
| core_class_backfill_pending | 151（verdict NULL） | 151 | ✅ 其中 7 条已填 suggested_class，144 留 NULL 待人工 |
| core_governor_pending | **3**（仅 C 卡） | 17 | ✅ A/B/D/E/F 已裁决 |
| core_watchlist | — | 3 | ⏳ 观察清单，非阻塞 |

> 注：`class_backfill_pending` 是**提案池非裁决池**，P2 只填 suggested_class 不改 verdict，
> 故 `verdict IS NULL` 仍=151 属正常；待人工审的实质=144 NULL + 7 待认可。

### 七、当前行动基线（2026-11-23 22:40 固化）

1. **维持** `DF_SUMMARY_EQUIV_DEDUPE=dry-run`，不切 on，不改 P0 逻辑。
2. **干净基线**：真实业务有效样本 ≈ 0（剔除 29 条污染后），距 8~10 缺口大。
3. **日常查看**：`python eval/audit/audit30_phase7_check.py`，但需**人工剔除 re-ingest 污染**再看 §④。
4. **触发条件**：真实业务 would_skip=true ≥ 8~10 → 先清 C 卡 + watchlist → 安全性评估 → 讨论切 on。
5. **禁止**：靠重跑存量记忆链/re-ingest 脚本期待指标上涨（已验证会虚增，属污染）。
