"""赫布回滚实验（用户设计2026-11-13）：3轮×3高危题，图=11-08快照。
控制变量：只动 mind_graph 边权快照（每轮前从干净快照重置，防赫布跨轮累积）。
记忆库/检索/裁判/LLM/题目逐字不动。
观察点（用户口径）：
  A. Jw调度偏好（边权管）——path 里 Jw 是否被选中、是否成为回答主体
  B. DEEPEN 标记自发生成（模型行为）——答案里有没有 [[DEEPEN:xxx]]，有没有接管主线
  中间态：标记生成但未接管主线 = 软通路漏洞仍在，只是放大器弱了
"""
import json, os, shutil, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from cgraph.llm import LLMClient
from cgraph.engine import Engine

EXP_GRAPH = os.path.join(HERE, "data", "mind_graph_experiment_rollback.json")
LIVE_GRAPH = os.path.join(HERE, "data", "mind_graph.json")
CLEAN_SNAP = os.path.join(HERE, "data", "mind_graph_backup_20261108.json")
QFILE = os.path.join(HERE, "eval", "questions_highrisk3.txt")

def snap_state():
    g = json.loads(open(LIVE_GRAPH, encoding="utf-8").read())
    return {k: v["weight"] for k, v in g["edges"].items()
            if k in ("Eo->Jw", "Fq->Jw", "En->Eo", "Aa->Eo")}

def main():
    questions = [l.strip() for l in open(QFILE, encoding="utf-8") if l.strip()]
    out = os.path.join(HERE, "logs",
                       f"ab_exp_rollback_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    BARE_PROMPT = "你是一个有用的助手。请直接、完整地回答用户的问题。"
    rounds = 3
    for rnd in range(1, rounds + 1):
        # 每轮前重置实验图（防赫布跨轮累积污染——轮内漂移保留，模拟真实连续对话）
        shutil.copy(CLEAN_SNAP, LIVE_GRAPH)
        engine = Engine()          # 每轮重建：加载刚重置的图快照
        llm = engine.llm
        w0 = snap_state()
        print(f"\n===== 轮 {rnd} | 图快照重置, Jw边权: "
              f"Eo->Jw={w0['Eo->Jw']:.2f} Fq->Jw={w0['Fq->Jw']:.2f}", flush=True)
        for q in questions:
            t0 = time.time()
            bare = llm.chat([{"role": "system", "content": BARE_PROMPT},
                             {"role": "user", "content": q}])
            bare_ms = int((time.time() - t0) * 1000)
            t0 = time.time()
            r = engine.run(q)
            g_ms = int((time.time() - t0) * 1000)
            ans = str(r["answer"])
            # 观察点B: DEEPEN 标记（引擎层自发输出，ab记录里 deepen_events 常为None）
            import re as _re
            marks = _re.findall(r"\[\[DEEPEN:([^\]]+)\]\]", ans)
            # 观察点A: Jw 调度
            path = r.get("actual_path", [])
            jw_in_path = "Jw" in (path or [])
            # 形态判定: 答案前120字是否被Jw·G21复盘模板接管
            hijack = ("经验进化闭环" in ans[:120]) or (
                bool(marks) and "系统性遗漏" in ans[:200])
            rec = {"round": rnd, "question": q,
                   "bare": {"answer": bare, "ms": bare_ms},
                   "graph": {"answer": ans, "ms": g_ms, "path": path,
                             "deepen_events": r.get("deepen_events")},
                   "obs": {"deepen_marks": marks, "jw_in_path": jw_in_path,
                           "jw_hijack": hijack, "head": ans[:120]},
                   "w_after": snap_state()}
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  [{q[:16]}] 图{g_ms}ms path{len(path)} | marks={marks or '无'} "
                  f"jw_path={jw_in_path} 劫持={hijack}", flush=True)
    # 还原生产图
    shutil.copy(os.path.join(HERE, "data", "mind_graph_pre_exp_20261113.json"), LIVE_GRAPH)
    print(f"\n实验完成, 产物: {out}\n生产图已还原(pre_exp快照)", flush=True)

if __name__ == "__main__":
    main()
