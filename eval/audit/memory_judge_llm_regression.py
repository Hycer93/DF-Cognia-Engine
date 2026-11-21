"""memory_judge_llm_regression.py — LLM 判定路径 + fallback 回归 (2026-11-21 P1-1)

口径: mock CodeChatLLM.chat 返回不同内容, 验证:
  L1 LLM 正常返回合法 JSON       → 走 LLM 路径, _source=llm
  L2 LLM 返回非 JSON 畸形        → 整体切桩, _source=stub
  L3 LLM chat 返回 None(调用异常) → 整体切桩, _source=stub, 不丢候选
  L4 LLM 单条漏答                → 缺的 idx 用桩补齐
  L5 LLM 单条 verdict 非法       → 该条降桩, 其余保留 LLM
  L6 JUDGE_USE_LLM=0             → 全走桩, 不调用 LLM

只读 _judge_call, 不落库。退出码 0=全过 1=有失败。输出 logs/memory_judge_llm_regression.json。
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
OUT = os.path.join(_HERE, "logs", "memory_judge_llm_regression.json")


def _cands():
    ld = ("该条为回归样本, 具体做法与结论完整给出, detail 长度超过三十字以满足 Q2 "
          "定义本体要求, 用于验证 store 路径。")
    return [
        {"summary": "回归样本一用于验证LLM路径store分支逻辑",
         "detail": ld, "memory_class": "scar_case", "behavior": "Dl", "domain": "005-003"},
        {"summary": "回归样本二用于验证LLM单条降级补充逻辑",
         "detail": ld, "memory_class": "skill_refine", "behavior": "Jw", "domain": "005-003"},
    ]


def _run_with_mock(mock_resp, cands, monkey_env=None):
    """注入 mock chat, 调 _judge_call, 返回 (verdicts, llm_call_count)。"""
    import memory_judge as mj
    import cgraph.llm_codechat as lc

    calls = {"n": 0}
    orig = lc.CodeChatLLM

    class MockLLM:
        def __init__(self, *a, **k):
            pass

        def chat(self, messages):
            calls["n"] += 1
            return mock_resp

    lc.CodeChatLLM = MockLLM
    old_env = {}
    if monkey_env:
        for k, v in monkey_env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        verdicts = mj._judge_call(cands, {"trigger": "regression"})
    finally:
        lc.CodeChatLLM = orig
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return verdicts, calls["n"]


def main():
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})

    # L1 LLM 正常返回合法 JSON (2 条都 store)
    good = json.dumps({"verdicts": [
        {"idx": 0, "verdict": "store", "memory_class": "scar_case",
         "conflict_suspect": False, "reasons": ["Q1pass", "Q5pass"], "evidence_gap": None},
        {"idx": 1, "verdict": "hold", "memory_class": "skill_refine",
         "conflict_suspect": True, "reasons": ["Q4缺血缘"], "evidence_gap": "缺血缘"},
    ]}, ensure_ascii=False)
    v, n = _run_with_mock(good, _cands())
    check("L1_llm_normal", v.get(0, {}).get("verdict") == "store"
          and v[0].get("_source") == "llm" and n == 1,
          f"v0={v.get(0)} calls={n}")
    check("L1b_llm_hold_preserved", v.get(1, {}).get("verdict") == "hold"
          and v[1].get("conflict_suspect") is True and v[1].get("_source") == "llm",
          f"v1={v.get(1)}")

    # L2 LLM 返回非 JSON → 整体切桩
    v, n = _run_with_mock("这不是JSON，只是一段白话说明", _cands())
    check("L2_malformed_to_stub", all(x.get("_source") == "stub" for x in v.values())
          and len(v) == 2, f"sources={[x.get('_source') for x in v.values()]}")

    # L3 LLM 返回 None → 整体切桩, 不丢候选
    v, n = _run_with_mock(None, _cands())
    check("L3_none_to_stub", all(x.get("_source") == "stub" for x in v.values())
          and len(v) == 2, f"count={len(v)}")

    # L4 LLM 单条漏答 (只答 idx=0) → idx=1 用桩补齐
    only0 = json.dumps({"verdicts": [
        {"idx": 0, "verdict": "store", "memory_class": "scar_case",
         "conflict_suspect": False, "reasons": ["Q1pass"], "evidence_gap": None}]},
        ensure_ascii=False)
    v, n = _run_with_mock(only0, _cands())
    check("L4_missing_idx_stub_fill", v.get(0, {}).get("verdict") == "store"
          and v[0].get("_source") == "llm"
          and v.get(1, {}).get("_source") == "stub" and len(v) == 2,
          f"v0={v.get(0,{}).get('_source')} v1={v.get(1,{}).get('_source')}")

    # L5 LLM 单条 verdict 非法 → 该条降桩, 其余保留
    bad_verdict = json.dumps({"verdicts": [
        {"idx": 0, "verdict": "maybe", "memory_class": "scar_case",
         "conflict_suspect": False, "reasons": ["?"], "evidence_gap": None},
        {"idx": 1, "verdict": "store", "memory_class": "skill_refine",
         "conflict_suspect": False, "reasons": ["Q1pass"], "evidence_gap": None}]},
        ensure_ascii=False)
    v, n = _run_with_mock(bad_verdict, _cands())
    check("L5_bad_verdict_stub", v.get(0, {}).get("_source") == "stub"
          and v.get(1, {}).get("_source") == "llm"
          and v[1].get("verdict") == "store",
          f"v0_src={v.get(0,{}).get('_source')} v1_src={v.get(1,{}).get('_source')}")

    # L6 JUDGE_USE_LLM=0 → 全桩, 不调 LLM
    v, n = _run_with_mock(good, _cands(), monkey_env={"JUDGE_USE_LLM": "0"})
    check("L6_switch_off", all(x.get("_source") == "stub" for x in v.values())
          and n == 0, f"calls={n} sources={[x.get('_source') for x in v.values()]}")

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}")
    print(f"[judge LLM 回归] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()