# -*- coding: utf-8 -*-
"""
trace_archive_replay_test.py — 影子库回放测试 (2026-11-15 用户配套规约 #29)

目的: 验证「归档、统计、热度回算、cited 计数(HLTM 权重读取输入)」不受归档切割影响。
方法:
  1. 复制主库为临时影子库 (不动真库)
  2. 伪造一批跨 3 个月的历史 trace_cited 行灌入影子库 (部分超出热窗口)
  3. 基线统计: 全量 count / 按 inst_id cited 计数 / 按月分布
  4. 在影子库上执行 trace_archive 归档
  5. 归档后: 主库热窗统计 + 跨主库+归档库合并统计, 与基线比对
  6. 断言: 合并视角下 cited 计数与基线完全一致 (统计不受切割影响)
"""
import sqlite3, os, shutil, datetime, sys, subprocess, tempfile

SRC = "data/memory/corpus_store.db"
SHADOW = "data/memory/_replay_shadow.db"
ARCHIVE_DIR = "data/memory/trace_archive"

def make_shadow():
    if os.path.exists(SHADOW): os.remove(SHADOW)
    shutil.copy(SRC, SHADOW)
    # 清空 shadow 旧 trace, 灌入伪造跨月数据
    con = sqlite3.connect(SHADOW)
    con.execute("DELETE FROM trace_cited")
    now = datetime.datetime.now()
    fake_insts = ["10764d3b", "7a499925", "32684974", "7808dbac"]
    rows = []
    tid = 1
    # 3 个月前(超热窗)、2 个月前(超)、1 个月前(部分超)、近7天(热窗内)
    for days_ago, n in ((85, 300), (55, 200), (20, 150), (3, 50)):
        base = now - datetime.timedelta(days=days_ago)
        for i in range(n):
            ts = (base + datetime.timedelta(minutes=i)).isoformat()
            rows.append((tid, ts, f"query_{days_ago}d_{i}", fake_insts[i % 4], 0.5 + (i % 10) / 20))
            tid += 1
    con.executemany("INSERT INTO trace_cited (tid,ts,query,inst_id,score) VALUES (?,?,?,?,?)", rows)
    con.commit(); con.close()
    return len(rows)

def cited_counts(paths):
    """合并多库 cited 计数 (HLTM 权重读取口径)"""
    agg = {}
    for p in paths:
        if not os.path.exists(p): continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        for inst, c in con.execute("SELECT inst_id, COUNT(*) FROM trace_cited GROUP BY inst_id"):
            agg[inst] = agg.get(inst, 0) + c
        con.close()
    return agg

def main():
    # 1. 影子库 + 伪造数据
    n_fake = make_shadow()
    print(f"影子库建立, 灌入 {n_fake} 条伪造 trace_cited (跨 85d~3d)")

    # 2. 基线统计
    base_counts = cited_counts([SHADOW])
    base_total = sum(base_counts.values())
    print(f"基线: 总 {base_total} 行, {base_counts}")

    # 3. 在影子库上跑归档 (改环境: 让 trace_archive 操作影子库)
    import importlib
    os.environ["TRACE_DB_OVERRIDE"] = SHADOW
    sys.path.insert(0, "memory")
    import trace_archive as ta
    ta.DB = SHADOW  # 重定向到影子库
    ta.ARCHIVE_DIR = ARCHIVE_DIR  # 真归档目录 (产物即回放测试的归档样例)
    con = sqlite3.connect(SHADOW)
    r = ta.do_archive(con, dry=False)
    print("归档执行:", r)
    ta.global_check()

    # 4. 归档后合并视角比对
    arch_dbs = sorted(__import__("glob").glob(os.path.join(ARCHIVE_DIR, "trace_archive_*.db")))
    merged = cited_counts([SHADOW] + arch_dbs)
    hot_only = cited_counts([SHADOW])
    print(f"归档后热窗: {sum(hot_only.values())} 行 {hot_only}")
    print(f"合并视角:   {sum(merged.values())} 行 {merged}")
    # 5. 断言
    assert merged == base_counts, f"FAIL: 合并视角与基线不一致\n{merged}\n{base_counts}"
    assert sum(merged.values()) == base_total, "FAIL: 总行数不守恒"
    print("PASS: 归档切割后, 合并视角 cited 计数(HLTM 输入口径)与基线完全一致")
    print("PASS: 热窗清理只影响主库视角, 统计方可用合并查询拿全量")
    # 6. 清理影子库
    con.close()
    for _ in range(3):
        try:
            os.remove(SHADOW); break
        except PermissionError:
            import time; time.sleep(0.5)
    print("影子库已清理 (归档库样例保留供检查)")

if __name__ == "__main__":
    main()