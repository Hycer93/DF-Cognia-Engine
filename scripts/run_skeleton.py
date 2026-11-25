#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""元核思维 FCLe 原型 —— run_skeleton (2026-11-25)

★ FCLe = Fixed-Framework (固定推理骨架) + LLM-Embedded (语义推理主体仍是主模型)
  一句话：run_skeleton 只输出结构化思维框架、路由候选、节点清单，不做任何语义推理、
  不调用 LLM；全部推演、打分、决策由主 AI 承接。
  旧管线 = 每个节点单独起子 LLM；FCLe = 骨架一次性给齐，主 AI 一条链跑完。

边界（严格遵守）：
  ✅ 只读、只给框架，完全不修改 engine.py / executor.py
  ✅ 只复用 Router 的【非 LLM】关键词分类 + 图贪心路径 + mandatory 收口（纯数据）
  ✅ 不碰记忆库、不改 core_memory_card / raw_corpus / 治理队列
  ✅ 只在 eval 脚本里对照用，不接入生产 PreCompact

输出（纯静态框架，无 LLM 生成内容）：
  {
    "router": {"depth": "complex/medium/simple", "path": [...], "reason": "..."},
    "node_list": [{"node_id","label","frame":["步骤1","步骤2",...],"trigger"}...],
    "summary": {"frame": "收敛、结构化输出、生成 repro_hint"},
    "hebb_placeholder": {"ratio": null, "edge_id": null},
    "constraint": ["不额外调用子LLM，全部推理由主模型完成"],
    "elapsed_ms": <工具内生成耗时>
  }

用法：
  python scripts/run_skeleton.py --selftest
  python scripts/run_skeleton.py "你的问题"      # 打印框架 JSON
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

_g = {"graph": None}

# Y2 缓存（配套工程补丁）：仅 eval 本地生效，缓存 Router 语义分类结果，同题不重复调 LLM。
# 默认关闭（use_cache=False），盲测核心指标仍观测 Router 裸跑表现。不接入正式管线。
_CACHE_PATH = os.path.join(_ROOT, "logs", ".fc_le_router_cache.json")


