"""
cognitive_engine_v3 · 思维图核心

27 条骨架（Aa..Jw）转译为带权有向图：
- 节点 = 骨架（含行为定义、触发问题、产出）
- 边 = 骨架间的触发关系（cognition_mapping.json 的 output 字段）
- 边权 = 触发倾向，初始值来自骨架优先级，运行期由"带衰减权重计数器"更新
  （非图学习/非神经网络，详见 hebbian.py 模块 docstring）

三条保底：发散(Aa) / 验证(Eo链) / 总结(Jw)，任何路由结果强制并入。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from typing import Optional

try:
    import networkx as nx
except ImportError:
    nx = None  # 图操作本模块自行实现兜底，networkx 仅用于可视化/分析

PRIORITY_WEIGHT = {"P0": 1.0, "P1": 0.9, "P2": 0.8, "P3": 0.7, "P4": 0.6,
                   "P5": 0.5, "P6": 0.4, "P7": 0.3, "P8": 0.2}

# 认知论保底：多维考虑(发散) / 意图回归(验证) / 轮末进化(总结)
MANDATORY_NODES = ["Aa", "Eo", "Jw"]

# 验证链核心：Eo(上下文等价性校验) 及其上游校验节点
VERIFY_CORE = ["Dl", "Eo"]


@dataclass
class SkeletonNode:
    code: str                 # e.g. "Aa"
    label: str                 # e.g. "G00 任务空间平铺扫描"
    keyword: str               # e.g. "发散"
    priority: str              # "P0".."P8"
    trigger: str = ""
    behaviors: list = field(default_factory=list)   # [{code, action, question, output}]
    is_mandatory: bool = False
    is_verify: bool = False

    def to_prompt_line(self) -> str:
        """节点注入 Prompt 的精简一行表示"""
        b = "；".join(f"{b_['action']}({b_['question']})" for b_ in self.behaviors[:3])
        return f"[{self.code}] {self.label}：{b}"


@dataclass
class EdgeWeight:
    weight: float
    success_count: int = 0
    failure_count: int = 0

    def to_dict(self):
        return asdict(self)


class MindGraph:
    """思维有向图。自实现轻量结构，不依赖 networkx 运行。"""

    def __init__(self, nodes: dict[str, SkeletonNode], edges: dict[tuple, EdgeWeight]):
        self.nodes = nodes
        self.edges = edges  # (src, dst) -> EdgeWeight

    # ---------- 构建 ----------
    @classmethod
    def from_mapping(cls, mapping: dict) -> "MindGraph":
        """从（已修复的）cognition_mapping.json 字典构建图"""
        nodes: dict[str, SkeletonNode] = {}
        edge_specs: list[tuple[str, str, str]] = []  # (src, dst, priority)

        for _lname, layer in mapping["layers"].items():
            for code, sk in layer["skeletons"].items():
                nodes[code] = SkeletonNode(
                    code=code,
                    label=sk.get("label", ""),
                    keyword=sk.get("keyword", ""),
                    priority=sk.get("priority", "P4"),
                    trigger=sk.get("trigger", ""),
                    behaviors=list(sk.get("behaviors", {}).values()),
                    is_mandatory=code in MANDATORY_NODES,
                    is_verify=code in VERIFY_CORE,
                )
                for dst in sk.get("output", []) or []:
                    edge_specs.append((code, dst, sk.get("priority", "P4")))

        edges: dict[tuple, EdgeWeight] = {}
        for src, dst, pri in edge_specs:
            if src in nodes and dst in nodes:
                edges[(src, dst)] = EdgeWeight(PRIORITY_WEIGHT.get(pri, 0.5))
        for code in nodes:
            if code not in ("Jw",) :  # 终点之外的孤立节点若无入边则从 Aa 补一条弱边
                if not any(d == code for (_s, d) in edges):
                    edges[("Aa", code)] = edges.get(("Aa", code), EdgeWeight(0.3))
        return cls(nodes, edges)

    # ---------- 查询 ----------
    def neighbors(self, code: str, top_k: int = 5) -> list[tuple[str, float]]:
        """从某骨架出发，按边权排序的后续候选"""
        cands = [(d, w.weight) for (s, d), w in self.edges.items() if s == code]
        cands.sort(key=lambda x: -x[1])
        return cands[:top_k]

    def path_weight(self, path: list[str]) -> float:
        w = 1.0
        for a, b in zip(path, path[1:]):
            ew = self.edges.get((a, b))
            w *= ew.weight if ew else 0.2
        return w

    def reachable_subgraph(self, start: str, max_depth: int = 6, min_weight: float = 0.3) -> list[str]:
        """从 start 沿权重达标边可达的节点集（做任务域裁剪）"""
        seen, frontier = {start}, [start]
        for _ in range(max_depth):
            nxt = []
            for cur in frontier:
                for dst, w in self.neighbors(cur, top_k=10):
                    if w >= min_weight and dst not in seen:
                        seen.add(dst)
                        nxt.append(dst)
            if not nxt:
                break
            frontier = nxt
        return list(seen)

    # ---------- 赫布更新 ----------
    def update_edge(self, src: str, dst: str, success: bool, epsilon: float = 0.05,
                    min_w: float = 0.05, max_w: float = 2.0):
        key = (src, dst)
        ew = self.edges.get(key)
        if ew is None:
            ew = EdgeWeight(PRIORITY_WEIGHT.get("P4", 0.5))
            self.edges[key] = ew
        if success:
            # 饱和保护（2026-11-09 正则裁决）：ε 随权重接近上限线性衰减——
            # 越接近 max_w 增量越小，权重收敛于上限而不再齐刷刷顶到 2.0。
            # 依据：E1 夜跑 123 题后 95 条边 |Δ|≥0.02，主干边全部顶死 2.0，
            # 相对排序信息丢失（全部并列=判别力退化），ε 软上限保住排序分辨。
            # 已知限制：稳态值相同（ε 衰减+全局衰减平衡≈1.904），同热度路径
            # 内部边权会趋同——排序判别力保留在"热边 vs 冷边"之间，热边之间拉平。
            headroom = max(0.0, max_w - ew.weight)
            eps_eff = epsilon * min(1.0, headroom / (max_w * 0.25))
            ew.weight = min(max_w, ew.weight + eps_eff)
            ew.success_count += 1
        else:
            ew.weight = max(min_w, ew.weight - epsilon)
            ew.failure_count += 1

    def reinforce_path(self, path: list[str], success: bool, epsilon: float = 0.05,
                       decay: float = 0.0):
        for a, b in zip(path, path[1:]):
            self.update_edge(a, b, success, epsilon)
        if decay > 0:
            self.global_decay(decay)

    def global_decay(self, factor: float):
        """全局轻衰减（2026-11-09 正则裁决）：每次 reinforce 后全图 ×factor。
        未被走到的边自然回落，走过的边增量 > 衰减仍净涨——保持相对排序的同时
        防止历史热边永久霸榜。floor 0.3：低于初值的边不参与衰减（保底结构权重）。"""
        for ew in self.edges.values():
            if ew.weight > 0.3:
                ew.weight = max(0.3, ew.weight * factor)

    # ---------- 持久化 ----------
    def save(self, path: str):
        data = {
            "nodes": {c: asdict(n) for c, n in self.nodes.items()},
            "edges": {f"{s}->{d}": w.to_dict() for (s, d), w in self.edges.items()},
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "MindGraph":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # 兼容：mind_graph.json 中可能带 watchlist_notes / provenance_note 等扩展字段（复现率门禁 v0.1 观测位）
        allowed = {f_.name for f_ in fields(SkeletonNode)}
        nodes = {c: SkeletonNode(**{k: v for k, v in n.items() if k in allowed})
                 for c, n in data["nodes"].items()}
        edges = {}
        for k, v in data["edges"].items():
            s, d = k.split("->")
            edges[(s, d)] = EdgeWeight(weight=v["weight"],
                                      success_count=v.get("success_count", 0),
                                      failure_count=v.get("failure_count", 0))
        return cls(nodes, edges)

    # ---------- 观测 ----------
    def stats(self) -> dict:
        strong = [(k, w.weight) for k, w in self.edges.items() if w.weight > 1.0]
        weak = [(k, w.weight) for k, w in self.edges.items() if w.weight < 0.3]
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "strong_edges": strong,
            "weak_edges": weak,
        }