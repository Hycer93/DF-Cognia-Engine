# -*- coding: utf-8 -*-
"""
skill_weakest_link.py — 原子技能·最弱环节定位(试点④, 原元核 Bc/G02, 2026-11-16 七段定稿)

七段定稿(用户裁决四分歧后逐条代码化):
  原节点定位: 原 Bc/G02 深挖分支原子化; 接收锚定目标+任务空间扫描产物,
    对任务涉及的系统/方案/论证链条做脆弱面扫描: 定位最弱环节、输出各维度
    风险评分画像。仅产出画像与评分原始材料; 不输出风险处置、修复、改进
    建议, 不做风险裁决, 处置逻辑交给下游技能。
  state_inputs:  goal_anchored, sub_questions, summary(同 skill_source_intent)
  state_outputs: weakest_link, summary
  result_fields: weakest_point(唯一主字段 {location, dimension, score,
    evidence})/others(次级列表硬上限截断)/dimension_scores(dict[str,float],
    LLM 输出各维度原始 0-1 风险分)/risk_level(Python 阈值表从 min 映射:
    critical/high/medium/low/negligible)/applicable/summary/confidence/
    ambiguity_note
  关键契约(分歧①): LLM 只做语义打分; min 求最小值 + risk_level 档位映射
    完全 Python 侧刚性计算, 不由模型输出(规避 LLM 算术幻觉, 评分逻辑
    可审计可复现——F20 范式)。
  量纲(分歧②): 内部统一 0-1 连续分(对齐全体系 confidence 惯例);
    离散五档由 RISK_LEVELS 阈值表映射, 调档只改表不改 prompt。
  唯一性(分歧③): weakest_point 单点语义; 其余候选进 others 带硬上限。
  next_options: skill_terminate / skill_weakest_link 互补切换不上(本技能
    menu: skill_terminate / __planned__, 深挖分支无自环)
  terminate_suggest: 恒 False(深挖分支无自身终止逻辑, 终止仅来自菜单
    显式选择 skill_terminate; 恒 False ≠ is_exit:true, 校验器不误判)
  风险&规约:
    - 风险1 评分自洽漂移: Python 校验 weakest_point.score 必须等于
      min(dimension_scores.values()); 不一致做修正+note, 严重失配降级
    - 风险2 越权输出处置建议: 解析层过滤修复/改进类建议文本, 检测到
      丢弃该部分内容并在 ambiguity_note 记录("只画像不处置"红线)
    - 风险3 无分析对象任务误产出: applicable=false 显式标记(试点③先例);
      _degraded 只留给解析/LLM 异常
    - dfcp_link=无: v1 完全不读写 DFCP 卡片/topic
    - 审计: op=atomic_skill:skill_weakest_link, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

import re

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

MAX_OTHERS = 4              # 次级脆弱点硬上限(超限截断+note, 沿用统一防护模式)

# 分歧②: 风险档位阈值表(维度健壮分 0-1 → 五档风险; 分越低越脆弱:
# min<0.2=critical, <0.4=high, <0.6=medium, <0.8=low, 其余 negligible)
RISK_LEVELS = [
    (0.2, "critical"), (0.4, "high"), (0.6, "medium"), (0.8, "low"),
]

def map_risk_level(score: float) -> str:
    for th, lv in RISK_LEVELS:
        if score < th:
            return lv
    return "negligible"

# 分歧④b: 处置建议过滤模式(只画像不处置红线)
_REMEDY_PAT = re.compile(
    r"(建议|应该|应当|需要|尽快|优先)[^。；;]{0,24}"
    r"(修复|修复方案|改进|整改|优化|重构|替换|加固|排期|上线|回滚|处置|补齐|修复该)", re.IGNORECASE)

# state_outputs 键 → result 字段映射(基类按此写回)
STATE_OUTPUT_MAP = {
    "weakest_link": "weakest_point",     # state 存主字段(单点语义)
    "summary": "summary",
}

SYSTEM_PROMPT = """你是系统脆弱面扫描模块(思考链路的深挖分支)。只做画像与评分, 严禁处置建议。
针对任务涉及的系统/方案/论证链条, 找出整个体系中最脆弱的一环, 并给各维度打风险分。

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "applicable": true,
  "weakest_point": {"location": "最脆弱点位于哪个模块/环节", "dimension": "薄弱维度名称",
                    "score": 0.0, "evidence": "判定为最脆弱的依据"},
  "others": [{"location": "次级脆弱点", "dimension": "维度", "score": 0.0, "evidence": "依据"}],
  "dimension_scores": {"维度名": 0.0},
  "summary": "本技能摘要(30-60字)",
  "confidence": 0.0,
  "ambiguity_note": "识别到的歧义; 无则输出空字符串"
}

判定口径:
- applicable: 任务是否存在可分析的系统/方案/论证体系; 纯闲聊/单点事实查询
  必须输出 false 且 weakest_point/others/dimension_scores 为空(正常路径非失败);
- score/dimension_scores: 0~1 维度健壮分(越高越稳, 越低越脆弱——最弱一环即
  最低分维度; 宁高勿低);
- weakest_point 必须是全体系最脆弱的单点(即各维度最低分所在, 取最弱一环
  定律语义), 其余候选放 others;
