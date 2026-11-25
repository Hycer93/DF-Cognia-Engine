# #41 统一 UPDATE + jsonl 同步 —— DRY-RUN 模拟验证报告

- 日期：2026-11-24 14:05
- 性质：**模拟验证，全部跑在 tmp 隔离库；真实 DB/jsonl 零改动**
- 验证脚本：`eval/audit/sim_41_unified_update.py` + `eval/audit/sim_41_demote_equiv.py`
- 结论：**✅ 三项验证全部通过，demote 改造完全等价**

---

## 〇、隔离与真库保护

| 项 | 值 |
|---|---|
| 隔离目录 | `tmp/sim41/`（副本 DB+jsonl，基线 323 条）|
| 真库 `corpus_store.db` md5 | `5518a987f66b7e522fa4bbbd28c2e9fd`（前后不变）|
| 真库 `memories.jsonl` md5 | `71515d2e0bb732e6184208c2df67247d`（前后不变）|
| 回归 | 69 PASS / 0 FAIL（e7_gate 13 / e30_p0 17 / e30_p2_p3 13 / e46 10 / e44 16）|

---

## 一、模拟代码片段

### 1.1 update_card_fields（统一封装，拟落地到 memory_organize.py）

```python
def update_card_fields(inst_id, fields, sync_jsonl=True):
    """统一封装：UPDATE core_memory_card 单卡字段 + 自动同步 jsonl。"""
    if not fields:
        return False, "空字段"
    allowed = {"event_ts", "memory_class", "status", "tier", "superseded_by", "detail"}
    bad = set(fields) - allowed
    if bad:
        return False, "非法字段:%s" % bad
    set_clause = ",".join("%s=?" % k for k in fields)
    args = tuple(fields.values()) + (inst_id,)
    con = None
    try:
        con = sqlite3.connect(DB, timeout=5, isolation_level=None)
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("UPDATE core_memory_card SET %s WHERE inst_id=?" % set_clause, args)
        if cur.rowcount == 0:
            con.execute("ROLLBACK"); con.close()
            return False, "卡不存在:%s" % inst_id
        con.commit()
    except Exception as e:
        if con:
            try: con.rollback()
            except Exception: pass
        _err(inst_id, fields, e)          # 落 dual_write_errors.jsonl
        return False, repr(e)
    finally:
        if con:
            try: con.close()
            except Exception: pass
    # 同步 jsonl（按 inst_id patch）
    if sync_jsonl:
        try:
            rows = load_jsonl(JL)
            hit = jl_get(rows, inst_id)
            if hit is None:
                _err(inst_id, fields, "jsonl无此卡,DB-jsonl将漂移")
                return True, "DB已更但jsonl无此卡"
            for k, v in fields.items():
                hit[k] = v
            save_jsonl(JL, rows)
        except Exception as e:
            _err(inst_id, fields, e)
            return True, "DB已更但jsonl同步失败:%r" % e
    return True, "OK"
```

**设计要点**：
- 白名单字段（防误改 ts/full_hash 等不可变字段）
- BEGIN IMMEDIATE + busy_timeout（与 `_write_card_to_db` 同款加固）
- rowcount==0 保护（卡不存在不写）
- jsonl 缺失卡 / 同步失败 → 落 `dual_write_errors.jsonl`，不静默

### 1.2 backfill_jsonl_from_db --full-sync（新增模式，保留 --nightly append-only）

```python
KEY_FIELDS = ["status", "tier", "superseded_by", "memory_class", "event_ts"]

def full_sync():
    """已存在 inst_id 比对关键字段，DB 为准覆盖；新增 append。"""
    rows = load_jsonl(JL); byid = {r["inst_id"]: r for r in rows}
    fixed, appended, unchanged = [], [], 0
    for d in db_all_cards():
        iid = d["inst_id"]
        if iid in byid:
            ch = False
            for k in KEY_FIELDS:
                if norm(d[k]) != norm(byid[iid].get(k)):
                    byid[iid][k] = d[k]; ch = True
            fixed.append(iid) if ch else None
            unchanged += 0 if ch else 1
        else:
            byid[iid] = db_row_to_entry(d); appended.append(iid)
    save_jsonl(JL, rows)
    return {"fixed": fixed, "appended": appended, "unchanged": unchanged}
```

- `--nightly`（原）：**保持 append-only，只补缺失 inst_id**（兼容旧夜间任务）
- `--full-sync`（新）：**对已存在卡也修关键字段漂移**，DB 为准
- 二者可并存，nightly_run 可择机切 full-sync

### 1.3 回填脚本模板（每个 backfill_*.py --apply 末尾）

```python
# --apply 完成后无条件全量对齐，防字段漂移
from eval.audit import backfill_jsonl_from_db as BF
r = BF.full_sync()
print("[full-sync] fixed=%d appended=%d unchanged=%d" %
      (len(r["fixed"]), len(r["appended"]), r["unchanged"]))
```

