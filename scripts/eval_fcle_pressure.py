#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FCLe-Y 对照压测 (2026-11-25)

对基线题（logs/ab_result_*.jsonl 存档的老管线题目）分别跑：
  - 老管线：Engine().run(q)（逐节点 LLM，真实耗时）
  - FCLe-Y：build_skeleton(q, router_llm=llm) 的骨架（Router 一次 LLM）
记录两边：总耗时、LLM调用次数、depth、路径、结论、repro_hint。

★ 老管线真跑很慢（每题数分钟），--limit 控制题数；--no-old 跳过老管线只跑 FCLe 骨架。
★ 默认不写记忆、不改图（Engine 内部会写，故用 NO_MEMORY=1 且跑完还原 mind_graph）。

产出：logs/fcle_pressure_<ts>.jsonl（每行一题对比）+ 控制台对照表。

用法:
  python scripts/eval_fcle_pressure.py --limit 3         # 前3题，两边都跑
  python scripts/eval_fcle_pressure.py --limit 2 --no-old  # 只跑FCLe骨架
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

GRAPH = os.path.join(_ROOT, "data", "mind_graph.json")


def _load_baseline_questions(limit: int) -> list:
    """从最新 ab_result_*.jsonl 取题（去重保序）。"""
    files = sorted(glob.glob(os.path.join(_ROOT, "logs", "ab_result_*.jsonl")))
    if not files:
        return []
    seen, qs = set(), []
    for f in reversed(files):  # 最新优先
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = (d.get("question") or "").strip()
            if q and q not in seen:
                seen.add(q)
                g = d.get("graph") or {}
                qs.append({"question": q, "old_path": g.get("path"),
                           "old_ms": g.get("ms"), "old_answer": (g.get("answer") or "")[:800],
                           "old_complexity": g.get("planned")})
        if len(qs) >= limit:
            break
    return qs[:limit]


def _run_old(q: str, engine) -> dict:
    t0 = time.perf_counter()
    try:
        r = engine.run(q)
        return {"ok": True, "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                "depth": r.get("complexity"), "path": r.get("actual_path"),
                "answer": (r.get("answer") or "")[:800],
                "llm_calls": len(r.get("actual_path") or []) + 2,
                "repro": r.get("repro_hints"), "verify": r.get("verify_note")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _run_fcle(q: str, llm) -> dict:
    from scripts.run_skeleton import build_skeleton
    t0 = time.perf_counter()
    try:
        sk = build_skeleton(q, router_llm=llm, use_cache=False)
        r = sk["router"]
        return {"ok": True, "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                "depth": r["depth"], "path": r["path"],
                "node_count": len(sk["node_list"]), "cache_hit": r.get("cache_hit"),
                "llm_calls": 1,  # Router 一次；主 AI 推理不在本脚本计
                "elapsed_ms_tool": sk["elapsed_ms_tool"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1)}


def main() -> int:
    limit = 8
    no_old = "--no-old" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    os.environ["NO_MEMORY"] = "1"  # 压测不碰记忆层

    from cgraph.llm import LLMClient
    llm = LLMClient()

    qs = _load_baseline_questions(limit)
    if not qs:
        print("[pressure] 无基线题（logs/ab_result_*.jsonl 缺失）", file=sys.stderr)
        return 1
    print(f"[pressure] 加载 {len(qs)} 题基线 | 老管线={'跳过' if no_old else '真跑'}")

    bak = GRAPH + ".pressure.bak"
    shutil.copy2(GRAPH, bak)
    print(f"[pressure] 备份 mind_graph（压测后还原）")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(_ROOT, "logs", f"fcle_pressure_{ts}.jsonl")

    engine = None
    if not no_old:
        from cgraph.engine import Engine
        engine = Engine()

    rows = []
    with open(out_path, "w", encoding="utf-8") as f:
        for idx, item in enumerate(qs, 1):
            q = item["question"]
            rec = {"idx": idx, "question": q,
                   "baseline": {"old_path": item["old_path"], "old_ms": item["old_ms"],
                                "old_complexity": item["old_complexity"]}}
            print(f"\n[{idx}/{len(qs)}] {q[:50]}...")
            if not no_old:
                old = _run_old(q, engine)
                rec["old"] = old
                print(f"   老管线: {old.get('depth')} path={old.get('path')} "
                      f"耗时={old.get('wall_ms')}ms LLM={old.get('llm_calls')}次"
                      if old["ok"] else f"   老管线 FAIL: {old.get('error')}")
            fcle = _run_fcle(q, llm)
            rec["fcle"] = fcle
            print(f"   FCLe-Y: {fcle.get('depth')} path={fcle.get('path')} "
                  f"Router耗时={fcle.get('elapsed_ms_tool')}ms LLM=1次"
                  if fcle["ok"] else f"   FCLe-Y FAIL: {fcle.get('error')}")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            rows.append(rec)

    # 还原 mind_graph（老管线 Engine 会写图）
    if os.path.exists(bak):
        shutil.move(bak, GRAPH)
        print(f"\n[pressure] 已还原 mind_graph")

    # 汇总
    print(f"\n=== 对照汇总 → {out_path} ===")
    print(f"{'#':2} {'老depth':8} {'FCdepth':8} {'老耗时s':9} {'FC Router s':11} {'老LLM':5} {'FC':3}")
    for r in rows:
        o, c = r.get("old", {}), r.get("fcle", {})
        od = o.get("depth", "-") if o.get("ok") else "FAIL"
        cd = c.get("depth", "-") if c.get("ok") else "FAIL"
        oms = f"{o.get('wall_ms',0)/1000:.1f}" if o.get("ok") else "-"
        cms = f"{c.get('elapsed_ms_tool',0)/1000:.1f}" if c.get("ok") else "-"
        ol = o.get("llm_calls", "-") if o.get("ok") else "-"
        print(f"{r['idx']:2} {od:8} {cd:8} {oms:9} {cms:11} {str(ol):5} {'1' if c.get('ok') else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())