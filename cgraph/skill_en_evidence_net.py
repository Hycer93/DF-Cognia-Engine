# -*- coding: utf-8 -*-
"""
skill_en_evidence_net.py — 原子技能·En证据网(P-skeleton-complete 段一定稿 v1.0)

七段定稿(2026-11-19 用户逐项裁决, docs/design_skeleton_skills_v01.md 节点④):
  - 定位: 多源证据横向交叉关联(印证/矛盾/补充); 只织网不裁决证据真伪
  - 主线: Dk -> En -> Bd -> G24 -> terminate
  - 配对契约(裁决2方案b): 只判 deduction_chains 内出现过的 evidence_ref
    集合生成的两两对; 集合为空 -> applicable=false 直接跳过; 单 prompt
    批量输出全部配对关系
  - 矛盾对(裁决3): contradiction_pairs 不进 G24.gaps——terminate 汇编
    独立"证据冲突说明"章节披露(事实≠缺口)
  - 混合范式: LLM 判定证据边关系; 引用计数/isolated_evidence 纯 Python
    派生; LLM 降级 -> 输出置空 + _degraded, 链路不阻塞
  - 红线: 不新增证据、不裁决哪一方证据为真; 不读记忆库(dfcp_link=无)
审计: op=atomic_skill:skill_en_evidence_net, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, _ref_material_block

# state_outputs 键 -> result 字段映射(基类按此写回)
STATE_OUTPUT_MAP = {
    "evidence_net": "net",
    "isolated_evidence": "isolated_evidence",
    "contradiction_pairs": "contradiction_pairs",
    "summary": "summary",
}

VALID_RELATIONS = ("印证", "矛盾", "补充")   # 关系三档(段一En裁决3同族: 主分类三值稳定)


def _refs_from_chains(deduction_chains: list) -> dict:
    """从 Dk 演绎链提取全部非空 evidence_ref -> {ref: 引用次数}。
    纯 Python, 是配对集合与孤立检出的共同数据源(锁定契约)。"""
    counter = {}
    for chain in deduction_chains or []:
        if not isinstance(chain, dict):
            continue
        for step in chain.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            ref = str(step.get("evidence_ref", "") or "").strip()
            if ref:
                counter[ref] = counter.get(ref, 0) + 1
    return counter


class SkillEnEvidenceNet(BaseAtomicSkill):
    skill_id = "skill_en_evidence_net"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        summary = self._in(state, "summary") or ""
        counter = _refs_from_chains(self._in(state, "deduction_chains"))
        ref_list = sorted(counter.keys())
        # 引用上下文: 让 LLM 判关系时能看到各证据的原文片段(从 ref_material
        # 抽取含该标记的行; matrix 卡按 id 对齐——只读, 白名单内引用)
        ref_ctx = []
        mat = self._in(state, "matrix_knowledge") or []
        for ref in ref_list:
            hit = next((m for m in mat if isinstance(m, dict)
                        and str(m.get("matrix_id", "")) == ref), None)
            if hit:
                ref_ctx.append("- %s: [matrix] %s" % (ref, hit.get("tech_name", "")))
                continue
            mat_raw = self._in(state, "ref_material") or ""
            pos = mat_raw.find(ref)
            if pos >= 0:
                snippet = mat_raw[max(0, pos - 60):pos + 120].replace("\n", " ")
                ref_ctx.append("- %s: %s..." % (ref, snippet[:150]))
            else:
                ref_ctx.append("- %s: (链内引用标记, 素材原文未定位)" % ref)

        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"证据清单(仅Dk演绎链内实际引用过的, 含原文定位):\n"
               + "\n".join(ref_ctx) +
               f"\n\n【历史摘要】\n{summary or '(无)'}")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        counter = _refs_from_chains(self._in(state, "deduction_chains"))
        # 前置守卫(裁决2契约): Dk 链引用集合为空 -> applicable=false 跳过
        if not counter:
            return {"net": [], "isolated_evidence": [], "contradiction_pairs": [],
                    "applicable": False, "summary": "Dk链无有效证据引用, 跳过织网",
                    "confidence": 0.0}

        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")   # 置空 + _degraded, 链路不阻塞

        # applicable 布尔顿化(家族同款: 缺失/非法回退 true)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # net 清洗: 只留白名单引用对 + 关系三档 + 去重(a,b)==(b,a)
        raw_net = j.get("net") if isinstance(j.get("net"), list) else []
        seen_pairs, net, dropped = set(), [], 0
        for e in raw_net:
            if not isinstance(e, dict):
                dropped += 1
                continue
            a = str(e.get("src_a", "") or "").strip()
            b = str(e.get("src_b", "") or "").strip()
            rel = str(e.get("relation", "") or "").strip()
            if not a or not b or a not in counter or b not in counter:
                dropped += 1                       # 白名单外引用 -> 丢弃
                continue
            if rel not in VALID_RELATIONS:
                dropped += 1
                continue
            key = tuple(sorted((a, b)))
            if key in seen_pairs:
                dropped += 1                       # (a,b)/(b,a) 同对只留一
                continue
            seen_pairs.add(key)
            net.append({"src_a": a, "src_b": b, "relation": rel,
                        "note": str(e.get("note", "") or "").strip()[:200]})

        # contradiction_pairs 纯 Python 派生(锁定契约2: LLM 只标 relation,
        # 矛盾对由 net 关系确定性筛出——比"LLM 单独输出矛盾数组"更稳)
        contradictions = [{"src_a": e["src_a"], "src_b": e["src_b"], "note": e["note"]}
                          for e in net if e["relation"] == "矛盾"]

        # isolated_evidence 纯 Python 派生: 引用次数=0 不可能(集合来自链内
        # 引用), 实际语义=只出现一次且无任何 net 边的孤立证据(无横向关联)
        in_net = {r for e in net for r in (e["src_a"], e["src_b"])}
        isolated = [ref for ref in sorted(counter.keys()) if ref not in in_net]

        note = f"织网{len(net)}边(丢弃{dropped}) 矛盾{len(contradictions)} 孤立{len(isolated)}"
        return {"net": net, "isolated_evidence": isolated,
                "contradiction_pairs": contradictions, "applicable": applicable,
                "summary": str(j.get("summary", "") or note) or note,
                "confidence": min(1.0, max(0.0, float(j.get("confidence", 0.5) or 0.5)))}

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return False            # 恒False(工具型节点无自身终止逻辑)


SYSTEM_PROMPT = """你是证据横向关联分析模块(思考链路的证据网分支, En)。
只对给定的证据清单做两两关系判定——不新增证据、不裁决哪一方证据为真。
关系三档(只允许这三个值):
- 印证: 两份证据指向同一结论, 互相支撑
- 矛盾: 两份证据在事实层面互相冲突
- 补充: 不冲突, 一份提供另一份没有的附加细节
规则严格遵守:
1. 只处理输入清单内的证据, 禁止编造清单外的证据标识;
2. 每对只判一次(a,b 与 b,a 视为同一对);
3. 关系不明或不构成实质关联的对, 直接不输出(宁缺勿滥);
4. 严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "net": [
    {"src_a": "证据id1", "src_b": "证据id2", "relation": "印证|矛盾|补充", "note": "简短说明"}
  ],
  "applicable": true,
  "summary": "一句话本轮织网摘要",
  "confidence": 0.0
}"""