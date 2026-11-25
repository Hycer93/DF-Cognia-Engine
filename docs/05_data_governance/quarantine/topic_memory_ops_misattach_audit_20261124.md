# topic_memory_ops 误挂审计报告

> 日期：2026-11-24
> 触发：#14 治理收尾中核查 C 卡（7a499925）时，发现其 topic_links 挂载与 governor 提案"禁挂"矛盾，顺藤查出 topic_memory_ops 簇存在系统性误挂。
> **本报告只读分析，未改数据。**

---

## 一、核心结论

**topic_memory_ops 共 16 条 topic_links，其中 13 条零 cue 命中**（卡 summary+detail 全文不含该 topic 任何 cue 词）。

对比：全库 topic_links 共 180 条，只有 topic_memory_ops 这一簇出现零命中（13/16），其余 10 个 topic 全部 0 零命中。**说明这不是全局算法缺陷，而是 topic_memory_ops 单簇的批量误挂。**

topic_memory_ops 定义：
- topic_abstract：`记忆系统运维簇: 入库断档/hold池治理/检索接线/管线规约——DFCP自身主线运维事故与方法论`
- cue_anchors：`['记忆系统','入库断档','hold池','检索排序','judge管道','hook钩子']`

---

## 二、误挂成因（已锁定）

**元凶记录在 f1577a38 卡自己的摘要里**：

> `图谱还债专项定案: 全库191失挂卡盘点→冲突预检29歧义四类处置→6 manual_seed簇+152 backfill补挂+9双挂, 挂载51→182`

即 **2026-11-15 图谱还债专项**做了一次 "152 张失挂卡 backfill 补挂" 的批量操作。这批补挂按"看到失挂就补"推进，把一批语义不属于 memory_ops 的卡（F系推演公式/专利/图谱还债本身等）也塞进了 topic_memory_ops，可能因其当时充当兜底簇。

**证据**：
- 13 张中 10 张 ts=2026-11-15（12:57:02 八张同秒批量 + 13:31:07 两张），method 多为 backfill
- 无任何脚本可追溯（scripts/oneoff 无 20261115 批次），无文档记录该批次意图 → DB 直写，事后失载
- 同样 11-15 批量挂到 patent/stock/novel/vehicle/user_relation 的卡全部 cue 命中正常，唯独 memory_ops 混入错域卡

---

## 三、13 张误挂逐条清单

| # | inst_id | method | ts | tier | 实际主题 | 建议归属 |
|---|---|---|---|---|---|---|
| 1 | `7a499925` | backfill | 11-15 12:57 | active | 伪演示基线法（方法论） | 无明确topic（C卡，已裁定维持hold+撤挂） |
| 2 | `32684974` | backfill | 11-15 12:57 | active | F系推演公式v2定稿 | 无明确topic（F系另立） |
| 3 | `e4f92fcb` | backfill | 11-15 12:57 | active | F系公式硬伤修复 | 无明确topic |
| 4 | `76f6b86b` | backfill | 11-15 12:57 | active | F系公式v2.1软约束 | 无明确topic |
| 5 | `12a06982` | backfill | 11-15 12:57 | active | 平行论→P7→公式体系考古 | 无明确topic |
| 6 | `7747ed4d` | backfill | 11-15 12:57 | active | 消费侧检索教训 | 无明确topic |
| 7 | `a65a500c` | backfill | 11-15 12:57 | active | 平行论公式工程实现待办 | 无明确topic |
| 8 | `f4198bf7` | backfill | 11-15 13:31 | weak-observ | 语料考古收尾+专利补库 | topic_patent_matrix (1命中) |
| 9 | `f1577a38` | judge | 11-15 14:25 | active | 图谱还债专项定案 | topic_patent_matrix (8命中) |
| 10 | `87f66cc1` | judge | 11-15 14:25 | active | F系推演最小闭环完成 | topic_user_relation (2命中) |
| 11 | `fa1b5458` | user-attach | 11-16 11:18 | active | F系晋升四门槛核对 | 无明确topic |
| 12 | `a642b04f` | manual_seed | 11-17 11:35 | active | 技能自持三层范式 | 无明确topic |
| 13 | `0f2f4458` | manual_seed | 11-17 12:00 | active | 记忆写入双层规约 key_pact | 无明确topic |

### 语义正确的 3 张（应保留）
| inst_id | method | 命中 cue | 摘要 |
|---|---|---|---|
| `10764d3b` | e7 | 入库断档/judge管道 | 记忆入库断档事故（D卡，已正式挂载） |
| `7808dbac` | backfill | 记忆系统 | E7双裁决定案（引用性提及过滤） |
| `58707552` | backfill | 记忆系统 | 复现率门禁专利写法待办 |

---

## 四、附带发现（数据一致性小问题）

- `core_topic_abstraction.mount_count` = **9**，但 `core_topic_links` 实际 **16** 条 → 计数漂移，属另一起待修（不影响本次裁决）。

---

## 五、待决策

1. **13 张误挂是否删除？** 建议：语义明显错域的（#2-#10 中 F系公式/专利/图谱还债共 9 张）删除挂载；#11-#13（F系晋升/技能自持/key_pact）语义也非 memory_ops，倾向一并撤。
2. 撤挂后是否要给 F系推演公式族另立 `topic_future_sim` 簇？（当前 7 张 F系卡散落无归属）
3. mount_count 漂移是否一并修正？

---

## 六、本次已执行（与 C 卡裁决绑定，见下）

- **C 卡 7a499925**：3 条 governor 行（gp_501466854fc6 / gp_ece99dfb8774 / gp_090943563f3c）裁为 `accepted-hold`，并撤销其 topic_memory_ops 误挂（#1 上表）。主卡保持 active。
- **watchlist 3 条**（w_66d5ac7d60 / w_a0bd84585a / w_9732ed8492）：统一裁为 `keep-watching` 留观，不删不转正。
- 其余 12 张误挂：**本次不动**，待本报告审核后决定。
- resolved_by = `user-p14-review-20261124`；备份 `corpus_store.db.bak_p14_20261124_103213`。
- 回归：e30_p0 17 PASS / e30_p2_p3 13 PASS；governor 未裁决 0、watchlist 未裁决 0、memory_ops 挂载 16→15。

### 待用户审核后的下一步
1. 12 张误挂（#2-#13）是否撤挂？语义大多错域（F系公式/专利/图谱还债）。
2. 是否给 F系推演公式族（7 张散卡）另立 `topic_future_sim` 簇。
3. mount_count 漂移（9 vs 15）是否修正。
