"""
夜间实验 2：反问门 match_eff 标定扫描（E2）

背景：GATE_MATCH_THRESHOLD=0.15 是 2026-11-08 用 7 样本人工标的断崖值。
样本太少（真命中 0.24-0.53 / 真模糊 0.08-0.13 / 断崖 0.13-0.24），
阈值的稳健性未经系统验证。

做法：构造正负两组查询（从 memory 账本 61 条真记忆 + 探针语义变体），
逐条调 memory_query.ask 记录 match_eff 分布，然后扫描阈值 0.08~0.30：
  - 正组（应命中的查询）：召回率曲线
  - 负组（不应命中的无主语/跨主题查询）：误报率曲线
  - 找 F1 最优点与当前 0.15 的距离，输出推荐阈值带

不改动 GATE_MATCH_THRESHOLD 本身——只产出标定报告，改值留给用户晨间裁决。

用法: NO_MEMORY=1 python eval/e2_gate_calibration.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

import memory_query as mq          # noqa: E402
import constants as C              # noqa: E402

E2_OUT = os.path.join(HERE, "logs", "e2_gate_calibration.json")
GATE_REGRESSION = os.path.join(HERE, "data", "probe", "gate_regression.jsonl")


def run_gate_regression():
    """固化回归用例（2026-11-09 落地）：双门阈值/锚点逻辑任何改动后必须全绿。
    POS=应放行(低分合法长模板) / NEG=应拦截(无锚) /
    CONDITIONAL=上下文依赖条件样例（2026-11-09 用户裁决：分场景二元定性，不做静态二元标签）——
    anchored 分支=有 bug 记忆实体锚（cited_log 有记录）→ 应放行；
    cold 分支=空会话无锚 → 应反问。当前 cited_log 有近7天记录，等效跑 anchored 分支；
    cold 分支需人工清空 cited_log 验证，此处记录期望不自动断言。
    TIME_WINDOW_HIT=时间回归（2026-11-09 design_event_ts.md 裁决）：
    应开时间窗（expr 匹配用例声明）且 top1 命中条目 event_ts 落窗内。"""
    if not os.path.exists(GATE_REGRESSION):
        return None
    results = []
    for line in open(GATE_REGRESSION, encoding="utf-8"):
        if not line.strip():
            continue
        c = json.loads(line)
        if c.get("expect") == "conditional":
            # 条件样例：当前环境(cited_log非空)验 anchored 分支即可
            try:
                mq.ask(c["q"], top_k=5, include_corpus=True)
                v = getattr(mq.ask, "last_verdict", {})
                verdict = v.get("verdict", "")
                anchored_ok = verdict in ("hit", "marginal_hit")
                results.append({"id": c["id"], "q": c["q"][:40],
                                "expect": "conditional(anchored分支)",
                                "verdict": verdict, "match_eff": v.get("match_eff"),
                                "ok": anchored_ok,
                                "cold_branch_expect": "uncertain/marginal_ask(需空cited_log人工验)"})
            except Exception as e:
                results.append({"id": c["id"], "q": c["q"][:40],
                                "expect": "conditional", "verdict": f"error:{e}",
                                "ok": False})
            continue
        if c.get("expect") == "time_window_hit":
            # 时间回归（design_event_ts.md）：窗开 + expr 正确 + top1 event_ts 落窗内
            try:
                r = mq.ask(c["q"], top_k=5, include_corpus=True)
                tw = getattr(mq.ask, "last_time_window", None)
                ok = False
                detail = "未开时间窗"
                if tw and tw["expr"] == c.get("window_expr"):
                    if r:
                        from datetime import date
                        ed = mq._entry_event_date(r[0][1], mq._load_event_ts_overlay())
                        if ed is None:
                            detail = "top1 时间未知"
                        else:
                            f = date.fromisoformat(tw["from"]); t = date.fromisoformat(tw["to"])
                            ok = f <= ed <= t
                            detail = "top1 event %s 窗 %s~%s" % (ed, tw["from"], tw["to"])
                    else:
                        detail = "无命中条目"
                elif tw:
                    detail = "窗 expr=%s 期望=%s" % (tw["expr"], c.get("window_expr"))
                results.append({"id": c["id"], "q": c["q"][:40],
                                "expect": "time_window_hit", "verdict": detail,
                                "ok": ok})
            except Exception as e:
                results.append({"id": c["id"], "q": c["q"][:40],
                                "expect": "time_window_hit", "verdict": f"error:{e}",
                                "ok": False})
            continue
        try:
            mq.ask(c["q"], top_k=5, include_corpus=True)
            v = getattr(mq.ask, "last_verdict", {})
            verdict = v.get("verdict", "")
            passed = verdict in ("hit", "marginal_hit") if c["expect"] == "pass" \
                else verdict in ("uncertain", "marginal_ask", "empty") if c["expect"] == "block" \
                else None
            results.append({"id": c["id"], "q": c["q"][:40], "expect": c["expect"],
                            "verdict": verdict, "match_eff": v.get("match_eff"),
                            "ok": passed})
        except Exception as e:
            results.append({"id": c["id"], "q": c["q"][:40], "expect": c["expect"],
                            "verdict": f"error:{e}", "ok": False})
    n_assert = sum(1 for r in results if r["ok"] is not None)
    n_ok = sum(1 for r in results if r["ok"] is True)
    return {"cases": results, "assert_total": n_assert, "assert_pass": n_ok,
            "all_green": n_ok == n_assert}


def build_positive_queries():
    """正组：每条真记忆生成一个'应命中'查询（改写摘要，去原词换说法）。
    口径修正（2026-11-09 尸检）：检索范围与目标一致——corpus 目标就带 corpus 检索。
    旧版一律 include_corpus=False，corpus 目标被排除出检索范围，制造了假性低分长尾。"""
    mems = mq.load_memories()
    queries = []
    for m in mems:
        summ = (m.get("summary") or "")[:80]
        if not summ:
            continue
        entity = m.get("entity_id") or m.get("inst_id")
        queries.append({"q": f"关于{summ[:40]}这件事后来怎么样了？现在怎么处理",
                        "want_inst": m.get("inst_id"), "entity": entity,
                        "is_corpus": m.get("asset_class") == "corpus",
                        "why": "实体改写应命中"})
    return queries


def build_negative_queries():
    """负组：无主语/跨主题漂移查询——match_eff 应低于阈值"""
    return [
        {"q": "那个东西后来怎么样了", "why": "无任何实义（地板分）"},
        {"q": "我们之前讨论的那个方案定了没", "why": "无主语泛指"},
        {"q": "帮我看看这个代码写得怎么样", "why": "无上下文新任务"},
        {"q": "今天天气适合出游吗", "why": "完全无关域"},
        {"q": "帮我写个周报", "why": "无主题泛任务"},
        {"q": "上次说的那个 bug 修了吗", "why": "泛指无实体"},
        {"q": "你觉得这样做对吗", "why": "无指代反问"},
        {"q": "继续", "why": "单字续写"},
    ]


def main():
    pos = build_positive_queries()
    neg = build_negative_queries()
    print(f"[E2] positive={len(pos)} negative={len(neg)}", flush=True)

    pos_scores = []
    for i, p in enumerate(pos):
        try:
            mq.ask(p["q"], top_k=5, include_corpus=p.get("is_corpus", True))
            v = getattr(mq.ask, "last_verdict", {})
            me = v.get("match_eff", 0.0)
            top1_hit = v.get("verdict") == "hit"
            # 命中的 top1 是否就是 want_inst（精确命中）或同 entity（同体命中）
            pos_scores.append({"q": p["q"][:60], "match_eff": me,
                               "verdict": v.get("verdict"),
                               "want": p["want_inst"]})
            print(f"[E2 pos {i+1}/{len(pos)}] me={me:.3f} {v.get('verdict')}", flush=True)
        except Exception as e:
            print(f"[E2 pos {i+1}] error {e}", flush=True)

    neg_scores = []
    for i, p in enumerate(neg):
        try:
            mq.ask(p["q"], top_k=5, include_corpus=False)
            v = getattr(mq.ask, "last_verdict", {})
            me = v.get("match_eff", 0.0)
            neg_scores.append({"q": p["q"], "match_eff": me,
                               "verdict": v.get("verdict"), "why": p["why"]})
            print(f"[E2 neg {i+1}/{len(neg)}] me={me:.3f} {v.get('verdict')} ({p['why']})", flush=True)
        except Exception as e:
            print(f"[E2 neg {i+1}] error {e}", flush=True)

    # 阈值扫描
    pos_me = [s["match_eff"] for s in pos_scores]
    neg_me = [s["match_eff"] for s in neg_scores]
    sweep = []
    best = None
    for th in [round(0.08 + 0.01 * k, 2) for k in range(23)]:
        rec = sum(1 for x in pos_me if x >= th) / max(1, len(pos_me))
        fp = sum(1 for x in neg_me if x >= th) / max(1, len(neg_me))
        prec = rec / (rec + fp) if (rec + fp) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        sweep.append({"threshold": th, "recall": round(rec, 3),
                      "false_rate": round(fp, 3), "precision": round(prec, 3),
                      "f1": round(f1, 3)})
        if best is None or f1 > best["f1"]:
            best = sweep[-1]

    # 双门分层评估（2026-11-09 方案C 落地后的真 verdict 口径）：
    #   放行 = hit / marginal_hit（边缘带锚点放行）→ 对正例算召回，对负例算漏报
    #   拦截 = uncertain / marginal_ask / empty
    # 单阈值 sweep 保留作历史对照。
    def _pass(s):
        return s["verdict"] in ("hit", "marginal_hit")

    dual = {
        "pos_pass": sum(1 for s in pos_scores if _pass(s)),
        "pos_n": len(pos_scores),
        "neg_leak": [s["q"] for s in neg_scores if _pass(s)],
        "neg_n": len(neg_scores),
        "pos_blocked_detail": [{"q": s["q"][:50], "me": s["match_eff"], "v": s["verdict"]}
                               for s in pos_scores if not _pass(s)],
    }
    dual["pos_recall"] = round(dual["pos_pass"] / max(1, dual["pos_n"]), 3)
    dual["neg_leak_rate"] = round(len(dual["neg_leak"]) / max(1, dual["neg_n"]), 3)

    report = {
        "current_threshold": C.GATE_MATCH_THRESHOLD,
        "dual_gate": dual,
        "pos_n": len(pos_scores), "neg_n": len(neg_scores),
        "pos_match_eff": sorted(pos_me, reverse=True)[:20],
        "pos_match_eff_tail": sorted(pos_me)[:10],
        "neg_match_eff": sorted(neg_me, reverse=True),
        "sweep": sweep,
        "best_f1_point": best,
        "verdict_015": {
            "recall@0.15": next(s["recall"] for s in sweep if s["threshold"] == 0.15),
            "fp@0.15": next(s["false_rate"] for s in sweep if s["threshold"] == 0.15),
        },
        "pos_details": pos_scores,
        "neg_details": neg_scores,
        "ts": __import__("time").strftime("%Y-%m-%d %H:%M"),
    }
    with open(E2_OUT, "w", encoding="utf-8") as f:
        reg = run_gate_regression()
        if reg:
            report["gate_regression"] = reg
        json.dump(report, f, ensure_ascii=False, indent=1)

    if reg:
        print(f"\n[E2] 固化回归: {reg['assert_pass']}/{reg['assert_total']} "
              f"{'全绿 ✓' if reg['all_green'] else '有红 ✗ — 见 gate_regression.cases'}", flush=True)
        for c in reg["cases"]:
            if c["ok"] is False:
                # time_window_hit 分支无 match_eff 键（用 verdict 承载窗明细）——.get 防KeyError
                print(f"  ✗ {c['id']} expect={c['expect']} verdict={c['verdict']} "
                      f"me={c.get('match_eff')}", flush=True)

    print("\n[E2] ===== 阈值扫描（节选） =====", flush=True)
    for s in sweep[::3]:
        print(f"  th={s['threshold']:.2f} recall={s['recall']:.2f} "
              f"fp={s['false_rate']:.2f} f1={s['f1']:.2f}", flush=True)
    print(f"当前 0.15: recall={report['verdict_015']['recall@0.15']:.2%} "
          f"误报={report['verdict_015']['fp@0.15']:.2%}", flush=True)
    print(f"双门分层: 正例放行 {dual['pos_pass']}/{dual['pos_n']} "
          f"({dual['pos_recall']:.1%}) | 负例漏报 {len(dual['neg_leak'])}/{dual['neg_n']}", flush=True)
    if dual["neg_leak"]:
        print(f"  负例漏报明细: {dual['neg_leak']}", flush=True)
    print(f"F1 最优: th={best['threshold']} f1={best['f1']}", flush=True)
    print(f"报告: {E2_OUT}", flush=True)


if __name__ == "__main__":
    main()