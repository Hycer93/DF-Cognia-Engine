# -*- coding: utf-8 -*-
"""
skill_sufficiency_check.py — 原子技能·结论充分性校验(P-C5, 2026-11-17 七段定稿)

七段定稿(用户裁决段一~段五+段六七合并终审):
  原节点定位: 链路收口后的后置参谋技能——对"结论↔证据"支撑关系做机械对账,
    可疑时 LLM 疑点复核。只告警不裁决不拦截(R15)。
  病灶链: ①llm_terminate 通道 terminate 汇编技能从未执行, 终态=最后一步
    60字 step_summary(病灶A, 由编排器补跑汇编修复, 本技能只消费) ②无任何机制
    校验结论被证据支撑(病灶B) ③素材检索盲区无人知晓(病灶C)。
  触发: 编排器后置(POST_EXIT_HINT 同 memory_gate 先例), 不占链路步数、
    不进 next_options 菜单、不写 steps_log, 审计独立 op=sufficiency:alert。
  state_inputs:  goal, goal_anchored, sub_questions, summary, trail, ref_material
  state_outputs: []  参谋不执行: 零回写(R15)
  result_fields: sufficiency_report, alerts, coverage_metrics, applicable,
    coverage_note, summary, confidence, ambiguity_note
  机械指标(段二2.3, 全 Python 可断言):
    subq_coverage / hypotheses_n / wl_addressed / empty_evidence_fields /
    degraded_steps / retrieval_dependent / material_unmapped /
    final_assembly_present(兼病灶A回归哨兵)
  LLM 复核(段三③c): 仅机械疑点触发; 疑点放大镜非法官; [LLM新发现]打标;
    措辞违规过滤保留原文; evidence_insufficient 独立键; 失败静默跳过+固定告警。
  红线:
    - R15: 输出永久只含告警/指标/疑点文本; 禁 pass/fail/阻断/写state;
      alerts 永不作任何自动机制触发条件
    - R16: LLM 复核永为机械指标下游放大器; 无机械疑点零 LLM 调用;
      禁升级常规全量评审
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, REF_INLINE_THRESHOLD
from cgraph.ref_retrieval import _tokens

# 候选告警阈值(段二A裁决: 初版候选, 段四实测校准, 无证据不动默认值)
THRESH_SUBQ_COVERAGE = 0.6       # 子问题覆盖率低于此告警
# wl_addressed==0 / retrieval_dependent=True / empty_evidence_fields>0 为布尔/计数判据, 无连续阈值

REVIEW_SYSTEM_PROMPT = """你是结论充分性疑点复核模块(参谋, 不是法官)。

你会收到: 已锚定目标、机械对账触发的疑点告警(量化指标)、思考链路各步骤的关键产出节选。
你的唯一职责: 对每个机械疑点, 判断"结论与现有证据之间的支撑关系"是否存在
进一步疑点, 把疑点用客观语言陈述出来。

