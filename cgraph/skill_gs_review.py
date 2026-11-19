# -*- coding: utf-8 -*-
"""
skill_gs_review.py — 原子技能·Gs全局回视(P-skeleton-complete 段一定稿 v1.0)

七段定稿(2026-11-19 用户逐项裁决, docs/design_skeleton_skills_v01.md 节点⑤):
  - 定位: 长链过程态轻量检查点(软挂全技能, router自主选路); 只观测不纠偏,
    不修改 trail/state 任何推理结论
  - 裁决1(触发): 邻接表软挂——E11/E12 放量采集触发率/命中率后回头议 router 强制
  - 裁决2(双消费): resume_view ①注入后续技能 prompt(解长链 trail 膨胀)
    ②落 state.gs_view 供 terminate 汇编复用; 一次产出两处消费
  - 裁决3(职责): drift_warnings=方向类风险, 严禁进 G24.gaps(那是覆盖类缺口)
    ——terminate 汇编独立"过程漂移"章节
  - 前置守卫: steps < 4 -> applicable=false 跳过(短链不浪费轮次), 非降级
  - 降级: drift 置空 + 极简兜底 resume_view + _degraded, 链路不阻断
  - terminate_suggest: 仅软性建议(drift 非空倾向收敛), 终裁 router
审计: op=atomic_skill:skill_gs_review, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

MAX_VIEW_CHARS = 3000         # resume_view 硬长度上限(Python 兜底防膨胀)

# state_outputs 键 -> result 字段映射
STATE_OUTPUT_MAP = {
    "gs_view": "gs_view",
    "summary": "summary",
}


class SkillGsReview(BaseAtomicSkill):
    skill_id = "skill_gs_review"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        trail = self._in(state, "trail") or []
        summary = self._in(state, "summary") or ""
        steps = self._in(state, "steps")

        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        # trail 压缩注入: 只取 skill_id+reason 骨架(锁死契约: 拒绝无节制复制原文;
        # resume_view 是 LLM 的压缩产出, 不是 trail 原文搬运)
        trail_lines = "\n".join("- [%s] %s" % (e.get("skill_id", "?"),
                                               str(e.get("reason", ""))[:100])
                                for e in trail if isinstance(e, dict)) or "(空)"

        ctx = (f"顶层锚定总目标:\n{goal}\n\n"
               f"【全部子问题】\n{sub_lines}\n\n"
               f"【执行轨迹骨架(steps={steps})】\n{trail_lines}\n\n"
               f"【当前summary】\n{summary or '(无)'}")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        steps = self._in(state, "steps") or 0

        # 前置守卫(锁定契约): steps<4 短链无需回视 -> applicable=false 非降级
        if int(steps) < 4:
            return {"drift_warnings": [], "resume_view": "",
                    "checkpoint_note": "steps=%d" % steps, "applicable": False,
                    "summary": "短链(steps<4)跳过回视", "confidence": 0.0}

        j = extract_json(raw)
        if not j:
            # 降级兜底: 极简 resume_view 保留检查点标记, drift 置空, 链路不阻断
            return {"drift_warnings": [], "_degraded": "LLM回视JSON解析失败",
                    "resume_view": "[checkpoint:steps=%d] LLM回视解析失败, "
                    "沿用历史摘要: %s" % (steps, (self._in(state, "summary") or "")[:500]),
                    "checkpoint_note": "steps=%d" % steps, "applicable": False,
                    "summary": "回视降级", "confidence": 0.0}

        # applicable 布尔顿化(家族同款: 缺失/非法回退 true)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # drift 清洗: 每条必须有告警文本
        drifts = []
        raw_d = j.get("drift_warnings") if isinstance(j.get("drift_warnings"), list) else []
        for d in raw_d:
            if isinstance(d, dict) and str(d.get("warning", "") or "").strip():
                drifts.append({"warning": str(d["warning"]).strip()[:200],
                               "reason": str(d.get("reason", "")).strip()[:150]})

        # resume_view: Python 硬截断兜底(锁死契约1)
        view = str(j.get("resume_view", "") or "")
        if len(view) > MAX_VIEW_CHARS:
            view = view[:MAX_VIEW_CHARS] + "\n……【回视视图长度截断】"

        gs_view = {"drift_warnings": drifts, "resume_view": view,
                   "last_checkpoint_step": int(steps)}      # 每次执行覆盖=最新检查点

        note = "检查点steps=%d 漂移告警%d条" % (steps, len(drifts))
        return {"drift_warnings": drifts, "resume_view": view,
                "checkpoint_note": str(j.get("checkpoint_note", "") or "steps=%d" % steps),
                "gs_view": gs_view, "applicable": applicable,
                "summary": str(j.get("summary", "") or note) or note,
                "confidence": min(1.0, max(0.0, float(j.get("confidence", 0.5) or 0.5)))}

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        # 软性建议(裁决3): drift 非空倾向收敛——只作 suggest, 终裁权 router
        return bool(result.get("drift_warnings"))


SYSTEM_PROMPT = """你是链路过程回视模块(Gs 全局回视检查点)。只做两件事:
1. resume_view: 生成结构化精简续链视图——把轨迹每步关键产出压缩成后续节点
   快速可读的摘要, 抓结论与关键证据引用, 拒绝大段复述原文;
2. drift_warnings: 比对总目标与子问题, 检查推理链是否发生主题/方向漂移
   (偏离锚定目标/子问题跑偏/论证主题偏移); 无漂移返回空数组。
硬性约束:
- 只观测告警, 禁止修改、禁止纠正历史推理结论, 漂移仅如实记录;
- 严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "drift_warnings": [{"warning": "告警文本", "reason": "漂移原因简述"}],
  "resume_view": "结构化精简续链视图文本",
  "checkpoint_note": "检查点说明",
  "applicable": true,
  "summary": "一句话回视摘要",
  "confidence": 0.0
}"""