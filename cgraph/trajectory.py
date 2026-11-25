"""
轨迹组装器：把走链全程拼装成 DPO 可用的思维轨迹。

背景（见 docs/02_memory_storage/design_affect_vector.md §5 P0）：
- WalkResult 现在只保留每节点 400 字摘要——模型只学得到结论学不到过程
- 本模块把 node_outputs（完整输出）拼成一段自然推理文本，
  带轻量结构标记（〔先想：…〕），作为 DPO 的 chosen 候选
- 同时产出裸问题直答的 rejected 候选位，质量闸门后成对

产出两种格式：
- train 格式：{prompt, chosen(思维轨迹), rejected(裸直答), meta}
  chosen = 轨迹文本 + 最终回答（轨迹在前答案在后，
  训练后模型先结构思考再作答——正是要内化的模式）
- raw 格式：走链原始记录（审计/人工抽检/裁判打分用）
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from cgraph.graph import MindGraph
from cgraph.executor import ChainExecutor, WalkResult
from cgraph.router import RouteDecision

# DPO 质量闸门（宁缺毋滥：脏一对不如少一对）
MIN_VERIFY_RATIO = 0.6


@dataclass
class TrajectorySample:
    prompt: str                     # 用户问题
    thought_chain: str              # 组装后的完整思维轨迹
    final_answer: str
    rejected: str                   # 裸模型直答（对照组）
    meta: dict = field(default_factory=dict)

    @property
    def passes_gate(self) -> bool:
        """质量闸门：意图回归达标才可进训练集"""
        return self.meta.get("verify_ratio", 0.0) >= MIN_VERIFY_RATIO

    def to_train_row(self) -> dict:
        return {
            "prompt": self.prompt,
            "chosen": self._compose_chosen(),
            "rejected": self.rejected,
            "meta": self.meta,
        }

    def _compose_chosen(self) -> str:
        """chosen = 思维轨迹 + 分隔 + 最终回答。
        训练后模型习得'先走结构、后出结论'的输出模式。"""
        return f"{self.thought_chain}\n\n---\n\n{self.final_answer}"


class TrajectoryAssembler:
    def __init__(self, graph: MindGraph):
        self.g = graph

    def assemble(self, result: WalkResult, decision: RouteDecision,
                 verify_ratio: float | None, rejected_answer: str = "") -> TrajectorySample:
        """从 WalkResult 组装一条训练样本。
        rejected_answer：裸模型同题直答（由调用方并行跑一次获得，可为空→闸门不放行）"""
        thought = self._chain_to_text(result)
        meta = {
            "planned_path": decision.path,
            "actual_path": result.path,
            "deepen_events": result.deepen_events,
            "complexity": decision.complexity,
            "verify_ratio": verify_ratio if verify_ratio is not None else -1.0,
            "event_counts": result.event_counts,   # 情感阀1原料同步落盘
            "ts": time.strftime("%Y%m%d_%H%M%S"),
        }
        return TrajectorySample(
            prompt=result.user_input, thought_chain=thought,
            final_answer=result.final_answer, rejected=rejected_answer, meta=meta)

    # ---------- 内部 ----------
    def _chain_to_text(self, result: WalkResult) -> str:
        """把节点序列拼成自然推理文本。
        设计取舍：
        - 每节点取完整输出但截 600 字（防超长链爆训练 token；完整版在 raw）
        - 深色结构标记用中文引导语而非节点编码——训练目标是让模型学会
          '先想动机、再看利益方、量化风险'的思考顺序，不是背节点号
        - DEEPEN 触发的节点标注'（此处自主加深）'——教模型何时该深挖
        """
        parts = []
        for code in result.path:
            out = (result.node_outputs or {}).get(code, "").strip()
            if not out:
                continue
            node = self.g.nodes.get(code)
            label = node.label if node else code
            deepen = "（此处自主加深）" if code in result.deepen_events else ""
            out = out[:600]
            parts.append(f"〔先想 · {label}〕{deepen}\n{out}")
        return "\n\n".join(parts)


def save_samples(samples: list, out_dir: str, prefix: str = "traj") -> str:
    """落盘 jsonl：train（过闸门）+ raw（全量审计）双文件"""
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    train_path = os.path.join(out_dir, f"{prefix}_train_{ts}.jsonl")
    raw_path = os.path.join(out_dir, f"{prefix}_raw_{ts}.jsonl")
    n_train = 0
    with open(train_path, "w", encoding="utf-8") as f:
        for s in samples:
            if s.passes_gate and s.rejected:   # 闸门：意图达标 + 有对照组
                f.write(json.dumps(s.to_train_row(), ensure_ascii=False) + "\n")
                n_train += 1
    with open(raw_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps({
                "prompt": s.prompt, "thought_chain": s.thought_chain,
                "final_answer": s.final_answer, "rejected": s.rejected,
                "meta": s.meta,
            }, ensure_ascii=False) + "\n")
    print(f"[traj] 落盘 {len(samples)} 条（过闸门 {n_train}）→ {train_path}")
    return train_path