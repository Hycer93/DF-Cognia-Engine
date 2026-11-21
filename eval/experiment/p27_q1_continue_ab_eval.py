"""p27_q1_continue_ab_eval.py — P2-7 Q1 continue 修复的影响评估（A/B，只读）

背景: memory_judge._stub_one 的 Q1 分支(discard)未 continue/return,
后续 Q3/Q4 继续执行, 孤条场景(零血缘)会被 Q4 覆写为 hold。
P2-7 拟给 Q1 分支补 continue, 使 discard 不被 Q4 覆盖。

本脚本为**改动前的影响评估**(用户裁决: 严禁提前改代码, 必须先评估):
  - 遍历现有 store_pending.jsonl 全部条目(即 hold 池现存条目)
  - 对每条分别用【现实现】与【Q1-continue 模拟版】判定
  - 统计: 多少条从 hold→discard, 多少不变, 并列出转变明细
  - 另对 judge_log 历史中 verdict=hold 的快照做同口径回放

不修改任何代码/数据, 只输出日志与报告。退出码恒 0(评估非门禁)。
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))


def _simulate_q1_continue(c):
    """模拟 Q1 补 continue 后的判定: 仅 Q1 条件命中时直接 discard, 否则走原逻辑。

    为隔离 Q1 影响, 这里 Q1 命中即返回 discard; 其余等价复用现 _stub_one 的后半段
    (通过构造一份"Q1 必不命中"的等价调用实现: 调用现 _stub_one 但先判 Q1)。
    """
    summary = (c.get("summary") or "").strip()
    if not summary or len(summary) < 8:
        return {"verdict": "discard", "memory_class": c.get("memory_class"),
                "conflict_suspect": False,
                "reasons": ["Q1fail: summary空/占位，无新认知(continue)"],
                "evidence_gap": None}
    # Q1 不命中 → 现实现后续逻辑(与补 continue 后完全一致)
    import memory_judge as MJ
    return MJ._stub_one(c)


def _load_pending():
    p = os.path.join(_HERE, "data", "memory", "store_pending.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]


def _load_judge_hold_snapshots():
    """从 judge_log 提取历史 verdict=hold 的记录(用于 Q1 影响回放)。

    judge_log 每条通常是 {summary, verdict, reasons, ...} 的单条判定日志,
    不含完整候选输入(detail/behavior 等)。但 Q1 判定只依赖 summary,
    因此可用 summary 做 Q1 维度的精确回放: Q1 命中(summary空/<8)才会 discard。
    返回 (records, skipped): records 为可做 Q1 回放的 hold 记录。"""
    p = os.path.join(_HERE, "data", "memory", "judge_log.jsonl")
    if not os.path.exists(p):
        return [], 0
    holds, skipped = [], 0
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("op") != "judge" and "verdict" not in rec:
            skipped += 1
            continue
        if rec.get("verdict") != "hold":
            continue
        # Q1 回放只需 summary; 缺 summary 字段的算不可回放
        if "summary" not in rec:
            skipped += 1
            continue
        holds.append(rec)
    return holds, skipped


def main():
    import memory_judge as MJ

    report = {"pending": {}, "judge_log": {}, "transitions": []}

    # ---- A/B: store_pending(hold 池现存) ----
    pending = _load_pending()
    p_total = len(pending)
    p_change, p_same = [], 0
    for c in pending:
        old = MJ._stub_one(c)
        new = _simulate_q1_continue(c)
        if old["verdict"] != new["verdict"]:
            p_change.append({"summary": (c.get("summary") or "")[:40],
                             "old": old["verdict"], "new": new["verdict"],
                             "why": new["reasons"][0] if new["reasons"] else ""})
        else:
            p_same += 1
    report["pending"] = {"total": p_total, "changed": len(p_change), "same": p_same}
    print(f"[store_pending] 共 {p_total} 条; verdict 变化 {len(p_change)} 条:")
    for t in p_change:
        print(f"  {t['old']}→{t['new']} | {t['summary']} | {t['why']}")
    print(f"  未变 {p_same} 条")

    # ---- A/B: judge_log 历史 hold 记录 Q1 维度回放 ----
    holds, skipped = _load_judge_hold_snapshots()
    j_total = len(holds)
    j_change = []
    for rec in holds:
        summary = (rec.get("summary") or "").strip()
        # Q1 命中条件: summary 空 或 <8 字符 → 补 continue 后会变 discard
        if not summary or len(summary) < 8:
            j_change.append({"summary": summary[:40], "old": "hold", "new": "discard"})
    report["judge_log"] = {"replayable_hold": j_total, "changed": len(j_change),
                           "skipped_no_summary": skipped}
    print(f"\n[judge_log] 可做 Q1 回放的 hold 记录 {j_total} 条(跳过 {skipped}); "
          f"Q1 命中→discard {len(j_change)} 条")

    # ---- 汇总影响判定 ----
    total_change = len(p_change) + len(j_change)
    total_pool = p_total + j_total
    report["summary"] = {
        "total_evaluated": total_pool,
        "total_changed": total_change,
        "change_rate": round(total_change / total_pool, 4) if total_pool else 0.0,
        "all_transitions_are_hold_to_discard": all(
            t["old"] == "hold" and t["new"] == "discard"
            for t in (p_change + [{"old": x["old"], "new": x["new"]} for x in j_change])
        ) if (p_change or j_change) else True,
    }
    print(f"\n[汇总] 评估 {total_pool} 条, 变化 {total_change} 条 "
          f"(变化率 {report['summary']['change_rate']:.2%}); "
          f"全部为 hold→discard: {report['summary']['all_transitions_are_hold_to_discard']}")

    out = os.path.join(_HERE, "logs", "p27_q1_continue_ab_eval.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    print(f"[写入] {out}")


if __name__ == "__main__":
    main()