"""
离线自测：不依赖 LLM/网络/chromadb，验证核心链路。
用 MockLLM 跑：路由 → 走链 → 意图回归 → 赫布更新 → 路径胜率。

用法: python scripts/smoke_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.graph import MindGraph, MANDATORY_NODES            # noqa: E402
from cgraph.router import ComplexityRouter                      # noqa: E402
from cgraph.executor import ChainExecutor                       # noqa: E402
from cgraph.hebbian import HebbianUpdater, WalkFeedback          # noqa: E402

GRAPH_FILE = os.path.join(HERE, "data", "mind_graph.json")
LOG = os.path.join(HERE, "logs", "edge_log_smoke.jsonl")


class MockLLM:
    """假 LLM：按节点返回覆盖关键词的回答，模拟一次成功走链"""
    def __init__(self):
        self.n = 0

    def chat(self, messages):
        self.n += 1
        last = messages[-1]["content"]
        # 抽 user 原始问题里的词，保证意图回归比率 > 0.3
        user = next(m["content"] for m in messages if m["role"] == "user")
        return f"关于[{user[:30]}]的多角度分析（第{self.n}步 {last[:20]}...）：考虑利益者全景、验证意图回归、总结要点。捧杀 潜力"


def main():
    g = MindGraph.load(GRAPH_FILE)
    router = ComplexityRouter(g)
    ex = ChainExecutor(g, MockLLM())
    he = HebbianUpdater(g)

    ok = True
    # 1. 保底路由：简单问题
    d1 = router.route("你好")
    assert d1.is_valid and d1.complexity == "simple", f"简单路由失败: {d1}"
    print(f"[1] 简单任务: {d1.path} ✓ (仅保底三条)")

    # 2. 复杂任务主路径
    d2 = router.route("对比三个技术方案的风险并做架构决策，需要多维度分析")
    assert d2.is_valid and d2.complexity == "complex", f"复杂路由失败: {d2}"
    print(f"[2] 复杂任务: {'→'.join(d2.path)} ✓ ({len(d2.path)} 节点, 域 {len(d2.domain_nodes)})")

    # 3. 走链 + 意图回归
    r2 = ex.run("对比方案风险并决策，涉及捧杀判断", d2)
    print(f"[3] 走链完成: {len(r2.path)} 节点 | {r2.verify_note}")
    assert "捧杀" in r2.final_answer

    # 4. 赫布更新（正/负各一次）+ 边权变化
    before = g.path_weight(r2.path)
    he.apply(WalkFeedback(path=r2.path, verify_ratio=0.8, utility=1.0), GRAPH_FILE, LOG)
    after_pos = g.path_weight(r2.path)
    g2 = MindGraph.load(GRAPH_FILE)  # 持久化回读
    he2 = HebbianUpdater(g2)
    he2.apply(WalkFeedback(path=r2.path, verify_ratio=0.1, utility=-1.0), GRAPH_FILE, LOG)
    after_neg = g2.path_weight(r2.path)
    assert after_pos > before, f"正反馈未增权: {before}→{after_pos}"
    assert after_neg < after_pos, f"负反馈未降权: {after_pos}→{after_neg}"
    print(f"[4] 赫布: 路径权 {before:.3f} → +ε {after_pos:.3f} → -ε {after_neg:.3f} ✓")

    # 5. 胜率统计（蒸馏预留）
    rates = HebbianUpdater.path_win_rates(LOG)
    assert rates, "胜率统计为空"
    sample = list(rates.items())[:3]
    print(f"[5] 路径胜率: {sample} ✓")

    print("\n全部自测通过 ✓  （LLM/记忆层接线请跑: python eval/ab_test.py）")


if __name__ == "__main__":
    main()