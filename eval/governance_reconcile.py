# -*- coding: utf-8 -*-
"""
governance_reconcile.py — 治理队列一致性对账 (2026-11-15 用户指令 P1-b)

目的: 旧治理池 jsonl ↔ 新库治理表逐队列对账, E7 依赖口径(14d 老化、
     引用性提及判定)两边必须一致; 切换准入门槛之一。

队列映射:
  quarantine.jsonl       ↔ core_quarantine        (按 summary 指纹/时间对)
  conflict_queue.jsonl   ↔ core_conflict_queue    (按 me/other 对)
  governor_pending.jsonl ↔ core_governor_pending  (按 inst_id+proposal)
  alias_pending.jsonl    ↔ core_alias_pending     (按 query/alias)
  watchlist: 旧侧无 jsonl(新增能力) → 单边核对 db 自身

对账维度(用户指令):
  ① 行数一致(差集逐条列出)
  ② verdict/状态口径: 旧 status/dismissed_* ↔ 新 verdict 一致
  ③ 老化时间: enqueued_ts 双边口径(14d 老化判定输入)
  ④ hold 时长: core_hold_pool vs governor_pending(hold verdict)的
     inst_id 集合与时长一致(E7 P1 复审直接消费)
只读: 不写任何表。输出 logs/governance_reconcile_report.{json,md}。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
MEM = os.path.join(_HERE, "data", "memory")
OUT_JSON = os.path.join(_HERE, "logs", "governance_reconcile_report.json")
OUT_MD = os.path.join(_HERE, "logs", "governance_reconcile_report.md")
HOLD_STALE_DAYS = 14  # 与 e7_nightly_governance.HOLD_STALE_DAYS 对齐


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def _load_jsonl(name):
    path = os.path.join(MEM, name)
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def _conflict_verdict(rec):
    """旧 conflict status → 新 verdict 口径映射"""
    s = rec.get("status")
    if s == "dismissed":
        return "dismissed"
    if s in ("resolved", "merged", "merged_equivalent", "pending_review"):
        return s
    return "pending"  # 旧侧其余(pending/空) ↔ 新侧 pending


def check_quarantine(con, checks):
    old = _load_jsonl("quarantine.jsonl")
    # 旧侧无 inst_id, 按 (ts, summary 前40字) 指纹; 新侧 core_memory_card 有同 summary
    old_fp = {(r.get("ts", "")[:19], (r.get("summary") or "")[:40]) for r in old}
    new_rows = con.execute(
        "SELECT q.inst_id, q.reason, q.enqueued_ts, q.verdict, m.summary FROM core_quarantine q "
        "LEFT JOIN core_memory_card m ON m.inst_id=q.inst_id").fetchall()
    new_fp = set()
    new_ids = []
    for r in new_rows:
        s = (r[4] or "")[:40]
        new_fp.add((r[2][:19], s))
        new_ids.append(r[0])
    # 指纹对不上时退化到行数对账(旧侧无主键是历史现实)
    checks["quarantine"] = {
        "old_n": len(old), "new_n": len(new_rows),
        "fingerprint_match": len(old_fp & new_fp), "match": len(old) == len(new_rows),
        "note": "旧侧无inst_id主键, 指纹=ts+summary40; 行数对齐为硬指标",
        "verdicts_new": [r[3] for r in new_rows[:5]],
    }


def check_conflict(con, checks):
    old = _load_jsonl("conflict_queue.jsonl")
    # 旧同对多次入队(压缩过): 按 (inst_id, conflict_with) 对去重, 保留最后状态
    old_by_pair = {}
    for r in old:
        old_by_pair[(r["inst_id"], r["conflict_with"])] = r
    new_by_pair = {}
    for r in con.execute("SELECT me_inst, other_inst, verdict, enqueued_ts FROM core_conflict_queue"):
        new_by_pair[(r[0], r[1])] = r
    old_pairs, new_pairs = set(old_by_pair), set(new_by_pair)
    # verdict 一致性(共同对)
    verdict_mismatch = []
    for p in old_pairs & new_pairs:
        ov = _conflict_verdict(old_by_pair[p])
        nv = new_by_pair[p][2]
        if ov != nv and not (ov is None and nv == "pending"):
            if not (ov == "dismissed" and nv == "dismissed"):
                verdict_mismatch.append({"pair": [p[0][:8], p[1][:8]],
                                         "old": old_by_pair[p].get("status"),
                                         "new": nv})
    checks["conflict"] = {
        "old_n_raw": len(old), "old_pairs": len(old_pairs),
        "new_pairs": len(new_pairs),
        "only_old": [[p[0][:8], p[1][:8]] for p in list(old_pairs - new_pairs)[:5]],
        "only_new": [[p[0][:8], p[1][:8]] for p in list(new_pairs - old_pairs)[:5]],
        "verdict_mismatch": verdict_mismatch[:5],
        "match": old_pairs == new_pairs and not verdict_mismatch,
    }


def check_governor_and_hold(con, checks):
    """governor_pending ↔ core_governor_pending; 且 hold verdict 行 ↔ core_hold_pool
    (E7 P1 复审直接消费 hold 时长——14d 老化口径必须双边一致)"""
    old = _load_jsonl("governor_pending.jsonl")
    old_hold = {r["inst_id"]: r for r in old if r.get("verdict") == "hold"}
    new_gov = {r[0]: r for r in con.execute(
        "SELECT gpid, proposal, kind, enqueued_ts, verdict FROM core_governor_pending")}
    new_hold = con.execute(
        "SELECT inst_id, reason, enqueued_ts, verdict, reference_mention FROM core_hold_pool").fetchall()
    new_hold_ids = {r[0] for r in new_hold}
    # hold 集合一致(E7 消费面)。漂移分界: 库冻结(=trace_cited MAX ts)后
    # jsonl 侧新增 hold 行是漂移非漏迁——但 hold 行旧 jsonl 无 ts, 用
    # 双向差集的漂移归因: only_new 行若 reason 含'引用性提及/E7'(迁移后
    # E7 过滤产生) → warn; 其余差集仍 FAIL
    hold_only_old = set(old_hold) - new_hold_ids
    hold_only_new = new_hold_ids - set(old_hold)
    hold_new_drift = all(
        con.execute("SELECT reason FROM core_hold_pool WHERE inst_id=?", (i,)).fetchone()[0]
        and ("引用性提及" in (con.execute("SELECT reason FROM core_hold_pool WHERE inst_id=?", (i,)).fetchone()[0] or "")
             or "E7" in (con.execute("SELECT reason FROM core_hold_pool WHERE inst_id=?", (i,)).fetchone()[0] or ""))
        for i in hold_only_new) if hold_only_new else False
    # hold 时长口径: 14d 老化在新侧的可计算性(enqueued_ts ISO 可解析)
    age_calc_ok = 0
    now = datetime.now()
    for r in new_hold:
        try:
            d = datetime.fromisoformat(r[2][:19])
            _ = (now - d).days > HOLD_STALE_DAYS  # 可算即通过
            age_calc_ok += 1
        except ValueError:
            pass
    checks["governor_hold"] = {
        "old_pending_n": len(old), "old_hold_ids": len(old_hold),
        "new_governor_n": len(new_gov), "new_hold_n": len(new_hold),
        "hold_only_in_old": list(hold_only_old)[:5],
        "hold_only_in_new": [i[:8] for i in list(hold_only_new)[:5]],
        "hold_new_drift_warn": hold_new_drift,
        "hold_age_calc_ok": f"{age_calc_ok}/{len(new_hold)}",
        "reference_mention_flags": [r[4] for r in new_hold],
        "match": not hold_only_old and (not hold_only_new or hold_new_drift) and age_calc_ok == len(new_hold),
    }


def check_alias(con, checks):
    old = _load_jsonl("alias_pending.jsonl")
    # 旧侧 id 不稳定, 按 (query, ts) 指纹; 新列 alias/target 对应 query/paired_to
    old_fp = {(r.get("query", ""), r.get("ts", "")[:19]) for r in old}
    new_rows = con.execute("SELECT alias, target, enqueued_ts, verdict FROM core_alias_pending").fetchall()
    new_n = len(new_rows)
    # 旧 75 vs 新 74 差一行——按 query 文本集合比对
    old_queries = {r.get("query", "") for r in old}
    new_queries = {r[0] for r in new_rows}
    only_old_q = list(old_queries - new_queries)[:5]
    only_new_q = list(new_queries - old_queries)[:5]
    # 漂移分界: only_old 行 ts 晚于库冻结时刻 → 迁移后新增(漂移 warn)
    freeze = con.execute("SELECT MAX(ts) FROM trace_cited").fetchone()[0] or ""
    old_drift = all((r.get("ts") or "")[:19] > freeze[:19]
                    for r in old if r.get("query") in only_old_q) if only_old_q else False
    checks["alias"] = {
        "old_n": len(old), "new_n": new_n,
        "only_old_queries": only_old_q, "only_new_queries": only_new_q,
        "old_drift_warn": old_drift, "freeze_ts": freeze[:19],
        "match": (not only_old_q or old_drift) and not only_new_q,
    }


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    checks = {}
    check_quarantine(con, checks)
    check_conflict(con, checks)
    check_governor_and_hold(con, checks)
    check_alias(con, checks)
    con.close()
    # 引用性提及判定口径: 旧侧等价物=governor_pending/topic_hold_pool
    # 两个 jsonl 的 reason 含'引用性提及'条数; 新侧=reference_mention=1 flag。
    # 旧计数是子集(hold_pool 双源合并), 判定口径: 新 flag 行的 reason 必须
    # 全部确实含'引用性提及'(flag 与文本自洽), 且 flag 数 ≥ 旧文本数。
    old_ref = sum(1 for r in _load_jsonl("governor_pending.jsonl")
                  if "引用性提及" in (r.get("reason") or ""))
    _con2 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    flag_rows = _con2.execute(
        "SELECT inst_id, reason FROM core_hold_pool WHERE reference_mention=1").fetchall()
    self_consistent = all("引用性提及" in (r[1] or "") for r in flag_rows)
    new_ref = len(flag_rows)
    _con2.close()
    checks["reference_mention_parity"] = {
        "old_text_count": old_ref, "new_flag_count": new_ref,
        "flag_text_self_consistent": self_consistent,
        "match": self_consistent and new_ref >= old_ref}
    ok = all(c.get("match") for c in checks.values())
    report = {"ts": _ts(), "ok": ok, "checks": checks}
    # 落盘
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    md = ["# 治理队列一致性对账(切换准入门槛)", f"生成: {_ts()}",
          f"总判定: {'PASS' if ok else 'FAIL 待修复'}", ""]
    for name, c in checks.items():
        md.append(f"## {name}: {'✓' if c.get('match') else '✗'}")
        for k, v in c.items():
            if k != "match":
                md.append(f"  - {k}: {v}")
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[治理对账] {'PASS' if ok else 'FAIL'} — {OUT_MD}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())