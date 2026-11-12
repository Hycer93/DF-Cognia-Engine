"""路由 LLM 化后的快速验证：3 道题看复杂度判定+加深候选"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.engine import Engine

e = Engine()
qs = [
    "面试官最后说我们内部再讨论一下，我是不是基本稳了？",
    "我想把家里全部积蓄投进朋友推荐的餐饮项目，他说稳赚。给我建议。",
    "今天天气怎么样",
]
for q in qs:
    d = e.router.route(q)
    print(f"[{d.complexity}] {d.reason}")
    print(f"  plan: {'->'.join(d.path)}")
    print(f"  domain: {' '.join(d.domain_nodes)}")
    print(f"  q: {q[:25]}")
    print()