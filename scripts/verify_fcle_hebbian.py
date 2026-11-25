#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FCLe-Y 赫布回填链路验证 (2026-11-25)

验证 FCLe-Y `on` 档上线后，赫布权重更新链路是否正常流转：
  1. 备份 mind_graph.json + edge_log.jsonl 当前状态
  2. 跑一轮 FCLe-Y 骨架 → 用骨架 path 构造 WalkResult → 走 engine 的赫布更新
  3. 断言：(a) edge_log.jsonl 新增 1 行
          (b) mind_graph 对应边 weight 有变化
          (c) success_count 或 failure_count 递增
  4. 还原 mind_graph（不留副产物，除非 --keep）

★ 默认还原，只做链路连通性验证；--keep 保留改动供人工观察。

用法:
  python scripts/verify_fcle_hebbian.py           # 验证后还原
  python scripts/verify_fcle_hebbian.py --keep    # 保留改动
"""
from __future__ import annotations

import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

GRAPH = os.path.join(_ROOT, "data", "mind_graph.json")
EDGE_LOG = os.path.join(_ROOT, "logs", "edge_log.jsonl")
TEST_Q = "验证赫布链路: 复盘我们项目延期两周，用第一因溯源找根本原因"


def _edge_weights(path: list) -> dict:
    """取 graph 中 path 相邻对的当前权重快照。"""
    from cgraph.graph import MindGraph
    g = MindGraph.load(GRAPH)
    out = {}
    for a, b in zip(path, path[1:]):
        e = g.edges.get((a, b))
        if e is not None:
            out[f"{a}->{b}"] = {"weight": getattr(e, "weight", None),
                                "succ": getattr(e, "success_count", None),
                                "fail": getattr(e, "failure_count", None)}
    return out


def _edge_log_lines() -> int:
    if not os.path.exists(EDGE_LOG):
        return 0
    return sum(1 for _ in open(EDGE_LOG, encoding="utf-8"))


def main() -> int:
    keep = "--keep" in sys.argv

    from cgraph.llm import LLMClient
    from cgraph.engine import Engine
    from cgraph.executor import WalkResult
    from cgraph.hebbian import WalkFeedback, HebbianUpdater
    from scripts.run_skeleton import build_skeleton

    # 0. 备份
    bak = GRAPH + ".hebbverify.bak"
    shutil.copy2(GRAPH, bak)
    print(f"[1/6] 备份 mind_graph → {os.path.basename(bak)}")

    # 1. FCLe 骨架（Y 模式）
    sk = build_skeleton(TEST_Q, router_llm=LLMClient(), use_cache=False)
    path = sk["router"]["path"]
    print(f"[2/6] FCLe 骨架 path={path} depth={sk['router']['depth']}")

    # 2. 快照
    before_log = _edge_log_lines()
    before_w = _edge_weights(path)
    print(f"[3/6] edge_log 行数={before_log} | 待验边 {len(before_w)} 条")

    # 3. 构造 WalkResult 模拟 FCLe 产出，走赫布更新
    wr = WalkResult(user_input=TEST_Q, final_answer="（验证用占位结论）",
                    path=path, planned_path=path)
    # 直接走 engine 同款链路（复用其 graph/hebbian 实例）
    eng = Engine()
    ratio = 0.85  # 模拟主 AI 输出的意图回归比率（可解析）
    fb = WalkFeedback(path=wr.path, verify_ratio=ratio, utility=0.0)
    update = eng.hebbian.apply(fb, eng.graph_file, eng.edge_log)
    print(f"[4/6] hebbian.apply 返回={update}")

    # 4. 校验
    after_log = _edge_log_lines()
    after_w = _edge_weights(path)
    checks = []
    checks.append(("edge_log 新增 1 行", after_log == before_log + 1,
                   f"{before_log}→{after_log}"))
    changed = []
    cnt_ok = []
    for k, bw in before_w.items():
        aw = after_w.get(k)
        if aw:
            if aw["weight"] != bw["weight"]:
                changed.append(k)
            if (aw["succ"] or 0) != (bw["succ"] or 0) or (aw["fail"] or 0) != (bw["fail"] or 0):
                cnt_ok.append(k)
    checks.append(("至少1条边 weight 变化", len(changed) > 0, f"{len(changed)}/{len(before_w)}: {changed[:3]}"))
    checks.append(("至少1条边计数递增", len(cnt_ok) > 0, f"{len(cnt_ok)}/{len(before_w)}"))

    print("[5/6] 断言结果:")
    all_ok = True
    for name, ok, detail in checks:
        print(f"      {'PASS' if ok else 'FAIL'}  {name}  ({detail})")
        all_ok = all_ok and ok

    # 5. 还原
    if not keep:
        shutil.move(bak, GRAPH)
        # edge_log 回滚末行
        with open(EDGE_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > before_log:
            with open(EDGE_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[:before_log])
        print("[6/6] 已还原 mind_graph + edge_log")
    else:
        os.remove(bak)
        print("[6/6] --keep：保留改动，已删除备份")

    print(f"\n结果: {'✅ 赫布链路 PASS' if all_ok else '❌ 赫布链路 FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())