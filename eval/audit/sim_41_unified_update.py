# -*- coding: utf-8 -*-
"""#41 统一 UPDATE + jsonl 同步 —— DRY-RUN 模拟验证（全部跑在 tmp 隔离库）。

验证三件事：
  ① update_card_fields() 统一封装行为正确（DB UPDATE + jsonl 同步）
  ② 裸 UPDATE 制造字段漂移 → backfill --full-sync 能修复
  ③ --full-sync 逻辑正确（只修已存在 inst_id 的关键字段漂移 + 新卡 append）

⚠ 红线：真实 data/memory/corpus_store.db 与 memories.jsonl 完全不碰。
   本脚本把二者复制到 tmp/sim41/ 后只在副本上操作。
"""
import sqlite3, json, os, io, shutil, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DB = os.path.join(ROOT, "data", "memory", "corpus_store.db")
SRC_JL = os.path.join(ROOT, "data", "memory", "memories.jsonl")
TMP = os.path.join(ROOT, "tmp", "sim41")
DB = os.path.join(TMP, "corpus_store.db")
JL = os.path.join(TMP, "memories.jsonl")
ERRLOG = os.path.join(TMP, "dual_write_errors.jsonl")

KEY_FIELDS = ["status", "tier", "superseded_by", "memory_class", "event_ts"]
RESULT = []

def log(msg=""):
    print(msg)
    RESULT.append(msg)

# ---------- 工具 ----------
def load_jsonl(path):
    rows = []
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows

def save_jsonl(path, rows):
    with io.open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def db_get(inst_id):
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True); c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM core_memory_card WHERE inst_id=?", (inst_id,)).fetchone()
    c.close()
    return dict(r) if r else None

def jl_get(rows, inst_id):
    for r in rows:
        if r.get("inst_id") == inst_id:
            return r
    return None

def norm(v):
    return "" if v is None else str(v)

# ---------- 模拟 1：update_card_fields ----------
def update_card_fields(inst_id, fields, sync_jsonl=True):
    """统一封装：UPDATE core_memory_card 单卡字段 + 自动同步 jsonl。
    返回 (ok, detail)。异常落 dual_write_errors.jsonl。"""
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
        _err(inst_id, fields, e)
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

def _err(inst_id, fields, e):
    try:
        with io.open(ERRLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                                "op": "update_card_fields", "inst_id": inst_id,
                                "fields": list(fields), "err": str(e)}, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ---------- 模拟 2：backfill --full-sync ----------
def db_row_to_entry(d):
    """DB row(dict) → jsonl entry（关键字段 + 主要字段）。"""
    def _j(v, dv):
        if v is None: return dv
        try: return json.loads(v)
        except Exception: return dv
    return {
        "inst_id": d["inst_id"], "ts": d["ts"], "event_ts": d["event_ts"],
        "full_hash": d["full_hash"], "asset_class": d["asset_class"], "repro": d["repro"],
        "confidence": d["confidence"], "dfcp_tag": _j(d["dfcp_tag"], {}),
        "affect_tags": _j(d["affect_tags"], None) if d["affect_tags"] else None,
        "summary": d["summary"], "summary_converged": bool(d["summary_converged"]) if d["summary_converged"] is not None else None,
        "detail": d["detail"], "source_ref": d["source_ref"],
        "origin_ids": _j(d["origin_ids"], []), "entity_id": d["entity_id"],
        "derived_from": _j(d["derived_from"], None), "tier": d["tier"],
        "weight": d["weight"], "protected": bool(d["protected"]),
        "status": d["status"], "superseded_by": d["superseded_by"],
        "memory_class": d["memory_class"],
    }

def full_sync():
    """遍历 DB 全卡：已存在 inst_id 比对关键字段，DB为准覆盖；新增 append。
    返回 dict(fixed=[], appended=[], unchanged=n)。"""
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True); c.row_factory = sqlite3.Row
    rows = load_jsonl(JL)
    byid = {r.get("inst_id"): r for r in rows}
    fixed, appended, unchanged = [], [], 0
    for d in c.execute("SELECT * FROM core_memory_card"):
        dd = dict(d); iid = dd["inst_id"]
        if iid in byid:
            ch = False
            for k in KEY_FIELDS:
                if norm(dd[k]) != norm(byid[iid].get(k)):
                    byid[iid][k] = dd[k]; ch = True
            if ch:
                fixed.append(iid)
            else:
                unchanged += 1
        else:
            byid[iid] = db_row_to_entry(dd); appended.append(iid)
    c.close()
    save_jsonl(JL, rows)
    return {"fixed": fixed, "appended": appended, "unchanged": unchanged}

# ---------- 初始化 tmp ----------
def setup():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    shutil.copy2(SRC_DB, DB)
    shutil.copy2(SRC_JL, JL)

