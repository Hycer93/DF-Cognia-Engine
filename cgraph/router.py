"""
路由器：决定本次任务走图上的哪条链。

设计（对应你的认知论）：
- 最长链是能力上限，不默认全走
- 简单任务只走保底三条（Aa 发散 / Eo 验证 / Jw 总结）
- 复杂任务从 Aa 出发，沿高权重边扩展任务域子图，按边权选主路径
- 保底三条永远强制并入——路由结果不能少于它
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from cgraph.graph import MindGraph, MANDATORY_NODES
from cgraph.json_utils import extract_json_obj


@dataclass
class RouteDecision:
    complexity: str                    # simple / medium / complex
    path: list = field(default_factory=list)   # 依序骨架编码，如 ["Aa","Bb","Cf","Eo","Jw"]
    domain_nodes: list = field(default_factory=list)  # 域内可用节点（供中途加深）
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        """保底校验：无论怎么路由，三条必须都在"""
        return all(m in self.path for m in MANDATORY_NODES)


class ComplexityRouter:
    def __init__(self, graph: MindGraph, cfg: dict | None = None, llm=None):
        self.g = graph
        self.llm = llm
        cfg = cfg or {}
        self.simple_max_chars = cfg.get("simple_max_chars", 40)
        # 词表仅作 LLM 不可用时的兜底（形式主义降级方案）
        self.task_keywords = cfg.get("task_keywords") or [
            "设计", "分析", "对比", "方案", "风险", "审查", "复盘", "架构", "决策", "溯源"]

    def route(self, user_input: str) -> RouteDecision:
        complexity, candidates, reason = self._classify(user_input)
        # LLM 给出的加深候选：只留图里真实存在、且不在保底三条里的
        candidates = [c for c in candidates
                      if c in self.g.nodes and c not in MANDATORY_NODES]

        if complexity == "simple":
            # simple 不再焊死加深门：保底三条 + LLM 判定的加深候选进域
            domain = list(MANDATORY_NODES) + candidates
            return RouteDecision(complexity, list(MANDATORY_NODES), domain, reason)

        # medium/complex：Aa 出发，沿边权贪心扩展主路径至 Jw 收口
        # 反思类节点不再用词表限流——LLM 路由判定 + 走链中 DEEPEN 自决何时启用
        domain = self.g.reachable_subgraph("Aa", max_depth=4 if complexity == "medium" else 8,
                                           min_weight=0.35 if complexity == "medium" else 0.2)
        for c in candidates:            # 加深候选并入域
            if c not in domain:
                domain.append(c)
        path = self._greedy_path("Aa", "Jw", set(domain))
        self._ensure_mandatory(path, set(domain))
        return RouteDecision(complexity, path, domain,
                             f"{complexity}任务，图贪心主路径 {len(path)} 节点")

    # ---------- 内部 ----------
    def _classify(self, text: str):
        """语义级判定：一次短 LLM 调用判深度 + 顺手给出值得加深的节点。
        LLM 不可用时退回词表（形式主义兜底，仅保可用性）。"""
        if self.llm is not None:
            res = self._classify_llm(text)
            if res is not None:
                return res
        return self._classify_keywords(text), [], "词表兜底判定"

    def _classify_llm(self, text: str):
        prompt = (
            "判断这个用户问题需要多深的分析。只输出严格 JSON，无其他文字：\n"
            '{"depth": "simple|medium|complex", "deepen_candidates": ["节点码", ...], '
            '"reason": "10字以内"}\n'
            "深度标准：simple=闲聊/单点事实/情绪表达；medium=单一领域分析/需拆解；"
            "complex=多利益方博弈/高风险决策/隐藏动机/多步推理。\n"
            "加深候选从这些认知节点里选（最多3个，没有就空数组）："
            "Dl=质疑完美结论, Em=隐性共识, En=因果时序, Db=利益全景, "
            "Fd=风险量化, Gl=复杂难题破解, Dk=第一因溯源。\n"
            "判断要看问题的实质结构（如全积蓄投熟人项目=高风险决策），"
            "不要只看字面有没有某个词。\n"
            f"用户问题: {text[:500]}"
        )
        try:
            resp = self.llm.chat([{"role": "user", "content": prompt}])
            j = extract_json_obj(resp)
            if j is None:
                return None
            depth = j.get("depth", "medium")
            if depth not in ("simple", "medium", "complex"):
                depth = "medium"
            cands = [str(c)[:2] for c in j.get("deepen_candidates", [])][:3]
            reason = str(j.get("reason", ""))[:30] or "语义判定"
            return depth, cands, f"语义判定: {reason}"
        except Exception:
            return None

    def _classify_keywords(self, text: str) -> str:
        """旧词表判定——仅 LLM 不可用时兜底"""
        t = text.strip()
        has_task_kw = any(k in t for k in self.task_keywords)
        if len(t) < self.simple_max_chars and not has_task_kw:
            return "simple"
        if any(k in t for k in ("架构", "设计", "方案", "对比", "决策", "复盘", "风险")) \
                or len(t) > 200 or t.count("?") + t.count("？") >= 2:
            return "complex"
        return "medium"

    def _greedy_path(self, start: str, end: str, domain: set) -> list:
        """域内从 start 到 end 的边权贪心路径（每步走当前最重的边，允许回溯点直接接 end）"""
        path = [start]
        cur = start
        for _ in range(12):
            if cur == end:
                break
            cands = [(d, w) for d, w in self.g.neighbors(cur, top_k=8) if d in domain and d not in path]
            if not cands:
                break
            nxt = max(cands, key=lambda x: x[1])[0]
            path.append(nxt)
            cur = nxt
        if end not in path:
            path.append(end)  # 强制收口到总结
        return path

    def _ensure_mandatory(self, path: list, domain: set):
        """把保底三条按认知顺序插进路径：发散在头，验证在总结前"""
        if "Eo" not in path:
            path.insert(max(1, len(path) - 1), "Eo")
        if "Aa" not in path:
            path.insert(0, "Aa")
        if "Jw" not in path:
            path.append("Jw")


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()