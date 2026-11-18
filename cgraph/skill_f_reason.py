# -*- coding: utf-8 -*-
"""
skill_f_reason.py — 原子技能·多假设推演(F20封装, 2026-11-16 七段定稿)

七段定稿(用户裁决分歧①-⑦后逐条代码化):
  原节点定位: F 系推演能力原子化封装。接收锚定目标+任务空间产物, 对关键
    不确定点生成多竞争性假设谱: 每条假设只陈列【假设-支持证据-反证-验证
    路径】四元组, 只推演不下最终裁决。分工: weakest_link 向内评估已知方案
    脆弱点; f_reason 向外生成可能性候选空间。
    软围栏(分歧①B案): 移除 plausibility 档位字段——倾向性信息只散落在
    supporting/contradicting/test_next 内, 不提炼顶层档位, 杜绝预结论诱导
    人闸(橡皮图章风险); 收紧容易放宽难, pilot 期最严口径。
  state_inputs:  goal_anchored, sub_questions, summary(读 scan 产物,
    定位哪些子问题含关键不确定点)
  state_outputs: hypotheses, summary
  result_fields: hypotheses(每项 {hypothesis, supporting, contradicting,
    test_next}——无 plausibility)/applicable(bool)/coverage_note/summary/
    confidence/ambiguity_note
  next_options: skill_terminate(假设谱完成收敛) / __planned__(后续深挖
    占位)。分歧④B案: 不纳入 skill_weakest_link——wl 的 state_inputs 不含
    hypotheses, 跳过去读不到假设谱属假连通; 未来做假设脆弱交叉验证先迭代
    wl 七段扩展 state_inputs 再补边。
  terminate_suggest: 恒 False(深挖分支无自身终止逻辑, 收敛决策交由
    skill_terminate 综合完整 state 完成)。恒 False ≠ is_exit:true, 校验器
    不误判。
  风险&规约:
    - 风险1 预结论诱导: 无 plausibility/无 priority 字段; 截断按输出顺序
      (不做优先级排序, 排序即裁决倾向); 超限 note 记"假设数量超限, 仅保留
      前5条, 原始生成M条"
    - 风险2 空谱三态(分歧⑤): applicable=true+清洗后空→降级(O2 同款前置
      防线); applicable=false+空→合法非降级(纯执行类任务无推演空间);
      true+有效条目→正常
    - 风险3 证据字段清洗(分歧⑦): 证据字段宽容/假设本体严格——supporting/
      contradicting 单字符串自动包 [str]; 既非字符串也非列表丢弃整条+note;
      仅 hypothesis 本体空才因本体丢弃
    - 风险4 越权裁决: 只陈列推演材料, 不下结论、不写记忆、不触发提案;
      红线: 不调 seed_topic/mount_topic/state 改写/审计处置写入
    - dfcp_link=无(段二裁决): 现阶段纯思考技能不读记忆库; 未来接入只读
      副本必须重走完整七段评审, 不能直接改字段
    - 审计: op=atomic_skill:skill_f_reason, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json, _ref_material_block
from cgraph.matrix_knowledge import matrix_block

MAX_HYPOTHESES = 5         # 假设条目硬上限(对齐 MAX_SOURCES/MAX_OTHERS 截断家族)

# state_outputs 键 → result 字段映射(基类按此写回)
STATE_OUTPUT_MAP = {
    "hypotheses": "hypotheses",
    "summary": "summary",
}

SYSTEM_PROMPT = """你是多假设推演模块(思考链路的深挖分支, F系推演能力的原子化封装)。
只生成竞争性假设谱, 不下最终裁决——你永远不说"结论是X", 只陈列每条假设的
支持证据、反证与验证路径, 裁决权在人。

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "applicable": true,
  "hypotheses": [
    {"hypothesis": "一句话竞争性假设",
     "supporting": ["支持该假设的证据/条件"],
     "contradicting": ["反证/与该假设冲突的点"],
     "test_next": "如何进一步验证该假设"}
  ],
  "coverage_note": "假设谱覆盖说明: 覆盖了哪些解释方向, 明显遗漏了哪些方向",
  "summary": "本技能摘要(30-60字)",
  "confidence": 0.0,
  "ambiguity_note": "歧义或截断说明; 无则输出空字符串"
}

判定口径:
- applicable: 任务存在关键不确定点(多解可能/因果不明/证据冲突)才为 true;
  纯执行类任务(无推演空间)必须输出 false 且 hypotheses 为空数组
  (这是正常业务路径, 不是失败);
