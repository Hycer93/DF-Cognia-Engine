"""green_card_writer.py — 绿档落库 writer（P2-2 从 cgraph/executor.py 迁出）

职责单一：把 memory_gate 产出的绿档清单机械写入 core_memory_card，
并落一条独立审计行 op=memory_commit:green（段六b案：成败一律可追溯）。

迁出动机：executor.py 承担编排主链，DB 写入细节属独立职责（P2-2 职责分离）。
业务逻辑自 cgraph/executor.py:498-581 原样迁移，未改任何 SQL / 阈值 / 幂等语义。

DB 路径同源硬约束（原 R17 回归锚，2026-11-18 round5 自举审计）：
必须用 cgraph.atomic_skill.AUDIT_DB 绝对路径，禁止相对路径硬编码
（CWD 漂移会静默落错库，曾是 E4 对账 drift 嫌疑根因）。
"""
import hashlib
import json
import sqlite3
import time
import uuid as _uuid
import sys

# 同源绝对路径（R17 锚点：AUDIT_DB as db）
from cgraph.atomic_skill import AUDIT_DB as db


def _jaccard(a: str, b: str) -> float:
    """bigram 重叠度: LLM 措辞漂移的语义级幂等兜底"""
    wa = {a[i:i + 2] for i in range(len(a) - 1)}
    wb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0


def commit_green_cards(result, gate, user_input: str):
    """绿档落库 writer(段二C案: 编排层机械执行, 技能外)。
    仅写绿档条目(asset_type ∈ 观测/测试结果/踩坑), 逐卡 full_hash 去重防重
    (段七红线5幂等), raw 回链锚=本会话 user_input+日期窗口(0f2f4458 硬约束)。
    段六b案: 成败一律落独立审计行 op=memory_commit:green。"""
    cards = [x for x in (result.result.get("gate_list") or []) if x["tier"] == "green"]
    if not cards or result.result.get("_degraded"):
        return {"written": 0, "skipped": len(cards)}
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    day = time.strftime("%Y-%m-%d")
    written, ids, errs = 0, [], []

    try:
        con = sqlite3.connect(db, timeout=5)
        # 语义级幂等补强(段七红线5编排侧实现): full_hash 只防完全同文,
        # 真实 LLM 两次盘点同资产措辞漂移→bigram-Jaccard>0.6 视为同资产跳过。
        # 对称口径: existing summary 剥 "[类型] "前缀与 "(evidence:...)"后缀后再比
        existing = []
        for r in con.execute(
                "SELECT summary FROM core_memory_card WHERE source_ref LIKE 'memory_gate%' "
                "AND ts LIKE ?", (day + "%",)).fetchall():
            e = r[0]
            e = e.split("] ", 1)[-1] if e.startswith("[") else e
            e = e.split("(evidence:", 1)[0]
            existing.append(e.strip())
        for x in cards:
            s = f"[{x['asset_type']}] {x['gist']}(evidence: {x['evidence_step']})"
            fh = hashlib.sha256((s + "\x1f" + user_input).encode()).hexdigest()
            if con.execute("SELECT 1 FROM core_memory_card WHERE full_hash=?",
                           (fh,)).fetchone():
                errs.append(f"dup:{x['gist'][:20]}")
                continue
            if any(_jaccard(x["gist"], e) > 0.6 for e in existing):
                errs.append(f"semidup:{x['gist'][:20]}")
                continue
            inst = _uuid.uuid4().hex
            con.execute(
                "INSERT INTO core_memory_card (inst_id, ts, event_ts, full_hash, "
                "asset_class, repro, confidence, dfcp_tag, summary, summary_converged, "
                "detail, source_ref, memory_class, tier, weight, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inst, ts, ts, fh, "runtime", "situational", "L2",
                 json.dumps({"domain": "001-003", "behavior": "Ht", "relation": "P",
                             "memory_class": None}, ensure_ascii=False),
                 s[:120], "0",
                 f"绿档自动立卡(memory_gate 0f2f4458双层写入规约); "
                 f"素材步: {x['evidence_step']}; 档案动词: {x['proposed_action']}",
                 f"memory_gate; raw锚: session {day}; user_input={user_input[:60]}",
                 None, "active", '{"match": 0.0, "cited": 0}', "active"))
            ids.append(inst[:16])
            written += 1
        con.commit()
        con.close()
    except Exception as e:
        errs.append(f"db_error:{type(e).__name__}")
    # 段六b案: 成败一律落独立审计行
    try:
        con = sqlite3.connect(db, timeout=5)
        con.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                    (ts, "memory_commit:green",
                     json.dumps({"written": written, "cards": ids,
                                 "skipped": errs, "raw": f"session {day}",
                                 "user_input": user_input[:60]}, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:
        # 段六b案要求成败一律可追溯——审计失败不阻断链路但必须可见
        print(f"[memory_commit] 审计行落库失败(不阻断, 需人工核): {type(e).__name__}: {e}",
              file=sys.stderr)
    if written:
        print(f"[memory_gate] 绿档落库 {written} 张: {ids}")
    return {"written": written, "skipped": errs}