def main():
    setup()
    log("# #41 统一 UPDATE + jsonl 同步 —— DRY-RUN 模拟验证")
    log("- 时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    log("- 隔离库：tmp/sim41/（真实库未动）")
    src_rows = load_jsonl(SRC_JL)
    c0 = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    n0 = c0.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]; c0.close()
    log("- 副本基线：DB %d 条 / jsonl %d 条" % (n0, len(src_rows)))
    log()

    # 选两张 active 卡做用例
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True); c.row_factory = sqlite3.Row
    cards = [dict(r) for r in c.execute(
        "SELECT inst_id,memory_class,event_ts FROM core_memory_card WHERE status='active' LIMIT 2")]
    c.close()
    cardA, cardB = cards[0], cards[1]
    log("用例选卡：A=%s(mc=%s)  B=%s(ev=%s)" % (
        cardA["inst_id"][:12], cardA["memory_class"], cardB["inst_id"][:12], cardA["event_ts"]))
    log()

    # ===== 用例①：裸 UPDATE 制造漂移 =====
    log("## 用例① 裸 UPDATE 制造字段漂移（不碰 jsonl）")
    newA = "scar_case" if cardA["memory_class"] != "scar_case" else "strategy_evolve"
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("UPDATE core_memory_card SET memory_class=? WHERE inst_id=?", (newA, cardA["inst_id"]))
    con.commit(); con.close()
    dbA = db_get(cardA["inst_id"]); jlA = jl_get(load_jsonl(JL), cardA["inst_id"])
    driftA = norm(dbA["memory_class"]) != norm(jlA["memory_class"])
    log("  DB.memory_class=%r jsonl.memory_class=%r → 漂移=%s %s" % (
        dbA["memory_class"], jlA["memory_class"], driftA, "✅人为漂移成立" if driftA else "❌"))
    log()

    # ===== 用例②：update_card_fields 同步 =====
    log("## 用例② update_card_fields() 更新另一张卡（应 DB+jsonl 同步）")
    newEv = "2026-01-01"
    ok, detail = update_card_fields(cardB["inst_id"], {"event_ts": newEv}, sync_jsonl=True)
    dbB = db_get(cardB["inst_id"]); jlB = jl_get(load_jsonl(JL), cardB["inst_id"])
    syncB = norm(dbB["event_ts"]) == norm(jlB["event_ts"]) == newEv
    log("  返回 ok=%s detail=%s" % (ok, detail))
    log("  DB.event_ts=%r jsonl.event_ts=%r → 同步一致=%s %s" % (
        dbB["event_ts"], jlB["event_ts"], syncB, "✅" if syncB else "❌"))
    # 非法字段保护
    okBad, detBad = update_card_fields(cardB["inst_id"], {"ts": "x"})
    log("  非法字段保护：ok=%s detail=%s %s" % (okBad, detBad, "✅" if not okBad else "❌"))
    log()

    # ===== 用例③：full-sync 修复漂移 =====
    log("## 用例③ backfill --full-sync 修复用例①的漂移")
    res = full_sync()
    jlA2 = jl_get(load_jsonl(JL), cardA["inst_id"]); dbA2 = db_get(cardA["inst_id"])
    fixedA = norm(jlA2["memory_class"]) == norm(dbA2["memory_class"])
    log("  full-sync: fixed=%d 张, appended=%d, unchanged=%d" % (
        len(res["fixed"]), len(res["appended"]), res["unchanged"]))
    log("  用例①卡 A 修复后：DB.mc=%r jsonl.mc=%r → 一致=%s %s" % (
        dbA2["memory_class"], jlA2["memory_class"], fixedA, "✅" if fixedA else "❌"))
    log("  A 是否出现在 fixed 列表：%s" % ("✅" if cardA["inst_id"] in res["fixed"] else "❌"))
    # 用例②卡 B 不应被 full-sync 改坏
    jlB2 = jl_get(load_jsonl(JL), cardB["inst_id"])
    keepB = norm(jlB2["event_ts"]) == newEv
    log("  用例②卡 B 未被误改：jsonl.event_ts=%r %s" % (jlB2["event_ts"], "✅" if keepB else "❌"))
    log()

    # ===== 全量漂移终检 =====
    log("## 终检：全量 DB vs jsonl 关键字段")
    rows = load_jsonl(JL); byid = {r.get("inst_id"): r for r in rows}
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True); c.row_factory = sqlite3.Row
    total = 0; drift_final = 0
    for d in c.execute("SELECT * FROM core_memory_card"):
        total += 1; dd = dict(d); j = byid.get(dd["inst_id"])
        if not j:
            drift_final += 1; continue
        for k in KEY_FIELDS:
            if norm(dd[k]) != norm(j.get(k)):
                drift_final += 1; break
    c.close()
    log("  DB %d 条，关键字段漂移 %d 条 → %s" % (total, drift_final, "✅ 0 漂移" if drift_final == 0 else "❌"))
    log()

    # ===== 真实库未被触碰确认 =====
    c = sqlite3.connect("file:%s?mode=ro" % SRC_DB, uri=True)
    src_now = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]; c.close()
    real_untouched = (src_now == n0)  # 数量同；实际数据内容未被写（只读连接）
    log("## 真库保护：源 DB 条数 %d（基线 %d）→ %s" % (
        src_now, n0, "✅ 未动" if real_untouched else "❌"))

    # 汇总
    allpass = driftA and syncB and (not okBad) and fixedA and (cardA["inst_id"] in res["fixed"]) and keepB and drift_final == 0
    log()
    log("=== 总判定：%s ===" % ("✅ 全部通过" if allpass else "❌ 有失败项"))
    return allpass

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)