def _cache_load() -> dict:
    if os.path.exists(_CACHE_PATH):
        try:
            return json.load(open(_CACHE_PATH, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _cache_save(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        json.dump(cache, open(_CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass


def _load_graph():
    if _g["graph"] is None:
        from cgraph.graph import MindGraph
        _g["graph"] = MindGraph.load(os.path.join(_ROOT, "data", "mind_graph.json"))
    return _g["graph"]


SUMMARY_FRAME = "收敛各节点结论 → 结构化输出 → 生成 repro_hint(derived/situational/flash) + repro_reason"


def build_skeleton(question: str, preserve_router_tip: bool = True,
                   router_llm=None, use_cache: bool = False) -> dict:
    """生成元核思维骨架（纯数据，节点框架零 LLM）。

    路由双模式（方案 Y 预演）：
      - router_llm=None（默认）：纯规则，ComplexityRouter(llm=None) 关键词分类+图贪心，
        工具内耗时 <1ms，完全确定。
      - router_llm=LLM 实例：保留 Router 语义分类头（方案 Y），LLM 输出 depth +
        deepen_candidates 驱动 mind_graph 选路，维持赫布权重更新回路；代价是每次
        多一次 LLM 分类调用（codechat 后端实测 56-73s）。

    ★ 无论哪种模式，节点【框架内容】都是纯数据（节点 behaviors），不调 LLM；
      语义推演一律由主 AI 承接。router_llm 只影响「选哪条路径」，不影响「节点怎么想」。

    - 节点：path 中每个节点取其 behaviors 的 action 序列作为 frame（固定推理骨架）。
    - 汇总：固定 summary frame（由主 AI 收敛作答 + 生成 repro_hint）。
    """
    t0 = time.perf_counter()
    from cgraph.router import ComplexityRouter
    from cgraph.router import RouteDecision
    g = _load_graph()
    router_mode = "llm_semantic" if router_llm is not None else "rule_keyword"

    # Y2 缓存：仅 Y 模式 + use_cache 时生效，缓存完整 Router 决策(depth+path+reason)。
    # 盲测默认 use_cache=False（观测 Router 裸跑）。命中则不调 LLM，秒回。
    # 注意：缓存的是 path 快照，若 mind_graph 期间被赫布改动需清 logs/.fc_le_router_cache.json。
    cache_hit = False
    cached = None
    if router_llm is not None and use_cache:
        cached = _cache_load().get(question)

    if cached is not None:
        cache_hit = True
        dec = RouteDecision(complexity=cached["depth"], path=list(cached["path"]),
                            domain_nodes=list(cached.get("domain_nodes") or cached["path"]),
                            reason=cached.get("reason", "缓存命中"))
    else:
        router = ComplexityRouter(g, llm=router_llm)  # None→关键词分类；有LLM→语义分类头
        dec = router.route(question)
        if router_llm is not None and use_cache:
            cache = _cache_load()
            cache[question] = {"depth": dec.complexity, "path": list(dec.path),
                               "domain_nodes": list(dec.domain_nodes or dec.path),
                               "reason": dec.reason}
            _cache_save(cache)

    node_list = []
    for code in dec.path:
        if code == "Jw":
            remain = True  # Jw 在路径末尾，作为复盘分支显式列出
        node = g.nodes.get(code)
        if node is None:
            continue
        frame = [b.get("action", "") for b in (node.behaviors or [])]
        node_list.append({
            "node_id": code,
            "label": node.label,
            "trigger": node.trigger,
            "frame": frame,
            "steps_detail": [
                {"action": b.get("action", ""), "question": b.get("question", ""),
                 "output": b.get("output", "")}
                for b in (node.behaviors or [])
            ],
        })

    has_jw = any(n["node_id"] == "Jw" for n in node_list)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    out = {
        "question": question,
        "mode": "fc_le",
        "router": {
            "depth": dec.complexity,
            "path": list(dec.path),
            "reason": dec.reason,
            "router_mode": router_mode,
            "cache_hit": cache_hit,
            "preserve_router_tip": preserve_router_tip,
        },
        "node_list": node_list,
        "summary": {"node_id": "summary", "frame": SUMMARY_FRAME},
        "has_jw_review": has_jw,
        "hebb_placeholder": {"ratio": None, "edge_id": None},
        "constraint": ["不额外调用子LLM，全部推理由主模型完成"],
        "elapsed_ms_tool": elapsed_ms,
    }
    return out


def _selftest():
    q = "对比方案A：本地部署开源模型 vs 方案B：调用云端API。我们是30人创业公司，做智能客服产品。请多维度分析"
    sk = build_skeleton(q)
    print(f"[selftest] router_mode={sk['router']['router_mode']} | depth={sk['router']['depth']} "
          f"| 节点数={len(sk['node_list'])} | 含Jw复盘={sk['has_jw_review']} "
          f"| 工具内耗时={sk['elapsed_ms_tool']}ms")
    print("[selftest] 节点 frame 预览:")
    for n in sk["node_list"]:
        print(f"  {n['node_id']} {n['label']}: {'→'.join(n['frame'])}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    router_llm = None
    use_cache = "--cache" in sys.argv
    if "--y" in sys.argv:
        # 方案 Y 预演：接入 Router 轻 LLM 分类头（每次多一次 LLM 调用，慢）
        from cgraph.llm import LLMClient
        router_llm = LLMClient()
        print("[run_skeleton] --y 模式：Router 语义分类头已启用（每次约 56-147s）", file=sys.stderr)
    if use_cache and router_llm is None:
        print("[run_skeleton] --cache 需配合 --y（仅 Y 模式有 LLM 分类可缓存），已忽略", file=sys.stderr)
        use_cache = False
    if args:
        print(json.dumps(build_skeleton(args[0], router_llm=router_llm, use_cache=use_cache),
                         ensure_ascii=False, indent=2))
    else:
        print("用法: python scripts/run_skeleton.py \"问题\" [--y [--cache]] | --selftest")