---

## 二、测试用例执行结果

| 用例 | 操作 | 预期 | 结果 |
|---|---|---|---|
| ① 裸 UPDATE 制造漂移 | `UPDATE ... SET memory_class='scar_case'` 不碰 jsonl | DB 改、jsonl 未改 → 漂移成立 | ✅ DB='scar_case' / jsonl='strategy_evolve' |
| ② update_card_fields 同步 | 更新另一卡 event_ts=2026-01-01 | DB==jsonl==新值 | ✅ 同步一致 |
| ②附加 非法字段保护 | 传 `{"ts": "x"}` | 拒绝 | ✅ 返回 False「非法字段:{'ts'}」|
| ③ full-sync 修复 | 对用例①卡跑 full_sync | 漂移被修正、A 入 fixed | ✅ fixed=1（正是 A），jsonl 修回 'scar_case' |
| ③附加 不误改 | 用例②卡 B 不应被动 | B 保持 event_ts=2026-01-01 | ✅ |
| 终检 | 全量 323 条关键字段比对 | 0 漂移 | ✅ 0 漂移 |
| demote 等价性 | 旧 `_sync_demote_to_db` vs 新 `update_card_fields` | 最终 DB+jsonl 状态一致 | ✅ 完全等价 |

**测试输出摘录**：
```
用例①  DB.memory_class='scar_case' jsonl.memory_class='strategy_evolve' → 漂移=True ✅
用例②  返回 ok=True detail=OK / DB.event_ts='2026-01-01' jsonl='2026-01-01' → ✅
        非法字段保护：ok=False detail=非法字段:{'ts'} ✅
用例③  full-sync: fixed=1 张, appended=0, unchanged=322
        用例①卡修复后一致=True ✅  A 在 fixed 列表 ✅  卡 B 未误改 ✅
终检    DB 323 条，关键字段漂移 0 条 → ✅
真库保护 源 DB 323 条（未动）✅
=== 总判定：✅ 全部通过 ===
```

---

## 三、风险点

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | `update_card_fields` 同步 jsonl 时若 jsonl 缺该卡（DB 独有），只能落 dual_write_errors，无法补全 | 中 | 提示「jsonl无此卡」，靠 full-sync append 补；不阻断 DB 已更 |
| R2 | full-sync 以 DB 为准覆盖 jsonl，若有人误改 jsonl 作"手工档案"会被覆盖 | 低 | 符合"DB=唯一真相"定案（memory_organize 头注释已明示）|
| R3 | full-sync 全量遍历 323 条 + 重写 jsonl，每次 O(n)；将来万级卡需评估耗时 | 低 | 当前 323 条毫秒级；可后续改增量 |
| R4 | 回填脚本末尾调 full-sync 会增加一次 jsonl 全量重写 | 低 | 幂等、有备份；回填本就是低频操作 |
| R5 | demote 改造复用 update_card_fields 时，需保留 conflict 整批回滚语义 | 中 | update_card_fields 单卡；demote 的多卡 conflict 逻辑在外层保留，逐卡调用 |

---

## 四、改动清单（拟落地，本次未改真实代码）

| 文件 | 改动 | 影响 |
|---|---|---|
| memory/memory_organize.py | 新增 `update_card_fields()`；demote 的 `_sync_demote_to_db` 可选改为调用它 | 业务主路径 |
| eval/audit/backfill_jsonl_from_db.py | 新增 `--full-sync` 模式（保留 `--nightly` append-only）| 兜底升级 |
| eval/audit/backfill_34/37/46 等 | `--apply` 末尾无条件调 full_sync | 审计脚本 |
| scripts/nightly_run.py | 可将 `--nightly` 升级为 `--full-sync`（可选）| 夜间任务 |

> 本次**全部未落地**，仅产出模拟脚本 `sim_41_*.py` + 报告。待用户确认后再 real apply。

---

## 五、回归建议（落地后执行）

1. 新增 `e41_unified_update_regression.py`：覆盖 update_card_fields 正/异常、full-sync 修复漂移、demote 等价。
2. 全套回归 e7/e30_p0/e30_p2_p3/e46/e44 + 新 e41 一起跑，要求全 PASS。
3. 落地前对真实库跑一次 `backfill_jsonl_from_db.py --full-sync`（dry-run 先看 fixed 列表，确认为 0 或仅预期项）。
4. 监控 `dual_write_errors.jsonl` 是否新增条目。

---

## 六、约束确认
- ✅ 全程 tmp 隔离库，真实 DB/jsonl md5 前后不变
- ✅ 三项验证 + demote 等价性全部通过
- ✅ 未落地任何真实代码改动（仅模拟脚本）
- ✅ 回归 69 PASS / 0 FAIL
- ⏳ 待用户评估后决定是否落地生产代码