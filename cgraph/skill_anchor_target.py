# -*- coding: utf-8 -*-
"""
skill_anchor_target.py — 原子技能·目标锚定(试点①, 2026-11-16 七段模板共识落地)

七段模板共识(用户定案):
  原节点定位: 原元核顶层路由前置动作原子化为独立首技能; 接收原始用户提问,
    收敛成结构化顶层锚定目标; 区分任务类型/识别歧义; 整套原子思考链路标准入口。
    只做目标解析——不做答案、不查 DFCP 记忆。
  state_inputs:  goal
  state_outputs: goal_anchored, ref_material, flags.confidence, flags.sensitive
  result_fields: anchored_goal, ref_material, goal_type, ambiguity_note, summary
  next_options:  skill_scope_scan(平铺拆解) / skill_terminate(简单查询直收敛)
  terminate_suggest: goal_type==simple_query 且 ambiguity_note 为空
    且 flags.confidence>=0.9 → 建议终止; 其他不建议(风险1兜底: 置信偏低
    强制走向扫描分支, 禁止直接终止)
  风险&规约:
    - 不读写 DFCP 卡片(dfcp_link=无), 完全解耦
    - 风险1 过度收敛: confidence 阈值兜底, 低置信强制进扫描
    - 风险2 歧义漏识别: ambiguity_note 强制输出字段(识别到模糊点必记录)
    - 权限红线: 只生产目标摘要, 不做事实裁决、不触发记忆提案
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

GOAL_TYPES = ("simple_query", "analysis", "scheme_design", "review", "unknown_ambiguous")
CONF_TERMINATE_MIN = 0.9     # 终止分支最低置信(风险1兜底)

# state_outputs 键 → result 字段映射(基类按此写回)
STATE_OUTPUT_MAP = {
    "goal_anchored": "anchored_goal",
    "ref_material": "ref_material",
    "flags.confidence": "confidence",
    "flags.sensitive": "sensitive",
}

SYSTEM_PROMPT = """你是目标锚定模块(思考链路的标准入口)。只做目标解析, 不做答案。
从用户的原始提问中收敛出一个结构化的顶层锚定目标。

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "anchored_goal": "一句话收敛后的清晰目标",
  "ref_material": "原始提问中若含大段文档/日志/专利正文等附属参考素材, 剥离其完整原文到此字段; 无则空字符串",
  "goal_type": "simple_query|analysis|scheme_design|review|unknown_ambiguous 之一",
  "ambiguity_note": "歧义说明; 无歧义必须输出空字符串, 识别到模糊点必须记录",
  "summary": "本技能摘要(30字内, 供后续技能作上下文)",
  "confidence": 0.0,
  "sensitive": false
}

判定口径:
- simple_query=事实型单点查询(无衍生子问题); analysis=归因/对比/推断;
  scheme_design=方案/架构/流程设计; review=审查复核已有结论或产物;
  unknown_ambiguous=目标本身无法收敛(多重解读/要素缺失)。
- confidence: 锚定置信 0~1(自己对收敛质量的评估, 宁低勿高)。
- sensitive: 涉及用户隐私/关系/高敏资产记忆主题时 true。
- ref_material 规则: 只做完整原文剥离, 严禁摘要压缩/删改(压缩=信息丢失);
  anchored_goal 只保留任务问题框架, 不掺杂大段材料;
  若材料已由入口通道剥离(提问中无大段材料), 输出空字符串即可。"""


class SkillAnchorTarget(BaseAtomicSkill):
    skill_id = "skill_anchor_target"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"原始用户提问:\n{goal}"},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")
        # 强校验清洗: 非法枚举→unknown_ambiguous; confidence 越界→夹取; 缺字段→补空
        gt = str(j.get("goal_type", "")).strip()
        if gt not in GOAL_TYPES:
            gt = "unknown_ambiguous"
        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0
        sens = j.get("sensitive") is True
        amb = str(j.get("ambiguity_note", "") or "").strip()
        ag = str(j.get("anchored_goal", "") or "").strip()
        if not ag:                                  # 目标收敛失败=最严重降级
            return self._degraded("anchored_goal 为空")
        # P-C4 LLM 通道剥离(段三3.1): 素材是增强非必需——解析失败得空串不降级;
        # anchor 是否降级只看 anchored_goal, 与 ref_material 语义完全解耦
        mat = str(j.get("ref_material", "") or "").strip()
        # R4 LLM 覆盖防御(段三3.1硬约束/R13 素材只读红线): 入口通道已写非空素材时,
        # 代码层直接丢弃 LLM 输出保留入口版本——prompt 是软约束, 代码是硬约束
        if getattr(state, "ref_material", ""):
            mat = state.ref_material
        return {
            "anchored_goal": ag,
            "ref_material": mat,                     # 原样搬运, 严禁压缩(入口通道写入优先, 见 run 覆写防御)
            "goal_type": gt,
            "ambiguity_note": amb,                  # 强制输出字段: 无歧义=空串(风险2兜底)
            "summary": str(j.get("summary", "") or "").strip()[:60],
            "confidence": conf,
            "sensitive": sens,
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """终止判定(纯 Python, 模型不可参与):
        simple_query + 无歧义 + confidence>=0.9 才建议终止; 否则继续思考。
        置信偏低强制走向扫描分支——风险1(过度收敛)的代码兜底。"""
        return (result.get("goal_type") == "simple_query"
                and not result.get("ambiguity_note")
                and result.get("confidence", 0.0) >= CONF_TERMINATE_MIN
                and not result.get("_degraded"))