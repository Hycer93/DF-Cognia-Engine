"""
Dl 路由回归（2026-11-09 固化）：只测 ComplexityRouter 判定，不走链，秒级。
用例：heldout 野生 3 条 + 昨晚 E1 漏触代表 8 条（Dl-R01~R08 对抗检验类）。

背景：E1 夜跑 Dl 正例召回 12/25(48%)、heldout 0/3——漏触题全部被判 simple
（planned=[Aa,Eo,Jw]，域内无 Dl）。修复方向（待用户裁决）：router prompt 的
深度标准里补"对抗检验/反向复盘/终检纪律"类任务应判 medium+，或把 Dl 加进
_classify_llm 的加深候选列表。

判定口径：target 进 path 或 domain_nodes 都算可触发（DEEPEN 可救），
但 path 含 = 强触发，domain 含 = 弱触发（依赖 executor 自决）。

用法: NO_MEMORY=1 python eval/dl_route_regression.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("NO_MEMORY", "1")

from cgraph.engine import Engine  # noqa: E402

CASES = os.path.join(HERE, "data", "probe", "dl_route_regression.jsonl")
OUT = os.path.join(HERE, "logs", "dl_route_regression.json")


def main():
    e = Engine()
    cases = [json.loads(l) for l in open(CASES, encoding="utf-8") if l.strip()]
    strong = weak = fail = 0
    results = []
    for c in cases:
        d = e.router.route(c["input"])
        in_path = c["target"] in d.path
        in_domain = c["target"] in d.domain_nodes
        if in_path:
            strong += 1
        elif in_domain:
            weak += 1
        else:
            fail += 1
        results.append({"id": c["id"], "depth": d.complexity,
                        "in_path": in_path, "in_domain": in_domain})
        print(f"{c['id']} depth={d.complexity:<8} path含={in_path} domain含={in_domain}")
    n = len(cases)
    summary = {"total": n, "strong": strong, "weak_deepen": weak, "fail": fail,
               "cases": results,
               "note": "strong=路由主路径含Dl | weak=域内可达靠DEEPEN | fail=完全饿死"}
    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nDl 路由回归: strong {strong}/{n} + weak(DEEPEN可救) {weak} | 完全饿死 {fail}")
    print(f"报告: {OUT}")


if __name__ == "__main__":
    main()
