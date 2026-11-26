#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""思维节点 MCP Server —— 框架注入版 (2026-11-25, v0.3.0)

★ 设计修正（用户裁决 2026-11-25）：
  旧版(v0.2)在工具内部又起一次 llm.chat() 跑节点——等于「主 AI 调工具、工具再叫
  另一个 LLM 思考」，多绕一层、单次 200-290s、撞 30s 客户端硬超时、同输入还跳标签。
  新版改为【框架注入】：工具只做纯数据拼装(实测 0.6s)，瞬间返回该节点的思维框架
  （behaviors 步骤 + repro 判定标准 + 输出契约），由【调用方主 AI】当场按框架推理，
  自己产出 analysis + repro_hint + repro_reason。零额外 LLM 调用。

  这符合用户原意：节点不是「计算节点」，而是「FC 增量补充」——给主 AI 补上
  "该怎么想"的结构化指令，思考本身由已在运行的模型完成。

试点范围：仅 Ht(G22 决策整合) / Jw(G21 经验进化闭环) 两个高频节点。

工具契约（均秒回，不耗时）：
  - ht_decision_integrate(question) -> {node,label,trigger,framework,output_contract,...}
  - jw_experience_review(question)  -> 同上结构
  返回的 framework 里带完整推理步骤与 repro 判定标准，供主 AI 立即据此作答。

传输：stdio（本地进程，CodeChat 客户端拉起）。依赖仅 mind_graph.json，无 LLM/chromadb。

用法：
  python scripts/mcp_thinking_nodes.py --selftest    # 打印框架(不调LLM)
  python scripts/mcp_thinking_nodes.py               # 作为 MCP server 运行
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

from mcp.server.mcpserver import MCPServer  # noqa: E402

PILOT_NODES = ("Ht", "Jw")
NODE_TOOL_NAMES = {
    "Ht": "ht_decision_integrate",
    "Jw": "jw_experience_review",
}

# 常用节点 FC 工具集（2026-11-26）：按 router_log+ab_result 实测频次挑高频节点，
# 将「一个节点一个 FC」全量暴露为独立 MCP 工具，零 LLM 秒回框架。
# 频次排序: Aa/Eo/Jw(67) Cf(54) Ch/Di(53) Bb/Cg(52) Fp(32) Fq(31) Dj(24) Dk(17) ...
# 先做 medium 骨架全覆盖（Aa/Bb/Cf/Cg/Ch/Di/Eo/Jw）+ complex 常用加深（Dj/Dk/Fp/Fq）+ 已有 Ht/Jw。
COMMON_NODES = {
    # code: (tool_name, title, 用途短描述)
    "Aa": ("aa_task_scan", "Aa 任务空间平铺扫描", "把问题空间全域平铺，识别真正要解决什么、边界在哪"),
    "Bb": ("bb_intent_detect", "Bb 信息源头意图检测", "识别信息发出方的真实意图与利益立场"),
    "Bc": ("bc_weakest_link", "Bc 最弱环节定律", "扫描系统脆弱面，取最低分维度为最弱环节并映射风险档位"),
    "Cf": ("cf_three_step", "Cf 三步认知法", "锚定核心变化→拆解生产要素→分析传导层级"),
    "Cg": ("cg_stakeholder_map", "Cg 利益者全景图", "铺开所有利益相关方及其诉求与影响力"),
    "Ch": ("ch_multidim_check", "Ch 多维状态机校验", "逐维度校验结论，任一条不达标即否决"),
    "Di": ("di_first_cause", "Di 第一因溯源链", "多层溯因穿透表象，拆到不可再分的原子底层原因"),
    "Dj": ("dj_reverse_first_cause", "Dj 逆向第一因提取", "剥离组织叙事外衣，挖掘个体底层真实动机"),
    "Dk": ("dk_intuition_anchor", "Dk 直觉可验证锚定", "为直觉结论建立可验证锚点与反证条件"),
    "Eo": ("eo_context_equiv", "Eo 上下文等价性校验", "破除术语外壳，确认底层逻辑结构是否等价成立"),
    "Fp": ("fp_risk_quantify", "Fp 动态风险量化", "对风险做动态量化评估，给出概率与影响区间"),
    "Fq": ("fq_periodic_prune", "Fq 周期性出清与择优", "周期性清出低效选项，保留最优路径"),
    "Ht": ("ht_decision_integrate", "Ht 决策整合", "目标锚定→目的校验→参数光谱→加权评估→决策输出"),
    "Jw": ("jw_experience_review", "Jw 经验进化闭环", "全链路复盘→区分偶然与系统性→固化改进规则"),
}
# 兼容旧引用
NODE_TOOL_NAMES = {code: spec[0] for code, spec in COMMON_NODES.items()}

