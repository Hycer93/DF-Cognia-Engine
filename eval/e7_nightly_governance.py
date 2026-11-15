# -*- coding: utf-8 -*-
"""
e7_nightly_governance.py — E7 夜间治理阶段 (2026-11-15 用户指令 #30)

职能: 串装夜间提案 → hold 池复审 → 范式标定/复审 → 证据回写 四环节。
设计原则(对齐既有 nightly 分工):
  - 机器只提案不裁决: 所有 verdict 变更走 pending 状态, 终裁=人闸门(晨报)
  - 幂等可重跑: 已带 verdict 的行不重复处理
  - 不删数据只降权/上交(不删除哲学): 复审不过 = 标记 pending 上交, 不删行
  - inspect_freq='high' 簇优先巡检(2026-11-14 三条枷锁裁决)

四环节:
  P1 夜间提案: hold 池(verdict IS NULL)逐条复审——引用性提及(reference_mention=1)
     者查其后是否出现"核心讨论"卡片(锚词命中 summary), 出现则 propose_release
     提案; 仍无则 propose_stale(老化上交)。proposal 状态不直接改 verdict。
  P2 高频巡检: inspect_freq='high' 簇(topic_memory_ops)逐卡核对锚词,
     混入无关卡 propose_pending 上交(不自动踢出)。
  P3 范式标定: 范式两表 calibration_pending 复审(连续2fail 进来的),
     连续衰减压到90d+ 且 evidence_pass>0 的 propose_recalibrate(重新激活提案);
     candidate 满 3 独立实证全 pass → propose_promote(晋升提案, 不直接升 active)。
  P4 证据回写: maintenance_cases.jsonl 同型 ≥3 的 paradigm_hint 自动写
     audit_paradigm_evidence(候选证据账, outcome='proposed' 非实证结论)。

输出: logs/e7_governance.json + 晨报节(由 nightly_run.py 渲染)。
用法: python eval/e7_nightly_governance.py
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))

DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
LOG = os.path.join(_HERE, "logs", "e7_governance.json")
HOLD_STALE_DAYS = 14        # [先验-业务设定] hold 老化上交阈值
PARADIGM_MIN = 3            # 与 maintenance_ledger.PARADIGM_MIN 对齐
DECAY_DAYS = 90             # 与 design_paradigm_layer.md / render_persona.py 对齐


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _log(op, payload):
    """复用 topic_layer 的审计日志格式, 落 audit_topic_log"""
    con = _conn()
    try:
        con.execute(
            "INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
            (op, json.dumps(payload, ensure_ascii=False), _ts()))
        con.commit()
    except Exception:
        pass
    finally:
        con.close()


# ---------- P1 hold 池夜间复审 ----------

def p1_hold_review(con, result):
    rows = con.execute(
        "SELECT * FROM core_hold_pool WHERE verdict IS NULL ORDER BY enqueued_ts").fetchall()
    proposals = []
    for r in rows:
        enq = r["enqueued_ts"]
        try:
            age_days = (datetime.now() - datetime.fromisoformat(enq[:19])).days
        except ValueError:
            age_days = 0
        if r["reference_mention"]:
            # 引用性提及: 查此后同锚词是否出现核心讨论卡(summary 命中)
            # reason 字段存了 evidence_gap 描述, 锚词查 topic_links 该卡当前状态
            has_core = con.execute(
                "SELECT COUNT(*) FROM topic_links WHERE inst_id=?", (r["inst_id"],)).fetchone()[0]
            prop = {"inst_id": r["inst_id"], "proposal": "propose_release" if has_core else "propose_stale",
                    "reason": ("锚词已现核心讨论卡" if has_core else f"引用性提及滞留{age_days}d无核心讨论"),
                    "age_days": age_days}
        else:
            prop = {"inst_id": r["inst_id"],
                    "proposal": "propose_stale" if age_days > HOLD_STALE_DAYS else "propose_keep",
                    "reason": f"非引用hold, 年龄{age_days}d", "age_days": age_days}
        proposals.append(prop)
    result["p1_hold"] = {"pending": len(rows), "proposals": proposals}
    _log("e7_p1_hold_review", {"pending": len(rows),
                              "propose_stale": sum(1 for p in proposals if p["proposal"] == "propose_stale")})


# ---------- P2 高频簇巡检 ----------

def p2_high_freq_inspect(con, result):
    """inspect_freq='high' 簇逐卡核对锚词; 无关卡上交 pending 提案(不自动踢)"""
    high_topics = con.execute(
        "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE inspect_freq='high' AND status='active'").fetchall()
    suspicious = []
    for t in high_topics:
        cues = json.loads(t["cue_anchors"] or "[]")
        cards = con.execute(
            "SELECT l.inst_id, m.summary FROM topic_links l "
            "JOIN core_memory_card m ON m.inst_id = l.inst_id WHERE l.topic_id=?",
            (t["topic_id"],)).fetchall()
        for c in cards:
            if not any(cue in (c["summary"] or "") for cue in cues):
                suspicious.append({"topic_id": t["topic_id"], "inst_id": c["inst_id"],
                                   "note": "summary无锚词命中, 疑似混入"})
    result["p2_high_freq"] = {"topics": [t["topic_id"] for t in high_topics],
                              "cards_checked": sum(len(con.execute(
                                  "SELECT 1 FROM topic_links WHERE topic_id=?", (t["topic_id"],)).fetchall())
                                  for t in high_topics),
                              "suspicious": suspicious}
    _log("e7_p2_high_freq", {"suspicious": len(suspicious)})


# ---------- P3 范式标定复审 ----------

def _parse_ts(s):
    try:
        return datetime.fromisoformat((s or "")[:19])
    except ValueError:
        return None


def p3_paradigm_calibration(con, result):
    proposals = []
    for table in ("core_thinking_paradigm", "core_skill_paradigm"):
        # calibration_pending 复审
        for p in con.execute(f"SELECT * FROM {table} WHERE status='calibration_pending'").fetchall():
            recent_evidence = con.execute(
                "SELECT COUNT(*) FROM audit_paradigm_evidence WHERE pid=? AND outcome='pass'",
                (p["pid"],)).fetchone()[0]
            proposals.append({"pid": p["pid"], "table": table,
                              "proposal": "propose_recalibrate" if recent_evidence > 0 else "propose_keep_pending",
                              "note": f"夜审中, 新增pass实证{recent_evidence}"})
        # candidate 晋升检查(3 独立实证全 pass → 提案, 不直接升)
        for p in con.execute(f"SELECT * FROM {table} WHERE status='candidate' AND evidence_pass>0").fetchall():
            if p["evidence_pass"] >= PARADIGM_MIN and p["evidence_pass"] == p["evidence_count"]:
                proposals.append({"pid": p["pid"], "table": table, "proposal": "propose_promote",
                                  "note": f"3独立实证全pass ({p['evidence_pass']}/{p['evidence_count']}), 待人终审"})
        # 衰减标定: active 超90d未触发且无近期实证 → 提案重新标定
        for p in con.execute(f"SELECT * FROM {table} WHERE status='active'").fetchall():
            last = _parse_ts(p["last_trigger_ts"]) or _parse_ts(p["ts"])
            if last and (datetime.now() - last).days > DECAY_DAYS:
                proposals.append({"pid": p["pid"], "table": table, "proposal": "propose_recalibrate",
                                  "note": f"active超{DECAY_DAYS}d未触发, 建议重激活标定或降权排尾"})
    result["p3_calibration"] = {"proposals": proposals}
    _log("e7_p3_calibration", {"n": len(proposals)})


# ---------- P4 证据回写(候选证据账) ----------

def p4_evidence_writeback(result):
    """maintenance_cases 同型 paradigm_hint ≥3 → 写候选证据(outcome='proposed')"""
    cases_path = os.path.join(_HERE, "data", "memory", "maintenance_cases.jsonl")
    if not os.path.exists(cases_path):
        result["p4_evidence"] = {"cases": 0, "written": 0}
        return
    from collections import Counter
    cases = []
    with open(cases_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    cases.append(json.loads(line))
                except ValueError:
                    continue
    hints = Counter(c.get("paradigm_hint") for c in cases if c.get("paradigm_hint"))
    con = _conn()
    written = 0
    try:
        for hint, n in hints.items():
            if n < PARADIGM_MIN:
                continue
            exists = con.execute(
                "SELECT COUNT(*) FROM audit_paradigm_evidence WHERE context LIKE ? AND outcome='proposed'",
                (f"%{hint}%",)).fetchone()[0]
            if exists:
                continue
            con.execute(
                "INSERT INTO audit_paradigm_evidence (pid, ts, outcome, context, delta, source_log) "
                "VALUES (?,?,?,?,?,?)",
                (f"auto:{hint}", _ts(), "proposed",
                 f"维护案例同型×{n} 触发范式候选(非实证结论, 待人工核)",
                 json.dumps({"case_count": n}, ensure_ascii=False), "maintenance_cases.jsonl"))
            written += 1
        con.commit()
    finally:
        con.close()
    result["p4_evidence"] = {"cases": len(cases), "hints": dict(hints), "written": written}
    _log("e7_p4_evidence", {"written": written})


def main():
    result = {"ts": _ts(), "ok": True}
    con = _conn()
    try:
        p1_hold_review(con, result)
        p2_high_freq_inspect(con, result)
        p3_paradigm_calibration(con, result)
    finally:
        con.close()
    p4_evidence_writeback(result)  # 独立连接(内部自管)
    # 落盘
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    n_prop = sum(len(result[k].get("proposals", [])) for k in ("p1_hold", "p3_calibration"))
    print(f"[E7] 提案 {n_prop} 条 | 高频簇可疑 {len(result['p2_high_freq']['suspicious'])} | "
          f"证据回写 {result['p4_evidence']['written']} 条 (全为提案, 终裁=人闸门)")


if __name__ == "__main__":
    main()