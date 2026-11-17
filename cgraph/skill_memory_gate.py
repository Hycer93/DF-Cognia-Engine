# -*- coding: utf-8 -*-
"""
skill_memory_gate.py — 原子技能·记忆写入闸门(Jw经验闭环后置收口, 2026-11-17 七段定稿)

七段定稿(用户裁决分歧后逐条代码化):
  原节点定位(段一B案): terminate 之后的独立后置收尾步——Jw"G21经验进化闭环"
    行为4"新认知评估固化形式"的落库判定体。接收整条 trail+Jw支线复盘文本,
    按双层写入规约(0f2f4458)绿黄红分档, 产出 gate_list 分档立卡清单。
    terminate 契约零侵入(不重开其七段评审)。
  state_inputs(段二):  goal, goal_anchored, summary, trail(分歧①b: 每步
    step_summary 截60字+result_fields清单, 与 terminate 分歧⑧C案同口径;
    全 trail ≤ max_steps=24 兜底)
  state_outputs: []  链路收尾步无后继消费者
  实现形态(段三②b): 标准 LLM 管线(基类 run 五步); Jw#side 支线复盘文本
    (node_outputs["Jw"])作为 build_prompt 的额外素材注入——G21 四行为职责
    递进: 行为1-3复盘在 Jw#side, 行为4固化分档判定在本技能。
  菜单(段四a案): next_options=[] / terminate_suggest 恒真 / is_exit=true;
    run 时断言 next_options 非空即 raise(防邻接表过校验后被热改);
    降级不炸主链路——链路已在 terminate 正常结束, 降级只损失本次立卡清单,
    编排器收 _degraded 清单仅告警, 支持单独重跑本技能不复跑整链。
  降级矩阵(段五a案): trail空/无资产=合法非降级(applicable=false);
    applicable=true+清洗后空=降级(自相矛盾); LLM空返回/解析失败=降级;
    单条非法丢弃+note不降级; >6条顺序截断+过载note不降级(禁排序——
    排序即裁决倾向); 越档条目(tier与asset_type交叉非法,如green+key_pact)
    整条丢弃+note——零权限穿透, 不做自动提档改写模型判定。
  审计(段六b案): op=atomic_skill:skill_memory_gate, payload 含 gate_stats
    (green/yellow/red/dropped/overload 计数)供编排器写入侧对账;
    绿档落库动作归编排器 commit_green_cards(), 其审计行独立
    (op=memory_commit:green)——判定与执行两主体分开审计。
  边界红线(段七a案):
    - 零 DB 触碰: 不 import 写路径/不调 seed_topic/mount_topic, 写库只在编排器
    - 只判定不裁决: 绿档权限来自规约卡 0f2f4458 封闭枚举(观测/测试结果/踩坑),
      技能不擅自扩权; topic挂载强制归黄档(绿档永不挂锚)
    - 动词枚举封闭: 写卡|建议立卡|仅标记; 永不产出更新/删除指令(删除属红档人工)
    - 幂等: 同 trail 重跑产出一致, 编排器 full_hash 去重防重复建卡
    - 不发起新推理: 只消费已产出 trail/Jw复盘, 不读记忆库(dfcp_link=无),
      不感知链路外世界(全局冲突归 E7 体系)
    - 红档仅在 gate_list 内标记(编排器会话内提示, 不持久化 pending/watchlist;
      升级路径在编排层扩展, 不动本技能七段)
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, extract_json

MAX_GATE_ITEMS = 6            # gate_list 硬上限(段三③裁决: 宁精勿滥, 超限截断+过载note)
STEP_SUMMARY_MAX = 60         # trail step_summary 截断(对齐 terminate 分歧⑧家族口径)

# 绿档封闭枚举(段七红线2: 权限来自规约卡0f2f4458, 技能不扩权; topic挂载强制黄档)
GREEN_TYPES = {"观测", "测试结果", "踩坑"}
YELLOW_TYPES = {"能力卡", "topic挂载", "指针卡"}
RED_TYPES = {"key_pact", "范式变更", "删除"}
ALL_TYPES = GREEN_TYPES | YELLOW_TYPES | RED_TYPES
TIER_ASSET_LEGAL = {          # 越档交叉合法表(段五: 校验用, 不做提档改写)
    "green": GREEN_TYPES,
    "yellow": YELLOW_TYPES,
    "red": RED_TYPES,
}
VERBS = {"写卡", "建议立卡", "仅标记"}
TIER_VERB_LEGAL = {"green": {"写卡"}, "yellow": {"建议立卡"}, "red": {"仅标记"}}

SYSTEM_PROMPT = """你是记忆写入闸门模块(元核链路的收尾步, Jw经验进化闭环的固化判定)。
链路已经结束, 你的唯一任务: 盘点整条思考轨迹中产生的【可复用记忆资产】, 按
绿/黄/红三档分类, 产出立卡清单。你只判定, 不执行——落库由编排器完成。

