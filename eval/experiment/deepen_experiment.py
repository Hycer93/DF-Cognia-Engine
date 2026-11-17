"""
DEEPEN 自主加深激活实验：3 道有隐藏疑点的复杂题，
观察强化引导后模型是否会主动输出 [[DEEPEN:节点码]]。

用法: python eval/deepen_experiment.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

QUESTIONS = [
    # 有隐藏动机可挖 → 期望加深 Bb(意图检测) 或 Cg(利益者全景)
    "同事主动帮我挡了一次失误，之后开始频繁找我帮忙。我该怎么看待这件事？",
    # 有量化风险可挖 → 期望加深 Fp(动态风险量化) 或 Gs(复杂难题破解)
    "我想把家里全部积蓄投进朋友推荐的餐饮项目，他说稳赚。给我建议。",
    # 有多层验证可挖 → 期望加深 Dk/Dl(直觉锚定/完美性怀疑)
    "面试官最后说'我们内部再讨论一下'，我是不是基本稳了？",
]


def main():
    from cgraph.engine import Engine
    e = Engine()
    for i, q in enumerate(QUESTIONS):
        print(f"\n>>> 题{i+1}: {q}", flush=True)
        t0 = time.time()
        r = e.run(q)
        dt = time.time() - t0
        print(f"  计划路径: {'→'.join(r['planned_path'])}", flush=True)
        print(f"  实际路径: {'→'.join(r['actual_path'])}", flush=True)
        if r["deepen_events"]:
            print(f"  ★ 自主加深: {r['deepen_events']}", flush=True)
        else:
            print(f"  ✗ 未触发加深", flush=True)
        print(f"  耗时 {dt:.0f}s | 意图: {r['verify_note']}", flush=True)
        with open(os.path.join(HERE, "logs", "deepen_experiment.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"q": q, "result": r}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()