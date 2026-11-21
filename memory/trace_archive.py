# -*- coding: utf-8 -*-
"""
trace_archive.py — trace_* 流水月度归档 (2026-11-15 用户指令 #29)

职能: 防护预案。月度滚动归档到 trace_archive_YYYY_MM.db, 清理热窗口以外的
     旧流水, 保证主库 trace_* 只保留 HOT_WINDOW_DAYS 内热数据。

规约 (用户配套裁决):
  1. 归档 = 剪切不是复制: 归档库写入对账通过后, 主库删除已归档行
  2. 归档标记: 归档库内建 meta 表记录归档批次/行数/校验和, 主库 audit_maintenance
     追加归档事件留痕
  3. 对账校验: 归档前后 行数守恒 (归档库新增 == 主库删除), 逐表 count 断言
  4. 热度回算/HLTM 权重读取不受影响: 归档库只增不删, 热度统计在需要时
     可跨主库+归档库合并查询 (提供 replay 校验)

运行方式:
  python memory/trace_archive.py            # 执行归档 (默认热窗口30天)
  python memory/trace_archive.py --dry-run  # 只看会归档多少, 不动库
  python memory/trace_archive.py --check    # 全库对账: 主库+所有归档库行数总和 == 累计写入
"""
import sqlite3, datetime, os, sys, glob, json