_server = MCPServer(
    name="cognitive-thinking-nodes",
    version="0.3.0",
    description="认知引擎思维节点 FC 原型（框架注入）：把决策整合(Ht)/经验复盘(Jw)的思维框架注入主 AI，零额外 LLM。",
)

# 仅加载图数据（纯 JSON，无 LLM/chromadb 依赖）
_graph = {"g": None}


def _graph_load():
    if _graph["g"] is not None:
        return _graph["g"]
    from cgraph.graph import MindGraph
    _graph["g"] = MindGraph.load(os.path.join(_ROOT, "data", "mind_graph.json"))
    return _graph["g"]


# ===== 量化层（2026-11-26）：主AI出分(语义) + Python聚合(确定性) =====
# FC 范式下「出分」是主 AI 的活；Python 只负责确定性聚合(阈值/加权/排序/统计)。
# 仅对有明确数学定义的节点提供；纯语义节点(Bb/Cf/Cg/Di/Dj/Dk/Eo/Jw)不硬塞量化。
QUANT_NODES = ("Bc", "Ch", "Ht", "Fp", "Fq")
QUANT_HINT = {
    "Bc": "{维度: 0-1健壮分(越高越稳)} → 最低分维度=最弱环节 + 风险五档",
    "Ch": "{threshold: 阈值, dimensions: {维度: 分}} → 全达标放行/任一否决",
    "Ht": "{weights: {维度: 权重}, options: {方案: {维度: 0-10分(越高越优)}}} → 加权分排序 top1",
    "Fp": "{风险项: {prob: 0-1, impact: 0-10}} → 期望风险=prob×impact 排序",
    "Fq": "{选项: 分} → 以均值为界, ≥均值保留, <均值淘汰",
}
# 风险五档阈值（与 skill_weakest_link.RISK_LEVELS 口径一致；内联避免拉 atomic_skill 依赖链）
_RISK_LEVELS = [(0.2, "critical"), (0.4, "high"), (0.6, "medium"), (0.8, "low")]


