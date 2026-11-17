# -*- coding: utf-8 -*-
"""
e11_blind20.py — f_reason 20 样本盲评(上线双准入第②步, 2026-11-17 用户裁决口径)

口径(用户定案):
- 样本: 历史真实元核会话 goal(根因推测/故障复盘型), 非手工编造
- schema 硬约束: 20 条全过才算(出现 schema 违规=不通过)
  (非降级 / 无 plausibility / 假设≤5 / 每条有 test_next)
- 语义质量: ≥17/20 合格才通过(LLM 裁判盲评: 相关性/多角度/可验证性)
- memory_gate 顺带复用同套基础设施跑参考观测, 不作强制门槛

产物: data/experiment/f_reason_blind20_<ts>/
  - runs.jsonl     每样本 f_reason 原始产出
  - schema_check.json  硬约束逐条核对
  - blind_score.json   LLM 盲评(裁判不告知这是哪个技能产出)
  - report.md      汇总
用法: python -X utf8 eval/audit/e11_blind20.py
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "experiment",
                   "f_reason_blind20_" + time.strftime("%Y%m%d_%H%M%S"))
SAMPLES_FILE = os.path.join(HERE, "data", "experiment", "f_reason_blind20_samples.json")

SCHEMA_PASS_MIN = 20      # 硬约束: 全过
SEMANTIC_PASS_MIN = 17    # 语义: ≥17


def run_f_reason(llm, goal: str) -> dict:
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_f_reason import SkillFReason
    state = ThinkState(goal=goal, goal_anchored=goal, sub_questions=[],
                       summary=goal[:80], steps=1)
    r = SkillFReason(llm=llm).run(state)
    return r.result


def schema_check(res: dict) -> dict:
    """硬约束逐条(全部必须真, 出一即整体不通过)"""
    hyps = res.get("hypotheses") or []
    applicable = res.get("applicable")
    # 七段分歧⑤空谱三态: applicable=false+空谱=合法非降级(纯执行型无推演空间);
    # applicable=true+空谱=降级(O2前置防线); true+有效条目=正常
    legal_empty = (applicable is False and not hyps)
    c = {
        "non_degraded": not res.get("_degraded"),
        "legal_spectrum": legal_empty or (applicable is True and bool(hyps)),
        "no_plausibility": all("plausibility" not in h for h in hyps),
        "cap5": len(hyps) <= 5,
        "has_test_next": (not hyps) or all(h.get("test_next") for h in hyps),
        "schema_fields": all(set(h) >= {"hypothesis"} for h in hyps),
    }
    c["pass"] = all(v for k, v in c.items() if k != "pass")
    return c


BLIND_PROMPT = """你是假设谱质量评审员。下面是一个根因推测问题与一组竞争性假设。
对假设谱整体评三档维度(各 1-5 分):
- relevance: 假设与问题强相关, 无无关臆想
- coverage: 多角度覆盖可能性(不同解释方向, 非同一假设细分)
- verifiability: test_next 验证路径可执行
输出严格 JSON(不要其他文字):
{"relevance": n, "coverage": n, "verifiability": n,
 "qualify": true, "note": "一句话理由"}
qualify 判定: 三维均 ≥3 才 true(单维 1-2 分=明显瑕疵)。
问题: {q}
假设谱: {h}"""


def blind_score(llm, goal: str, hyps: list) -> dict:
    import re as _re
    h = json.dumps(hyps, ensure_ascii=False)[:1500]
    msg = [{"role": "user", "content": BLIND_PROMPT.replace("{q}", goal[:300]).replace("{h}", h)}]
    raw = llm.chat(msg) or ""
    t = raw.strip()
    if t.startswith("```"):
        t = _re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = _re.sub(r"\n?```\s*$", "", t)
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except Exception:
            pass
    return {"relevance": 0, "coverage": 0, "verifiability": 0,
            "qualify": False, "note": "裁判输出解析失败"}


def main():
    from cgraph.llm import LLMClient
    llm = LLMClient()
    os.makedirs(OUT, exist_ok=True)
    goals = json.load(io.open(SAMPLES_FILE, encoding="utf-8"))

    runs, schemas, semantics = [], [], []
    for i, goal in enumerate(goals, 1):
        t0 = time.time()
        res = run_f_reason(llm, goal)
        sc = schema_check(res)
        hyps = res.get("hypotheses") or []
        sm = blind_score(llm, goal, hyps) if hyps else             {"relevance": None, "coverage": None, "verifiability": None,
             "qualify": True, "note": "合法空谱(applicable=false), 免评"}
        runs.append({"i": i, "goal": goal, "secs": round(time.time() - t0, 1), "result": res})
        schemas.append({"i": i, "goal": goal[:50], **sc})
        semantics.append({"i": i, "goal": goal[:50], **sm})
        print(f"[{i:02d}/20] schema={'P' if sc['pass'] else 'F'} "
              f"semantic={'P' if sm.get('qualify') else 'F'} "
              f"({runs[-1]['secs']}s) {goal[:36]}", flush=True)

    schema_pass_n = sum(1 for s in schemas if s["pass"])
    semantic_pass_n = sum(1 for s in semantics if s.get("qualify"))
    overall = (schema_pass_n >= SCHEMA_PASS_MIN and semantic_pass_n >= SEMANTIC_PASS_MIN)

    io.open(os.path.join(OUT, "runs.jsonl"), "w", encoding="utf-8").write(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in runs))
    json.dump(schemas, io.open(os.path.join(OUT, "schema_check.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(semantics, io.open(os.path.join(OUT, "blind_score.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    md = [f"# f_reason 20 样本盲评报告", f"生成: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
          f"总判定: {'PASS 准入通过' if overall else 'FAIL 未通过'}", "",
          f"- schema 硬约束: {schema_pass_n}/20 (门槛 {SCHEMA_PASS_MIN}/20 全过)",
          f"- 语义质量: {semantic_pass_n}/20 (门槛 {SEMANTIC_PASS_MIN}/20)", "",
          "## schema 失败明细" if schema_pass_n < 20 else "## schema 全过"]
    for s in schemas:
        if not s["pass"]:
            bad = [k for k, v in s.items() if v is False]
            md.append(f"- [{s['i']:02d}] {s['goal']} 失败项: {','.join(bad)}")
    md.append("\n## 语义未合格明细" if semantic_pass_n < 20 else "\n## 语义全合格")
    for s in semantics:
        if not s.get("qualify"):
            md.append(f"- [{s['i']:02d}] {s['goal']} "
                      f"r{s.get('relevance')}/c{s.get('coverage')}/v{s.get('verifiability')} {s.get('note','')[:50]}")
    io.open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(md))

    print(f"\n[E11-blind20] schema {schema_pass_n}/20 | 语义 {semantic_pass_n}/20 "
          f"| 总判定 {'PASS' if overall else 'FAIL'} | 产物 {OUT}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()