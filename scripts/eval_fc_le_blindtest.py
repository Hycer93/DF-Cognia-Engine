#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""方案 Y 盲测批处理 (2026-11-25)

对 5 道封存盲测题 (B1-B5) 各跑 2 轮 Router「裸跑」(use_cache=False)，记录：
  - Router 耗时 (elapsed_ms_tool)
  - 判定 depth
  - 节点路径
  - 是否含 Jw 复盘
  - reason
用于观测：Router 会不会 depth 漂移（同题两轮 depth/path 是否一致）。

★ 只跑骨架生成，不做语义推理。结论/repro_hint 由主 AI 后续按骨架独立推理产出。
★ 核心观测指标 = Router 原始裸跑表现，故 use_cache=False。

产出：logs/fc_le_blindtest_run_<ts>.jsonl  —— 每行一条 {round, question_id, ...}

用法：
  python scripts/eval_fc_le_blindtest.py            # 跑全部 5 题 × 2 轮
  python scripts/eval_fc_le_blindtest.py --cache    # 配套：第2轮走缓存(演示 Y2)，不用于裸跑指标
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

from scripts.run_skeleton import build_skeleton  # noqa: E402

# 封存盲测样本（与 docs/04_cgraph_skill/fc_le_blindtest_samples_20261125.md 一致）
BLIND_SAMPLES = {
    "B1": "我们要做一个内部知识库问答系统，团队5人，没有专职运维。候选：①买SaaS（如Dify/Coze）②用开源RAG框架自搭（FastGPT/RAGFlow）。请给出选型决策和理由。",
    "B2": "公司两个技术合伙人意见分裂：A主张全力押注自研大模型，B主张全部用现成API做应用层。CEO让我给个判断。请从多角度分析这个决策该怎么下。",
    "B3": "复盘：我们的代码评审总是拖到最后一刻才做，经常卡住发布。这是偶然还是系统性问题？给出根因和改法。",
    "B4": "我们想把用户行为日志用于训练推荐模型，法务担心合规风险。请评估这个方案的可行性和主要风险点。",
    "B5": "有个供应商说\"这个组件只有我们有，别家做不了\"。这话能信几分？我该怎么核实。",
}


def _run_one(question_id: str, question: str, rnd: int, router_llm, use_cache: bool) -> dict:
    t0 = time.perf_counter()
    try:
        sk = build_skeleton(question, router_llm=router_llm, use_cache=use_cache)
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)
        r = sk["router"]
        return {
            "question_id": question_id,
            "round": rnd,
            "ok": True,
            "depth": r["depth"],
            "path": r["path"],
            "router_mode": r["router_mode"],
            "cache_hit": r.get("cache_hit", False),
            "preserve_router_tip": r.get("preserve_router_tip", True),
            "reason": r["reason"],
            "node_count": len(sk["node_list"]),
            "has_jw": sk["has_jw_review"],
            "node_ids": [n["node_id"] for n in sk["node_list"]],
            "elapsed_ms_tool": sk["elapsed_ms_tool"],
            "wall_ms": wall_ms,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "question_id": question_id, "round": rnd, "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


def main() -> int:
    use_cache = "--cache" in sys.argv
    if use_cache:
        print("[blindtest] ⚠ --cache：第2轮走缓存演示 Y2；裸跑指标请勿使用本模式", file=sys.stderr)

    # --only B3,B4,B5  只跑指定题号（续跑用）；--append 追加到已有文件而非新建
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = {x.strip().upper() for x in sys.argv[i + 1].split(",") if x.strip()}
    append = "--append" in sys.argv
    append_path = None
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            append_path = sys.argv[i + 1]

    from cgraph.llm import LLMClient
    router_llm = LLMClient()

    samples = {k: v for k, v in BLIND_SAMPLES.items() if (only is None or k in only)}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if append_path:
        out_path = append_path
        mode = "a"
    else:
        out_path = os.path.join(_ROOT, "logs", f"fc_le_blindtest_run_{ts}.jsonl")
        mode = "w"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    rounds = 2
    total = len(samples) * rounds
    done = 0
    print(f"[blindtest] 开始：{len(samples)} 题 × {rounds} 轮 = {total} 次 Router 裸跑 (mode={mode})")
    print(f"[blindtest] 输出：{out_path}")

    with open(out_path, mode, encoding="utf-8") as f:
        for qid, q in samples.items():
            for rnd in range(1, rounds + 1):
                # 裸跑：use_cache 仅第2轮且在 --cache 模式下开启（默认全 False）
                rc = use_cache and rnd == 2
                rec = _run_one(qid, q, rnd, router_llm, rc)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done += 1
                if rec["ok"]:
                    print(f"  [{done}/{total}] {qid} r{rnd}: depth={rec['depth']} "
                          f"节点={rec['node_count']} Jw={rec['has_jw']} "
                          f"tool={rec['elapsed_ms_tool']}ms wall={rec['wall_ms']}ms"
                          f"{' (cache)' if rec['cache_hit'] else ''}")
                else:
                    print(f"  [{done}/{total}] {qid} r{rnd}: FAIL {rec['error']}")

    print(f"[blindtest] 完成 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())