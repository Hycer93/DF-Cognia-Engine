# -*- coding: utf-8 -*-
"""
skill_g24_topology.py — 原子技能·G24全局拓扑构建(P-skeleton-complete 段一定稿 v1.0)

七段定稿(2026-11-19 用户逐项裁决, docs/design_skeleton_skills_v01.md 节点①):
  - 定位: 决策整合前全局回视; 四图投影+遗漏检测; 只投影不生产新证据不裁决
  - 裁决1(混合架构): 四图投影全部纯 Python(审计基底不能飘, 零LLM开销);
    仅子问题遗漏语义检测交 LLM
  - 裁决3(gaps双通路): gaps=系统唯一对外缺口出口, 合并三类——G24原生
    uncovered_subquestions + Bd.unaddressed(无回应薄弱点) +
    Dk.unsupported_premises(无证据前提); contradiction_pairs/drift_warnings
    严禁写入 gaps(留给 terminate 独立章节)
  - 降级兜底(裁决1): LLM 挂 -> 四图照常输出, uncovered 置空, Bd/Dk 来源
    缺口照常合并, 打 _degraded, 链路不崩
  - 循环防护(裁决3): 单子问题最多回流 f_reason 补推演 1 次——本技能只在
    gaps 条目产回流标记, 计数管控在 router 层 trail 打点
  - 锁定契约1: 四图只读 state 已有数据, 绝不主动拉取 ref_material 新检索
  - 红线: 不评价假设对错只陈述覆盖状态(dfcp_link=无)
审计: op=atomic_skill:skill_g24_topology, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, _ref_material_block

# state_outputs 键 -> result 字段映射(topology/gaps 由 parse 产同名键)
STATE_OUTPUT_MAP = {
    "topology": "topology",
    "gaps": "gaps",
    "summary": "summary",
}


def _project_four_maps(state: ThinkState, skill) -> dict:
    """四图投影——纯 Python, 不受 LLM 故障影响(裁决1: 审计基底)。
    1.骨架族谱图: trail 执行序列+每步理由摘要
    2.工具资源拓扑图: 邻接表全技能 vs trail 已执行 -> 未触发技能集
    3.知识资产关联图: matrix_knowledge 命中卡(matrix_id/tech_name)
    4.推理路径状态图: 核心字段存在性+步数+trail 长度"""
    trail = state.trail or []
    ran = {e.get("skill_id") for e in trail if isinstance(e, dict)}
    all_skills = sorted((skill.adjacency or {}).get("entries", {}).keys())
    return {
        "skeleton_family": [
            {"skill_id": e.get("skill_id"), "step_summary": str(e.get("reason", ""))[:120]}
            for e in trail if isinstance(e, dict)],
        "tool_topology": {"ran": sorted(ran), "not_ran": [s for s in all_skills if s not in ran]},
        "knowledge_map": [
            {"matrix_id": m.get("matrix_id"), "tech_name": m.get("tech_name")}
            for m in (state.matrix_knowledge or []) if isinstance(m, dict)],
        "path_status": {
            "hypotheses_exist": bool(state.hypotheses),
            "weakest_link_exist": bool(state.weakest_link),
            "deduction_chains_exist": bool(state.deduction_chains),
            "evidence_net_exist": bool(state.evidence_net),
            "bd_matrix_exist": bool(state.bd_matrix),
            "steps": state.steps,
            "trail_length": len(trail)},
    }


class SkillG24Topology(BaseAtomicSkill):
    skill_id = "skill_g24_topology"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        hyps = self._in(state, "hypotheses") or []
        wl = self._in(state, "weakest_link")
        summary = self._in(state, "summary") or ""

        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        hyp_lines = "\n".join("- %s" % (h.get("hypothesis", "") if isinstance(h, dict) else h)
                              for h in hyps) or "(无)"
        wl_block = ""
        if isinstance(wl, dict) and wl.get("location"):
            wl_block = (f"- 位置: {wl.get('location')} | 维度: {wl.get('dimension')}"
                        f" | 风险分: {wl.get('score')}")

        ctx = (f"总锚定目标:\n{goal}\n\n"
               f"【待核查子问题清单】\n{sub_lines}\n\n"
               f"【已有假设谱】\n{hyp_lines}\n\n"
               f"【已识别薄弱点】\n{wl_block or '(无)'}\n\n"
               f"【历史摘要】\n{summary or '(无)'}")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        # ① 四图投影(纯 Python, 先行——LLM 挂了图还在)
        topology = _project_four_maps(state, self)
        subs = self._in(state, "sub_questions") or []
        unaddressed = self._in(state, "unaddressed") or []
        chains = self._in(state, "deduction_chains") or []

        # ② LLM 子问题遗漏检测
        uncovered, degraded = [], ""
        if not subs:
            # 无子问题清单(scan 未产) -> 遗漏检测无对象, 非降级
            applicable = False
            conf = 0.0
        else:
            j = extract_json(raw)
            if not j:
                degraded = "LLM子问题遗漏检测JSON解析失败"
                uncovered, applicable, conf = [], False, 0.0
            else:
                raw_app = j.get("applicable", True)
                if isinstance(raw_app, str):
                    raw_app = raw_app.strip().lower() in ("true", "yes", "1")
                applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True
                raw_unc = j.get("uncovered_subquestions")
                if isinstance(raw_unc, list):
                    for u in raw_unc:
                        if isinstance(u, dict) and str(u.get("q", "") or "").strip():
                            uncovered.append({
                                "q": str(u.get("q", "")).strip()[:200],
                                "why_missed": str(u.get("why_missed", "")).strip()[:150],
                                "suggest_next": str(u.get("suggest_next", "")).strip()[:150]})
                conf = min(1.0, max(0.0, float(j.get("confidence", 0.5) or 0.5)))

        # ③ gaps 合并三类缺口(纯 Python, 唯一对外出口; Bd/Dk 来源照常合并不受LLM影响)
        gaps = []
        for u in uncovered:
            gaps.append({"gap_type": "uncovered_subquestion", "payload": u,
                         "reflow_allowed": True})          # 回流f_reason标记(router层计数管控)
        for loc in unaddressed:
            gaps.append({"gap_type": "unaddressed_weakpoint",
                         "payload": {"location": loc, "why_missed": "无任何假设回应该薄弱点"}})
        for c in chains:
            for p in (c.get("unsupported_premises") or []):
                if isinstance(p, dict) and p.get("premise"):
                    gaps.append({"gap_type": "unsupported_premise",
                                 "payload": {"target_ref": c.get("target_ref"),
                                             "premise": p.get("premise"),
                                             "note": "该前提无有效evidence_ref证据支撑"}})
        # 契约红线: contradiction_pairs / drift_warnings 永不写入 gaps

        result = {"skeleton_family": topology["skeleton_family"],
                  "tool_topology": topology["tool_topology"],
                  "knowledge_map": topology["knowledge_map"],
                  "path_status": topology["path_status"],
                  "topology": topology,
                  "uncovered_subquestions": uncovered,
                  "gaps": gaps, "applicable": applicable,
                  "summary": "四图投影完成, 缺口合并%d条" % len(gaps),
                  "confidence": conf}
        if degraded:
            result["_degraded"] = degraded
        return result

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return False            # 恒False(交 router 从 next_options 选 terminate/f_reason)


SYSTEM_PROMPT = """你是子问题遗漏检测模块(G24全局拓扑构建的一部分)。
对照子问题清单, 识别哪些子问题没有被假设谱或薄弱点充分回应。
只做覆盖状态陈述, 不对假设/薄弱点做真假对错裁决。
规则严格遵守:
1. 已被假设或薄弱点充分覆盖的子问题, 不放入 uncovered;
2. 严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "uncovered_subquestions": [
    {"q": "原始子问题文本", "why_missed": "未被回应原因",
     "suggest_next": "建议下一步方向"}
  ],
  "applicable": true,
  "summary": "一句话检测摘要",
  "confidence": 0.0
}"""