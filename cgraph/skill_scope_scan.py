# -*- coding: utf-8 -*-
"""
skill_scope_scan.py — 原子技能·任务空间平铺扫描(试点②, 原元核 Aa/G00, 2026-11-16 七段定稿)

七段定稿(用户裁决四分歧后逐条代码化):
  原节点定位: 原 Aa/G00 发散动作原子化; 接收已锚定顶层目标做任务空间平铺,
    输出子问题清单/关键参数/涉及领域。v1 仅收编 behavior-1(任务空间扫描);
    参数变更溯源/遗留问题回溯移出本技能(跨任务能力, 未来独立技能+跨任务
    持久 state 再做)。只做拆解, 不产出答案、不读写 DFCP 记忆。
  state_inputs:  goal_anchored, flags(读 confidence/sensitive)
  state_outputs: sub_questions, summary, flags.scan_confidence(独立键,
    绝不覆盖 flags.confidence——两阶段置信分开存, 保留责任链可审计)
  result_fields: sub_questions(每项一句话+类型标注)/key_parameters/
    domains_touched(自由文本列表, 不映射 topic_id)/summary/confidence/
    ambiguity_note(扫描中新识别歧义, 无歧义空串)
  next_options: skill_terminate(空间单点闭合) / __planned__(深挖占位)
  terminate_suggest: sub_questions 为空 AND 非降级 AND scan_confidence>=0.9
  风险&规约:
    - 风险1 子问题爆炸: 硬编码上限 MAX_SUB_QUESTIONS=8, 超出截断+note 说明
    - 风险2 空输出误判闭合: not _degraded 前置(与试点①同口径, 降级态永不终止)
    - 权限红线: 仅产出任务空间描述, 不做事实裁决、不生成提案
    - dfcp_link=无: 不读不写任何 DFCP 卡片/topic
    - 审计: op=atomic_skill:skill_scope_scan, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

CONF_TERMINATE_MIN = 0.9     # 终止分支最低扫描置信
MAX_SUB_QUESTIONS = 8        # 风险1: 子问题爆炸硬上限, 超出截断+note

# state_outputs 键 → result 字段映射(基类按此写回; flags.scan_confidence 走嵌套)
STATE_OUTPUT_MAP = {
    "sub_questions": "sub_questions",
    "summary": "summary",
    "flags.scan_confidence": "confidence",
}

SYSTEM_PROMPT = """你是任务空间平铺扫描模块(思考链路的发散起点)。只做拆解, 不做答案。
接收已锚定的目标, 把它平铺成结构化的任务空间。

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "sub_questions": [{"q": "一句话子问题", "type": "factual|analytical|design|verification"}],
  "key_parameters": ["本任务涉及的关键输入或约束参数"],
  "domains_touched": ["涉及的领域名称(自由文本)"],
  "summary": "本技能摘要(30字内, 供后续技能作上下文)",
  "confidence": 0.0,
  "ambiguity_note": "扫描中新识别的歧义; 无新歧义必须输出空字符串"
}

判定口径:
- 简单单点任务: sub_questions 输出空数组 [](任务空间闭合, 无需拆解);
- 每个子问题必须是可独立回答的一句话; type 标注其性质;
- domains_touched 只写领域名, 不做任何知识库映射;
- confidence: 本次扫描置信 0~1(对拆解完整性的自评, 宁低勿高)。"""


class SkillScopeScan(BaseAtomicSkill):
    skill_id = "skill_scope_scan"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        flags = self._in(state, "flags") or {}
        ctx = f"已锚定目标:\n{goal}\n\n锚定置信: {flags.get('confidence', '?')} | 敏感: {flags.get('sensitive', False)}"
        amb = flags.get("ambiguity_note")
        if not amb:
            # 锚定阶段歧义从 goal_anchored 不可得——本技能只声明读 flags, 歧义若在
            # 锚定 result 内, 由编排器透传进 prompt 上下文; 此处读 flags 已声明项
            pass
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")

        # sub_questions 强校验: 列表→逐项清洗(q 必须非空串, type 非法→analytical)
        raw_subs = j.get("sub_questions")
        if not isinstance(raw_subs, list):
            raw_subs = [] if raw_subs in (None, "") else None
            if raw_subs is None:
                return self._degraded("sub_questions 非列表")
        subs, truncated = [], False
        for it in raw_subs:
            if isinstance(it, dict):
                q = str(it.get("q", "") or "").strip()
                t = str(it.get("type", "") or "").strip()
            else:                                       # 容错: 纯字符串项
                q, t = str(it).strip(), "analytical"
            if not q:
                continue
            if t not in ("factual", "analytical", "design", "verification"):
                t = "analytical"
            subs.append({"q": q, "type": t})
        # 风险1: 爆炸截断(硬上限, 超出丢弃并 note)
        if len(subs) > MAX_SUB_QUESTIONS:
            subs = subs[:MAX_SUB_QUESTIONS]
            truncated = True

        kp = [str(x).strip() for x in (j.get("key_parameters") or []) if str(x).strip()] \
            if isinstance(j.get("key_parameters"), list) else []
        # 分歧②: domains_touched 纯文本列表, 不做任何映射
        dt = [str(x).strip() for x in (j.get("domains_touched") or []) if str(x).strip()] \
            if isinstance(j.get("domains_touched"), list) else []

        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0

        amb = str(j.get("ambiguity_note", "") or "").strip()
        if truncated:
            amb = (amb + " | " if amb else "") + f"子问题超上限截断: 原始{len(raw_subs)}项取前{MAX_SUB_QUESTIONS}项"

        summary = str(j.get("summary", "") or "").strip()[:60]
        if not summary:
            summary = "空间平铺: %d子问题/%d域" % (len(subs), len(dt))
        if not subs and not kp and not dt and not j.get("summary"):
            return self._degraded("全空产出(既无子问题也无参数/领域)")
        return {
            "sub_questions": subs,
            "key_parameters": kp,
            "domains_touched": dt,
            "summary": summary,
            "confidence": conf,
            "ambiguity_note": amb,
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """终止判定(纯 Python, 与试点①同口径):
        sub_questions 空 AND 非降级 AND scan_confidence>=0.9。
        降级态永不终止——空输出可能是扫描失败而非任务闭合(风险2)。"""
        return (not result.get("sub_questions")
                and not result.get("_degraded")
                and result.get("confidence", 0.0) >= CONF_TERMINATE_MIN)