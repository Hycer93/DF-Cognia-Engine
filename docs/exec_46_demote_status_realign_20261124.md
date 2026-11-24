# #46 demote() 语义对齐 + 37 张混叠态回填 —— 执行归档报告

- 日期：2026-11-24 13:03
- 性质：**已真实执行**（代码改动 + 存量回填 + jsonl 同步）
- 前置：`dryrun_46_demote_status_realign_20261124.md` 预演通过
- 来源：本次 #43 专项预演发现（6 对"待降级"卡其实 #29 已完成，暴露 status/tier 混叠态）

---

## 一、执行摘要

| 项 | 结果 |
|---|---|
| 代码改动 | 2 处（demote L529 / _sync_demote_to_db L482）|
| 新增回归 | `e46_demote_status_regression.py`（10 用例）|
| 回填脚本 | `eval/audit/backfill_46_status_realign.py`（dry-run/--apply 双模式）|
| 存量回填 | 37 张 status: active→superseded |
| 个案修正 | 9b48551b asset: runtime→corpus |
| jsonl 同步 | 37 行 status + 1 行 asset |
| 混叠态校验 | **status='active' AND tier='precedent' = 0 ✅ 已清零** |
| 零物理删除 | ✅ 全部 UPDATE 字段，无 DELETE |

---

## 二、执行流程（三步）

### Step 1 — tmp 副本 dry-run 全量模拟
- `tmp/corpus_store_sim46.db` + `tmp/memories_sim46.jsonl` 副本
- dry-run 列 37 张清单 → 链路校验 ALL PASS → apply 模拟
- 模拟结果：DB 279 active/42 superseded/2 deleted，jsonl 完全对齐，混叠清零

### Step 2 — 真实库备份 + apply
- 备份：`data/memory/corpus_store.db.bak_46_20261124_130210`
- 备份：`data/memory/memories.jsonl.bak_46_20261124_130210`
- apply：DB status 更新 37 行 + asset 更新 1 行；jsonl 同步 37+1 行

### Step 3 — 校验
- 真实库：279 active/42 superseded/2 deleted（total 323）
- 混叠态 `status='active' AND tier='precedent'` = **0**
- DB == jsonl（完全一致）
- 9b48551b：asset=corpus tier=precedent status=superseded ✅

---

## 三、代码改动明细

### 改动 1：`memory/memory_organize.py:529`（demote jsonl 侧）
```python
if not cur:
    m["tier"] = "precedent"
    m["status"] = "superseded"   # #46 语义对齐：tier 与 status 同步，防混叠
    m["superseded_by"] = superseded_by
```

### 改动 2：`memory/memory_organize.py:482`（_sync_demote_to_db DB 侧）
```python
wc.execute(
    "UPDATE core_memory_card SET tier='precedent', status='superseded', superseded_by=? "
    "WHERE inst_id=?", (superseded_by, iid))
```

> `_write_card_to_db` 的 `"active"` 硬编码**不动**（新卡入库本就 active，降级另走 demote）。

---

## 四、37 张回填明细

**条件**：`status='active' AND tier='precedent' AND superseded_by NOT NULL`

| 类别 | 数量 | 处置 |
|---|---|---|
| 归元仔系 corpus（7） | 7 | status→superseded |
| 专利单卡 runtime（指向总账卡 44d69231，26） | 26 | status→superseded，asset 不动 |
| 9b48551b（runtime，指向 32684974eb） | 1 | status→superseded + asset→corpus |
| 其他归元仔 runtime（029ff/e6ed/fc24 中的 runtime 部分） | 3 | status→superseded |
| **合计** | **37** | — |

> 说明：27 张专利卡（含 9b48551b 之外的 26 张）asset 保持 runtime 不变，仅 status→superseded。9b48551b 因正本 asset=corpus 且 runtime 会绕过 superseded_by 过滤，单独把 asset 改为 corpus。

---

## 五、影响面（已确认接受）

- 37 张卡 status=superseded 后，`load_memories()`（只认 status）不再召回
- **27 张专利单卡从 ask() 检索池收敛消失**（被总账卡 44d69231cca9 取代，总账卡保持 active 可检索）
- 版本溯源/审计/排查不受影响（DB+jsonl 完整留存，superseded_by 链可回溯）
- 归元仔系 7 张空壳 + 其他 runtime 3 张同样收敛（正本有 detail，符合治理预期）

---

## 六、回归结果（全 PASS）

| 套件 | 结果 |
|---|---|
| e7_gate_regression | **13 PASS / 0 FAIL** |
| e30_p0_regression | **17 PASS / 0 FAIL** |
| e30_p2_p3_regression | **13 PASS / 0 FAIL** |
| e46_demote_status_regression（新增） | **10 PASS / 0 FAIL** |
| **合计** | **53 PASS / 0 FAIL** |

新增用例覆盖：demote 后 jsonl/DB 双侧 status=superseded + tier=precedent、混叠态不产生、幂等 skipped、冲突 conflict 保护。

---

## 七、回滚方案

如需回滚：
```bash
cp data/memory/corpus_store.db.bak_46_20261124_130210 data/memory/corpus_store.db
cp data/memory/memories.jsonl.bak_46_20261124_130210 data/memory/memories.jsonl
```
代码回滚：恢复 demote L529 / _sync_demote_to_db L482 两行。

---

## 八、约束确认
- ✅ 零物理删除（全部 UPDATE 字段）
- ✅ tmp 副本先模拟，确认后 apply 真实库
- ✅ 真实库与 jsonl 双备份
- ✅ 混叠态清零（0 张）
- ✅ 全套回归 53 PASS / 0 FAIL
- ✅ DB-jsonl 完全对齐