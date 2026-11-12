# memory/confidence_dynamics.py — 置信度动态升降级（2026-11-10 用户裁决②落地）
# 语料来源（origin_corpus3 置信度联动进化闭环章）：
#   升级规则：L3臆测被后续证实 → 同类型推论下次可直接标注 L1
#   回退规则：L1强推论被证伪 → 同类型降 L2 + 已归档结论追溯修正
# 纪律：
#   - 叠加层 append-only（仿 event_ts_backfill），不改原子层 confidence 字段
#   - "同类型"口径 = 编码三元组 (domain, behavior, relation)——与库幂等/判重口径同源，
#     可枚举可回归；不引入语义聚类模糊性（那是 distill 的活，不是置信度判定的事）
#   - 双信号触发（考古先于发明：DS两轮整理机制 + 蒸馏仔S015主动盘点）：
#     信号1 主AI在会话中主动调用 mark_verified/mark_falsified（置信事件）
#     信号2 E5 夜间整理调用 scan_confidence_evidence（被动兜底，扫描新增证据）
# 使用：
#   python -c "from memory.confidence_dynamics import *; mark_verified(inst_id=..., evidence=...)"
#   E5 集成：con = confidence_dynamics_scan() 并入报告 ops

import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MEM = os.path.join(os.path.dirname(HERE), "data", "memory")
OVERLAY = os.path.join(MEM, "confidence_dynamics.jsonl")

LEVELS = ["L0", "L1", "L2", "L3", "L4"]  # 低→高可靠
UPGRADE = {"L3": "L1", "L2": "L1"}       # 证实：臆测/弱推论 → 强推论（语料原文：L3→L1）
DOWNGRADE = {"L1": "L2", "L0": "L2"}      # 证伪：强推论/已证实 → 弱推论+追溯修正


def _load_memories():
    out = []
    with io.open(os.path.join(MEM, "memories.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def _append(rec):
    with io.open(OVERLAY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def mark_verified(inst_id, evidence, new_summary=None, source="session"):
    """L2/L3 被证实 → 叠加层记录升级 + 同类型先验规则更新。"""
    m = next((x for x in _load_memories() if x["inst_id"] == inst_id), None)
    if not m:
        return {"ok": False, "msg": f"inst_id 不存在: {inst_id}"}
    conf = m.get("confidence")
    if conf not in UPGRADE:
        return {"ok": False, "msg": f"当前 {conf} 不在可升级集合（{list(UPGRADE)}）"}
    tag = m.get("dfcp_tag") or {}
    rec = {"op": "upgrade", "inst_id": inst_id, "from": conf, "to": UPGRADE[conf],
           "evidence": evidence, "type_key": f"{tag.get('domain')}|{tag.get('behavior')}|{tag.get('relation')}",
           "summary": m["summary"][:60], "source": source,
           "ts": datetime.now().isoformat()}
    _append(rec)
    if new_summary:  # 追溯修正线索（仅记录，不改原子层）
        _append({"op": "retro_clue", "inst_id": inst_id, "new_summary": new_summary[:200],
                 "evidence": evidence, "ts": datetime.now().isoformat()})
    return {"ok": True, "msg": f"{conf}→{UPGRADE[conf]} 已叠加", "rec": rec}


def mark_falsified(inst_id, evidence, retrofix_note=None, source="session"):
    """L0/L1 被证伪 → 叠加层记录降级 + 同类型先验规则更新 + 归档结论追溯线索。"""
    m = next((x for x in _load_memories() if x["inst_id"] == inst_id), None)
    if not m:
        return {"ok": False, "msg": f"inst_id 不存在: {inst_id}"}
    conf = m.get("confidence")
    if conf not in DOWNGRADE:
        return {"ok": False, "msg": f"当前 {conf} 不在可降级集合（{list(DOWNGRADE)}）"}
    tag = m.get("dfcp_tag") or {}
    rec = {"op": "downgrade", "inst_id": inst_id, "from": conf, "to": DOWNGRADE[conf],
           "evidence": evidence, "type_key": f"{tag.get('domain')}|{tag.get('behavior')}|{tag.get('relation')}",
           "summary": m["summary"][:60], "source": source,
           "ts": datetime.now().isoformat()}
    _append(rec)
    if retrofix_note:  # 语料要求"已归档结论追溯修正"——降级后依赖它的旧结论必须重审
        dependents = _find_dependents(inst_id)
        _append({"op": "retrofix_pending", "inst_id": inst_id,
                 "dependents": [d["inst_id"] for d in dependents],
                 "note": retrofix_note[:200], "ts": datetime.now().isoformat()})
    return {"ok": True, "msg": f"{conf}→{DOWNGRADE[conf]} 已叠加，依赖结论 {len(_find_dependents(inst_id))} 条待重审",
            "rec": rec}


def _find_dependents(inst_id):
    """沿 relation_graph 找直接引用该记忆的下游（P/D 边）。"""
    path = os.path.join(MEM, "relation_graph.jsonl")
    out, seen = [], set()
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("from") == inst_id and e.get("rel") in ("P", "D") and e.get("to") not in seen:
                out.append(e); seen.add(e.get("to"))
    return out


def effective_confidence(inst_id):
    """查询时用：原子层 confidence + 叠加层最新动态 = 生效置信度。"""
    m = next((x for x in _load_memories() if x["inst_id"] == inst_id), None)
    if not m:
        return None
    eff = m.get("confidence")
    if os.path.exists(OVERLAY):
        with io.open(OVERLAY, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r.get("inst_id") == inst_id and r.get("op") in ("upgrade", "downgrade"):
                        eff = r["to"]
    return {"inst_id": inst_id, "base": m.get("confidence"), "effective": eff,
            "summary": m["summary"][:60]}


def confidence_dynamics_scan():
    """E5 夜间被动兜底：统计叠加层 + 检查待重审积压。不做任何写入外的决策。"""
    report = {"total_events": 0, "upgrades": 0, "downgrades": 0,
              "retrofix_pending": 0, "type_rules": 0}
    if not os.path.exists(OVERLAY):
        return report
    types = set()
    with io.open(OVERLAY, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            report["total_events"] += 1
            if r.get("op") == "upgrade":
                report["upgrades"] += 1; types.add(r.get("type_key"))
            elif r.get("op") == "downgrade":
                report["downgrades"] += 1; types.add(r.get("type_key"))
            elif r.get("op") == "retrofix_pending":
                report["retrofix_pending"] += 1
    report["type_rules"] = len(types)
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "falsify", "effective", "scan"])
    ap.add_argument("--id")
    ap.add_argument("--ev", default="")
    ap.add_argument("--note")
    a = ap.parse_args()
    if a.cmd == "scan":
        print(json.dumps(confidence_dynamics_scan(), ensure_ascii=False))
    elif a.cmd == "effective":
        print(json.dumps(effective_confidence(a.id), ensure_ascii=False))
    elif a.cmd == "verify":
        print(json.dumps(mark_verified(a.id, a.ev, new_summary=a.note), ensure_ascii=False))
    elif a.cmd == "falsify":
        print(json.dumps(mark_falsified(a.id, a.ev, retrofix_note=a.note), ensure_ascii=False))
