# memory_gate 并发去重修复 + jsonl 同步管线缺陷 —— 归档报告

- 日期：2026-11-24 11:25
- 范围：`cgraph/green_card_writer.py`（单文件，单 commit）
- 状态：✅ 已落地，dry-run 并发验证 PASS，三回归全过

---

## 一、问题背景

`memory_gate` 绿档落库存在两类缺陷：

1. **并发重复立卡**：`scripts/collab_pipeline.py:455` 用 `multiprocessing.Pool(spawn)`
   多进程并行调 `run_atomic` → 各进程独立 `memory_gate`。旧代码"先查后插"无事务，
   进程间互相看不见对方未提交的写入 → 各算各的、同时插入副本。
2. **jsonl 不同步**：`commit_green_cards` 只写 DB `core_memory_card`，未同步
   `memories.jsonl`，导致审计快照漂移。

## 二、最终判定逻辑（用户拍板）

| 项 | 决策 | 说明 |
|---|---|---|
| 去重阈值 | **保留 bigram-Jaccard 0.6 不变** | 实测对本批几十字短文本措辞漂移样本全部有效拦截 |
| 并发核心 | **BEGIN IMMEDIATE 排他事务** | 查询-插入整块包裹，后到进程排队、可见前者已提交卡 |
| 第二层兜底 | **session + evidence-node 硬去重** | 同 (day, node_XXX) 已立卡则 skip，事务外防护 |
| jsonl 同步 | **DB INSERT 后 append memories.jsonl** | 保留全部 7 项 jsonl 独有审计字段 |
| embedding | **本次不引入** | 当前问题是并发非单进程判定不准；避免推理开销 |

## 三、实测样本数据（为何不换 embedding）

4 对真实措辞漂移样本，对比 Jaccard 0.6 与 embedding ≥0.95：

| 样本 | Jaccard | embedding | J>0.6 拦? | E≥0.95 拦? |
|---|---|---|---|---|
| vhal 未部署~未预置 | 0.735 | 0.949 | ✅ | ❌ 漏 |
| dumpsys bd~dk | 0.654 | 0.932 | ✅ | ❌ 漏 |
| dumpsys bd~di | 0.870 | 0.975 | ✅ | ✅ |
| dumpsys dk~di | 0.654 | 0.931 | ✅ | ❌ 漏 |

**结论：embedding 0.95 仅拦 1/4，Jaccard 0.6 拦 4/4。**
负样本（不同知识点 vhal vs dumpsys）：Jaccard=0.012, embedding=0.544，均不误拦。

短文本（几十字）措辞漂移让 embedding 相似度掉到 0.93-0.949 够不到 0.95；
而 bigram 共词对短文本敏感，反而全拦住。故**保留 Jaccard，不替换为 embedding**。

## 四、代码改动（cgraph/green_card_writer.py）

1. 新增 `import re`；`_jaccard` 文档补充实测结论。
2. 新增 `_extract_node(text)`：正则 `node_[A-Za-z0-9]+` 提取 evidence node。
3. 新增 `_sync_committed_to_jsonl(committed, ts, day, user_input, errs)`：
   DB 成功后同步 append jsonl，构造与现有 green 卡一致的 23 字段 entry，
   import 失败/写失败不阻断主流程（记 errs）。
4. `commit_green_cards` 重写：
   - `timeout=10`；`con.isolation_level = None`（显式事务完全接管，避免隐式事务冲突）
   - `con.execute("BEGIN IMMEDIATE")` 包裹全部查询+插入
   - existing 查询取 `summary, detail`，同时收集 `existing_nodes = {(day, node)}`
   - 三层判重：full_hash → `_jaccard>0.6` → `(day,node) in existing_nodes`（判 `nodedup:`）
   - `committed` 收集成功插入项（inst_id, summary, full_hash, card）
   - except 分支：`committed=[]` 防幻影同步 + `con.rollback()`
   - 事务后 `if committed: _sync_committed_to_jsonl(...)`

## 五、dry-run 并发验证结果

隔离方式：复制真库到 `tmp_dryrun.db`，`MEMORIES_JSONL` 指向临时 jsonl，**绝不碰真库/真 jsonl**。
场景：6 进程 `spawn` Pool 并发，每进程提交 3 张卡（2 张不同知识点 + 1 张 dumpsys 漂移版）。

```
[concurrency] 6 进程并发结果:
  worker0: written=2 skipped=1     ← 抢到写锁，落 2 张，漂移版被 Jaccard 拦
  worker1~5: written=0 skipped=3   ← 排队后看到已提交卡，全部 skip

[DB] 今日 memory_gate 卡总数 = 2 (期望=2，不重复)  ✅
[jsonl] 同步条数 = 2，字段数=23 完整，与DB一致 ✅
=== DRY-RUN 结论: PASS ✅ 无重复+jsonl一致 ===
```

**核心证据：6 进程并发只落 2 张卡，无重复副本；jsonl 同步 2 条、23 字段完整。**
（旧逻辑同场景会插 2 条重复 —— 前序验证已确认。）

## 六、回归结果

| 回归 | 结果 |
|---|---|
| e7_gate_regression | 13 PASS / 0 FAIL |
| e30_p0_regression | 17 PASS / 0 FAIL |
| e30_p2_p3_regression | 13 PASS / 0 FAIL |

合计 **43 PASS / 0 FAIL**。

## 七、遗留 / 后续迭代

1. **其他写入入口 jsonl 同步巡检**：本次只修 gate 路径，其它落库入口是否同步 jsonl 待巡检（用户已登记待办）。
2. **Jaccard OR embedding 组合判据**：本次不启用。若业务遇到 Jaccard 拦不住的新案例，再迭代叠加。