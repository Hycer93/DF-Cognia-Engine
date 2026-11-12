"""
逐题裁判：一次只评一条记录，结果追加写 verdict.jsonl。
用法: python eval/score_one.py <ab_result.jsonl> <index 0-based>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.llm import LLMClient  # noqa: E402

JUDGE_PROMPT = """你是回答质量评审员。对同一个问题有两个匿名回答（回答一、回答二）。
从以下维度评分（各 1-10）：多维性(是否考虑多角度)、意图贴合(是否回答了真正的问题)、
结构完整性(是否有验证/总结)、实用价值。
输出严格 JSON: {{"one": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"two": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"winner": "one"|"two"|"tie", "reason": "..."}}
问题: {q}
回答一: {a}
回答二: {b}"""


def main():
    src, idx = sys.argv[1], int(sys.argv[2])
    recs = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    rec = recs[idx]
    a_graph = idx % 2 == 0
    one = rec["graph"]["answer"] if a_graph else rec["bare"]["answer"]
    two = rec["bare"]["answer"] if a_graph else rec["graph"]["answer"]
    llm = LLMClient(os.path.join(HERE, "config", "config.example.yaml"))
    resp = llm.chat([{"role": "user", "content": JUDGE_PROMPT.format(
        q=rec["question"], a=str(one)[:3000], b=str(two)[:3000])}])
    winner = "parse_error"
    try:
        j = json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
        if j["winner"] == "tie":
            winner = "tie"
        elif (j["winner"] == "one") == a_graph:
            winner = "graph"
        else:
            winner = "bare"
        reason = j.get("reason", "")
        scores = {"one": j["one"], "two": j["two"]}
    except Exception:
        reason = str(resp)[:100]
        scores = {}
    out = {"idx": idx, "question": rec["question"][:40], "winner": winner,
           "reason": reason, "scores": scores, "a_was_graph": a_graph}
    vfile = os.path.join(HERE, "logs", "verdict.jsonl")
    with open(vfile, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()