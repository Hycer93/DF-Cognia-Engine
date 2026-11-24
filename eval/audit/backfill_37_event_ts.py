# -*- coding: utf-8 -*-
"""#37 event_ts 回填执行器（只写 strong+summary 可安全推导的 20 张）。
铁律：只写日期即本卡事件日的项；op/date/refdate/B 一律留空，绝不编造。
用法:
  python backfill_37_event_ts.py --tiers strong,summary            # dry-run
  python backfill_37_event_ts.py --tiers strong,summary --apply     # 真写
"""
import sqlite3, re, os, sys, io, json, shutil, datetime, argparse

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM = os.path.join(BASE, "data", "memory")
DB = os.path.join(MEM, "corpus_store.db")
JSONL = os.path.join(MEM, "memories.jsonl")

RE8 = re.compile(r"(20\d{2})(\d{2})(\d{2})")
RE_DASH = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
RE_CN = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})")

REFDATE_PREFIX = ("aa1d785b75", "ddd691156d")

def src_tier(src):
    s = (src or "").lower()
    if any(k in s for k in ("quarantine-restore", "watchlist-promote", "restore", "promote")):
        return "op"
    if "session" in s or "会话" in s:
        return "strong"
    return "date"

def extract(text):
    for rx in (RE8, RE_DASH, RE_CN):
        m = rx.search(text or "")
        if m:
            y, mo, d = m.groups()
            try:
                datetime.date(int(y), int(mo), int(d))
                return "%04d-%02d-%02d" % (int(y), int(mo), int(d))
            except Exception:
                continue
    return None

def classify(inst, src, summary):
    """返回 (tier, event_ts or None)。tier in strong/summary/op/date/refdate/B"""
    hs = extract(src) if src else None
    if hs:
        t = src_tier(src)
        return (t if t == "strong" else t), (hs if t == "strong" else None)
    hsum = extract(summary)
    if hsum:
        if any(inst.startswith(k) for k in REFDATE_PREFIX):
            return "refdate", None
        return "summary", hsum
    return "B", None

def build_targets(want_tiers):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT inst_id,source_ref,substr(summary,1,300) s FROM core_memory_card "
                     "WHERE (event_ts IS NULL OR event_ts='')").fetchall()
    c.close()
    targets = []
    stat = {}
    for r in rows:
        tier, ev = classify(r["inst_id"], r["source_ref"] or "", r["s"] or "")
        stat[tier] = stat.get(tier, 0) + 1
        if tier in want_tiers and ev:
            targets.append((r["inst_id"], ev, tier))
    return rows, targets, stat

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="strong,summary")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    want = set(t.strip() for t in a.tiers.split(",") if t.strip())

    rows, targets, stat = build_targets(want)
    print("[#37] event_ts 空值总数 = %d" % len(rows))
    print("      tier 分布:", stat)
    print("      本次写入 tiers=%s -> %d 张" % (sorted(want), len(targets)))
    if not a.apply:
        print("\n[DRY-RUN] 未写库。预览:")
        for iid, ev, tier in targets:
            print("  %-32s %s  <%s>" % (iid, ev, tier))
        print("\n--apply 执行。")
        return 0

    if not targets:
        print("无可写目标"); return 0

    # 备份
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dbak = DB + ".bak_37_" + ts
    jbak = JSONL + ".bak_37_" + ts
    shutil.copy2(DB, dbak); shutil.copy2(JSONL, jbak)

    # DB 写
    con = sqlite3.connect(DB, isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    try:
        for iid, ev, tier in targets:
            con.execute("UPDATE core_memory_card SET event_ts=? WHERE inst_id=? AND (event_ts IS NULL OR event_ts='')", (ev, iid))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise
    finally:
        con.close()

    # jsonl 同步
    tmap = {iid: ev for iid, ev, tier in targets}
    n = 0; out = []
    for line in io.open(JSONL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        if o.get("inst_id") in tmap and not o.get("event_ts"):
            o["event_ts"] = tmap[o["inst_id"]]; n += 1
        out.append(o)
    with io.open(JSONL, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    # 校验
    c = sqlite3.connect(DB)
    left = c.execute("SELECT COUNT(*) FROM core_memory_card WHERE event_ts IS NULL OR event_ts=''").fetchone()[0]
    c.close()
    print("\n[APPLY] DB 写入 %d 张, jsonl 同步 %d 张" % (len(targets), n))
    print("       备份: %s / %s" % (os.path.basename(dbak), os.path.basename(jbak)))
    print("       剩余 event_ts 空值: %d (期望 %d)" % (left, len(rows) - len(targets)))
    return 0

if __name__ == "__main__":
    sys.exit(main())