def _risk_level(score: float) -> str:
    for th, lv in _RISK_LEVELS:
        if score < th:
            return lv
    return "negligible"


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quantize(node_code: str, scores) -> dict | None:
    """确定性量化聚合。scores 结构随节点不同(见 QUANT_HINT)；返回{formula,结果}。"""
    if not isinstance(scores, dict):
        return {"error": "scores 必须是 dict"}
    if node_code == "Bc":
        valid = {k: _to_float(v) for k, v in scores.items()}
        valid = {k: v for k, v in valid.items() if v is not None}
        if not valid:
            return {"error": "Bc 需要 {维度: 0-1健壮分}，无可解析分数"}
        weakest = min(valid.items(), key=lambda kv: kv[1])
        return {
            "formula": "取最低分维度为最弱环节, 分数映射风险五档(越低越危险)",
            "weakest_dimension": weakest[0],
            "weakest_score": weakest[1],
            "risk_level": _risk_level(weakest[1]),
            "ranked_ascending": dict(sorted(valid.items(), key=lambda kv: kv[1])),
        }
    if node_code == "Ch":
        thr = _to_float(scores.get("threshold"))
        dims = scores.get("dimensions") or {}
        if thr is None or not isinstance(dims, dict) or not dims:
            return {"error": "Ch 需要 {threshold: 阈值, dimensions: {维度: 分}}"}
        results, failed = {}, []
        for k, v in dims.items():
            fv = _to_float(v)
            if fv is None:
                continue
            ok = fv >= thr
            results[k] = {"score": fv, "pass": ok}
            if not ok:
                failed.append(k)
        return {
            "formula": f"逐维校验分≥{thr}为达标, 全部达标才放行, 任一不达标即否决",
            "threshold": thr,
            "verdict": "放行" if not failed else "否决",
            "failed_dimensions": failed,
            "details": results,
        }
    if node_code == "Ht":
        weights = scores.get("weights") or {}
        options = scores.get("options") or {}
        if not isinstance(weights, dict) or not isinstance(options, dict) or not options:
            return {"error": "Ht 需要 {weights:{维度:权重}, options:{方案:{维度:0-10分}}}"}
        wsum = sum(w for w in (_to_float(x) for x in weights.values()) if w)
        if not wsum:
            return {"error": "weights 全为0或不可解析"}
        ranked = []
        for name, dim_scores in options.items():
            if not isinstance(dim_scores, dict):
                continue
            total = sum((_to_float(weights.get(d)) or 0.0) * (_to_float(dim_scores.get(d)) or 0.0)
                        for d in weights)
            ranked.append({"option": name, "weighted_score": round(total / wsum, 3)})
        if not ranked:
            return {"error": "options 无可解析方案"}
        ranked.sort(key=lambda x: x["weighted_score"], reverse=True)
        return {"formula": "加权分=Σ(维度分×权重)/Σ权重, 降序",
                "best_option": ranked[0]["option"], "ranking": ranked}
    if node_code == "Fp":
        items = []
        for name, spec in scores.items():
            if not isinstance(spec, dict):
                continue
            p, i = _to_float(spec.get("prob")), _to_float(spec.get("impact"))
            if p is None or i is None:
                continue
            items.append({"risk": name, "prob": p, "impact": i, "expected": round(p * i, 3)})
        if not items:
            return {"error": "Fp 需要 {风险项:{prob:0-1, impact:0-10}}"}
        items.sort(key=lambda x: x["expected"], reverse=True)
        return {"formula": "期望风险=prob×impact, 降序; 总风险=Σ",
                "top_risk": items[0]["risk"],
                "total_expected_risk": round(sum(x["expected"] for x in items), 3),
                "ranking": items}
    if node_code == "Fq":
        valid = {k: _to_float(v) for k, v in scores.items()}
        valid = {k: v for k, v in valid.items() if v is not None}
        if not valid:
            return {"error": "Fq 需要 {选项: 分}，无可解析分数"}
        mean = sum(valid.values()) / len(valid)
        keep = sorted([k for k, v in valid.items() if v >= mean], key=lambda k: valid[k], reverse=True)
        drop = sorted([k for k, v in valid.items() if v < mean], key=lambda k: valid[k])
        return {"formula": "以均值为界, ≥均值保留, <均值淘汰",
                "mean": round(mean, 3), "keep": keep, "drop": drop}
    return None


# repro 三级判定标准（与 executor 内节点 prompt 一致）
REPRO_CRITERIA = (
    "repro 三级判定：\n"
    "- derived(完全可复现)：确定性计算/检索所得，同样输入必得同样结果；\n"
    "- situational(条件可复现)：需特定上下文/条件，条件变则结论变；\n"
    "- flash(不可复现)：依赖当时现场/个人判断/临时状态，无法重算。"
)

OUTPUT_CONTRACT = (
    "输出契约：按框架逐步推理后给出最终答复，并标注三件事：\n"
    "  1) analysis：按框架步骤得出的结论正文；\n"
    "  2) repro_hint：derived | situational | flash 之一；\n"
    "  3) repro_reason：该可复现性的理由(≤40字)。"
)


