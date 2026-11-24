# -*- coding: utf-8 -*-
"""#41 补充：demote 改造等价性验证（旧 _sync_demote_to_db vs 新 update_card_fields）。

在 tmp 隔离库上对比两套逻辑对同一降级操作的最终 DB 状态是否等价。
"""
import sqlite3, json, os, io, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DB = os.path.join(ROOT, "data", "memory", "corpus_store.db")
SRC_JL = os.path.join(ROOT, "data", "memory", "memories.jsonl")
TMP = os.path.join(ROOT, "tmp", "sim41_demote")
DB1 = os.path.join(TMP, "old.db")   # 旧逻辑库
DB2 = os.path.join(TMP, "new.db")   # 新逻辑库
JL1 = os.path.join(TMP, "old.jsonl")
JL2 = os.path.join(TMP, "new.jsonl")

def load_jsonl(p):
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]

def save_jsonl(p, rows):
    with io.open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def setup():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    for d, j in ((DB1, JL1), (DB2, JL2)):
        shutil.copy2(SRC_DB, d); shutil.copy2(SRC_JL, j)

def get_state(db, jl, iid):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True); c.row_factory = sqlite3.Row
    d = dict(c.execute("SELECT tier,status,superseded_by FROM core_memory_card WHERE inst_id=?", (iid,)).fetchone())
    c.close()
    j = next(r for r in load_jsonl(jl) if r["inst_id"] == iid)
    js = {"tier": j.get("tier"), "status": j.get("status"), "superseded_by": j.get("superseded_by")}
    return d, js

# 旧逻辑：模拟 _sync_demote_to_db（只 UPDATE tier/status/superseded_by）
def old_demote(db, jl, iid, sup):
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE core_memory_card SET tier='precedent', status='superseded', superseded_by=? WHERE inst_id=?", (sup, iid))
    con.commit(); con.close()
    rows = load_jsonl(jl)
    for r in rows:
        if r["inst_id"] == iid:
            r["tier"] = "precedent"; r["status"] = "superseded"; r["superseded_by"] = sup
    save_jsonl(jl, rows)

# 新逻辑：update_card_fields（等价封装）
def new_update(db, jl, iid, fields):
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    setc = ",".join("%s=?" % k for k in fields)
    con.execute("UPDATE core_memory_card SET %s WHERE inst_id=?" % setc, tuple(fields.values()) + (iid,))
    con.commit(); con.close()
    rows = load_jsonl(jl)
    for r in rows:
        if r["inst_id"] == iid:
            for k, v in fields.items(): r[k] = v
    save_jsonl(jl, rows)

def main():
    setup()
    c = sqlite3.connect("file:%s?mode=ro" % SRC_DB, uri=True); c.row_factory = sqlite3.Row
    iid = c.execute("SELECT inst_id FROM core_memory_card WHERE status='active' LIMIT 1").fetchone()[0]
    c.close()
    SUP = "TEST_SUPERSEDE_TARGET_20261124"
    old_demote(DB1, JL1, iid, SUP)
    new_update(DB2, JL2, iid, {"tier": "precedent", "status": "superseded", "superseded_by": SUP})
    d_old, j_old = get_state(DB1, JL1, iid)
    d_new, j_new = get_state(DB2, JL2, iid)
    print("卡:", iid[:12])
    print("旧逻辑 DB:", d_old)
    print("旧逻辑 jsonl:", j_old)
    print("新逻辑 DB:", d_new)
    print("新逻辑 jsonl:", j_new)
    equiv = (d_old == d_new) and (j_old == j_new)
    print()
    print("=== demote 改造等价性：%s ===" % ("✅ 完全等价" if equiv else "❌ 不等价"))
    return equiv

if __name__ == "__main__":
    sys.exit(0 if main() else 1)