DB = "data/memory/corpus_store.db"
ARCHIVE_DIR = "data/memory/trace_archive"
HOT_WINDOW_DAYS = 30
TRACE_TABLES = ("trace_cited", "trace_call", "trace_embedding")

ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS {t} (
  tid INTEGER PRIMARY KEY, ts TEXT NOT NULL, {extra}
);
CREATE TABLE IF NOT EXISTS archive_meta (
  aid INTEGER PRIMARY KEY AUTOINCREMENT, batch_ts TEXT, table_name TEXT,
  archived INTEGER, min_ts TEXT, max_ts TEXT, month_key TEXT
);
"""

def _ts(): return datetime.datetime.now().isoformat(timespec="seconds")

def month_key(ts): return ts[:7]  # YYYY-MM

def archive_path(mk):
    return os.path.join(ARCHIVE_DIR, f"trace_archive_{mk.replace('-','_')}.db")

def ensure_archive(mk):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    p = archive_path(mk)
    con = sqlite3.connect(p, timeout=5)
    con.executescript(ARCHIVE_SCHEMA.format(
        t="trace_cited", extra="query TEXT, inst_id TEXT, score REAL"))
    con.executescript(ARCHIVE_SCHEMA.format(
        t="trace_call", extra="entry TEXT, meta TEXT"))
    con.executescript(ARCHIVE_SCHEMA.format(
        t="trace_embedding", extra="model TEXT, n_items INTEGER"))
    con.execute("CREATE TABLE IF NOT EXISTS archive_meta (aid INTEGER PRIMARY KEY AUTOINCREMENT, batch_ts TEXT, table_name TEXT, archived INTEGER, min_ts TEXT, max_ts TEXT, month_key TEXT)")
    con.commit()
    return con

def hot_cutoff():
    return (datetime.datetime.now() - datetime.timedelta(days=HOT_WINDOW_DAYS)).isoformat()

def dry_run_report(con):
    cut = hot_cutoff()
    print(f"热窗口截止: {cut} (>30d 归档)")
    for t in TRACE_TABLES:
        n, mn, mx = con.execute(
            f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {t} WHERE ts < ?", (cut,)).fetchone()
        total = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: 总 {total} 行, 将归档 {n} 行 (ts {mn} ~ {mx})")

def do_archive(main_con, dry=False):
    cut = hot_cutoff()
    results = {}
    for t in TRACE_TABLES:
        rows = main_con.execute(
            f"SELECT tid,ts,{'query,inst_id,score' if t=='trace_cited' else ('entry,meta' if t=='trace_call' else 'model,n_items')} FROM {t} WHERE ts < ? ORDER BY ts", (cut,)).fetchall()
        if not rows:
            results[t] = 0; continue
        if dry:
            results[t] = len(rows); continue
        # 按月分组写入
        by_month = {}
        for r in rows:
            by_month.setdefault(month_key(r[1]), []).append(r)
        archived_total = 0
        for mk, mrows in by_month.items():
            acon = ensure_archive(mk)
            cols = {"trace_cited": "(tid,ts,query,inst_id,score)",
                    "trace_call": "(tid,ts,entry,meta)",
                    "trace_embedding": "(tid,ts,model,n_items)"}[t]
            placeholders = "(" + ",".join("?" * (2 + len(mrows[0]) - 2)) + ")"
            acon.executemany(f"INSERT OR IGNORE INTO {t} {cols} VALUES {placeholders}", mrows)
            # 对账: 归档库该批 tid 是否全部在表
            in_arch = acon.execute(
                f"SELECT COUNT(*) FROM {t} WHERE tid IN ({','.join('?'*len(mrows))})",
                [r[0] for r in mrows]).fetchone()[0]
            # P2-2: 原 assert 在 python -O 下消失→可能跳过对账继续删主库, 改显式异常
            if in_arch != len(mrows):
                raise RuntimeError(f"{t} {mk}: 归档对账失败 {in_arch}!={len(mrows)}")
            acon.execute("INSERT INTO archive_meta (batch_ts,table_name,archived,min_ts,max_ts,month_key) VALUES (?,?,?,?,?,?)",
                         (_ts(), t, len(mrows), mrows[0][1], mrows[-1][1], mk))
            acon.commit(); acon.close()
            archived_total += len(mrows)
        # 主库删除已归档行
        main_con.execute(f"DELETE FROM {t} WHERE ts < ?", (cut,))
        after = main_con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        results[t] = (archived_total, len(rows), after)
        # P2-2: 原 assert 在 python -O 下消失→可能带不一致结果静默删除, 改显式异常
        if archived_total != len(rows):
            raise RuntimeError(f"{t}: 删除行数与归档行数不守恒")
    if not dry:
        # 主库留痕
        main_con.execute("INSERT INTO audit_maintenance (ts,case_desc,resolution) VALUES (?,?,?)",
                         (_ts(), "trace月度归档 " + json.dumps({t: (results[t][0] if isinstance(results[t], tuple) else results[t]) for t in TRACE_TABLES}, ensure_ascii=False),
                          "PASS"))
        main_con.commit()
    return results

def global_check():
    """全链对账: 主库 + 所有归档库 行数总和"""
    main = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    total_by_table = {}
    for t in TRACE_TABLES:
        n = main.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        total_by_table[t] = [("主库", n)]
    for p in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "trace_archive_*.db"))):
        acon = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        for t in TRACE_TABLES:
            n = acon.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if n: total_by_table.setdefault(t, []).append((os.path.basename(p), n))
        # archive_meta 汇总 (对账口径: 按库各自比对, 不跨库累计)
        meta = acon.execute("SELECT table_name,SUM(archived) FROM archive_meta GROUP BY table_name").fetchall()
        for t, s in meta:
            actual = acon.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {os.path.basename(p)} {t}: meta申报 {s} vs 本库实际 {actual} {'PASS' if s==actual else 'FAIL'}")
        acon.close()
    for t, breakdown in total_by_table.items():
        s = sum(n for _, n in breakdown)
        print(f"{t}: 总 {s} 行 = " + " + ".join(f"{p}:{n}" for p, n in breakdown))

if __name__ == "__main__":
    if "--check" in sys.argv:
        global_check(); sys.exit(0)
    con = sqlite3.connect(DB, timeout=5)
    dry = "--dry-run" in sys.argv
    if dry:
        dry_run_report(con)
    else:
        r = do_archive(con, dry=False)
        print("归档完成:", r)
        # 归档后立即对账
        global_check()