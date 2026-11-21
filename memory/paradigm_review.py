# -*- coding: utf-8 -*-
"""
paradigm_review.py — 范式提案评审辅助 (2026-11-15 用户指令 P2-a)

职能: E7 夜间治理产出 propose_promote / propose_recalibrate 提案后,
     人工终审的预览工具——一条命令列出:
       · 待晋升范式(candidate 3实证全pass) + 全部证据明细
       · 待重标定(calibration_pending / active衰减) + 状态理由
       · 通过率 / failure_mode / 触发场景 一屏可读
     只读, 不改任何 verdict。

用法:
  python memory/paradigm_review.py            # 全量提案视图
  python memory/paradigm_review.py <pid>      # 单范式深看(全部证据流水)
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
TABLES = ("core_thinking_paradigm", "core_skill_paradigm")


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def _over90(ts):
    try:
        return (datetime.now() - datetime.fromisoformat((ts or "")[:19])).days > 90
    except ValueError:
        return True


def collect(con):
    """汇总全部需人工关注的范式, 分组: 可晋升 / 待标定 / 待复审 / 健康在岗"""
    groups = {"promotable": [], "calibration": [], "decay": [], "active_ok": []}
    for t in TABLES:
        for p in con.execute(f"SELECT * FROM {t}"):
            p = dict(zip([d[0] for d in con.execute(f"SELECT * FROM {t} LIMIT 0").description], p))
            p["_table"] = t
            ev = con.execute(
                "SELECT ts, outcome, context, source_log FROM audit_paradigm_evidence "
                "WHERE pid=? ORDER BY ts", (p["pid"],)).fetchall()
            p["_evidence"] = [dict(zip(("ts", "outcome", "context", "source_log"), e)) for e in ev]
            passes = sum(1 for e in p["_evidence"] if e["outcome"] == "pass")
            fails = sum(1 for e in p["_evidence"] if e["outcome"] == "fail")
            p["_ev_pass"], p["_ev_fail"] = passes, fails
            p["_rate"] = f"{passes}/{passes+fails}" if (passes + fails) else "0/0"
            if p["status"] == "candidate" and (p["evidence_pass"] or 0) >= 3 \
                    and p["evidence_pass"] == p["evidence_count"] and (p["evidence_pass"] or 0) > 0:
                groups["promotable"].append(p)
            elif p["status"] == "calibration_pending":
                groups["calibration"].append(p)
            elif p["status"] == "active" and _over90(p.get("last_trigger_ts") or p["ts"]):
                groups["decay"].append(p)
            elif p["status"] == "active":
                groups["active_ok"].append(p)
    return groups


def render_one(p, verbose=True):
    L = [f"### {p['name']}  [{p['_table'].replace('core_','').replace('_paradigm','')}/{p['pid']}] {p['status']}",
         f"  触发场景: {p['trigger_when']}"]
    body = p.get("procedure") or p.get("op_sequence")
    L.append(f"  执行程序: {body}")
    L.append(f"  通过率: {p['_rate']} (表计 {p['evidence_pass']}/{p['evidence_count']}) | "
             f"最近触发: {p.get('last_trigger_ts') or '从未'}")
    if p.get("failure_mode"):
        L.append(f"  失败模式: {p['failure_mode']}")
    if p.get("precondition"):
        L.append(f"  前置条件: {p['precondition']}")
    L.append(f"  溯源: {p.get('origin') or '-'}")
    if verbose:
        if p["_evidence"]:
            L.append("  证据流水:")
            for e in p["_evidence"][-10:]:
                L.append(f"    [{e['outcome']}] {e['ts'][:16]} {str(e['context'])[:80]} (源:{e['source_log']})")
        else:
            L.append("  证据流水: (空——实证账未记, 晋升须补)")
    return "\n".join(L)


def main(pid=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    if pid:
        for t in TABLES:
            row = con.execute(f"SELECT * FROM {t} WHERE pid=?", (pid,)).fetchone()
            if row:
                p = dict(zip([d[0] for d in con.execute(f"SELECT * FROM {t} LIMIT 0").description], row))
                p["_table"] = t
                ev = con.execute("SELECT ts,outcome,context,source_log FROM audit_paradigm_evidence WHERE pid=? ORDER BY ts", (pid,)).fetchall()
                p["_evidence"] = [dict(zip(("ts","outcome","context","source_log"), e)) for e in ev]
                p["_rate"] = f"{sum(1 for e in p['_evidence'] if e['outcome']=='pass')}/{len(p['_evidence'])}"
                print(render_one(p))
                con.close()
                return
        print(f"未找到 pid={pid}")
        con.close()
        return
    g = collect(con)
    con.close()
    print(f"范式提案评审视图  {_ts()}  (只读, 终审在人)")
    print(f"\n== 待晋升提案 (candidate 3实证全pass) {len(g['promotable'])} 条 ==")
    for p in g["promotable"]:
        print(render_one(p))
    print(f"\n== 待标定 (calibration_pending) {len(g['calibration'])} 条 ==")
    for p in g["calibration"]:
        print(render_one(p))
    print(f"\n== 衰减预警 (active >90d 未触发) {len(g['decay'])} 条 ==")
    for p in g["decay"]:
        print(render_one(p, verbose=False))
    print(f"\n== 健康在岗 (active) {len(g['active_ok'])} 条 ==")
    for p in g["active_ok"]:
        print(render_one(p, verbose=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)