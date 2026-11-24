# eval/audit/e46_demote_status_regression.py — #46 demote 语义对齐回归
# 目的: 防 status=active + tier=precedent 混叠态复现。
# 断言: demote() 调用后, 目标卡 jsonl 侧 status='superseded' && tier='precedent',
#       DB 侧 status='superseded' && tier='precedent' && superseded_by 正确。
# 隔离测试: 复制主库→临时库, 重定向 MEM_DIR, 不碰主库。
# 跑法: python eval/audit/e46_demote_status_regression.py
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

MAIN_DB = os.path.join(HERE, "data", "memory", "corpus_store.db")
NOW = datetime.now().isoformat(timespec="seconds")

print("== #46 demote 语义对齐回归 ==")

tmpdir = tempfile.mkdtemp(prefix="t46_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
con.execute("DELETE FROM core_memory_card")   # 干净库


def mk_card(iid, summary, *, asset_class="corpus", status="active",
            tier="active", detail="d", superseded_by=None):
    con.execute(
        "INSERT OR REPLACE INTO core_memory_card "
        "(inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, "
        "summary, detail, source_ref, tier, status, superseded_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, NOW, "h" + iid[-8:], asset_class, "situational", "L1", "{}",
         summary, detail, "test", tier, status, superseded_by))


# 布局: 一张正本 active 卡 + 一张待降级卡
KEEP = "a1000000000000000000000000000001"
SHELL = "b2000000000000000000000000000002"
SHELL_SUM = "demote语义对齐测试: 蒸馏仔第一次被欺骗,G07骨架诞生现场"
mk_card(KEEP, SHELL_SUM, asset_class="corpus", tier="active", status="active",
        detail="正本详情")
mk_card(SHELL, SHELL_SUM, asset_class="corpus", tier="active", status="active",
        detail="")
con.commit()
con.close()

# 同步 jsonl(要 demote 走 jsonl 读写, 先把两张卡写进 jsonl)
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("SELECT * FROM core_memory_card")]
con.close()
# 补齐 demote 读 jsonl 需要的字段(demote 用 load_memories 读 jsonl)
import io
with io.open(mo.MEMORIES_JSONL, "w", encoding="utf-8") as f:
    for r in rows:
        o = {
            "inst_id": r["inst_id"], "ts": r["ts"], "event_ts": r.get("event_ts"),
            "full_hash": r.get("full_hash"), "asset_class": r.get("asset_class"),
            "repro": r.get("repro"), "confidence": r.get("confidence"),
            "dfcp_tag": json.loads(r["dfcp_tag"]) if r.get("dfcp_tag") else {},
            "affect_tags": None, "summary": r.get("summary"),
            "summary_converged": r.get("summary_converged"), "detail": r.get("detail"),
            "source_ref": r.get("source_ref"), "origin_ids": [],
            "entity_id": r.get("entity_id"), "derived_from": r.get("derived_from"),
            "tier": r.get("tier"), "weight": {}, "protected": r.get("protected"),
            "status": r.get("status"), "memory_class": r.get("memory_class"),
            "superseded_by": r.get("superseded_by"),
        }
        f.write(json.dumps(o, ensure_ascii=False) + "\n")

# ---- 用例1: demote 后 jsonl 侧 status/tier 同步 ----
res = mo.demote([SHELL], KEEP, reason="e46-regression", calibration_source="e46")
check("用例1a: demote 返回 applied 含 SHELL", SHELL in res.get("applied", []), str(res))
jb = {}
with io.open(mo.MEMORIES_JSONL, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            o = json.loads(line)
            if o["inst_id"] == SHELL:
                jb = o
check("用例1b: jsonl 侧 tier=='precedent'", jb.get("tier") == "precedent", str(jb.get("tier")))
check("用例1c: jsonl 侧 status=='superseded'（#46 核心断言，防混叠）",
      jb.get("status") == "superseded", f"实际={jb.get('status')}")
check("用例1d: jsonl 侧 superseded_by==KEEP", jb.get("superseded_by") == KEEP,
      str(jb.get("superseded_by")))

# ---- 用例2: demote 后 DB 侧 status/tier 同步 ----
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
db_row = dict(con.execute("SELECT * FROM core_memory_card WHERE inst_id=?", (SHELL,)).fetchone())
con.close()
check("用例2a: DB 侧 tier=='precedent'", db_row.get("tier") == "precedent", str(db_row.get("tier")))
check("用例2b: DB 侧 status=='superseded'（#46 核心断言）",
      db_row.get("status") == "superseded", f"实际={db_row.get('status')}")
check("用例2c: DB 侧 superseded_by==KEEP", db_row.get("superseded_by") == KEEP,
      str(db_row.get("superseded_by")))

# ---- 用例3: 混叠态不产生(active+precedent 共存判定) ----
con = sqlite3.connect(test_db, timeout=5)
mixed = con.execute(
    "SELECT COUNT(*) FROM core_memory_card WHERE status='active' AND tier='precedent'").fetchone()[0]
con.close()
check("用例3: demote 后无 status=active+tier=precedent 混叠卡", mixed == 0, f"残留={mixed}")

# ---- 用例4: 幂等(重复 demote 同 superseded_by → skipped, 不改坏) ----
res2 = mo.demote([SHELL], KEEP, reason="e46-again", calibration_source="e46")
check("用例4a: 重复 demote 同目标 → skipped", SHELL in res2.get("skipped", []), str(res2))

# ---- 用例5: 冲突保护(不同 superseded_by → conflict, 不落盘) ----
res3 = mo.demote([SHELL], "c3000000000000000000000000000003", reason="e46-conflict",
                 calibration_source="e46")
check("用例5: 不同 superseded_by → conflict 不改", SHELL in res3.get("conflicts", []), str(res3))

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("#46 demote 语义对齐回归全过")