铁律(逐条遵守):
- 只输出疑点陈述文本, 不输出 pass/fail/合格/不合格等任何裁决性结论
- 不建议任何行动(不写"应补跑""应修改", 只写"某某支撑关系存在缺口/风险")
- 不改写、不评价机械指标数值, 只引用
- 不生成新结论, 只质疑既有结论与证据的支撑关系
- 复核不覆盖时如实说明"证据不足以判断支撑关系", 禁止脑补
- 检索分片盲区: 若结论依赖参考素材片段, 明确陈述"该结论仅基于检索命中的
  片段, 素材全局可能存在未遍历的相关内容"——这是固有局限, 不是可修复缺陷

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "reviewed_alerts": ["对机械疑点1的复核陈述...", ...],
  "new_concerns": ["复核过程中新发现的支撑关系疑点(没有则空数组)", ...],
  "evidence_insufficient": ["证据不足以判断支撑关系的项(没有则空数组)", ...],
  "summary": "本次复核摘要(30字内)",
  "confidence": 0.0
}"""


class SkillSufficiencyCheck(BaseAtomicSkill):
    skill_id = "skill_sufficiency_check"

    def build_prompt(self, state: ThinkState) -> list:
        """仅疑点复核路径调用; 机械对账层零 prompt(段三3.1)"""
        goal = self._in(state, "goal_anchored") or self._in(state, "goal") or ""
        subs = self._in(state, "sub_questions") or []
        trail = self._in(state, "trail") or []
        mat = self._in(state, "ref_material") or ""
        report = getattr(self, "_pending_report", {}) or {}
        alerts = report.get("_mech_alerts", [])
        ctx_lines = [f"已锚定目标: {goal}", "", "机械疑点告警:"]
        for a in alerts:
            ctx_lines.append(f"- {a}")
        ctx_lines.append("")
        ctx_lines.append("链路关键产出节选(与疑点相关的步骤):")
        for e in trail:
            if not isinstance(e, dict):
                continue
            sid = str(e.get("skill_id", "?"))
            if sid in ("skill_sufficiency_check",):
                continue
            rf = e.get("result_fields") or {}
            ctx_lines.append(f"[{sid}]")
            ctx_lines.append(f"  step_summary: {str(e.get('step_summary', ''))[:120]}")
            if isinstance(rf, dict):
                for k, v in rf.items():
                    if k in ("hypotheses", "sub_questions", "weakest_point", "final_output",
                             "coverage_check", "termination_reason", "token_in", "token_out"):
                        ctx_lines.append(f"  {k}: {str(v)[:300]}")
            if mat and len(mat) > REF_INLINE_THRESHOLD and sid != "skill_anchor_target":
                ctx_lines.append("  (本技能消费过检索分片素材)")
        return [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(ctx_lines)},
        ]

    # ---- 机械对账(纯 Python, 段三3.1 无 prompt 无 LLM) ----

    def _mechanical_report(self, state: ThinkState) -> dict:
        subs = self._in(state, "sub_questions") or []
        trail = self._in(state, "trail") or []
        mat = self._in(state, "ref_material") or ""
        goal_anchored = self._in(state, "goal_anchored") or ""
        summary = self._in(state, "summary") or ""

        all_out = []
        for e in trail:
            if isinstance(e, dict):
                all_out.append(str(e.get("step_summary", "")))
                rf = e.get("result_fields")
                if isinstance(rf, dict):
                    all_out.extend(str(v) for v in rf.values())
        all_text = " ".join(all_out)

        # 1) subq_coverage: 子问题被下游产出回应的比例(2-gram 重叠, 段二B自洽基线)。
        #    对账对象排除 scan 自身的 sub_questions——问题清单复读不算"回应"
        #    (否则子问题自命中恒 100%, 指标失真)
        downstream_out = []
        past_scan = False
        for e in trail:
            if isinstance(e, dict):
                sid = e.get("skill_id")
                if sid == "skill_scope_scan":
                    past_scan = True
                    downstream_out.append(str(e.get("step_summary", "")))
                    continue
                if not past_scan:                       # scan 前的步(anchor)不回应子问题
                    continue
                downstream_out.append(str(e.get("step_summary", "")))
                rf = e.get("result_fields")
                if isinstance(rf, dict):
                    downstream_out.extend(str(v) for k, v in rf.items() if k != "token_in" and k != "token_out")
        subq_hit = 0
        for q in subs:
            qtxt = str(q.get("q", q) if isinstance(q, dict) else q) or ""
            qt2 = set(x for x in _tokens(qtxt) if len(str(x)) >= 2)
            if qt2 and any(qt2 & _tokens(seg) for seg in downstream_out if seg):
                subq_hit += 1
        subq_coverage = round(subq_hit / len(subs), 2) if subs else None

        # 2) hypotheses_n
        hypotheses_n = 0
        for e in trail:
            if isinstance(e, dict) and e.get("skill_id") == "skill_f_reason":
                rf = e.get("result_fields") or {}
                hyps = rf.get("hypotheses") if isinstance(rf, dict) else None
                if isinstance(hyps, list):
                    hypotheses_n = len(hyps)
        # 3) wl_addressed: weakest_point 与假设谱/后续产出的关键词重叠数
        wl_point = ""
        for e in trail:
            if isinstance(e, dict) and e.get("skill_id") == "skill_weakest_link":
                rf = e.get("result_fields") or {}
                wl_point = str(rf.get("weakest_point", "")) if isinstance(rf, dict) else ""
        wl_tokens = set(x for x in _tokens(wl_point) if len(x) >= 2)
        wl_addressed = 0
        if wl_tokens:
            for e in trail:
                if isinstance(e, dict) and e.get("skill_id") == "skill_f_reason":
                    rf = e.get("result_fields") or {}
                    hyps = rf.get("hypotheses") if isinstance(rf, dict) else None
                    for h in (hyps if isinstance(hyps, list) else []):
                        htxt = " ".join(str(v) for v in h.values()) if isinstance(h, dict) else str(h)
                        wl_addressed += len(wl_tokens & _tokens(htxt))
        # 4) empty_evidence_fields: 关键证据字段空值计数
        empty_ev = 0
        for e in trail:
            if isinstance(e, dict) and e.get("skill_id") in ("skill_scope_scan", "skill_f_reason"):
                rf = e.get("result_fields") or {}
                if isinstance(rf, dict):
                    for k in ("sub_questions", "hypotheses"):
                        if k in rf and not rf.get(k):
                            empty_ev += 1
        # 5) degraded_steps(terminate 同口径)
        degraded_steps = sum(1 for t in trail if isinstance(t, dict) and t.get("degraded"))
        # 6) retrieval_dependent: 检索分支 + 结论关键词命中素材(2-gram, 段二B)
        retrieval_branch = bool(mat) and len(mat) > REF_INLINE_THRESHOLD
        retrieval_dependent = False
        if retrieval_branch:
            mt = _tokens(mat)
            for key_txt in (goal_anchored, summary):
                kt = set(x for x in _tokens(key_txt) if len(x) >= 2)
                if kt and kt & mt:
                    retrieval_dependent = True
                    break
        # 7) material_unmapped: 素材存在但全链产出零命中素材关键词
        material_unmapped = False
        if mat:
            mt2 = set(x for x in _tokens(mat[:5000]) if len(str(x)) >= 2)
            out_t = _tokens(all_text)
            if mt2 and not (mt2 & out_t):
                material_unmapped = True
        # 8) final_assembly_present(病灶A哨兵)
        final_assembly_present = any(isinstance(e, dict) and e.get("skill_id") == "skill_terminate"
                                     for e in trail)

        return {
            "subq_coverage": subq_coverage, "hypotheses_n": hypotheses_n,
            "wl_addressed": wl_addressed, "empty_evidence_fields": empty_ev,
            "degraded_steps": degraded_steps, "retrieval_dependent": retrieval_dependent,
            "material_unmapped": material_unmapped,
            "final_assembly_present": final_assembly_present,
        }

    def _mechanical_alerts(self, metrics: dict, trail: list) -> list:
        """机械告警(段二候选阈值); 布尔/计数判据无连续阈值"""
        alerts = []
        sc = metrics.get("subq_coverage")
        if sc is not None and sc < THRESH_SUBQ_COVERAGE:
            alerts.append(f"[指标] subq_coverage={sc} 低于候选阈值{THRESH_SUBQ_COVERAGE}: 部分子问题未获任一步产出回应")
        if metrics.get("hypotheses_n", 0) == 0 and len(trail) >= 3:
            alerts.append("[指标] hypotheses_n=0: 多步链路未产出竞争假设谱(纯执行类属正常, 请结合任务类型判断)")
        if metrics.get("wl_addressed") == 0 and metrics.get("hypotheses_n", 0) > 0:
            alerts.append("[指标] wl_addressed=0: weakest_link 定位疑点未被任何假设关键词回应")
        if metrics.get("empty_evidence_fields", 0) > 0:
            alerts.append(f"[指标] empty_evidence_fields={metrics['empty_evidence_fields']}: 关键证据字段存在空值")
        if metrics.get("retrieval_dependent"):
            alerts.append("[指标] retrieval_dependent=True: 结论依赖检索分片素材, 存在文档全局未遍历盲区(固有局限)")
        if metrics.get("material_unmapped"):
            alerts.append("[指标] material_unmapped=True: 参考素材存在但全链产出零命中, 素材可能未被消费")
        return alerts

    # ---- LLM 疑点复核清洗(段三3.4) ----

    @staticmethod
    def _clean_review(j: dict) -> tuple:
        """返回 (reviewed, new_concerns, insufficient, filtered_raw)"""
        def _is_violating(txt: str) -> bool:
            t = txt.strip()
            return (t.startswith("建议") or t.startswith("应") or t.startswith("需要")
                    or t.startswith("必须") or "合格" in t[:6] or "不合格" in t[:8])

        reviewed, newc, insuf, dropped = [], [], [], []
        for x in (j.get("reviewed_alerts") or []) if isinstance(j.get("reviewed_alerts"), list) else []:
            s = str(x).strip()
            if not s:
                continue
            if _is_violating(s):
                dropped.append(s)                    # 过滤不丢弃: 原文保留供人审
                reviewed.append(f"[措辞违规已过滤] 原文: {s}")
            else:
                reviewed.append(s)
        for x in (j.get("new_concerns") or []) if isinstance(j.get("new_concerns"), list) else []:
            s = str(x).strip()
            if not s:
                continue
            if _is_violating(s):
                dropped.append(s)
                newc.append(f"[措辞违规已过滤] 原文: {s}")
            else:
                newc.append(f"[LLM新发现] {s}")     # 段三B: 来源强制打标
        for x in (j.get("evidence_insufficient") or []) if isinstance(j.get("evidence_insufficient"), list) else []:
            s = str(x).strip()
            if s:
                insuf.append(s)
        return reviewed, newc, insuf, dropped

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        # ① 机械对账(任何异常→技能降级: 机械层是存在意义, 段三3.4)
        try:
            metrics = self._mechanical_report(state)
        except Exception as e:
            return self._degraded(f"对账异常: {e}")
        trail = self._in(state, "trail") or []
        mech_alerts = self._mechanical_alerts(metrics, trail)
        goal_type = ""
        for e in trail:
            if isinstance(e, dict) and e.get("skill_id") == "skill_anchor_target":
                rf = e.get("result_fields") or {}
                goal_type = str(rf.get("goal_type", "")) if isinstance(rf, dict) else ""

        # ② 空链路/simple_query 不适用(合法非降级, 段三3.4)
        n_main = sum(1 for e in trail if isinstance(e, dict)
                     and e.get("skill_id") not in ("skill_terminate", "skill_memory_gate",
                                                   "skill_sufficiency_check"))
        if n_main <= 1 or goal_type == "simple_query":
            return {"sufficiency_report": {"applicable": False},
                    "alerts": [], "coverage_metrics": metrics, "applicable": False,
                    "coverage_note": "链路过短/simple_query直收口, 充分性校验不适用",
                    "summary": "不适用(短链路)", "confidence": 1.0, "ambiguity_note": ""}

        # ③ 无机械疑点→零LLM调用(R16), 正常路径
        if not mech_alerts:
            note = ""
            if metrics.get("retrieval_dependent"):
                note = "(素材走检索分支, 盲区局限已在指标标记)"
            return {"sufficiency_report": {"applicable": True, "llm_review": False},
                    "alerts": [], "coverage_metrics": metrics, "applicable": True,
                    "coverage_note": note, "summary": "机械对账无疑点", "confidence": 0.9,
                    "ambiguity_note": ""}

        # ④ 有机械疑点→LLM复核(段三; 失败静默跳过+固定告警)
        alerts = list(mech_alerts)
        reviewed, newc, insuf, dropped = [], [], [], []
        llm_reviewed = False
        if self.llm is not None:
            self._pending_report = {"_mech_alerts": mech_alerts}
            try:
                raw2 = self.llm.chat(self.build_prompt(state))
                j = extract_json(raw2) if raw2 else None
                if j:
                    reviewed, newc, insuf, dropped = self._clean_review(j)
                    llm_reviewed = True
            except Exception:
                pass                                    # fail-safe: 复核失败不炸
        if not llm_reviewed:
            alerts.append("[复核失败] 机械疑点仍待人工复核(LLM复核未产出)")
        else:
            alerts.extend(reviewed)
            alerts.extend(newc)
            for s in insuf:
                alerts.append(f"[证据不足] {s}")

        return {"sufficiency_report": {"applicable": True, "llm_review": llm_reviewed,
                                      "filtered_violations": dropped},
                "alerts": alerts, "coverage_metrics": metrics, "applicable": True,
                "coverage_note": ("结论可能依赖检索分片素材, 素材全局未遍历为固有局限"
                                  if metrics.get("retrieval_dependent") else ""),
                "summary": f"充分性校验: {len(alerts)}条告警", "confidence": 0.85,
                "ambiguity_note": ""}

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return True                                     # 恒真(链路收尾步, 编排器后置触发)