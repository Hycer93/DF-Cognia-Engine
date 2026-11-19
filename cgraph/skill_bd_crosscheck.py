# -*- coding: utf-8 -*-
"""
skill_bd_crosscheck.py — 原子技能·Bd双维交叉验证(P-skeleton-complete 段一定稿 v1.0)

七段定稿(2026-11-19 用户逐项裁决, docs/design_skeleton_skills_v01.md 节点②):
  - 定位: 假设谱x薄弱点逐对判定回应关系; 产关联矩阵+无回应薄弱点清单
  - 主线: f_reason -> Dk -> En -> Bd -> G24 -> terminate(就近交叉, gap提前暴露)
  - 裁决1: 输入消费 Dk 演绎链 + En 证据网(纵向链+横向网背景, 判定更扎实)
  - relation 三档(裁决3): 回应/竞争解释/无关——残缺覆盖写 note, 主标签仍"回应",
    不引入"部分回应"(三值稳定便于矩阵统计与观测告警)
  - unaddressed(裁决2): 纯 Python 派生(无任何回应/竞争解释关系的薄弱点),
    送 G24 合并进 gaps 单事实源; state 独立留观测
  - 前置守卫: hypotheses 为空 或 weakest_link 为空 -> applicable=false 跳过
  - 降级: LLM 异常 -> 输出置空 + _degraded, 链路不阻塞
  - 红线: 只判关系, 不评假设对错、不新增假设、不新增薄弱点(dfcp_link=无)
审计: op=atomic_skill:skill_bd_crosscheck, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, _ref_material_block

VALID_RELATIONS = ("回应", "竞争解释", "无关")   # 段一En裁决3同族: 主分类三值
HANDLED_RELATIONS = ("回应", "竞争解释")         # 有效承接关系(无关=不承接)


def _wl_locations(weakest_link) -> list:
    """薄弱点定位标识清单。wl 家族契约=单 dict {location, dimension, score,
    evidence}(单点语义), location 即 G24/Bd 全链路的薄弱点唯一标识;
    容错 list 形态(未来多点位扩展), 逐条取 location。"""
    if isinstance(weakest_link, dict):
        loc = str(weakest_link.get("location", "") or "").strip()
        return [loc] if loc else []
    if isinstance(weakest_link, list):
        out = []
        for w in weakest_link:
            if isinstance(w, dict):
                loc = str(w.get("location", "") or "").strip()
                if loc:
                    out.append(loc)
        return out
    return []


class SkillBdCrosscheck(BaseAtomicSkill):
    skill_id = "skill_bd_crosscheck"
    STATE_OUTPUT_MAP = {
        "bd_matrix": "cross_matrix",
        "unaddressed": "unaddressed_points",
        "summary": "summary",
    }

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        hyps = self._in(state, "hypotheses") or []
        wl = self._in(state, "weakest_link")
        chains = self._in(state, "deduction_chains") or []
        net = self._in(state, "evidence_net") or []
        summary = self._in(state, "summary") or ""

        hyp_lines = "\n".join("- %s" % (h.get("hypothesis", "") if isinstance(h, dict) else h)
                              for h in hyps) or "(无)"
        wl_locs = _wl_locations(wl)
        wl_block = ("\n".join("- 位置: %s" % loc for loc in wl_locs)) or "(无)"
        # 背景压缩: Dk 链只给 target+premise 骨架, En 网只给边三元组——
        # 足够支撑判定, 避免全量 JSON 塞 prompt 上下文爆炸
        chain_bg = "\n".join(
            "- %s: %s" % (c.get("target_ref", ""),
                          " / ".join(s.get("premise", "")[:60]
                                     for s in c.get("steps", [])[:4]))
            for c in chains if isinstance(c, dict)) or "(无)"
        net_bg = "\n".join("- %s x %s: %s" % (e.get("src_a", ""), e.get("src_b", ""),
                                              e.get("relation", ""))
                           for e in net if isinstance(e, dict)) or "(无)"

        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"【假设谱 hypotheses】\n{hyp_lines}\n\n"
               f"【薄弱点 weakest_link】\n{wl_block}\n\n"
               f"【Dk演绎链背景(纵向)】\n{chain_bg}\n\n"
               f"【En证据网背景(横向)】\n{net_bg}\n\n"
               f"【历史摘要】\n{summary or '(无)'}"
               + _ref_material_block(state, self))    # P-C4: 阈值分流注入
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        hyps = self._in(state, "hypotheses") or []
        wl = self._in(state, "weakest_link")
        wl_locs = _wl_locations(wl)

        # 前置守卫(锁定契约2): 任一侧空 -> applicable=false 跳过(非降级)
        if not hyps or not wl_locs:
            return {"cross_matrix": [], "unaddressed_points": [],
                    "applicable": False, "summary": "假设或薄弱点为空, 跳过交叉", "confidence": 0.0}

        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")   # 置空 + _degraded, 链路不阻塞

        # applicable 布尔顿化(家族同款: 缺失/非法回退 true)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # cross_matrix 清洗: 白名单(hyp/wl 标识必须在输入侧)+三档校验
        hyp_ids = {str(h.get("hypothesis", h if isinstance(h, str) else "") or "").strip()
                   for h in hyps}
        raw_m = j.get("cross_matrix") if isinstance(j.get("cross_matrix"), list) else []
        matrix, dropped = [], 0
        for e in raw_m:
            if not isinstance(e, dict):
                dropped += 1
                continue
            href = str(e.get("hypothesis_ref", "") or "").strip()
            wloc = str(e.get("wl_location", "") or "").strip()
            rel = str(e.get("relation", "") or "").strip()
            if not href or not wloc or not hyp_ids or href not in hyp_ids \
                    or wloc not in wl_locs or rel not in VALID_RELATIONS:
                dropped += 1                       # 白名单外/非法档位 -> 丢弃
                continue
            matrix.append({"hypothesis_ref": href, "wl_location": wloc,
                           "relation": rel,
                           "note": str(e.get("note", "") or "").strip()[:200]})

        # unaddressed 纯 Python 派生(锁定契约3): 无任何 回应/竞争解释 关系的
        # 薄弱点 -> 送 G24 合并进 gaps(单事实源); state 独立留观测
        handled = {e["wl_location"] for e in matrix if e["relation"] in HANDLED_RELATIONS}
        unaddressed = [loc for loc in wl_locs if loc not in handled]

        note = f"交叉{len(matrix)}对(丢弃{dropped}) 无承接薄弱点{len(unaddressed)}"
        return {"cross_matrix": matrix, "unaddressed_points": unaddressed,
                "applicable": applicable,
                "summary": str(j.get("summary", "") or note) or note,
                "confidence": min(1.0, max(0.0, float(j.get("confidence", 0.5) or 0.5)))}

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return False            # 恒False(工具型节点无自身终止逻辑)


SYSTEM_PROMPT = """你是双维交叉验证模块(思考链路的交叉验证分支, Bd)。
把每一条假设与每一条薄弱点做配对, 判定关系——只判关系, 不评假设对错、
不新增假设、不新增薄弱点。可参考演绎链与证据网背景辅助判断。
relation 严格三档(禁止自定义值):
- 回应: 该假设直接解答/覆盖这条薄弱点; 覆盖残缺不改档位, 残缺情况写进 note
- 竞争解释: 该假设给出与薄弱点相冲突的另一套解释
- 无关: 假设与该薄弱点主题不相关
规则严格遵守:
1. hypothesis_ref 与 wl_location 只能使用输入清单内的标识, 禁止编造;
2. 每对只判一次;
3. 严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "cross_matrix": [
    {"hypothesis_ref": "假设文本或编号", "wl_location": "薄弱点位置标识",
     "relation": "回应|竞争解释|无关", "note": "补充说明, 覆盖残缺/信息不足写这里"}
  ],
  "applicable": true,
  "summary": "一句话本轮交叉摘要",
  "confidence": 0.0
}"""