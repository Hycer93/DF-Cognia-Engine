# #40 全写入入口 jsonl 同步巡检 —— 只读审计报告

- 日期：2026-11-24 13:55
- 性质：**只读审计，未改任何代码/数据**
- 目的：找出所有修改 `core_memory_card` 的位置，判定是否同步 `memories.jsonl`，为 #41 根因定位提供依据
- 实测：当前 DB 323 条 / jsonl 323 条，**字段级漂移 = 0**（当前一致，但机制存在缺口）

---

## 一、系统同步机制全景（三层）

| 层 | 机制 | 状态 |
|---|---|---|
| L1 写入即同步 | `atomic_store`（append jsonl + INSERT db）、`demote`（重写 jsonl + UPDATE db）、`green_card_writer`（INSERT db + append jsonl）| 3 条主路径已双向同步 |
| L2 夜间兜底 | `nightly_run.py` 每晚调 `backfill_jsonl_from_db.py --nightly`：**只补 jsonl 缺失的 inst_id**（append-only）| 存在，但**只补新增，不修已存在卡的字段漂移** |
| L3 手工/审计脚本 | 各 `backfill_*.py` 自行处理 jsonl | 质量参差 |

**关键结论（#41 根因伏笔）**：L2 兜底是 **append-only、按 inst_id 判缺失**。若某卡已存在于 jsonl，但 DB 侧字段（event_ts/memory_class/status/tier/superseded_by 等）被 UPDATE 改动，**L2 不会感知、不会修** —— 这就是 #41「DB 真值 vs jsonl 快照字段级漂移」的结构性缺口。

---

## 二、全量写入入口清单

### A. 业务主代码（memory/、cgraph/）—— 高风险关注区

| # | 文件:行号 | 函数 | 操作 | 场景 | 同步jsonl | 方式 | 风险 |
|---|---|---|---|---|---|---|---|
| 1 | memory/memory_organize.py:305 | `atomic_store` | INSERT | 业务入库 | ✅ | append_jsonl 先写 + INSERT db 后写 | 低 |
| 2 | memory/memory_organize.py:521 | `_sync_demote_to_db` | UPDATE | demote 降级 | ✅ | demote 先全量重写 jsonl + 此函数 UPDATE db（#46 已修）| 低 |
| 3 | cgraph/green_card_writer.py:173 | `commit_green_cards` | INSERT | 绿档落库 | ✅ | DB COMMIT 后 `_sync_committed_to_jsonl` append（#33 已修）| 低 |
| 4 | memory/memory_organize.py:378 | `change_code` | **仅写 CHANGELOG** | 字段变更留痕 | N/A | 写 changelog.jsonl，**不碰 core_memory_card** | 低（当前无人调用，仅回归断言里出现）|
| 5 | memory/schema_migrate.py:85,100 | `_m0001_enum_normalize` | UPDATE | 枚举规范化(脏值→NULL/浮点→L档) | ❌ | **仅 DB，不同步 jsonl** | **中**（一次性迁移，但确属缺口）|

### B. eval/audit 治理/回填脚本

| # | 文件:行号 | 函数 | 操作 | 场景 | 同步jsonl | 方式 | 风险 |
|---|---|---|---|---|---|---|---|
| 6 | backfill_34_memory_class.py:141 | main --apply | UPDATE memory_class | #34 回填 | ✅ | DB 后全量重写 jsonl | 低 |
| 7 | backfill_37_event_ts.py:100 | main --apply | UPDATE event_ts | #37 回填 | ✅ | DB 后逐条 patch + 全量重写 | 低 |
| 8 | backfill_46_status_realign.py:75 | main --apply | UPDATE status/tier | #46 回填 | ✅ | DB 后全量重写 jsonl | 低 |
| 9 | backfill_jsonl_from_db.py | main --apply/--nightly | **只写 jsonl** | DB→jsonl 增量回补 | N/A | append-only 补缺失 inst_id（**不修字段漂移**）| 中（兜底不完整）|
| 10 | memory_consistency_fix.py:142,156 | main --apply | UPDATE tier/superseded_by + INSERT | #14 一致性对账 | ❌ | **明确"不处理 jsonl 侧"**（注释 L7-8）| **中**（历史一次性，已跑完）|
| 11 | pending_writeback_regression.py | 测试 | UPDATE | 回归 | N/A | tmpdir 隔离 | 低 |
| 12 | topic_layer_regression / f40 / e7 / e30_* / e44 / e46 等回归 | 测试 | INSERT/DELETE | 回归 | N/A | tmpdir 隔离或测试专用 | 低 |

### C. scripts/（含 oneoff 治理）

| # | 文件:行号 | 函数 | 操作 | 同步jsonl | 风险 |
|---|---|---|---|---|---|
| 13 | oneoff/20261123_governance/fix_quarantine_20261123.py:174 | 重跑 `atomic_store` 入库 | INSERT（经 atomic_store）| ✅ 走 atomic_store 同步 | 低 |
| 14 | oneoff/.../backfill_suggested_class_20261123.py:101 | UPDATE **core_class_backfill_pending**（非 core_memory_card）| N/A | 低（不碰主表）|
| 15 | oneoff/.../p3_governor_verdict_20261123.py:68 | UPDATE **core_governor_pending**（非主表）| N/A | 低（不碰主表）|
| 16 | scripts/f40_cross_cluster_eval.py | **只读**（mode=ro，仅写题库 jsonl）| N/A | 低 |
| 17 | scripts/migrate_*_to_store.py | 产出数据文件，不直写 core_memory_card | N/A | 低 |

