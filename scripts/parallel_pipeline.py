# -*- coding: utf-8 -*-
"""管线并行调度器: 任务级多进程开关 (config pipeline.mode: single|parallel)

设计:
- single  = 现状串行单链, 逐任务 run_atomic
- parallel = 多进程池并发, 每任务独立 ChainExecutor(零共享状态), 总耗时≈最慢单路
- 开关读 config.yaml pipeline.mode / pipeline.workers
- 每任务产物独立落盘 JSON + 单任务异常不炸全局(记 error 字段)

用法:
  python scripts/parallel_pipeline.py --tasks task1.txt task2.txt ... --out data/exp/run_x
  (每 txt 一份任务全文, 文件名=slug)
"""
import io, json, os, sys, time, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml


def load_cfg():
    cfg = yaml.safe_load(io.open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "config.yaml"), encoding="utf-8"))
    mode = (cfg.get("pipeline") or {}).get("mode", "single")
    if os.environ.get("_PP_MODE_OVERRIDE"):  # 命令行 --mode 覆盖优先
        mode = os.environ["_PP_MODE_OVERRIDE"]
    return mode, int((cfg.get("pipeline") or {}).get("workers", 4))


def _run_one(args):
    """子进程入口: 单任务跑 run_atomic, 落盘快照, 返回摘要 dict。"""
    slug, task_path, out_dir = args
    from cgraph.executor import ChainExecutor
    from cgraph.llm import LLMClient
    task = io.open(task_path, encoding="utf-8").read()
    snap_path = os.path.join(out_dir, f"pipeline_{slug}.json")
    try:
        cl = LLMClient()
        ex = ChainExecutor.__new__(ChainExecutor)
        ex.llm = cl
        t0 = time.time()
        report, state, gate = ex.run_atomic(task)
        secs = round(time.time() - t0, 1)
        snap = {
            "slug": slug, "secs": secs, "ok": report.ok,
            "exit_channel": report.exit_channel, "fallback_count": report.fallback_count,
            "steps": state.steps,
            "trail": [e.get("skill_id") for e in state.trail],
            "hypotheses": state.hypotheses, "weakest_link": state.weakest_link,
            "deduction_chains": state.deduction_chains, "gaps": state.gaps,
        }
        io.open(snap_path, "w", encoding="utf-8").write(
            json.dumps(snap, ensure_ascii=False, indent=1, default=str))
        return {"slug": slug, "ok": report.ok, "secs": secs,
                "exit": report.exit_channel, "steps": state.steps, "error": None}
    except Exception as e:
        return {"slug": slug, "ok": False, "secs": None, "exit": None,
                "steps": None, "error": f"{type(e).__name__}: {e}"}


def run(tasks, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    mode, workers = load_cfg()
    args = [(slug, path, out_dir) for slug, path in tasks]
    print(f"[parallel_pipeline] mode={mode} workers={workers} tasks={len(args)}", flush=True)
    t0 = time.time()
    if mode == "parallel":
        from multiprocessing import get_context
        with get_context("spawn").Pool(min(workers, len(args))) as pool:
            results = pool.map(_run_one, args)
    else:
        results = [_run_one(a) for a in args]
    total = round(time.time() - t0, 1)
    io.open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8").write(
        json.dumps({"mode": mode, "workers": workers, "tasks": len(args),
                    "total_secs": total, "results": results}, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {r['slug']}: ok={r['ok']} secs={r['secs']} steps={r['steps']} err={r['error']}", flush=True)
    print(f"[total] {total}s mode={mode}", flush=True)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", required=True, help="任务txt路径, 文件名(去扩展)=slug")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["single", "parallel"], default=None,
                    help="临时覆盖 config 的 mode")
    a = ap.parse_args()
    if a.mode:  # 命令行覆盖优先
        os.environ["_PP_MODE_OVERRIDE"] = a.mode
    tasks = [(os.path.splitext(os.path.basename(p))[0], p) for p in a.tasks]
    run(tasks, a.out)