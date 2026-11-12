"""
A/B 验尸测试：同一问题双路对比。

A 组: 裸模型（同一后端、同配置，单轮直答）
B 组: 图路由走链（cognitive_engine_v3 全流程）

输出: logs/ab_result_*.jsonl（含两组回答、路径、耗时），供人工盲评或后续自动评分。
评分器（可选）: eval/score.py 用同一模型做裁判对比两组回答。

用法:
  python eval/ab_test.py "问题"              # 单题
  python eval/ab_test.py --file questions.txt # 批量
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.llm import LLMClient            # noqa: E402
from cgraph.engine import Engine            # noqa: E402

BARE_PROMPT = "你是一个有用的助手。请直接、完整地回答用户的问题。"


def run_ab(engine: Engine, llm: LLMClient, question: str) -> dict:
    # A: 裸模型
    t0 = time.time()
    bare = llm.chat([
        {"role": "system", "content": BARE_PROMPT},
        {"role": "user", "content": question},
    ])
    bare_ms = int((time.time() - t0) * 1000)

    # B: 图路由（会触发赫布更新与记忆归档——注意：这会给 B 组累积优势，这正是要测的）
    t0 = time.time()
    r = engine.run(question)
    graph_ms = int((time.time() - t0) * 1000)

    return {
        "question": question,
        "bare": {"answer": bare, "ms": bare_ms},
        "graph": {"answer": r["answer"], "ms": graph_ms,
                  "path": r["actual_path"], "planned": r["planned_path"],
                  "deepen": r["deepen_events"], "verify": r["verify_note"]},
    }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    questions = []
    if args[0] == "--file":
        questions = [l.strip() for l in open(args[1], encoding="utf-8") if l.strip()]
    else:
        questions = [" ".join(args)]

    engine = Engine()
    llm = engine.llm

    out_file = os.path.join(HERE, "logs",
                            f"ab_result_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    os.makedirs(os.path.dirname(out_file), exist_ok=True)

    for q in questions:
        print(f"\n>>> {q}")
        rec = run_ab(engine, llm, q)
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  裸模型: {rec['bare']['ms']}ms | 图路由: {rec['graph']['ms']}ms "
              f"路径 {len(rec['graph']['path'])} 节点")
        print(f"  裸: {str(rec['bare']['answer'])[:80]}...")
        print(f"  图: {str(rec['graph']['answer'])[:80]}...")

    print(f"\n结果已写入 {out_file}（用 LLM 裁判盲评: python eval/score.py {out_file}）")


if __name__ == "__main__":
    main()