- 严禁输出任何修复/改进/处置/排期建议——只画像不处置, 处置属下游;
- confidence: 本次扫描置信 0~1。"""


class SkillWeakestLink(BaseAtomicSkill):
    skill_id = "skill_weakest_link"
    STATE_OUTPUT_MAP = STATE_OUTPUT_MAP

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal_anchored")
        subs = self._in(state, "sub_questions") or []
        scan_summary = self._in(state, "summary") or ""
        sub_lines = "\n".join("- %s" % (s.get("q") if isinstance(s, dict) else s)
                              for s in subs) or "(无子问题)"
        ctx = (f"已锚定目标:\n{goal}\n\n"
               f"任务空间扫描摘要:\n{scan_summary or '(无)'}\n\n"
               f"子问题清单(定位哪些子问题涉及系统/方案/论证面):\n{sub_lines}")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ]

    # ---- 刚性聚合(分歧①: min/档位映射全 Python, LLM 只给原始分) ----

    @staticmethod
    def _clean_scores(d) -> dict:
        """dimension_scores 清洗: 只留可转 float 的 0-1 项"""
        if not isinstance(d, dict):
            return {}
        out = {}
        for k, v in d.items():
            try:
                f = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
            out[str(k).strip()] = f
        return out

    @staticmethod
    def _clean_point(p) -> dict | None:
        """单点结构清洗(location 必须非空; score 夹取; 处置建议文本剔除)"""
        if not isinstance(p, dict):
            return None
        loc = SkillWeakestLink._strip_remedy(str(p.get("location", "") or "").strip())
        if not loc:
            return None
        try:
            score = max(0.0, min(1.0, float(p.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        return {
            "location": loc,
            "dimension": SkillWeakestLink._strip_remedy(str(p.get("dimension", "") or "").strip()),
            "score": score,
            "evidence": SkillWeakestLink._strip_remedy(str(p.get("evidence", "") or "").strip()),
        }

    @staticmethod
    def _strip_remedy(text: str) -> str:
        """风险2红线: 剔除处置/修复类建议文本(调用方检测是否发生了剔除)"""
        return _REMEDY_PAT.sub("", text).strip()

    @staticmethod
    def _has_remedy(text: str) -> bool:
        return bool(_REMEDY_PAT.search(text))

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        j = extract_json(raw)
        if not j:
            return self._degraded("JSON解析失败")

        notes = []

        # 分歧②/风险3: applicable 布尔顿化(试点③同款)
        raw_app = j.get("applicable", True)
        if isinstance(raw_app, str):
            raw_app = raw_app.strip().lower() in ("true", "yes", "1")
        applicable = bool(raw_app) if isinstance(raw_app, (bool, int)) else True

        # 风险2: 处置建议过滤(整个 raw 文本层面检测, 记录后剔除)
        remedy_detected = self._has_remedy(raw)

        scores = self._clean_scores(j.get("dimension_scores"))
        wp_raw = j.get("weakest_point")
        wp = self._clean_point(wp_raw) if wp_raw else None

        if applicable:
            # 风险1: 评分自洽校验(空分/非法 → 降级, _degraded 只留解析类异常口径延伸)
            if not scores:
                return self._degraded("dimension_scores 空或全非法")
            min_score = min(scores.values())
            # weakest_point 缺失 → 用 min 维度构造(单点语义不可为空)
            if wp is None:
                dim = min(scores, key=scores.get)
                wp = {"location": "(未给出定位)", "dimension": dim,
                      "score": min_score, "evidence": ""}
                notes.append("weakest_point 缺失, 已由 min 维度构造")
            # 分歧①风险1: score 必须等于 min; 不一致做修正+note
            elif abs(wp["score"] - min_score) > 0.05:
                wp = dict(wp)
                notes.append(f"评分自洽修正: weakest_point.score {wp['score']:.2f}"
                             f" → min {min_score:.2f}")
                wp["score"] = min_score
            else:
                wp = dict(wp)
                wp["score"] = min_score          # 微差对齐, 不记 note
        else:
            wp = None
            notes.append("任务无系统分析对象, 技能不适用(语义正常路径)")

        # 分歧③: others 次级列表清洗+硬上限截断
        others_raw = j.get("others")
        others, truncated = [], False
        if isinstance(others_raw, list) and applicable:
            for it in others_raw:
                p = self._clean_point(it)
                if p and p["location"] != (wp or {}).get("location"):
                    others.append(p)
            if len(others) > MAX_OTHERS:
                others = others[:MAX_OTHERS]
                truncated = True

        risk_level = map_risk_level(min(scores.values())) if scores else ""

        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0

        amb = str(j.get("ambiguity_note", "") or "").strip()
        if remedy_detected:
            amb = (amb + " | " if amb else "") + "输入包含处置建议已被过滤(只画像不处置)"
        if truncated:
            amb = (amb + " | " if amb else "") + \
                f"次级脆弱点超上限截断: 原始{len(others_raw)}项取前{MAX_OTHERS}项"
        for n_ in notes:
            amb = (amb + " | " if amb else "") + n_

        summary = str(j.get("summary", "") or "").strip()[:60]
        if not summary:
            summary = ("最弱环节: %s(%s, %.2f)" % (wp["location"][:20], wp["dimension"],
                                                    wp["score"])) if wp else "无分析对象, 不适用"
        return {
            "weakest_point": wp or "",
            "others": others,
            "dimension_scores": scores,
            "risk_level": risk_level,
            "applicable": applicable,
            "summary": summary,
            "confidence": conf,
            "ambiguity_note": amb,
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """恒 False(七段定稿): 深挖分支无自身主动终止业务逻辑;
        终止只能显式选择 skill_terminate。正常/不适用/降级三态均不终止。"""
        return False