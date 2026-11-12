"""语义路由修复后的 DEEPEN 复测：3 题端到端走链"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.engine import Engine

e = Engine()
qs = [
    "同事主动帮我挡了一次失误，之后开始频繁找我帮忙。我该怎么看待这件事？",
    "我想把家里全部积蓄投进朋友推荐的餐饮项目，他说稳赚。给我建议。",
    "面试官最后说'我们内部再讨论一下'，我是不是基本稳了？",
]
for i, q in enumerate(qs, 1):
    print(f">>> 题{i}: {q}", flush=True)
    t0 = time.time()
    r = e.run(q)
    print(f"  计划路径: {'->'.join(r['planned_path'])}", flush=True)
    print(f"  实际路径: {'->'.join(r['actual_path'])}", flush=True)
    tag = "[OK] 触发加深 " + ",".join(r["deepen_events"]) if r["deepen_events"] else "[NO] 未触发加深"
    print(f"  {tag}", flush=True)
    print(f"  耗时 {int(time.time()-t0)}s | {r['verify_note']}", flush=True)
    print(f"  回答前80字: {r['answer'][:80]}", flush=True)
    print(flush=True)