### D. eval/core（守卫/探针）

| # | 文件:行号 | 操作 | 同步jsonl | 风险 |
|---|---|---|---|---|
| 18 | eval/core/e0_topic_gate_guard.py:71 | DELETE（探针自清理）| 独立清 jsonl（`_purge_memories_jsonl`）| 低（测试探针）|

---

## 三、统计

| 分类 | 数量 |
|---|---|
| 总写入位置（触及 core_memory_card）| **8**（其余为只读/写别的表/测试）|
| 整同步（业务主路径）| 3（atomic_store / demote / green_card_writer）|
| **缺失或部分同步** | **3**（schema_migrate UPDATE、memory_consistency_fix、backfill_jsonl 兜底不修字段）|
| 只写 jsonl 不写 DB（设计如此）| 1（backfill_jsonl_from_db，兜底）|
| 测试/隔离 | 多个（低风险）|

---

## 四、#41 根因定位（基于本巡检）

### 4.1 结构性缺口（真根因）

**`memory_organize.py` 没有统一的「UPDATE 后同步 jsonl」封装**。现状：
- `atomic_store` = INSERT 路径，有 jsonl 同步（append 在前，天然对上）。
- `demote` = 唯一有专用同步的 UPDATE 路径（#46 手工加的 `_sync_demote_to_db` + 全量重写）。
- 除 demote 外，**任何未来的 UPDATE 路径（hold 转正 / isolate 隔离 / 状态修正 / 字段订正）都没有任何同步脚手架可用**，写库的人只能自己记得重写 jsonl，极易漏。

### 4.2 为什么现在没漂移却仍有缺陷

- 当前漂移=0：因为 (a) 历史 UPDATE 多由带同步的审计脚本完成；(b) nightly 兜底补了缺失 inst_id。
- 但 **nightly 兜底只按 inst_id 判缺失、append-only**，**对"已存在卡的字段被 UPDATE"完全免疫**。一旦有代码直接 `UPDATE core_memory_card SET memory_class=...` 而不重写 jsonl，DB 立即偏离 jsonl 且 nightly 不会修 —— 这是悬着的雷。

### 4.3 用户提到的 hold/isolate/quarantine-restore/watchlist-promote 现状澄清

经核查：**memory_organize.py 里目前没有 hold/isolate 的 core_memory_card 写入函数**。这些操作实际落在**其它表**（core_hold_pool / core_governor_pending / core_quarantine），或由 oneoff 治理脚本（fix_quarantine 走 atomic_store）完成。所以「hold/isolate UPDATE 漏同步」当前不成立，但**一旦将来在主表实现这些状态变更，就会踩 4.1 的坑**。

---

## 五、修复建议（#41，待评估后再做）

### 5.1 业务主代码：统一封装

在 `memory_organize.py` 新增统一的字段更新辅助函数，例如：

```
def update_card_fields(inst_id, fields: dict, sync_jsonl=True):
    """统一 UPDATE core_memory_card 单卡字段，并自动同步 jsonl。"""
    # 1) DB 事务 UPDATE（BEGIN IMMEDIATE）
    # 2) 读回该卡 → 全量重写或按 inst_id patch memories.jsonl
    # 3) 失败回滚 + 落 dual_write_errors.jsonl
```

所有 UPDATE 路径（含未来 hold/isolate/状态修正）统一走它，杜绝"记得手动重写 jsonl"。`demote` 也可改为复用它（减少 `_sync_demote_to_db` 的特化逻辑）。

### 5.2 eval 审计脚本：执行后强制全量同步

要求每个 `backfill_*.py --apply` 结束时**无条件调用**一次「DB→jsonl 全量对齐」（不是 append-only 补缺失，而是**按 inst_id 把 DB 字段覆盖回 jsonl**，修字段漂移）。可把 `backfill_jsonl_from_db.py` 升级为支持 `--mode full-sync`（全字段覆盖），审计脚本末尾调用。

### 5.3 nightly 兜底升级

`backfill_jsonl_from_db.py` 当前 append-only 补缺失；建议新增 `--full-sync` 模式：对已存在 inst_id 也比对关键字段（event_ts/memory_class/status/tier/superseded_by），不一致则以 DB 为准覆盖 jsonl。让 nightly 真正兜住字段漂移。

---

## 六、约束确认
- ✅ 纯只读审计，未改任何代码/DB/jsonl
- ✅ 实测当前 DB==jsonl 字段级漂移=0（机制缺口 ≠ 当前已漂移）
- ⏳ 修复方案待用户评估改动范围后再做 dry-run 模拟
- 🔲 #39 组8锚点 detail 仍挂起；#38 P0 观测后台运行

---

## 七、结论一句话

**当前数据一致（漂移0），但 `memory_organize.py` 除 demote 外无通用「UPDATE 后同步 jsonl」封装，nightly 兜底只补新增不修字段漂移 —— 这是 #41 的真根因。** 修复方向：业务侧统一封装 update_card_fields + 审计脚本强制全量对齐 + nightly 支持 full-sync。