def _framework(node_code: str, question: str, scores=None) -> dict:
    """拼装节点思维框架（纯数据，零 LLM）。可量化节点可传 scores 触发确定性聚合。"""
    g = _graph_load()
    node = g.nodes.get(node_code)
    if node is None:
        return {"error": f"节点 {node_code} 不存在"}
    steps = []
    for i, b in enumerate(node.behaviors or []):
        steps.append({
            "step": i + 1,
            "action": b.get("action", ""),
            "question": b.get("question", ""),
            "output": b.get("output", ""),
        })
    lines = [f"〔思维节点 {node_code} · {node.label}〕"]
    if node.trigger:
        lines.append(f"触发条件：{node.trigger}")
    lines.append("请依次完成以下思维步骤：")
    for s in steps:
        lines.append(f"  {s['step']}. 【{s['action']}】{s['question']}")
        if s["output"]:
            lines.append(f"     → 产出：{s['output']}")
    lines.append("")
    lines.append(REPRO_CRITERIA)
    lines.append("")
    lines.append(OUTPUT_CONTRACT)

    out = {
        "node": node_code,
        "label": node.label,
        "trigger": node.trigger,
        "question": question,
        "steps": steps,
        "repro_criteria": REPRO_CRITERIA,
        "output_contract": OUTPUT_CONTRACT,
        "framework": "\n".join(lines),
    }
    # 量化层：本节点支持量化时，告知输入格式；传入 scores 则跑确定性聚合
    if node_code in QUANT_NODES:
        out["quant_schema"] = QUANT_HINT[node_code]
        if scores is not None:
            out["quant_result"] = _quantize(node_code, scores)
    return out


def _make_tool(node_code: str):
    """闭包工厂：为指定节点生成工具函数。
    可量化节点(QUANT_NODES)签名带可选 scores，由 schema 暴露给主 AI；其余仅 question。"""
    if node_code in QUANT_NODES:
        def _tool(question: str, scores: dict | None = None) -> dict:
            return _framework(node_code, question, scores)
    else:
        def _tool(question: str) -> dict:
            return _framework(node_code, question)
    _tool.__name__ = NODE_TOOL_NAMES[node_code]
    _tool.__doc__ = f"返回 {node_code} 节点的思维框架，由主 AI 据此推理。"
    return _tool


def _build_desc(node_code: str, short: str) -> str:
    """按节点 behaviors 自动拼接工具描述：label + 动作链 + 用途 + 量化提示。"""
    g = _graph_load()
    node = g.nodes.get(node_code)
    label = node.label if node else node_code
    actions = "→".join(b.get("action", "") for b in (node.behaviors or []) if b.get("action"))
    desc = f"思维节点 {node_code}({label})的思维框架：{actions}。{short}。附 repro 三级判定。"
    desc += "请主 AI 按返回的 framework 自行推理，产出 analysis + repro_hint + repro_reason。"
    if node_code in QUANT_NODES:
        desc += (f"【可量化】传入 scores={QUANT_HINT[node_code]}，工具将做确定性数值聚合返回"
                 f" quant_result（不传则只返框架）。")
    return desc


# 数据驱动注册：COMMON_NODES 里每个节点注册为一个独立 MCP 工具（零 LLM 秒回）
for _code, (_tname, _title, _short) in COMMON_NODES.items():
    _server.add_tool(_make_tool(_code), name=_tname, title=_title,
                     description=_build_desc(_code, _short))


def _selftest():
    print(f"[selftest] 已注册 {len(COMMON_NODES)} 个节点 FC 工具:")
    for code, (tname, title, _s) in COMMON_NODES.items():
        print(f"  - {tname:26} ({code}) {title}")
    print("[selftest] 秒回框架、零 LLM 调用。以下为 Ht 框架：\n")
    print(_framework("Ht", "示例问题：三级可复现存哪层？")["framework"])
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    _server.run(transport="stdio")