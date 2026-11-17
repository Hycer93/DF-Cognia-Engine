# -*- coding: utf-8 -*-
"""
skill_terminate.py — 原子技能·链路收敛与整体汇编(2026-11-16 七段定稿, 分歧⑧-⑪b裁决)

七段定稿(用户裁决后逐条代码化):
  原节点定位: 链路收敛与整体汇编节点, 不是简单"停止执行"。三职责递进:
    终止(消费终态产出final_output) / 汇编(依据trail内每步技能自报摘要拼接
    全局图景——修复单步summary覆盖state.summary造成的信息丢失, 不禁止各
    技能写局部summary, 不改链路惯例) / 质量自检(termination_reason+steps_used)。
    边界铁律: 只汇编已产出内容, 不发起任何新推理/评估/假设; 发现信息缺口
    只在coverage_check如实陈述, 补跑决策交人闸或上层编排器。
  state_inputs:  goal, goal_anchored, summary, trail(分歧⑧C案: 不全量展开
    业务键——terminate对新增技能封闭, 汇编素材来自trail携带的每步自报摘要,
    新技能只要输出summary契约即自动纳入; 耦合方向若反了会产生O2类静默漏编)
  state_outputs: []  链路出口不回写任何state字段(T9契约)
  result_fields: final_output / termination_reason / steps_used / coverage_check
  next_options: []  空菜单(is_exit双通道识别: is_exit=true + sid==skill_terminate,
    校验器规则C不误判死端); run时断言非空即raise(防邻接表过校验后被热改)
  terminate_suggest: 恒真(本技能即终止; 链路出口)
  实现形态(分歧⑩B案): 纯Python拼装, 无build_prompt, 不调用LLM——出口产物
    直接交付人闸, 能代码断言的环节全部代码化; 零幻觉源/零JSON降级面/耗时
    ≈0。演进备注: 可读性不足才迭代C案(Python骨架+≤100字LLM总括), 禁止
    直接跳A案完整LLM自由汇编(放宽容易收紧难)。
  风险&规约:
    - 风险1 编造归因(分歧⑪b a案): termination_reason只输出可观测两态
      normal_stop|max_steps_hit; "谁决定停"不在state/trail感知域, 强写
      枚举=编造事实; 停机发起方归编排器外部日志
    - 风险2 coverage_check认知诱导(分歧⑨): 只陈列客观事实(哪步触及哪个
      方向/哪些完全未触及), 禁止"覆盖充分/不足"类定性评价
    - 风险3 降级语义特殊(分歧⑪): terminate降级=终局汇编崩溃(全链路最高
      级失败), 强制保留final_output=""/termination_reason="assembler_crash:
      <原因>"/steps_used三字段, 人闸至少知道链路结束+汇编炸了+走了几步
    - dfcp_link=无: 只汇编思考链路内部state/trail; 结论转策略卡提案属
      独立人闸流程不在本技能内
    - 审计: op=atomic_skill:skill_terminate, 独立 skill 审计不进 E7 晨报
"""
from __future__ import annotations

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, _ref_material_block

STEP_SUMMARY_MAX = 60          # trail 条目 step_summary 截断上限(分歧⑧配套规约)


