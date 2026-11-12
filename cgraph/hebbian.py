"""
赫布更新：fire together, wire together。

每轮对话后依据两个信号更新路径边权：
1. 意图回归比率（executor 的验证自检）——低比率说明链走完但答非所问 → 负反馈
2. 外部效用反馈（可延迟到达）——用户后续确认有用/没用

快尺度：只改图的边权（JSON 持久化），不动模型。
慢尺度：edge_log 累积轨迹，留给未来蒸馏用。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict

from cgraph.graph import MindGraph


@dataclass
class WalkFeedback:
    path: list
    verify_ratio: float      # 意图回归比率 0~1
    utility: float = 0.0     # 外部效用：+1 有用 / -1 没用 / 0 未评价
    timestamp: float = 0.0

    def to_dict(self):
        return asdict(self)


class HebbianUpdater:
    def __init__(self, graph: MindGraph, cfg: dict | None = None):
        self.g = graph
        cfg = cfg or {}
        self.eps = cfg.get("epsilon", 0.05)
        self.min_w = cfg.get("min_weight", 0.05)
        self.max_w = cfg.get("max_weight", 2.0)
        # 全局轻衰减（2026-11-09 正则裁决）：防历史热边永久霸榜。
        # 0.995 ≈ 每 200 轮未走边权重 ×0.37；走过边 +0.05 > ×0.995 仍净涨。
        self.decay = cfg.get("global_decay", 0.995)

    def apply(self, fb: WalkFeedback, graph_file: str, edge_log_file: str):
        """综合信号 → 边权更新 → 持久化图 + 追加轨迹日志"""
        # 成败判定：意图回归 < 0.3 或效用 < 0 → 负反馈；否则正反馈
        negative = fb.verify_ratio < 0.3 or fb.utility < 0
        self.g.reinforce_path(fb.path, success=not negative,
                              epsilon=self.eps, decay=self.decay)

        self.g.save(graph_file)
        os.makedirs(os.path.dirname(edge_log_file) or ".", exist_ok=True)
        rec = fb.to_dict()
        rec["result"] = "negative" if negative else "positive"
        rec["timestamp"] = fb.timestamp or time.time()
        with open(edge_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        return {"result": rec["result"], "updated_edges": max(0, len(fb.path) - 1)}

    # ---------- 慢尺度预留：蒸馏轨迹统计 ----------
    @staticmethod
    def path_win_rates(edge_log_file: str) -> dict:
        """从轨迹日志统计各骨架边胜率——蒸馏前先看哪些思维路径真正有效"""
        if not os.path.exists(edge_log_file):
            return {}
        edges: dict = {}
        with open(edge_log_file, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                win = rec.get("result") == "positive"
                for a, b in zip(rec["path"], rec["path"][1:]):
                    key = f"{a}->{b}"
                    s, t = edges.get(key, (0, 0))
                    edges[key] = (s + (1 if win else 0), t + 1)
        return {k: {"win_rate": s / t, "n": t} for k, (s, t) in edges.items()}