分档规约(来自记忆写入双层规约, 封闭枚举):
- green(可直接写卡): 仅限【观测记录 | 测试结果 | 简单踩坑】三类;
  永远不包含 topic 挂载、能力卡、任何规约类内容。
- yellow(建议立卡, 等用户一句话确认): 【能力卡 | topic挂载 | 指针卡】。
- red(仅标记, 等完整人工终审): 【key_pact | 范式变更 | 删除记忆】。

触发信号(什么算记忆资产): 用户纠正了模型的错误做法 / 使用了外部工具或文档 /
发现新踩坑 / 范式级发现 / 某能力本次已是第2次及以上复用。
大多数纯执行类链路没有记忆资产——applicable=false 是正常且常见的路径,
宁缺勿滥, 不要为了产出而编造资产。

严格输出 JSON(不要输出任何 JSON 以外的文字):
{
  "applicable": true,
  "gate_list": [
    {"tier": "green|yellow|red",
     "asset_type": "观测|测试结果|踩坑|能力卡|topic挂载|指针卡|key_pact|范式变更|删除",
     "gist": "一句话资产摘要(30字内)",
     "evidence_step": "素材来源技能id",
     "proposed_action": "写卡|建议立卡|仅标记"}
  ],
  "coverage_note": "盘点覆盖说明: 识别了哪些方向, 明显遗漏哪些",
  "summary": "本技能摘要(30-60字)",
  "confidence": 0.0,
  "ambiguity_note": "歧义/丢弃/截断说明; 无则空字符串"
}

判定口径:
- applicable: 本链路确有值得沉淀的资产才 true; 纯执行/闲聊/无新知必须 false
  且 gate_list 为空数组(正常路径, 不是失败);
