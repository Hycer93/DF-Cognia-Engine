"""#23 冒烟验证（只验路由行为，不做质量评估，不跑 executor 链）。

- 只调 router.route()：每条 = 1 次 classify LLM
- 校验点：simple 3节点 / medium ~8节点 / complex 14节点，router_log 记录正确
- 不跑 score.py，不评分，不消耗执行链 token
用法: NO_MEMORY=1 python eval/experiment/smoke_router_23.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
os.environ.setdefault("NO_MEMORY", "1")

import yaml
from cgraph.graph import MindGraph
from cgraph.router import ComplexityRouter

# 4 条：1 simple + 2 medium + 1 complex
CASES = [
    ("simple", "法国首都是哪里", 3),
    ("medium", "分析一下我们团队过去半年的技术债主要集中在哪些模块", 8),
    ("medium", "评估这个新功能的实现难度和可能的瓶颈点", 8),
    ("complex", "公司要在纯自研大模型和采购第三方云服务之间做战略决策，涉及成本、数据安全、团队能力、长期演进多个博弈方，帮我做完整的风险与收益权衡", 14),
]

TOL = 2  # 路径节点数允许误差（medium 目标8，贪心实际可能7-9）


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config", "config.yaml"), encoding="utf-8"))
    graph_file = os.path.join(HERE, cfg["graph"]["data_file"])
    g = MindGraph.load(graph_file)

    # 真实 LLM（codechat），只用于 classify
    from cgraph.llm_codechat import CodeChatLLM
    model = cfg["llm"]["codechat"].get("model") or None
    llm = CodeChatLLM(model=model)

    router = ComplexityRouter(g, cfg.get("router"), llm=llm)
    router_log = os.path.join(HERE, "logs", "router_log.jsonl")
    os.makedirs(os.path.dirname(router_log), exist_ok=True)

    print("=" * 60)
    print("#23 Router 冒烟验证（只验行为，不评质量）")
    print("=" * 60)

    results = []
    for expect_depth, q, expect_len in CASES:
        t0 = time.time()
        d = router.route(q)
        ms = int((time.time() - t0) * 1000)
        # 写 router_log（与 engine._log_route 同格式）
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "q": q[:120],
               "complexity": d.complexity, "path_len": len(d.path), "path": d.path,
               "domain_len": len(d.domain_nodes), "reason": d.reason, "smoke": True}
        with open(router_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        ok_cls = (d.complexity == expect_depth)
        ok_len = abs(len(d.path) - expect_len) <= TOL
        verdict = "✅" if (ok_cls and ok_len) else "⚠"
        print(f"\n{verdict} [{expect_depth:7}期望] -> 实际 {d.complexity:7} | {len(d.path)}节点 "
              f"(期望~{expect_len}, {ms}ms)")
        print(f"   path={d.path}")
        print(f"   reason={d.reason}")
        results.append({"expect": expect_depth, "got": d.complexity,
                        "len": len(d.path), "expect_len": expect_len,
                        "cls_ok": ok_cls, "len_ok": ok_len})

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["cls_ok"] and r["len_ok"])
    print(f"冒烟结果：{passed}/{len(results)} 符合预期")
    print(f"router_log: {router_log}")
    print("注：本冒烟仅验证路由行为符合配置，不替代正式质量回归/盲评。")


if __name__ == "__main__":
    main()