# -*- coding: utf-8 -*-
"""session_dialog 碎片巡检(2026-11-21 用户裁决: 每周跑一次输出碎片占比报告)

背景: 历史 session_dialog 表 93% 是单句碎片(t 通道 transcript 批量导入未过滤),
     2026-11-21 已归档 811 条 ≤20 字节碎片(见 docs/maintenance_session_dialog_cleanup_20261121.md)。
     本脚本长期防护: 定期巡检碎片占比, 超阈值告警, 防止 t/h 通道防护回退再度污染。

用法:
  python eval/core/patrol_dialog_fragments.py            # 周频锁: 本周首次全量, 其余静默
  python eval/core/patrol_dialog_fragments.py --json     # 机器可读
  python eval/core/patrol_dialog_fragments.py --force    # 强制全量(忽略周频锁)
退出码: 0=健康/本周已跑(静默) 1=碎片占比超阈值(默认 15%)

2026-11-21 用户裁决: 接入 nightly 但脚本内做周频锁——nightly 每天调度只做轻量检查,
仅本周首次触发完整校验+阈值告警, 其余日期静默 exit 0 不打报告不推失败。
"""
import io, json, os, sqlite3, sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
MARK = os.path.join(_HERE, "data", "memory", "patrol_last_run.json")
THRESHOLD = 0.15          # 碎片占比告警阈值(≤20 字节视为碎片)
FRAG_BYTES = 20


def _week_key(dt=None):
    """ISO 年-周编号, 用于周频锁判重(跨年边界由 ISO 周历保证正确)。"""
    d = dt or datetime.now()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _last_week_key():
    try:
        with open(MARK, "r", encoding="utf-8") as f:
            return json.load(f).get("week")
    except (FileNotFoundError, ValueError):
        return None


def _write_mark(week, result_ts):
    try:
        os.makedirs(os.path.dirname(MARK), exist_ok=True)
        with open(MARK, "w", encoding="utf-8") as f:
            json.dump({"week": week, "last_full_run": result_ts}, f, ensure_ascii=False)
    except OSError as e:
        print(f"[dialog碎片巡检] 警告: 周频标记写入失败 {e}", file=sys.stderr)


def patrol():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total = con.execute("SELECT COUNT(*) FROM raw_corpus WHERE kind='session_dialog'").fetchone()[0]
    frag = con.execute("SELECT COUNT(*) FROM raw_corpus WHERE kind='session_dialog' AND length(body)<=?",
                       (FRAG_BYTES,)).fetchone()[0]
    buckets = {}
    for lo, hi in [(0, 20), (21, 100), (101, 1000), (1001, 10 ** 9)]:
        n = con.execute("SELECT COUNT(*) FROM raw_corpus WHERE kind='session_dialog' "
                        "AND length(body) BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
        buckets[f"{lo}-{hi if hi < 10**9 else 'inf'}"] = n
    by_ch = {}
    for r in con.execute("SELECT substr(tree_ch,1,1) p, COUNT(*) FROM raw_corpus "
                         "WHERE kind='session_dialog' GROUP BY p").fetchall():
        by_ch[r[0] or "_"] = r[1]
    archived = 0
    try:
        archived = con.execute("SELECT COUNT(*) FROM session_dialog_archive_fragment").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    con.close()
    ratio = (frag / total) if total else 0.0
    return {"ts": datetime.now().isoformat(timespec="seconds"), "total": total,
            "fragment_le20": frag, "fragment_ratio": round(ratio, 4),
            "threshold": THRESHOLD, "healthy": ratio <= THRESHOLD,
            "buckets": buckets, "by_channel": by_ch, "archived_fragment": archived}


def main():
    as_json = "--json" in sys.argv
    force = "--force" in sys.argv
    week = _week_key()

    # 周频锁: 本周已跑过完整巡检 → 轻量静默退出, 不输出报告/不推 nightly 失败
    if not force and _last_week_key() == week:
        sys.exit(0)

    r = patrol()
    _write_mark(week, r["ts"])
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(f"[dialog碎片巡检] {r['ts']} (周 {week})")
        print(f"  总数 {r['total']} | ≤{FRAG_BYTES}字节碎片 {r['fragment_le20']} "
              f"({r['fragment_ratio']:.1%}) | 阈值 {r['threshold']:.0%} | "
              f"{'✅健康' if r['healthy'] else '⚠️超阈值'}")
        print(f"  长度分桶: {r['buckets']}")
        print(f"  通道分布: {r['by_channel']} | 已归档碎片: {r['archived_fragment']}")
    sys.exit(0 if r["healthy"] else 1)


if __name__ == "__main__":
    main()