- 条目宁精勿滥: 通常 0-3 条, 上限 6;
- 每条 evidence_step 必须是 trail 中真实出现的技能id, 不许编;
- confidence: 盘点覆盖度自评 0~1(宁低勿高);
- 严禁混合档位语义: green 条目的 asset_type 只能是三类绿档类目。"""


def build_trail_digest(trail: list) -> str:
    """trail 素材摘要(段三①b: 60字截断+result_fields, 与 terminate 分歧⑧同口径)"""
    lines = []
    for t in trail:
        if not isinstance(t, dict):
            continue
        sid = str(t.get("skill_id", "?"))
        ssum = str(t.get("step_summary", "") or "").strip()[:STEP_SUMMARY_MAX]
        if not ssum:
            ssum = "(无自报摘要)"
        fields = t.get("result_fields") or []
        extra = f" 产出字段: {', '.join(fields)}" if fields else ""
        lines.append(f"[{sid}] {ssum}{extra}")
    return "\n".join(lines)


class SkillMemoryGate(BaseAtomicSkill):
    skill_id = "skill_memory_gate"

    def build_prompt(self, state: ThinkState) -> list:
        goal = self._in(state, "goal") or ""
        anchored = self._in(state, "goal_anchored") or ""
        summary = self._in(state, "summary") or ""
        trail = self._in(state, "trail") or []
        digest = build_trail_digest(trail) or "(空链路)"
        jw_review = str(getattr(self, "jw_review_text", "") or "").strip()
        jw_part = f"\n\n[Jw 支线复盘产出(资产识别线索)]\n{jw_review[:800]}" if jw_review else ""
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"链路原始目标: {goal[:500]}\n锚定目标: {anchored[:500]}\n"
                f"链路终态摘要: {summary[:400]}\n\n完整轨迹(每步60字自报摘要):\n{digest}{jw_part}\n\n"
                "请执行记忆资产盘点, 输出分档立卡清单 JSON。"},
        ]

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        data = extract_json(raw)
        if not data:
            return self._degraded(f"JSON解析失败(两级容错均失败): {str(raw)[:80]}")

        applicable = data.get("applicable")
        if not isinstance(applicable, bool):
            applicable = False                      # 非法布尔宽容降为false(宁缺勿滥)

        notes = []
        items = data.get("gate_list")
        if not isinstance(items, list):
            items = []
        if not applicable and items:
            items = []                              # applicable=false 携带条目=自相矛盾, 清空
            notes.append("applicable=false但仍带条目, 已清空")

        # 逐条清洗(段五矩阵: 单条非法丢弃+note; 越档整条丢弃; 动词与档位不符丢弃)
        cleaned, dropped = [], 0
        for it in items:
            if not isinstance(it, dict):
                dropped += 1
                continue
            tier = str(it.get("tier", "") or "")
            atype = str(it.get("asset_type", "") or "")
            gist = str(it.get("gist", "") or "").strip()
            step = str(it.get("evidence_step", "") or "").strip()
            verb = str(it.get("proposed_action", "") or "")
            if tier not in TIER_ASSET_LEGAL or atype not in ALL_TYPES:
                dropped += 1
                continue
            if atype not in TIER_ASSET_LEGAL[tier]:    # 越档(green+key_pact等)=权限穿透, 整条丢弃
                dropped += 1
                notes.append(f"越档丢弃: {tier}+{atype}")
                continue
            if not gist or verb not in VERBS or verb not in TIER_VERB_LEGAL[tier]:
                dropped += 1
                continue
            trail_ids = {str(t.get("skill_id")) for t in state.trail
                         if isinstance(t, dict)}
            if step and step not in trail_ids:          # 编造来源步=编事实, 丢弃
                dropped += 1
                notes.append(f"evidence_step非trail真实技能: {step}")
                continue
            cleaned.append({"tier": tier, "asset_type": atype, "gist": gist[:60],
                            "evidence_step": step, "proposed_action": verb})

        overload = False
        if len(cleaned) > MAX_GATE_ITEMS:
            overload = True
            notes.append(f"资产过载: 原始{len(cleaned)}条, 截断保留前{MAX_GATE_ITEMS}条, 建议人工复核")
            cleaned = cleaned[:MAX_GATE_ITEMS]          # 顺序截断禁排序(排序即裁决倾向)

        # 段五矩阵: applicable=true + 清洗后空 = 自相矛盾 → 降级
        if applicable and not cleaned:
            d = self._degraded("applicable=true但清洗后gate_list为空(全部非法或越档)")
            d["ambiguity_note"] = "; ".join(notes) or "条目全数被清洗"
            return d

        return {
            "applicable": applicable,
            "gate_list": cleaned,
            "coverage_note": str(data.get("coverage_note", "") or "")[:300],
            "summary": str(data.get("summary", "") or "")[:60] or "记忆闸门盘点完成",
            "confidence": data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else 0.0,
            "ambiguity_note": "; ".join(notes),
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return True                                  # 恒真: 收尾步即出口(段四)

    def run(self, state: ThinkState) -> "SkillResult":        # type: ignore[override]
        """收尾步出口断言(段四a案: next_options非空即raise, 防校验后热改)。"""
        if self.spec.get("next_options"):
            raise RuntimeError(
                f"skill_memory_gate next_options 必须为空(收尾出口无后继), "
                f"当前: {self.spec.get('next_options')}")
        return super().run(state)