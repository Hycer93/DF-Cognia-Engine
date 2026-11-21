"""hebbian_wording_regression.py — P2-3 文档如实化回归 (2026-11-21)

hebbian 实为"带衰减权重计数器"(weight += eps / weight *= decay), 非图学习/神经网络,
负反馈阈值 0.3 为业务先验无消融支撑。本回归固化"文档不得虚高"这一断言, 防回退。

检查:
  1. hebbian.py 不再含 "fire together, wire together" 虚高措辞
  2. hebbian.py 明示"不是神经网络/图学习"
  3. hebbian.py 明示阈值无消融实验支撑
  4. hebbian.py 明示更新规则为 +=eps / *=decay(如实描述实现)
  5. graph.py docstring 同步为"带衰减权重计数器"
  6. 实现未变: eps 默认 0.05 / decay 默认 0.995 / min_w 默认 0.05(纯文档改动)
退出码 0=全过 1=有失败。
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)


def main():
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), str(detail)[:200]))

    hb = io.open(os.path.join(_HERE, "cgraph", "hebbian.py"), encoding="utf-8").read()
    gr = io.open(os.path.join(_HERE, "cgraph", "graph.py"), encoding="utf-8").read()

    check("no_hype_phrase", "fire together, wire together" not in hb,
          "仍有 'fire together, wire together' 虚高措辞")
    check("declares_not_graph_learning",
          ("不是神经网络" in hb) and ("图学习" in hb),
          "未明示'不是神经网络/图学习'")
    check("declares_threshold_no_ablation",
          "无消融实验支撑" in hb or "无消融支撑" in hb,
          "未明示阈值无消融实验支撑")
    check("documents_add_decay_rule",
          ("+= eps" in hb.replace("weight += eps", "weight += eps")
           or "weight += eps" in hb) and ("*=" in hb),
          "未如实描述 +=eps / *=decay 计数规则")
    check("graph_doc_aligned", "带衰减权重计数器" in gr,
          "graph.py docstring 未同步")

    # 实现未变(纯文档): 从源码确认默认值
    from cgraph.hebbian import HebbianUpdater
    from cgraph.graph import MindGraph
    u = HebbianUpdater(MindGraph.__new__(MindGraph))
    check("impl_eps_default", abs(u.eps - 0.05) < 1e-9, f"eps={u.eps}")
    check("impl_decay_default", abs(u.decay - 0.995) < 1e-9, f"decay={u.decay}")
    check("impl_min_w_default", abs(u.min_w - 0.05) < 1e-9, f"min_w={u.min_w}")

    failed = [r for r in results if not r[1]]
    print()
    for name, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
    print(f"\n[hebbian 措辞如实化] {len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()