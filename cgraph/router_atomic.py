# -*- coding: utf-8 -*-
"""skill_router 编排层路由器(2026-11-17 七段定稿, commit 4165c76 设计基线).

定位: 消费邻接表的刚性调度循环——LLM 千 token 瘦身选路 + 刚性校验,
管住模型不越界/不死循环/不乱跳。不是第 9 个原子技能(R1): 不继承
BaseAtomicSkill、不进邻接表 entries、不走 run 五步生命周期。

红线 R1-R12(docs/design_skill_router.md + P-C3 附录):
  R1 编排层组件, 非原子技能;      R2 只读邻接表绝不修改;
  R3 __planned__ 绝不进候选;       R4 冷启动 anchor_target 硬跑不走 LLM;
  R5 技能降级透传不干预;           R6 零 DB 写零记忆写入(gate 归 executor);
  R7 决策素材瘦身不喂 sub_questions; R8 无阈值告警只产信号;
  R9 审计只记异常(c3_preempt 为受控例外: 干预必须可追溯, 见 P-C3 豁免注记);
  R10 永远返回可用 state(兜底残 trail);
  R11 受控拦截先例封闭(仅 C3 场景; 扩展必走新七段, 禁止直接编码);
  R12 task_type 枚举/词表/首轮锁定封闭(增删必走七段)。

P-C3(2026-11-17 七段定稿): eval/root_cause 类任务在 weakest_link 后,
prompt 偏好提示优先 + 最多一次软否决拦截 terminate 转 f_reason。
六条件互锁: 类型∈{eval,root_cause} ∧ 上一步==weakest_link ∧ f_reason∈菜单
∧ 未拦过 ∧ f_reason 执行<2 ∧ 剩余预算≥2 步。
拦截付步数成本; kind=c3_preempt 留痕; 不计 fallback/collapse(策略域≠故障域);
C3 分支整体 fail-safe: 异常视同条件不满足, 退化为无偏好基线。
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
from cgraph.skill_dk_deduction import SkillDkDeduction    # P-skeleton(Dk)
from cgraph.skill_en_evidence_net import SkillEnEvidenceNet   # P-skeleton(En)
from cgraph.skill_terminate import SkillTerminate
from cgraph.skill_synthesize import SkillSynthesize

ENTRY_HINT = "skill_anchor_target"          # 冷启动硬编码入口(段二①a, R4)
TERMINATE = "skill_terminate"
MAX_VISITS = 2                               # 单技能最多执行 2 次(段四③b 软去重)
COLLAPSE_LIMIT = 2                           # 连续 2 次路由故障→信任崩塌(段二③)

EXIT_CHANNELS = ("terminate_suggest", "llm_terminate", "collapse", "max_steps")
# R9 受控例外(P-C3 豁免注记): c3_preempt 是唯一非故障审计 kind——router 掰弯了
# LLM 菜单内合法选择, 必须强制留痕; 是"干预必须可追溯"精神的延伸。
AUDIT_KINDS = ("route_fallback_parse", "route_fallback_illegal", "route_collapse",
               "menu_anomaly", "c3_preempt")
AUDIT_OP = "orchestrate:skill_router"        # 段六⑤: 调度异常归编排, 同表不同 op 前缀

# ---- P-C3 段三(b2+b1): task_type 五枚举 + 词表兜底(仅归 root_cause, R12 封闭) ----
TASK_TYPES = ("eval", "root_cause", "decision", "execution", "other")
C3_TYPES = ("eval", "root_cause")             # C3 触发类型集合(R11: 扩展必走新七段)
LEXICON_ROOT_CAUSE = ("根因", "分析", "审查", "复盘")   # 词表只归 root_cause, 不归 eval
C3_PREEMPT_REASON = "C3偏好拦截terminate→f_reason"

SKILL_REGISTRY = {                            # 与探针一致; router 只消费不注册自身(R1)
    "skill_anchor_target": SkillAnchorTarget,
    "skill_scope_scan": SkillScopeScan,
    "skill_source_intent": SkillSourceIntent,
    "skill_weakest_link": SkillWeakestLink,
    "skill_f_reason": SkillFReason,
    "skill_dk_deduction": SkillDkDeduction,     # P-skeleton(Dk): 演绎链
    "skill_en_evidence_net": SkillEnEvidenceNet,  # P-skeleton(En): 证据网
    "skill_synthesize": SkillSynthesize,
    "skill_terminate": SkillTerminate,
}

ROUTE_SYSTEM = (
    "你是思维链路由器。从候选菜单中选出下一个要执行的技能, 只输出 JSON: "
    '{"chosen": "skill_id", "reason": "一句话理由", "task_type": "枚举"}。 '
    "不展开分析、不执行任务本身, 只做选路判断。"
    "task_type 五枚举(每轮必输出, 描述本任务整体类型): "
    "eval=对方案/专利/系统做优劣或风险评估; root_cause=定位故障或问题产生根源; "
    "decision=决策研判(选择/建仓/取舍); execution=信息搜集/任务执行/工具调用; "
    "other=不属于以上。"
)


@dataclass
class RouteReport:
    """路由器对外唯一报告(段五③)。ok=False 也带残 trail, 保障 memory_gate 后置照走(R10)。
    P-C3 段五: 新增 task_type/task_type_source 观测字段(仅复测归因, 不参与业务逻辑)。"""
    ok: bool
    exit_channel: str                          # EXIT_CHANNELS 四枚举之一
    final_state: ThinkState
    steps_log: list = field(default_factory=list)
    fallback_count: int = 0
    task_type: str = "other"                   # 首轮锁定的任务类型
    task_type_source: str = "other"            # llm | lexicon | other


class RouterAtomic:
    """编排层调度循环(段五①b: 类封装跨轮可变状态, 便于单测注入 mock-llm)。"""

    def __init__(self, llm, adjacency: dict):
        self.llm = llm
        self.adjacency = adjacency
        self.max_steps = int(adjacency.get("global", {}).get("max_steps", 24))
        self.collapse_counter = 0              # 连续路由故障计数(解析失败/非法 chosen 可混搭)
        self.fallback_count = 0
        self.steps_log: list = []              # {step, skill_id, chosen, candidates, reason, fallback, preempt, degrade, secs, ts}
        # ---- P-C3 状态(段三/段四) ----
        self.task_type = None                  # 首次成功解析后锁定, 只驻留实例不进 ThinkState
        self.task_type_source = "other"        # llm | lexicon | other
        self.c3_preempt_done = False           # 一次任务最多一次策略拦截(布尔, 非计数器)

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
            menu = self._clean_menu(sr.next_options, state)
            if not menu:
                if exit_channel is None:
                    exit_channel = "terminate_suggest"   # 菜单耗尽=链路闭合(探针 C4/C5 先例)
                break

            # LLM 选路(上下文瘦身, 段三①a; P-C3 段三: 顺带输出 task_type)
            chosen, reason = self._route_llm(state, menu)

            # 解析失败 → 回退菜单首项 + 计入崩塌计数(段三②1)
            if chosen is None:
                self._audit("route_fallback_parse", str(menu[0]["skill"]), self._ids(menu), len(self.steps_log))
                chosen, reason, fallback = self._first_non_terminate(menu), "路由LLM降级回退", True
                preempt = False
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
                    preempt = False
                    self.fallback_count += 1
                    self.collapse_counter += 1
                    if self.collapse_counter >= COLLAPSE_LIMIT:
                        self._audit("route_collapse", chosen, self._ids(menu), len(self.steps_log))
                        exit_channel = "collapse"; break
                else:
                    fallback = False
                    preempt = False
                    self.collapse_counter = 0     # 合法决策清零连续计数
                    # 通道②: LLM 主动选 terminate —— P-C3 唯一可干预通道
                    if chosen == TERMINATE:
                        # 六条件互锁全满足 → 最多一次软否决拦截(R11: 仅此场景)
                        if self._c3_should_preempt(state, menu):
                            self._audit("c3_preempt", "skill_f_reason", self._ids(menu), len(self.steps_log))
                            chosen = "skill_f_reason"
                            reason = C3_PREEMPT_REASON
                            preempt = True
                            self.c3_preempt_done = True      # 窗口用完, 二次无条件放行(段四边界3)
                            state.steps -= 0                 # 拦截付步数成本: f_reason 执行正常 +1, 不豁免
                        else:
                            exit_channel = "llm_terminate"; break

            sr = self._exec_skill(chosen, state, chosen=chosen,
                                  candidates=self._ids(menu), reason=reason,
                                  fallback=fallback, preempt=preempt)

        return RouteReport(ok=(exit_channel != "collapse"), exit_channel=exit_channel,
                           final_state=state, steps_log=self.steps_log,
                           fallback_count=self.fallback_count,
                           task_type=self.task_type or "other",
                           task_type_source=self.task_type_source)

    # ---------------- 技能执行(透传降级, R5) ----------------
    def _exec_skill(self, skill_id: str, state: ThinkState, chosen: str,
                    candidates: list, reason: str, fallback: bool = False, preempt: bool = False):
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
                               "fallback": fallback, "preempt": preempt, "degrade": degrade,
                               # P-C4(段二2.5): token埋点纯观测透传, router不接管不计算;
                               # 取值来自技能层写入的result; 缺失/None照存, 不干预链路
                               "token_in": sr.result.get("token_in"),
                               "token_out": sr.result.get("token_out"),
                               "secs": round(sr.secs, 3), "ts": ts})
        return sr

    # ---------------- 菜单清洗(段五②a) ----------------
    def _anchor_goal_type(self, state: ThinkState) -> str:
        """P-skill-synthesize(段二裁决2): goal_type 不落 ThinkState, 从 trail 内
        anchor 记录读; 读不到(降级/异常)返回 "" → 门控按非 scheme_design 处理
        (fail-safe: 宁可不进 synthesize, 不引入误触发)。"""
        try:
            for e in state.trail:
                if isinstance(e, dict) and e.get("skill_id") == "skill_anchor_target":
                    rf = e.get("result_fields") or {}
                    gt = rf.get("goal_type")
                    if isinstance(gt, str) and gt:
                        return gt
        except Exception:
            pass
        return ""

    def _clean_menu(self, next_options: list, state: ThinkState = None) -> list:
        menu, illegal = [], []
        # P-skill-synthesize 路由门(段二裁决2, 零侵入 f_reason):
        # 菜单含 skill_synthesize 且 goal_type != scheme_design → 剔除。
        # analysis/review/simple_query/unknown_ambiguous/读取失败 全部不进
        # synthesize, 链路字节级回归。R9 例外注记: 剔除留审计行可追溯。
        if state is not None and any(o.get("skill") == "skill_synthesize"
                                     for o in next_options or []):
            if self._anchor_goal_type(state) != "scheme_design":
                next_options = [o for o in (next_options or [])
                                if o.get("skill") != "skill_synthesize"]
                self._audit("synthesize_gate", "goal_type!=scheme_design",
                            self._ids(next_options or []) or [], len(self.steps_log))
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
        # P-C3 段四边界5(a案): 提示与拦截资格共用同一套六条件
        if self._c3_should_preempt(state, menu):
            user += "\n【任务标记】评估/根因分析类: 已定位薄弱环节, 建议优先推演竞争假设谱(选 skill_f_reason); 若判断证据已充分可直接收口。"
        try:
            raw = self.llm.chat([{"role": "system", "content": ROUTE_SYSTEM},
                                 {"role": "user", "content": user}])
        except Exception:
            return None, "路由LLM调用异常"
        parsed = extract_json(raw or "")
        if not parsed or not parsed.get("chosen"):
            # P-C3 段三: 解析失败路径同样走降级链锁定 task_type(词表兜底在此触发)
            self._lock_task_type(parsed if isinstance(parsed, dict) else {}, state)
            return None, "路由LLM降级回退"
        # P-C3 段三: task_type 顺带解析 + 首轮锁定 + 降级链(LLM>词表>other)
        self._lock_task_type(parsed, state)
        return str(parsed.get("chosen")), str(parsed.get("reason") or "")[:120]

    def _lock_task_type(self, parsed: dict, state: ThinkState):
        """task_type 首轮锁定(段三): LLM 合法枚举 > 词表兜底(仅归 root_cause) > other。"""
        if self.task_type is not None:
            return                                # 已锁定, 后续轮次不再更新(防漂移)
        tt = parsed.get("task_type")
        if isinstance(tt, str) and tt in TASK_TYPES:
            self.task_type = tt
            self.task_type_source = "llm"
            return
        # 降级1: LLM 解析失败/字段缺失 → 词表兜底(b1, 只归 root_cause, 不归 eval)
        goal_text = (state.goal or "") + (state.goal_anchored or "")
        if any(w in goal_text for w in LEXICON_ROOT_CAUSE):
            self.task_type = "root_cause"
            self.task_type_source = "lexicon"
            return
        # 降级2: 安全默认 other —— C3 整套关闭(fail-safe: 宁可不深挖, 不乱拦截)
        self.task_type = "other"
        self.task_type_source = "other"

    # ---------------- P-C3 六条件互锁(段五完整清单) ----------------
    def _c3_should_preempt(self, state: ThinkState, menu: list) -> bool:
        """C3 开关: 六条件全部满足才开启(提示注入与拦截资格共用, 段四边界5)。
        整体 fail-safe(段六五): 任何内部异常视同条件不满足, 退化为无偏好基线。"""
        try:
            if self.c3_preempt_done:                       # ④ 一次上限(窗口用完)
                return False
            if self.task_type not in C3_TYPES:             # ① 类型∈{eval,root_cause}
                return False
            if not state.trail or state.trail[-1].get("skill_id") != "skill_weakest_link":
                return False                              # ② 上一步==weakest_link
            if "skill_f_reason" not in self._ids(menu):    # ③ f_reason 在清洗后菜单
                return False
            ids = [e.get("skill_id") for e in state.trail]
            if ids.count("skill_f_reason") >= MAX_VISITS:  # ⑤ f_reason 执行<2
                return False
            if state.steps > self.max_steps - 2:           # ⑥ 剩余预算≥2(拦截1步+收口1步)
                return False
            return True
        except Exception:
            # 段六五: C3 判定异常→menu_anomaly 留痕, 退化为无偏好
            self._audit("menu_anomaly", "C3判定异常", [], len(self.steps_log))
            return False

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