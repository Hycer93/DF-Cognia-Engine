"""轨迹数据批量采集：每题双路跑（图走链 + 裸直答）→ 组装 → 闸门落盘。

用法：
  DEEPSEEK_API_KEY=... python -u scripts/collect_trajectories.py [题库文件]
题库文件默认 eval/questions.txt，每行一题。裸直答与图走链同模型同配置，
保证 DPO 对内唯一变量是'有无思维结构'。
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.engine import Engine
from cgraph.trajectory import TrajectoryAssembler, save_samples

BARE_PROMPT = "你是一个乐于助人的助手。直接回答用户问题。"


def bare_answer(llm, q: str) -> str:
    try:
        return llm.chat([{"role": "system", "content": BARE_PROMPT},
                         {"role": "user", "content": q}]) or ""
    except Exception:
        return ""


def main():
    qfile = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "questions.txt")
    questions = [l.strip() for l in open(qfile, encoding="utf-8")
                 if l.strip() and not l.startswith("#")]
    print(f"[collect] {len(questions)} 题，来自 {qfile}", flush=True)

    e = Engine()
    asm = TrajectoryAssembler(e.graph)
    samples = []
    for i, q in enumerate(questions, 1):
        print(f">>> 题{i}/{len(questions)}: {q[:30]}", flush=True)
        t0 = time.time()
        # 双路：图走链 + 裸直答（同模型）
        decision = e.router.route(q)
        result = e.executor.run(q, decision)
        import re
        m = re.search(r"比率\s*([\d.]+)", result.verify_note or "")
        ratio = float(m.group(1)) if m else None
        rejected = bare_answer(e.llm, q)
        s = asm.assemble(result, decision, ratio, rejected)
        samples.append(s)
        print(f"    路径 {len(result.path)} 节点 | 比率 {ratio} | 裸答 {len(rejected)}字"
              f" | {int(time.time()-t0)}s | 闸门 {'过' if s.passes_gate else '不过'}",
              flush=True)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "trajectories")
    save_samples(samples, out)


if __name__ == "__main__":
    main()