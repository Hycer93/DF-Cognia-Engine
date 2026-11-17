"""
全量裁判：逐题调用 score_one 逻辑，支持断点续跑（读已有 verdict.jsonl 跳过）。
用法: python eval/judge_all.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

SRC = os.path.join(HERE, "logs", "ab_result_20261103_102050.jsonl")
VFILE = os.path.join(HERE, "logs", "verdict.jsonl")

JUDGE_PROMPT = """你是回答质量评审员。对同一个问题有两个匿名回答（回答一、回答二）。
从以下维度评分（各 1-10）：多维性(是否考虑多角度)、意图贴合(是否回答了真正的问题)、
结构完整性(是否有验证/总结)、实用价值。
输出严格 JSON: {{"one": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"two": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"winner": "one"|"two"|"tie", "reason": "..."}}
问题: {q}
回答一: {a}
回答二: {b}"""


def done_idx() -> set:
    if not os.path.exists(VFILE):
        return set()
    s = set()
    for line in open(VFILE, encoding="utf-8"):
        try:
            s.add(json.loads(line)["idx"])
        except Exception:
            pass
    return s


def main():
    from cgraph.llm import LLMClient
    llm = LLMClient(os.path.join(HERE, "config", "config.example.yaml"))
    recs = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    finished = done_idx()
    print(f"total {len(recs)}, already judged {len(finished)}", flush=True)

    for idx in range(len(recs)):
        if idx in finished:
            continue
        rec = recs[idx]
        a_graph = idx % 2 == 0
        one = rec["graph"]["answer"] if a_graph else rec["bare"]["answer"]
        two = rec["bare"]["answer"] if a_graph else rec["graph"]["answer"]
        t0 = time.time()
        resp = llm.chat([{"role": "user", "content": JUDGE_PROMPT.format(
            q=rec["question"], a=str(one)[:3000], b=str(two)[:3000])}])
        winner, reason, scores = "parse_error", str(resp)[:100], {}
        try:
            j = json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
            if j["winner"] == "tie":
                winner = "tie"
            elif (j["winner"] == "one") == a_graph:
                winner = "graph"
            else:
                winner = "bare"
            reason, scores = j.get("reason", ""), {"one": j["one"], "two": j["two"]}
        except Exception:
            pass
        out = {"idx": idx, "question": rec["question"][:40], "winner": winner,
               "reason": reason[:200], "scores": scores,
               "graph_scores": (scores.get("one") if a_graph else scores.get("two")),
               "bare_scores": (scores.get("two") if a_graph else scores.get("one"))}
        with open(VFILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
        print(f"[{idx}] {winner} ({time.time()-t0:.0f}s) {str(reason)[:60]}", flush=True)

    # 汇总
    wins = {"graph": 0, "bare": 0, "tie": 0, "parse_error": 0}
    for line in open(VFILE, encoding="utf-8"):
        w = json.loads(line)["winner"]
        wins[w] = wins.get(w, 0) + 1
    print("\n===== 验尸裁决 =====", flush=True)
    print(f"图路由: {wins.get('graph',0)} | 裸模型: {wins.get('bare',0)} | 平: {wins.get('tie',0)}"
          f" | 解析失败: {wins.get('parse_error',0)}", flush=True)


if __name__ == "__main__":
    main()