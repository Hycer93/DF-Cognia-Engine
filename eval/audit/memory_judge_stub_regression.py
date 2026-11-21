"""memory_judge_stub_regression.py — 规则桩判定矩阵固化 (2026-11-21 P1-1 前置)

背景: P1-1 要把 memory_judge._judge_call 拆成
     _judge_call(调度) / _judge_call_llm / _judge_call_stub / _stub_one,
     其中原桩逻辑逐字搬迁到 _judge_call_stub。本脚本先于改动固化现有桩的
     全部五问判定矩阵, 搬迁前后各跑一次做对照, 防搬代码时手滑改逻辑。

口径: 直接调用当前 _judge_call(桩) 的逐条判定, 断言 verdict/memory_class/
     conflict_suspect/evidence_gap 是否符合预期。只读, 不落库(不调 judge_and_store)。

运行: python eval/audit/memory_judge_stub_regression.py
     --baseline 把当前结果写入 baseline json(供搬迁后 --compare 对照)
退出码 0=全过 1=有断言失败。
"""
import io
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEM = os.path.join(_HERE, "memory")
sys.path.insert(0, _HERE)
sys.path.insert(0, _MEM)
OUT = os.path.join(_HERE, "logs", "memory_judge_stub_matrix.json")
BASE = os.path.join(_HERE, "logs", "memory_judge_stub_baseline.json")


def _cases():
    """(名称, 候选dict, 期望 verdict, 期望证据线索子串/None)。

    覆盖五问全部出口分支。期望值对齐**现桩实际行为**，
    基线作用靠 --baseline/--compare 对照，防止搬迁时手滑改逻辑。

    P2-7（2026-11-21）变更：Q1 命中改为 early return discard（一票否决），
    阻断后续 Q2-Q4。故 Q1 fail 样本期望由 hold 改为 discard，并新增
    "Q1 短摘要 + 有血缘重叠" 用例专测「即便 Q4 能判有血缘，也不得覆写 discard」。
    历史发现保留记录：
    2. Q4 用中文 2-gram，零重叠极难达成，构造的"独特"摘要仍判 store。
    """
    long_detail = ("该条为回归样本, 具体做法与结论完整给出, detail 长度超过三十字以满足 Q2 "
                   "定义本体要求, 用于验证 store 路径。")
    return [
        # Q1 fail: summary 空 / 过短 —— P2-7 后 early return discard，不再被 Q4 覆盖
        ("Q1_empty_summary", {"summary": "", "detail": long_detail,
                              "memory_class": "scar_case", "behavior": "Dl"}, "discard", "Q1fail"),
        ("Q1_short_summary", {"summary": "短", "detail": long_detail,
                              "memory_class": "scar_case", "behavior": "Dl"}, "discard", "Q1fail"),
        # P2-7 专测: 短摘要(必 Q1 fail) + 与库内高置信条目有 2-gram 重叠(Q4 本会判有血缘)，
        # 断言仍为 discard——证明 Q1 early return 阻断了 Q4 覆写。
        # "置信度五级" len=5<8 触发 Q1；实测与 L0 条目 "置信度五级分类L0-L4" 重叠 4 gram>0。
        ("Q1_discard_not_overwritten_by_q4",
         {"summary": "置信度五级", "detail": long_detail,
          "memory_class": "scar_case", "behavior": "Dl"}, "discard", "Q1fail"),
        # Q2 fail: detail 空 / 过短
        ("Q2_no_detail", {"summary": "这是一个足够长的摘要用于验证Q2分支逻辑",
                          "detail": "", "memory_class": "scar_case", "behavior": "Dl"}, "hold", "detail"),
        ("Q2_short_detail", {"summary": "这是一个足够长的摘要用于验证Q2分支逻辑",
                             "detail": "太短", "memory_class": "scar_case", "behavior": "Dl"}, "hold", "detail"),
        # Q3 fail: memory_class 白名单外
        ("Q3_bad_class", {"summary": "这是一个足够长的摘要用于验证Q3分支逻辑",
                          "detail": long_detail, "memory_class": "not_a_class",
                          "behavior": "Dl"}, "hold", "分类"),
        # Q4: 构造独特摘要 —— 现状确认中文 2-gram 仍可能命中库内条目（见发现2）
        ("Q4_unique", {"summary": "ZZZQQQ独有词组回归孤条样本qqqzzz",
                       "detail": long_detail, "memory_class": "scar_case",
                       "behavior": "Dl"}, "store", None),
        # Q5 fail: skill_refine 含个体指涉
        ("Q5_individual", {"summary": "优化方案供我自己使用的工程改进路径办法",
                           "detail": long_detail, "memory_class": "skill_refine",
                           "behavior": "Jw"}, "hold", "知识层"),
    ]


def _run_stub(cands):
    """调用当前桩判定, 返回逐条 verdict dict。不落库。

    强制 JUDGE_USE_LLM=0：本脚本固化的就是**规则桩**判定矩阵, 若走 LLM 路径
    结果随模型/网络漂移, 基线对照失去意义（P2-7 发现该脚本未强制开关）。
    """
    os.environ["JUDGE_USE_LLM"] = "0"
    import memory_judge as mj
    return mj._judge_call(cands, {"trigger": "regression"})


def _key(v):
    """抽取断言关心的字段(排除 reasons 文案, 避免文案微调误判)。"""
    return {"verdict": v.get("verdict"),
            "memory_class": v.get("memory_class"),
            "conflict_suspect": bool(v.get("conflict_suspect")),
            "evidence_gap": v.get("evidence_gap")}


def main():
    as_baseline = "--baseline" in sys.argv
    compare = "--compare" in sys.argv

    cases = _cases()
    cands = [c for _, c, _, _ in cases]
    verdicts = _run_stub(cands)

    results = []
    for i, (name, c, want_v, gap_kw) in enumerate(cases):
        v = verdicts.get(i, {})
        k = _key(v)
        ok = k["verdict"] == want_v
        if gap_kw not in ("", None):
            ok = ok and gap_kw in (k["evidence_gap"] or "") or \
                 gap_kw in " ".join(v.get("reasons") or [])
        results.append({"name": name, "want_verdict": want_v, "gap_kw": gap_kw,
                        "got": k, "ok": bool(ok)})

    # 基线写入 / 对照
    comparison = None
    if as_baseline:
        io.open(BASE, "w", encoding="utf-8").write(
            json.dumps({r["name"]: r["got"] for r in results},
                       ensure_ascii=False, indent=1))
        print(f"[baseline] 已写入 {BASE}")
    if compare and os.path.exists(BASE):
        base = json.loads(io.open(BASE, encoding="utf-8").read())
        diffs = []
        for r in results:
            b = base.get(r["name"])
            if b != r["got"]:
                diffs.append({"name": r["name"], "baseline": b, "current": r["got"]})
        comparison = {"diffs": diffs, "identical": not diffs}
        if diffs:
            for d in diffs:
                results.append({"name": "DIFF_" + d["name"], "ok": False,
                                "got": d["current"], "baseline": d["baseline"]})

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results, "comparison": comparison}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))

    for r in results:
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {r['name']}: got={r.get('got')}")
    if comparison is not None:
        print(f"[baseline对照] {'一致' if comparison['identical'] else '有差异'}")
    print(f"[桩判定矩阵] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()