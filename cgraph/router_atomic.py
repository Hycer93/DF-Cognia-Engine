# -*- coding: utf-8 -*-
"""skill_router 编排层路由器(2026-11-17 七段定稿, commit 4165c76 设计基线).

定位: 消费邻接表的刚性调度循环——LLM 千 token 瘦身选路 + 刚性校验,
管住模型不越界/不死循环/不乱跳。不是第 9 个原子技能(R1): 不继承
BaseAtomicSkill、不进邻接表 entries、不走 run 五步生命周期。

红线 R1-R10(docs/design_skill_router.md):
  R1 编排层组件, 非原子技能;      R2 只读邻接表绝不修改;
  R3 __planned__ 绝不进候选;       R4 冷启动 anchor_target 硬跑不走 LLM;
  R5 技能降级透传不干预;           R6 零 DB 写零记忆写入(gate 归 executor);
  R7 决策素材瘦身不喂 sub_questions; R8 无阈值告警只产信号;
  R9 审计只记异常;                 R10 永远返回可用 state(兜底残 trail)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.atomic_skill import ThinkState, SkillResult, extract_json, _audit_log
from cgraph.skill_anchor_target import SkillAnchorTarget
from cgraph.skill_scope_scan import SkillScopeScan
from cgraph.skill_source_intent import SkillSourceIntent
from cgraph.skill_weakest_link import SkillWeakestLink
from cgraph.skill_f_reason import SkillFReason
from cgraph.skill_terminate import SkillTerminate

ENTRY_HINT = "skill_anchor_target"          # 冷启动硬编码入口(段二①a, R4)
TERMINATE = "skill_terminate"
MAX_VISITS = 2                               # 单技能最多执行 2 次(段四③b 软去重)
COLLAPSE_LIMIT = 2                           # 连续 2 次路由故障→信任崩塌(段二③)

EXIT_CHANNELS = ("terminate_suggest", "llm_terminate", "collapse", "max_steps")
AUDIT_KINDS = ("route_fallback_parse", "route_fallback_illegal", "route_collapse", "menu_anomaly")
AUDIT_OP = "orchestrate:skill_router"        # 段六⑤: 调度异常归编排, 同表不同 op 前缀

SKILL_REGISTRY = {                            # 与探针一致; router 只消费不注册自身(R1)
    "skill_anchor_target": SkillAnchorTarget,
    "skill_scope_scan": SkillScopeScan,
    "skill_source_intent": SkillSourceIntent,
    "skill_weakest_link": SkillWeakestLink,
    "skill_f_reason": SkillFReason,
    "skill_terminate": SkillTerminate,
}

ROUTE_SYSTEM = (
    "你是思维链路由器。从候选菜单中选出下一个要执行的技能, 只输出 JSON: "
    '{"chosen": "skill_id", "reason": "一句话理由"}。 '
    "不展开分析、不执行任务本身, 只做选路判断。"
)


@dataclass
class RouteReport:
    """路由器对外唯一报告(段五③)。ok=False 也带残 trail, 保障 memory_gate 后置照走(R10)。"""
    ok: bool
    exit_channel: str                          # EXIT_CHANNELS 四枚举之一
    final_state: ThinkState
    steps_log: list = field(default_factory=list)
    fallback_count: int = 0


class RouterAtomic:
    """编排层调度循环(段五①b: 类封装跨轮可变状态, 便于单测注入 mock-llm)。"""

    def __init__(self, llm, adjacency: dict):
        self.llm = llm
        self.adjacency = adjacency
        self.max_steps = int(adjacency.get("global", {}).get("max_steps", 24))
        self.collapse_counter = 0              # 连续路由故障计数(解析失败/非法 chosen 可混搭)
        self.fallback_count = 0
        self.steps_log: list = []              # {step, skill_id, chosen, candidates, reason, fallback, degrade, secs, ts}

    # ---------------- 主入口(四通道并联退出, 段四①c) ----------------
    def run(self, state: ThinkState) -> RouteReport:
        try:
            return self._run_inner(state)
        except Exception as e:                 # R10 兜底: 任何未捕获异常→残 trail 报告
            return RouteReport(ok=False, exit_channel="collapse", final_state=state,
                               steps_log=self.steps_log, fallback_count=self.fallback_count)

    def _run_inner(self, state: ThinkState) -> RouteReport:
        exit_channel = None

        # 冷启动: anchor_target 硬跑, 不调 LLM(段二①a/R4)
        sr = self._exec_skill(ENTRY_HINT, state, chosen=ENTRY_HINT,
                              candidates=[ENTRY_HINT], reason="冷启动硬编码入口")

        while True:
            # 通道①: 技能侧宣告收敛
            if sr.terminate_suggest:
                exit_channel = "terminate_suggest"; break

            # 通道④: 全局步数硬兜底
            if state.steps >= self.max_steps:
                exit_channel = "max_steps"; break

            # 菜单清洗(R3: __planned__/表外项在 prompt 组装前剔除, LLM 不可见)
            menu = self._clean_menu(sr.next_options)
            if not menu:
                if exit_channel is None:
                    exit_channel = "terminate_suggest"   # 菜单耗尽=链路闭合(探针 C4/C5 先例)
                break

            # LLM 选路(上下文瘦身, 段三①a)
            chosen, reason = self._route_llm(state, menu)

            # 解析失败 → 回退菜单首项 + 计入崩塌计数(段三②1)
            if chosen is None:
                self._audit("route_fallback_parse", str(menu[0]["skill"]), self._ids(menu), len(self.steps_log))
                chosen, reason, fallback = self._first_non_terminate(menu), "路由LLM降级回退", True
                self.fallback_count += 1
                self.collapse_counter += 1
                if self.collapse_counter >= COLLAPSE_LIMIT:
                    self._audit("route_collapse", chosen, self._ids(menu), len(self.steps_log))
                    exit_channel = "collapse"; break
            else:
                # chosen 非法(菜单外) → 回退 + 计数(段二③)
                ok, why = self._validate(chosen, menu, state)
                if not ok:
                    self._audit("route_fallback_illegal", chosen, self._ids(menu), len(self.steps_log))
                    chosen, reason, fallback = self._first_non_terminate(menu), f"chosen非法已回退({why})", True
                    self.fallback_count += 1
                    self.collapse_counter += 1
                    if self.collapse_counter >= COLLAPSE_LIMIT:
                        self._audit("route_collapse", chosen, self._ids(menu), len(self.steps_log))
                        exit_channel = "collapse"; break
                else:
                    fallback = False
                    self.collapse_counter = 0     # 合法决策清零连续计数
                    # 通道②: LLM 主动选 terminate
                    if chosen == TERMINATE:
                        exit_channel = "llm_terminate"; break

            sr = self._exec_skill(chosen, state, chosen=chosen,
                                  candidates=self._ids(menu), reason=reason, fallback=fallback)

        return RouteReport(ok=(exit_channel != "collapse"), exit_channel=exit_channel,
                           final_state=state, steps_log=self.steps_log,
                           fallback_count=self.fallback_count)

    # ---------------- 技能执行(透传降级, R5) ----------------
    def _exec_skill(self, skill_id: str, state: ThinkState, chosen: str,
                    candidates: list, reason: str, fallback: bool = False):
        skill = SKILL_REGISTRY[skill_id](llm=self.llm, adjacency=self.adjacency)
        t0 = time.time()
        try:
            sr = skill.run(state)
        except Exception as e:
            # 技能异常不炸链路: 构造降级 SkillResult 继续(基类同哲学; router 不补救不绕开)
            sr = SkillResult(skill_id=skill_id, result={"degraded": True, "summary": f"技能异常: {e}"},
                             next_options=self.adjacency["entries"].get(skill_id, {}).get("next_options", []),
                             terminate_suggest=False, secs=time.time() - t0)
        degrade = bool(sr.result.get("degraded"))
        step_summary = str(sr.result.get("summary") or sr.result.get("degraded") or "")[:120]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        state.steps += 1
        # trail 单条目原地扩展(段二②b): 与探针字段对齐, 无独立 router_decision 条目
        state.trail.append({"skill_id": skill_id, "chosen": chosen, "candidates": list(candidates),
                            "reason": reason, "step_summary": step_summary,
                            "result_fields": sr.result, "ts": ts})
        self.steps_log.append({"step": len(self.steps_log) + 1, "skill_id": skill_id,
                               "chosen": chosen, "candidates": list(candidates), "reason": reason,
                               "fallback": fallback, "degrade": degrade,
                               "secs": round(sr.secs, 3), "ts": ts})
        return sr

    # ---------------- 菜单清洗(段五②a) ----------------
    def _clean_menu(self, next_options: list) -> list:
        menu, illegal = [], []
        for opt in next_options or []:
            sid = opt.get("skill")
            if sid == "__planned__":
                continue                        # R3: 未实现占位, LLM 不可见
            if sid not in self.adjacency.get("entries", {}):
                illegal.append(sid)
                continue
            menu.append(opt)
        if illegal:
            # 规约与代码脱节: 必须留痕(R2: 只审计不修表)
            self._audit("menu_anomaly", ",".join(illegal), self._ids(next_options or []), len(self.steps_log))
        return menu

    # ---------------- LLM 选路(自建调用, 段三①a) ----------------
    def _route_llm(self, state: ThinkState, menu: list):
        # R7 上下文瘦身: goal_anchored+summary+skill_id 序列+steps 计数; 不喂 sub_questions
        trail_ids = [e.get("skill_id") for e in state.trail]
        user = (
            f"目标: {state.goal_anchored or state.goal}\n"
            f"当前结论: {state.summary or '(暂无)'}\n"
            f"已走步骤: {'→'.join(trail_ids) or '(仅冷启动)'} ({state.steps}/{self.max_steps})\n"
            "候选菜单:\n" + "\n".join(
                f"- {o['skill']}: 收益 {o.get('gain', '')} / 成本 {o.get('cost', '')}" for o in menu)
        )
        try:
            raw = self.llm.chat([{"role": "system", "content": ROUTE_SYSTEM},
                                 {"role": "user", "content": user}])
        except Exception:
            return None, "路由LLM调用异常"
        parsed = extract_json(raw or "")
        if not parsed or not parsed.get("chosen"):
            return None, "路由LLM降级回退"
        return str(parsed.get("chosen")), str(parsed.get("reason") or "")[:120]

    # ---------------- 刚性校验(段四③b 软去重) ----------------
    def _validate(self, chosen: str, menu: list, state: ThinkState):
        if chosen not in self._ids(menu):
            return False, "菜单外"
        if state.trail and chosen == state.trail[-1].get("skill_id"):
            return False, "与上一步相同(乒乓)"
        ids = [e.get("skill_id") for e in state.trail]
        if ids.count(chosen) >= MAX_VISITS:
            return False, f"已执行{MAX_VISITS}次上限"
        return True, ""

    @staticmethod
    def _ids(menu: list) -> list:
        return [o.get("skill") for o in menu or []]

    @staticmethod
    def _first_non_terminate(menu: list):
        for o in menu:
            if o.get("skill") != TERMINATE:
                return o["skill"]
        return TERMINATE                         # 菜单只剩 terminate 时回退到它即收口

    # ---------------- 仅异常审计(R9, 段六①a: kind 封闭四枚举, 轻量) ----------------
    def _audit(self, kind: str, chosen: str, candidates: list, step: int):
        if kind not in AUDIT_KINDS:
            return
        _audit_log(AUDIT_OP, {"kind": kind, "chosen": chosen, "candidates": candidates,
                              "step": step, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})