class SkillTerminate(BaseAtomicSkill):
    skill_id = "skill_terminate"

    def run(self, state: ThinkState) -> "SkillResult":        # type: ignore[override]
        """纯Python拼装出口(B案铁证: 全程不触碰 self.llm / build_prompt)。
        不走基类run(LLM管线), 但保留基类的降级兜底/审计/schema补齐语义。"""
        t0 = __import__("time").time()
        # 段四: next_options 非空防篡改断言(邻接表过校验后被热改的窗口防御)
        if self.spec.get("next_options"):
            raise RuntimeError(
                f"skill_terminate next_options 必须为空(链路出口无后继), "
                f"当前: {self.spec.get('next_options')}")

        # 降级保护壳: assembler_crash 时仍强制保留三核心字段(分歧⑪)
        try:
            result = self.parse_result("", state)
        except Exception as e:
            result = {"final_output": "",
                      "termination_reason": f"assembler_crash:{type(e).__name__}: {e}",
                      "steps_used": state.steps, "coverage_check": ""}

        # 基类② schema 补齐(降级态字段已前置保住, 此处只对缺字段兜底)
        for f in self.spec.get("result_fields", []):
            if f not in result:
                result[f] = ""

        secs = round(__import__("time").time() - t0, 2)
        __import__("cgraph.atomic_skill", fromlist=["_audit_log"])._audit_log(
            self.skill_id, {"result_keys": list(result.keys()),
                            "terminate": True, "secs": secs,
                            "degraded": bool(result.get("_degraded"))})
        from cgraph.atomic_skill import SkillResult
        return SkillResult(skill_id=self.skill_id, result=result,
                           next_options=[], terminate_suggest=True, secs=secs)

    def build_prompt(self, state: ThinkState) -> list:        # pragma: no cover
        raise NotImplementedError("B案纯Python拼装, terminate不调用LLM(分歧⑩)")

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        """汇编逻辑本体。签名保留基类契约, raw 恒空不使用。"""
        goal = self._in(state, "goal") or ""
        anchored = self._in(state, "goal_anchored") or ""
        last_summary = self._in(state, "summary") or ""
        trail = self._in(state, "trail")
        steps = getattr(state, "steps", 0)

        if not isinstance(trail, list):
            # 底层结构异常 → 真降级(assembler_crash, 分歧⑪降级表末行)
            raise TypeError(f"trail 非列表: {type(trail).__name__}")

        # termination_reason: 可观测两态(分歧⑪b a案) + 降级步数计数
        max_steps = (self.adjacency.get("global", {}) or {}).get("max_steps", 24)
        verdict = "max_steps_hit" if steps >= max_steps else "normal_stop"
        deg_steps = sum(1 for t in trail
                        if isinstance(t, dict) and t.get("degraded"))

        # 拼装 final_output: 锚定→路线→各步视角→关键产出引用
        route_ids, step_views, field_refs, missing_summary = [], [], [], 0
        for t in trail:
            if not isinstance(t, dict):
                continue
            sid = str(t.get("skill_id", "?"))
            route_ids.append(sid)
            ssum = str(t.get("step_summary", "") or "").strip()
            if not ssum:
                missing_summary += 1
                ssum = "(无自报摘要)"
            fields = t.get("result_fields") or []
            step_views.append(f"[{sid}] {ssum}")
            if fields:
                field_refs.append(f"第{len(route_ids)}步({sid}) 产出字段: {', '.join(fields)}")

        if not trail:
            # 合法: 一步未走直达terminate(simple_query场景)
            return {
                "final_output": f"(空链路直达终止) 原始目标: {goal}",
                "termination_reason": {"verdict": verdict,
                                       "detail": f"steps={steps}(上限{max_steps}), trail为空一步未执行"},
                "steps_used": steps,
                "coverage_check": "trail为空, 无可汇编过程记录",
            }

        head = anchored or goal or "(无目标)"
        if not anchored:
            head = f"(锚定未完成, 回退原始目标) {goal}"
        parts = [f"锚定目标: {head}",
                 "执行路线: " + " → ".join(route_ids),
                 "各步视角:"] + ["  " + v for v in step_views]
        if field_refs:
            parts.append("关键产出引用(完整内容见外部trail日志):")
            parts += ["  " + r for r in field_refs]
        parts.append(f"终局摘要(最后一步局部视角): {last_summary or '(无)'}")
        # P-C4(段二2.5): terminate 汇编可访问素材——缓解长输入终态单薄; 纯Python
        # 复用分流块(短直塞/长检索同口径); terminate不调LLM, 素材作为汇编附件陈述
        mat_block = _ref_material_block(state, self)
        if mat_block:
            parts.append("参考素材附件(用户原始输入材料, 供人闸复核):" + mat_block)

        # coverage_check: 只陈列触及/未触及事实, 禁定性评价(分歧⑨)
        covered = [t.get("skill_id", "?") for t in trail if isinstance(t, dict)]
        coverage = (f"方向触及事实: 链路执行了{', '.join(str(c) for c in covered)}; "
                    f"未经任何步骤执行的方向: 深挖分支未选中项见各步菜单记录")
        if missing_summary:
            coverage += f"; {missing_summary}步缺失自报摘要(旧格式或编排器漏写)"
        if not anchored:
            coverage += "; 锚定未完成(anchor降级或被跳过)"

        return {
            "final_output": "\n".join(parts),
            "termination_reason": {"verdict": verdict,
                                   "detail": f"steps={steps}(上限{max_steps}), 降级步数{deg_steps}"
                                             + (f", 缺摘要步数{missing_summary}" if missing_summary else "")},
            "steps_used": steps,
            "coverage_check": coverage,
        }

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        return True