- 各假设之间应相互竞争(解释同一现象的不同可能), 不是同一假设的细分;
- 严禁输出可信度档位/优先级排序/倾向性结论——证据自陈, 判断留人;
- 假设条目宁精勿滥: 通常 2-4 条, 上限 5;
- confidence: 本次推演覆盖度的自评 0~1(宁低勿高, 不代表任何假设的成立概率)。"""


class SkillFReason(BaseAtomicSkill):
    skill_id = "skill_f_reason"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        scan_summary = self._in(state, "summary") or ""
        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"任务空间扫描摘要:\n{scan_summary or '(无)'}\n\n"
               f"子问题清单(定位哪些子问题含关键不确定点, 对其生成竞争假设):\n{sub_lines}"
               + _ref_material_block(state, self))    # P-C4: 参考素材阈值分流注入
        # P-matrix-feed(2026-11-18 段二契约): 外部领域知识注入, 仅本技能+wl;
        # 物理隔离于 ref_material(独立分隔符头); 空 matrix → 恒''(R15c 字节级回归);
        # sufficiency 判据零读取 matrix(R15a 源码断言把守)
        ctx += matrix_block(state, self)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")

        # applicable 布尔顿化(对齐 source_intent 分歧②同款: 缺失/非法回退 true)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # hypotheses 强校验: 列表→逐项清洗
        raw_hyp = j.get("hypotheses")
        if not isinstance(raw_hyp, list):
            if raw_hyp in (None, ""):
                raw_hyp = []
            else:
                return self._degraded("hypotheses 非列表")

        items, dropped, ev_dropped = [], 0, 0
        for it in raw_hyp:
            if not isinstance(it, dict):
                dropped += 1
                continue
            hyp = str(it.get("hypothesis", "") or "").strip()
            if not hyp:                          # 假设本体空→丢弃(分歧⑦: 本体严格)
                dropped += 1
                continue
            # 证据字段宽容清洗(分歧⑦): str→[str]; 非str非list→丢整条
            sup = self._clean_evidence(it.get("supporting"), allow_missing=True)
            con = self._clean_evidence(it.get("contradicting"), allow_missing=True)
            if sup is None or con is None:
                ev_dropped += 1
                continue
            items.append({
                "hypothesis": hyp,
                "supporting": sup,
                "contradicting": con,
                "test_next": str(it.get("test_next", "") or "").strip(),
            })

        # 风险1: 条数硬上限——顺序截断(不做优先级排序, 排序即裁决倾向)
        truncated = len(items) > MAX_HYPOTHESES
        if truncated:
            items = items[:MAX_HYPOTHESES]

        # 一致性: applicable=false 时 hypotheses 必须为空(反向强制)
        if not applicable:
            items = []

        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0

        notes = []
        amb = str(j.get("ambiguity_note", "") or "").strip()
        if amb:
            notes.append(amb)
        if dropped:
            notes.append(f"条目清洗丢弃: {dropped}条(缺hypothesis本体或非dict)")
        if ev_dropped:
            notes.append(f"证据字段非法丢弃整条: {ev_dropped}条(supporting/contradicting非字符串非列表)")
        if truncated:
            notes.append(f"假设数量超限, 仅保留前{MAX_HYPOTHESES}条, 原始生成{len(raw_hyp)}条")
        amb = " | ".join(notes)

        summary = str(j.get("summary", "") or "").strip()[:60]
        if not summary:
            summary = "假设谱: %d条竞争假设" % len(items)
        if not applicable and not items and not j.get("summary"):
            summary = "无关键不确定点, 技能不适用(语义正常路径)"

        # 风险2 三态(分歧⑤/O2前置防线): 声称适用但清洗后空谱 → 强制降级
        if applicable and not items:
            return self._degraded(f"声称适用但假设谱全空(清洗后0条; 原始{len(raw_hyp)}项)")

        return {
            "hypotheses": items,
            "applicable": applicable,
            "coverage_note": str(j.get("coverage_note", "") or "").strip(),
            "summary": summary,
            "confidence": conf,
            "ambiguity_note": amb,
        }

    @staticmethod
    def _clean_evidence(v, allow_missing: bool = True):
        """证据字段清洗(分歧⑦): 宽容——str包[str]保留; 缺失→[]; 非str非list→None(丢整条)"""
        if v is None or v == "":
            return [] if allow_missing else None
        if isinstance(v, str):
            v = v.strip()
            return [v] if v else []
        if isinstance(v, list):
            out = []
            for x in v:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return out
        return None

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """恒 False(分歧⑥): 深挖推演类技能不做链路收敛决策; applicable 仅是
        result 内语义标记, 不变成终止控制信号。收敛交由 skill_terminate
        综合完整 state(含 hypotheses/applicable)判断。"""
        return False