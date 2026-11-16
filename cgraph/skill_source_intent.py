# -*- coding: utf-8 -*-
"""
skill_source_intent.py — 原子技能·信息源头意图检测(试点③, 原元核 Bb/G01, 2026-11-16 七段定稿)

七段定稿(用户裁决四分歧后逐条代码化):
  原节点定位: 原 Bb/G01 发散后深挖动作原子化; 接收锚定目标+任务空间扫描产物,
    对任务涉及的外部信息源做意图画像: 识别发布主体、发布时机与底层动机诉求。
    v1 合并 behavior-1(主体追溯)+behavior-2(时机动机挖掘); behavior-3(可信度
    打分)移出——属事实核查类裁决能力, 未来独立技能(需重分歧③, 且大概率接
    DFCP 历史卡片做信源回溯, 会破 dfcp_link=无 约束)。只产出画像参考材料,
    不做信息采纳/拒绝裁决, 不触发提案, 供下游分馏核查使用。
  state_inputs:  goal_anchored, sub_questions, summary(读 scope_scan 产物,
    定位哪些子问题包含外部信息线索)
  state_outputs: source_intents, summary
  result_fields: source_intents(每项 {source, actor, timing_motive,
    is_inference}——无 trust 字段)/applicable(bool)/summary/confidence/
    ambiguity_note
  next_options: skill_terminate(深挖完成收敛) / __planned__(后续深挖占位);
    禁自环(邻接表校验器自环检测拦截)
  terminate_suggest: 恒 False(深挖分支无自身终止逻辑, 终止仅来自菜单显式
    选择 skill_terminate)。恒 False ≠ is_exit:true, 校验器不误判。
  风险&规约:
    - 风险1 动机臆造脑补: 强制 is_inference 标记推测内容; 推测条目数量
      不超过显式线索条目, 超限截断写入 ambiguity_note
    - 风险2 越权裁决: 仅产出画像素材; v1 不做可信度判定、不做采纳拒绝、
      不写记忆、不生成提案; 严守人闸纪律
    - 风险3 无信源任务误产出: applicable=false 显式标记"语义不适用";
      _degraded 只留给解析/LLM 异常——二者严格分开(分歧②裁决)
    - dfcp_link=无: v1 完全不读写 DFCP 卡片/topic
    - 审计: op=atomic_skill:skill_source_intent, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

MAX_SOURCES = 6            # 信源条目硬上限(防画像爆炸, 超出截断+note)


# state_outputs 键 → result 字段映射(基类按此写回)
STATE_OUTPUT_MAP = {
    "source_intents": "source_intents",
    "summary": "summary",
}

SYSTEM_PROMPT = """你是信息源头意图检测模块(思考链路的深挖分支)。只做源画像, 不做裁决。
针对任务涉及的外部信息源, 回答: 谁放出的? 为什么这个时间放? 想达成什么?

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "applicable": true,
  "source_intents": [
    {"source": "信息源标识(文档/人/媒体/数据出处)",
     "actor": "发布主体(谁放的)",
     "timing_motive": "发布时机与底层诉求(为什么这个时间, 想达成什么)",
     "is_inference": false}
  ],
  "summary": "本技能摘要(30-60字)",
  "confidence": 0.0,
  "ambiguity_note": "识别到的歧义或推测超限说明; 无则输出空字符串"
}

判定口径:
- applicable: 任务是否存在外部信息源; 纯设计/纯计算/纯情绪类任务无信源时
  必须输出 false 且 source_intents 为空数组(这是正常业务路径, 不是失败);
- is_inference: 该条目属模型推测而非任务文本直接给出时必须为 true;
  推测条目数不得超过文本显式线索条目数, 严禁无依据脑补动机;
- 不评估信源可信度, 不判断信息该不该采纳——画像材料而已;
- confidence: 本次挖掘置信 0~1(宁低勿高)。"""


class SkillSourceIntent(BaseAtomicSkill):
    skill_id = "skill_source_intent"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        scan_summary = self._in(state, "summary") or ""
        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"任务空间扫描摘要:\n{scan_summary or '(无)'}\n\n"
               f"子问题清单(定位哪些含外部信息线索):\n{sub_lines}")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")

        # 分歧②: applicable 布尔顿化(缺失/非法回退 true——语义判断交给 LLM,
        # 但只信严格布尔; 解析失败不算 applicable 语义, 那是 _degraded 的事)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # source_intents 强校验: 列表→逐项清洗(无 trust 字段——分歧①裁决)
        raw_src = j.get("source_intents")
        if not isinstance(raw_src, list):
            if raw_src in (None, ""):
                raw_src = []
            else:
                return self._degraded("source_intents 非列表")
        items, truncated = [], False
        for it in raw_src:
            if not isinstance(it, dict):
                continue
            src = str(it.get("source", "") or "").strip()
            if not src:
                continue
            items.append({
                "source": src,
                "actor": str(it.get("actor", "") or "").strip(),
                "timing_motive": str(it.get("timing_motive", "") or "").strip(),
                "is_inference": bool(it.get("is_inference", False)),
            })
        # 风险1: 条目数硬上限
        if len(items) > MAX_SOURCES:
            items = items[:MAX_SOURCES]
            truncated = True

        # 风险1: 推测条目不得超过显式线索条目(脑补防泄漏)
        infer_n = sum(1 for x in items if x["is_inference"])
        explicit_n = len(items) - infer_n
        cut_infer = False
        if infer_n > explicit_n:
            kept, seen_infer = [], 0
            for x in items:
                if x["is_inference"]:
                    if seen_infer < explicit_n:
                        kept.append(x)
                        seen_infer += 1
                    else:
                        cut_infer = True
                else:
                    kept.append(x)
            items = kept

        # 一致性: applicable=false 时 source_intents 必须为空(反向强制)
        if not applicable:
            items = []

        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0

        amb = str(j.get("ambiguity_note", "") or "").strip()
        if truncated:
            amb = (amb + " | " if amb else "") + \
                f"信源条目超上限截断: 原始{len(raw_src)}项取前{MAX_SOURCES}项"
        if cut_infer:
            amb = (amb + " | " if amb else "") + \
                "推测条目超限截断: 保留数不超过显式线索数"

        summary = str(j.get("summary", "") or "").strip()[:60]
        if not summary:
            summary = "源意图画像: %d源(推测%d)" % (len(items), infer_n)
        if not applicable and not items and not j.get("summary"):
            # applicable=false 且无摘要属正常路径——构造默认摘要而非降级
            summary = "无外部信息源, 技能不适用(语义正常路径)"
        if applicable and not items and not j.get("summary"):
            return self._degraded("声称有信源但画像全空")
        return {
            "source_intents": items,
            "applicable": applicable,
            "summary": summary,
            "confidence": conf,
            "ambiguity_note": amb,
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """恒 False(七段定稿): 深挖分支不存在自身触发终止的业务逻辑;
        终止只能来自菜单中显式选择 skill_terminate。
        注意降级态/不适用态同样不终止——终止判定与本技能完全无关。"""
        return False