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


# repro 三级判定标准（与 executor 内节点 prompt 完全一致，作为框架的一部分注入）
REPRO_CRITERIA = (
    "repro 三级判定标准：\n"
    "- derived(完全可复现)：结论本质是确定性计算/检索所得，输入相同则稳定重算出同一结果；\n"
    "- situational(条件可复现)：需特定上下文/条件才能复现，条件变了结论可能变；\n"
    "- flash(不可复现)：依赖当时现场、个人判断、临时状态，无法重算。"
)

OUTPUT_CONTRACT = (
    "输出契约：请按上述框架逐步推理后，给出最终答复，并显式标注三件事：\n"
    "  1) analysis：按框架步骤得出的结论正文；\n"
    "  2) repro_hint：derived | situational | flash 之一；\n"
    "  3) repro_reason：判定为该可复现性的理由(≤40字)。"
)


def _framework(node_code: str, question: str) -> dict:
    """拼装节点思维框架（纯数据，零 LLM）。返回给主 AI，由主 AI 自己推理。"""
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
    # 人类可读的框架正文（主 AI 直接读这段工作）
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
    return {
        "node": node_code,
        "label": node.label,
        "trigger": node.trigger,
        "question": question,
        "steps": steps,
        "repro_criteria": REPRO_CRITERIA,
        "output_contract": OUTPUT_CONTRACT,
        "framework": "\n".join(lines),
        "mode": "framework_injection（本工具不调用LLM；请主AI按framework自行推理并产出analysis/repro_hint/repro_reason）",
    }


def _make_tool(node_code: str):
    """闭包工厂：为指定节点生成一个 (question)->framework 的工具函数。
    保持签名 (question: str) -> dict，供 MCP 生成参数 schema。"""
    def _tool(question: str) -> dict:
        return _framework(node_code, question)
    _tool.__name__ = NODE_TOOL_NAMES[node_code]
    _tool.__doc__ = f"返回 {node_code} 节点的思维框架（不调 LLM，由主 AI 据此推理）。"
    return _tool


def _build_desc(node_code: str, short: str) -> str:
    """按节点 behaviors 自动拼接工具描述：label + 动作链 + 用途 + 零LLM提示。"""
    g = _graph_load()
    node = g.nodes.get(node_code)
    label = node.label if node else node_code
    actions = "→".join(b.get("action", "") for b in (node.behaviors or []) if b.get("action"))
    return (
        f"思维节点 {node_code}({label})的思维框架：{actions}。{short}。"
        f"附 repro 三级判定标准。★本工具只返回框架、不调用 LLM、秒回；请调用方主 AI 按返回的"
        f" framework 自行推理，产出 analysis + repro_hint + repro_reason。"
    )


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