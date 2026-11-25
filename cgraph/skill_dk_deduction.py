# -*- coding: utf-8 -*-
"""
skill_dk_deduction.py — 原子技能·Dk演绎链(P-skeleton-complete 段一定稿 v1.0)

七段定稿(2026-11-19 用户逐项裁决, docs/04_cgraph_skill/design_skeleton_skills_v01.md 节点③):
  - 定位: 关键结论的证据->前提->结论可追溯链构建; 只织链不裁决不生成新假设
  - 主线: f_reason -> Dk -> En -> Bd -> G24 -> terminate(链先行, Bd判定需要链)
  - 混合范式: LLM 拆解生成推理步骤链; unsupported_premises 纯 Python 派生检出
  - 硬约束: 单链最多 4 步截断(成本锁死); evidence_ref 白名单校验禁凭空引用
  - 前置守卫: hypotheses 与 weakest_link 两者全空 -> applicable=false 跳过
  - 降级: LLM 异常 -> chains 置空 + _degraded 标记, 链路不阻塞
  - unsupported_premises 送 G24 合并进 gaps 单事实源; state 独立留观测
  - 红线: 不修改不新增假设、不做真假裁决; 不读记忆库(dfcp_link=无)
审计: op=atomic_skill:skill_dk_deduction, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, _ref_material_block

MAX_CHAIN_STEPS = 4           # 单链步数硬上限(段一裁决2: 全量织链+4步截断锁死成本)

# state_outputs 键 -> result 字段映射(基类按此写回; unsupported_premises
# 并入 deduction_chains 容器字段, 不单独成 state 键——段一定稿汇总§2)
STATE_OUTPUT_MAP = {
    "deduction_chains": "chains",
    "summary": "summary",
}

SYSTEM_PROMPT = """你是演绎链构建模块(思考链路的证据溯源分支, Dk)。
只为给定假设与薄弱点构建【证据->前提->结论】可追溯推理链——不评判真假、
不新增假设、不新增薄弱点。规则严格遵守:
1. 每条链的 target: 一条 hypothesis 或 weakest_link 条目, 全部 hypotheses
   与 weakest_link 都要织链(全量织链契约);
2. 单条演绎链最多 4 个步骤, 超出选最关键的 4 步;
3. 每步 premise 必须携带 evidence_ref, 只能引用输入中真实出现的参考素材
   片段标记或矩阵知识卡 id, 禁止编造不存在的引用;
4. 找不到支撑证据的前提, evidence_ref 留空字符串(将由系统标记为
   unsupported, 供后续缺口汇总);
5. 严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "chains": [
    {"target_ref": "H1 或 薄弱点位置标识",
     "steps": [{"premise": "前提文本", "evidence_ref": "素材引用或空串", "note": "补充说明"}],
     "verdict_ready": true}
  ],
  "applicable": true,
  "summary": "一句话本轮链构建摘要",
  "confidence": 0.0
}"""


class SkillDkDeduction(BaseAtomicSkill):
    skill_id = "skill_dk_deduction"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        hyps = self._in(state, "hypotheses") or []
        wl = self._in(state, "weakest_link")
        summary = self._in(state, "summary") or ""
        hyp_lines = "\n".join("- %s" % (h.get("hypothesis", "") if isinstance(h, dict) else h)
                              for h in hyps) or "(无)"
        wl_block = ""
        if isinstance(wl, dict) and wl.get("location"):
            wl_block = (f"\n【薄弱点 weakest_link】\n- 位置: {wl.get('location')}\n"
                        f"- 维度: {wl.get('dimension')} | 风险分: {wl.get('score')}\n"
                        f"- 依据: {wl.get('evidence')}")
        else:
            wl_block = "\n【薄弱点 weakest_link】\n(无)"
        ctx = (f"已锚定目标:\n{goal}\n"
               f"\n【已有假设谱 hypotheses】\n{hyp_lines}"
               f"{wl_block}"
               f"\n\n【历史摘要】\n{summary or '(无)'}"
               + _ref_material_block(state, self))    # P-C4: 阈值分流注入
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        # 前置守卫(锁定契约2): hypotheses 与 weakest_link 两者全空 ->
        # applicable=false 直接跳过, 不降级不报错(纯执行前置链未走时常态)
        hyps = self._in(state, "hypotheses") or []
        wl = self._in(state, "weakest_link")
        if not hyps and not (isinstance(wl, dict) and wl.get("location")):
            return {"chains": [], "applicable": False, "unsupported_premises": [],
                    "summary": "前置输入全空, 跳过织链", "confidence": 0.0}

        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")     # chains 置空 + _degraded, 链路不阻塞

        # applicable 布尔顿化(对齐 f_reason 分歧②同款: 缺失/非法回退 true)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # chains 强校验: 列表逐条清洗; 非法条目丢弃计数
        raw_chains = j.get("chains")
        if not isinstance(raw_chains, list):
            raw_chains = [] if raw_chains in (None, "") else None
            if raw_chains is None:
                return self._degraded("chains 非列表")

        chains, dropped = [], 0
        for c in raw_chains:
            if not isinstance(c, dict):
                dropped += 1
                continue
            tgt = str(c.get("target_ref", "") or "").strip()
            if not tgt:                               # 本体空 -> 整条丢弃
                dropped += 1
                continue
            # 步骤清洗 + 4步硬截断(裁决2契约)
            raw_steps = c.get("steps") if isinstance(c.get("steps"), list) else []
            steps = []
            for s in raw_steps[:MAX_CHAIN_STEPS]:
                if not isinstance(s, dict):
                    continue
                premise = str(s.get("premise", "") or "").strip()
                if not premise:
                    continue
                steps.append({"premise": premise,
                              "evidence_ref": str(s.get("evidence_ref", "") or "").strip(),
                              "note": str(s.get("note", "") or "").strip()})
            if len(raw_steps) > MAX_CHAIN_STEPS and steps:
                steps[-1]["note"] = (steps[-1]["note"] + "【已达单链4步上限截断】").strip()
            chains.append({"target_ref": tgt, "steps": steps,
                           "verdict_ready": bool(c.get("verdict_ready", False))})

        # Python 派生(锁定契约3): unsupported_premises 检出——evidence_ref 为空
        # 的前提, 内嵌各链; 汇总列表同步给 result(供 G24 合并进 gaps 单事实源)
        total_unsupported = 0
        for c in chains:
            un = [s for s in c["steps"] if not s["evidence_ref"]]
            c["unsupported_premises"] = un
            total_unsupported += len(un)

        note = f"织链{len(chains)}条(丢弃{dropped}) 无证据前提{total_unsupported}步"
        return {"chains": chains, "applicable": applicable,
                "unsupported_premises": [s for c in chains for s in c["unsupported_premises"]],
                "summary": str(j.get("summary", "") or note) or note,
                "confidence": min(1.0, max(0.0, float(j.get("confidence", 0.5) or 0.5)))}

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return False            # 恒False(工具型节点无自身终止逻辑)