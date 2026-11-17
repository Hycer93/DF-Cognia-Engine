"""
LLM 裁判盲评：对 ab_test 产物的 A/B 两组回答做盲评打分。

裁判用同一 LLM 后端，不告知哪组是哪路，输出: win/graph/bare/tie + 理由。
最终汇总裁决：图路由版是否真的优于裸模型（验尸结论）。

用法: python eval/score.py logs/ab_result_xxx.jsonl
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

from cgraph.llm import LLMClient  # noqa: E402

JUDGE_PROMPT = """你是回答质量评审员。对同一个问题有两个匿名回答（回答一、回答二）。
从以下维度评分（各 1-10）：多维性(是否考虑多角度)、意图贴合(是否回答了真正的问题)、
结构完整性(是否有验证/总结)、实用价值。
输出严格 JSON: {{"one": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"two": {{"multidim":n,"intent":n,"structure":n,"value":n}}, "winner": "one"|"two"|"tie", "reason": "..."}}
问题: {q}
回答一: {a}
回答二: {b}"""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    src = sys.argv[1]
    records = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    llm = LLMClient()

    # 随机盲化：偶数序 graph 作为"回答一"，奇数序相反
    wins = {"graph": 0, "bare": 0, "tie": 0}
    for i, rec in enumerate(records):
        a_graph = i % 2 == 0
        one = rec["graph"]["answer"] if a_graph else rec["bare"]["answer"]
        two = rec["bare"]["answer"] if a_graph else rec["graph"]["answer"]
        prompt = JUDGE_PROMPT.format(q=rec["question"], a=str(one)[:3000], b=str(two)[:3000])
        resp = llm.chat([{"role": "user", "content": prompt}])
        j = {}
        try:
            j = json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
            winner = "graph" if ((j["winner"] == "one") == a_graph and j["winner"] != "tie") else \
                     ("bare" if j["winner"] != "tie" else "tie")
        except Exception:
            winner = "tie"
        wins[winner] = wins.get(winner, 0) + 1
        print(f"[{i+1}] {winner:5s} | {j.get('reason', '')[:60]}")

    print("\n===== 验尸裁决 =====")
    print(f"图路由: {wins.get('graph',0)} | 裸模型: {wins.get('bare',0)} | 平: {wins.get('tie',0)}")
    if wins.get("graph", 0) > wins.get("bare", 0):
        print("结论: 图路由版有净增益——值得继续投入/进入蒸馏路线")
    elif wins.get("graph", 0) < wins.get("bare", 0):
        print("结论: 裸模型已覆盖——骨架退役，转向数据层+现代模型")
    else:
        print("结论: 无显著差异——建议增大样本或换更难的问题集")


if __name__ == "__main__":
    main()