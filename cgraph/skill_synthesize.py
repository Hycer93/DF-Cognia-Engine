# -*- coding: utf-8 -*-
"""skill_synthesize: 假设谱收敛 + 完整交付推演(P-skill-synthesize 2026-11-18 段二契约).

定位(一体单步, 段二裁决1):
  f_reason 下游 / terminate 上游; 一次 LLM 调用内完成
  ① 权衡多条竞争假设并给路线选择理由
  ② 基于选定路线做完整推演生成交付终稿
  输出固定三段:
    selection_rationale     路线权衡(引用原始 hypothesis 编号)
    final_deliverable       主输出(完整推演/算子/公式/交底段落)
    alternatives_appendix   未选中竞争假设完整保留(不丢弃)

路由契约(段二裁决2, 零侵入 f_reason):
  邻接表 f_reason.next_options 头部插入本技能; 非 scheme_design 任务由
  router._clean_menu 依据 trail 内 anchor 的 goal_type 剔除本技能,
  analysis/review/simple_query 链路字节级回归。

强制红线(段二裁决3, R16 单测把守):
  - hypotheses 只读: state.trail 与 hypotheses 数组原样保留, 本技能只读不写;
  - 产出可回溯: selection_rationale/final_deliverable 必须引用原始编号,
    内容可追溯 hypotheses/ref_material, 禁止凭空编造;
  - 备选留存: alternatives_appendix 完整保留未选假设;
  - 零触碰 sufficiency: 不修改其任何输入输出;
  - 解析/LLM 异常静默降级: parse_result 只在 result 打 degraded 标记,
    绝不修改 state.hypotheses, next 强制 terminate, 链路回退旧行为。

v0 边界: 不注入 matrix_knowledge(最小面; 后续观测收益再放开)。
  state_inputs:  goal_anchored, sub_questions, summary(读 scan/f_reason 产物,
    state.summary 为链上最近一次写入), hypotheses, ref_material
  state_outputs: selection_rationale, final_deliverable, alternatives_appendix, summary
  result_fields: 同 state_outputs 三主段 + selection/final/appendix 对应键
  next_options:  skill_terminate(收口, synthesize 不做终止决策)
  terminate_suggest: 恒 False(与 f_reason 分歧⑥同款: 生成类不参与终止判定)
"""
from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, SkillResult, extract_json, _ref_material_block

SYSTEM_PROMPT = """你是方案终稿撰写专家。上游已产出多条互相竞争的候选假设(带编号),
你的任务分两步一次完成:

第一步·路线权衡(selection_rationale):
- 逐条评估各候选假设, 明确选定一条主路线(或说明融合多条的理由);
- 必须引用原始假设编号(如 H1/H2);
- 说明主路线优势与其他候选的短板。

第二步·完整推演(final_deliverable):
- 基于选定路线展开完整推演: 定义、公式体系、算子、判据、终止条件等;
- 输出必须达到可直接写入交底书/方案文档的完成度;
- 内容只能来自原始假设与参考素材, 不得凭空引入新证据; 需要新假设时
  显式标注"(本步新引入)"。

第三步·备选附录(alternatives_appendix):
- 其余未选中的竞争假设完整保留, 每条附一句保留理由。

只输出 JSON, 三段键: selection_rationale(字符串),
final_deliverable(字符串), alternatives_appendix(数组, 每条含
hypothesis_ref/summary/keep_reason), summary(一句话), confidence(0-1)。"""

STATE_OUTPUT_MAP = {
    "selection_rationale": "selection_rationale",
    "final_deliverable": "final_deliverable",
    "alternatives_appendix": "alternatives_appendix",
    "summary": "summary",
}


class SkillSynthesize(BaseAtomicSkill):
    skill_id = "skill_synthesize"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        hyps = self._in(state, "hypotheses") or []
        scan_summary = self._in(state, "summary") or ""
        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        # 假设谱全量陈列并编号(H1..Hn); 红线: 本技能只读, 编号仅用于引用
        hyp_lines = []
        for i, h in enumerate(hyps, 1):
            core = h.get("hypothesis") if isinstance(h, dict) else str(h)
            hyp_lines.append("H%d: %s" % (i, core or "(空)"))
        hyp_block = "\n".join(hyp_lines) or "(上游无竞争假设, 说明后可基于目标直接推演)"
        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"任务空间扫描摘要:\n{scan_summary or '(无)'}\n\n"
               f"子问题清单:\n{sub_lines}\n\n"
               f"候选假设谱(原始编号, 权衡与推演的对象):\n{hyp_block}"
               + _ref_material_block(state, self))  # P-C4: 参考素材注入(推演可回溯素材)
        # P-skill-synthesize v0: 不注入 matrix_knowledge(段二最小面裁决)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        d = extract_json(raw) or {}
        sel = str(d.get("selection_rationale") or "").strip()
        fin = str(d.get("final_deliverable") or "").strip()
        app = d.get("alternatives_appendix")
        out = {
            "selection_rationale": sel,
            "final_deliverable": fin,
            "alternatives_appendix": app if isinstance(app, list) else [],
            "summary": str(d.get("summary") or "假设谱收敛与终稿推演")[:200],
            "confidence": d.get("confidence") if isinstance(d.get("confidence"), (int, float)) else 0.6,
        }
        # 风险锚②: schema 校验失败只打降级标记, 绝不改 state.hypotheses;
        # 三段中 final_deliverable 为核心, 缺失即判 degraded(主链由 router
        # 感知 next 强制 terminate——result 内标记供 trail 留痕)
        if not fin:
            out["degraded"] = "final_deliverable_missing"
        if not sel and not fin:
            out["degraded"] = "empty_output"
        return out

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """恒 False(与 f_reason 分歧⑥同款): 生成类技能不做链路终止决策;
        收敛交由 skill_terminate。degraded 场景的 terminate 切换由
        router 依据 result_fields 内 degraded 标记处理, 非本方法职责。"""
        return False