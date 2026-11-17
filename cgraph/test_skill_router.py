# -*- coding: utf-8 -*-
"""skill_router 单测(七段段五④冻结 12 项清单, 编码前已冻结不再删减).

mock LLM + mock 原子技能; 禁止真实 LLM; 单测阶段不访问 DB。
运行: python -X utf8 cgraph/test_skill_router.py  (12P/0F 为准入门槛)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cgraph.router_atomic as R
from cgraph.router_atomic import RouterAtomic, RouteReport, EXIT_CHANNELS
from cgraph.atomic_skill import ThinkState, SkillResult


# ---------------- mock 基建 ----------------

class MockLLM:
    """可编程回复; 记录每次调用供断言(⑧冷启动零调用/⑨__planned__不进prompt)。"""
    def __init__(self, replies):
        self.replies = list(replies) if isinstance(replies, list) else [replies]
        self.calls = []

    def chat(self, msgs):
        self.calls.append(msgs)
        r = self.replies.pop(0) if len(self.replies) > 1 else (self.replies[0] if self.replies else "")
        return r


def _mk_skill(sid, adjacency, term=False, nxt=None):
    if nxt is None:
        nxt = adjacency["entries"].get(sid, {}).get("next_options", [])
    def _run(self, st):
        return SkillResult(skill_id=sid, result={"summary": f"{sid} done"},
                           next_options=list(nxt), terminate_suggest=term)
    return type(sid, (object,), {"__init__": lambda s, llm, adjacency: None, "run": _run})


BASE_ADJ = {
    "global": {"max_steps": 24},
    "entries": {
        "skill_anchor_target": {"next_options": [
            {"skill": "skill_scope_scan", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]},
        "skill_scope_scan": {"next_options": [
            {"skill": "skill_weakest_link", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]},
        "skill_weakest_link": {"next_options": [{"skill": "skill_terminate", "gain": "g", "cost": "c"}]},
        "skill_f_reason": {"next_options": [{"skill": "skill_terminate", "gain": "g", "cost": "c"}]},  # P-C3
        "skill_terminate": {"next_options": []},
    },
}


def _patch_registry(adjacency, overrides=None):
    """把注册表换成 mock 技能类; overrides: {sid: {"term": bool, "nxt": [...]}}"""
    saved = dict(R.SKILL_REGISTRY)
    for sid in adjacency["entries"]:
        term = (overrides or {}).get(sid, {}).get("term", sid == "skill_terminate")
        nxt = (overrides or {}).get(sid, {}).get("nxt")
        R.SKILL_REGISTRY[sid] = _mk_skill(sid, adjacency, term=term, nxt=nxt)
    return saved


class RouterTestBase(unittest.TestCase):
    def setUp(self):
        self.adj = {  # 深拷贝防用例间污染
            "global": {"max_steps": 24},
            "entries": {k: {"next_options": [dict(o) for o in v["next_options"]]}
                        for k, v in BASE_ADJ["entries"].items()},
        }
        self._saved = _patch_registry(self.adj)

    def tearDown(self):
        R.SKILL_REGISTRY.clear(); R.SKILL_REGISTRY.update(self._saved)


# ---------------- 12 项冻结用例 ----------------

class T01_NormalFlow(RouterTestBase):
    """① 正常流转: goal→anchor→scan→weakest_link→terminate_suggest, 断言 trail 序列"""
    def test(self):
        # anchor→scan→wl→terminate: wl 返回 terminate_suggest=True
        llm = MockLLM([
            '{"chosen": "skill_scope_scan", "reason": "需扫描"}',
            '{"chosen": "skill_weakest_link", "reason": "找弱点"}',
        ])
        self._saved_wl = R.SKILL_REGISTRY["skill_weakest_link"]
        R.SKILL_REGISTRY["skill_weakest_link"] = _mk_skill("skill_weakest_link", self.adj, term=True)
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="正常目标"))
        ids = [e["skill_id"] for e in rep.final_state.trail]
        self.assertEqual(ids, ["skill_anchor_target", "skill_scope_scan", "skill_weakest_link"])
        self.assertEqual(rep.exit_channel, "terminate_suggest")
        self.assertTrue(rep.ok)
        self.assertEqual(rep.fallback_count, 0)


class T02_LLMEmptyFallback(RouterTestBase):
    """② LLM 空返回: 回退菜单首项+计数+reason标记"""
    def test(self):
        llm = MockLLM(["", '{"chosen": "skill_terminate", "reason": "收"}'])
        # anchor 后菜单首项=scope_scan → 回退执行 scan; scan 菜单首项=wl; 第二次空返回又回退wl(已2次崩塌→collapse)
        llm2 = MockLLM(["", ""])
        rep = RouterAtomic(llm2, self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "collapse")
        self.assertEqual(rep.fallback_count, 2)
        fb = [s for s in rep.steps_log if s["fallback"]]
        self.assertTrue(all("降级回退" in s["reason"] for s in fb))
        # 单次空返回后 LLM 恢复合法 → 继续走(计数未满2)
        rep2 = RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep2.steps_log[1]["fallback"], True)
        self.assertIn("降级回退", rep2.steps_log[1]["reason"])


class T03_IllegalChosenFallback(RouterTestBase):
    """③ chosen 菜单外非法值: 回退并计数"""
    def test(self):
        # 恒回 ghost: 第一轮回退 scan+计数; 第二轮(scan后菜单=wl/terminate)仍 ghost→再计数→崩塌
        llm = MockLLM('{"chosen": "skill_ghost", "reason": "幻觉"}')
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertGreaterEqual(rep.fallback_count, 1)
        self.assertEqual(rep.steps_log[1]["skill_id"], "skill_scope_scan")  # 回退到菜单首项
        self.assertIn("chosen非法已回退", rep.steps_log[1]["reason"])


class T04_CollapseExit(RouterTestBase):
    """④ 连续2次故障(混搭: 解析失败+非法chosen)→exit_channel=collapse"""
    def test(self):
        llm = MockLLM(["不是json", '{"chosen": "skill_ghost", "reason": "x"}'])
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "collapse")
        self.assertFalse(rep.ok)


class T05_SelfLoopPingpong(RouterTestBase):
    """⑤ 自环拦截①: chosen 等于上一步技能→回退"""
    def test(self):
        # 乒乓前提: chosen∈菜单 且 ==上一步。构造 scan 菜单含自身 [scan, terminate]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [
            {"skill": "skill_scope_scan", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "扫描"}',
                       '{"chosen": "skill_scope_scan", "reason": "重复扫"}',
                       '{"chosen": "skill_terminate", "reason": "收"}'])
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertGreaterEqual(rep.fallback_count, 1)
        fb_steps = [s for s in rep.steps_log if s["fallback"]]
        self.assertTrue(any("乒乓" in s["reason"] for s in fb_steps))
        self.assertEqual(rep.exit_channel, "llm_terminate")


class T06_SelfLoopThirdVisit(RouterTestBase):
    """⑥ 自环拦截②: 同一技能第3次执行→回退"""
    def test(self):
        # 单元级验证 _validate: scan 已在 trail 出现 2 次(非上一步) → 第三次拦截
        st = ThinkState(goal="g")
        st.trail = [{"skill_id": "skill_weakest_link"}, {"skill_id": "skill_scope_scan"},
                    {"skill_id": "skill_weakest_link"}, {"skill_id": "skill_scope_scan"}]  # scan×2, 上一步=scan? 否: 末位是scan→先乒乓; 用非scan末位
        st.trail = [{"skill_id": "skill_scope_scan"}, {"skill_id": "skill_weakest_link"},
                    {"skill_id": "skill_scope_scan"}, {"skill_id": "skill_terminate"}]   # scan×2, 上一步=terminate(在菜单中不冲突)
        st.steps = 4
        r = RouterAtomic(MockLLM("{}"), self.adj)
        ok, why = r._validate("skill_scope_scan", [{"skill": "skill_scope_scan"}], st)
        self.assertFalse(ok)
        self.assertIn("上限", why)


class T07_FourExitChannels(RouterTestBase):
    """⑦ 四通道逐一 mock 触发"""
    def test_terminate_suggest(self):
        R.SKILL_REGISTRY["skill_scope_scan"] = _mk_skill("skill_scope_scan", self.adj, term=True)
        rep = RouterAtomic(MockLLM('{"chosen": "skill_scope_scan", "reason": "r"}'), self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "terminate_suggest")

    def test_llm_terminate(self):
        rep = RouterAtomic(MockLLM('{"chosen": "skill_terminate", "reason": "r"}'), self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "llm_terminate")

    def test_collapse(self):
        rep = RouterAtomic(MockLLM("垃圾"), self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "collapse")

    def test_max_steps(self):
        self.adj["global"]["max_steps"] = 1
        rep = RouterAtomic(MockLLM('{"chosen": "skill_terminate", "reason": "r"}'), self.adj).run(ThinkState(goal="g"))
        self.assertEqual(rep.exit_channel, "max_steps")


class T08_ColdStartNoLLM(RouterTestBase):
    """⑧ 冷启动: 断言 LLM 实例没有被调用(anchor_target 硬跑)"""
    def test(self):
        # anchor 后菜单空 → 全程零 LLM
        self.adj["entries"]["skill_anchor_target"]["next_options"] = []
        _patch_registry(self.adj)
        llm = MockLLM("{}")
        RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertEqual(len(llm.calls), 0)


class T09_PlannedFiltered(RouterTestBase):
    """⑨ __planned__ 节点: 断言不出现在送入 LLM 的 prompt 字符串"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "__planned__", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM('{"chosen": "skill_terminate", "reason": "r"}')
        RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertTrue(len(llm.calls) >= 1)
        for msgs in llm.calls:
            for m in msgs:
                self.assertNotIn("__planned__", m["content"])


class T10_EmptyMenuTerminate(RouterTestBase):
    """⑩ 清洗后菜单为空: 直接 terminate, 零 LLM 调用"""
    def test(self):
        # anchor 后继全是 __planned__ → 清洗后空 → 直接退出, 不调 LLM
        self.adj["entries"]["skill_anchor_target"]["next_options"] = [
            {"skill": "__planned__", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM("{}")
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="g"))
        self.assertEqual(len(llm.calls), 0)
        self.assertEqual(rep.exit_channel, "terminate_suggest")


class T11_RunAtomicE2E(RouterTestBase):
    """⑪ executor.run_atomic 端到端 mock: 伪 node_outputs 映射正确, mock gate 被调用, 不真实写库"""
    def test(self):
        import cgraph.executor as EX
        # mock 网内全技能 + 记录 gate 调用
        gate_calls, commit_calls = [], []
        orig_gate, orig_commit = EX._ChainExecutor__dict__ if False else (None, None)
        # monkeypatch 实例方法
        class FakeExec:
            pass
        # 直接构造 ChainExecutor 并替换两方法
        saved_gate = EX.ChainExecutor._run_memory_gate
        saved_commit = EX.commit_green_cards
        class _FakeGateResult:                       # 对齐 _run_memory_gate 真实契约: (result, gate)
            def __init__(self):
                self.result = {"applicable": True, "gate_list": [
                    {"tier": "green", "gist": "测试", "atype": "观测",
                     "verb": "建议立卡", "evidence_step": 1}], "coverage_note": "", "summary": "s",
                    "confidence": 0.8, "ambiguity_note": "", "degraded": False}
        EX.ChainExecutor._run_memory_gate = lambda self, ui, no, walked: (
            gate_calls.append({"node_outputs": dict(no), "walked": list(walked)}) or
            (_FakeGateResult(), object()))
        EX.commit_green_cards = lambda result, gate, ui: commit_calls.append(1)
        try:
            ex = EX.ChainExecutor.__new__(EX.ChainExecutor)
            ex.llm = MockLLM('{"chosen": "skill_terminate", "reason": "收口"}')  # 绕过__init__需补llm
            report, state, gate = ex.run_atomic("端到端测试目标")
            # 伪 node_outputs 映射: trail 每步 skill_id→step_summary
            self.assertEqual(gate_calls[0]["node_outputs"],
                             {e["skill_id"]: e["step_summary"] for e in report.final_state.trail})
            self.assertEqual(len(commit_calls), 1)
            self.assertEqual(gate_calls[0]["walked"], [e["skill_id"] for e in report.final_state.trail])
        finally:
            EX.ChainExecutor._run_memory_gate = saved_gate
            EX.commit_green_cards = saved_commit


class T12_RouteReportSchema(RouterTestBase):
    """⑫ RouteReport 全部字段、类型校验"""
    def test(self):
        rep = RouterAtomic(MockLLM('{"chosen": "skill_terminate", "reason": "r"}'), self.adj).run(ThinkState(goal="g"))
        self.assertIsInstance(rep, RouteReport)
        self.assertIsInstance(rep.ok, bool)
        self.assertIn(rep.exit_channel, EXIT_CHANNELS)
        self.assertIsInstance(rep.final_state, ThinkState)
        self.assertIsInstance(rep.steps_log, list)
        self.assertIsInstance(rep.fallback_count, int)
        for s in rep.steps_log:
            self.assertEqual(set(s.keys()),   # P-C3: +preempt 字段(段二配套)
                             {"step", "skill_id", "chosen", "candidates", "reason", "fallback", "preempt", "degrade", "secs", "ts"})
        # P-C3 段五: RouteReport 观测字段
        self.assertIn(rep.task_type, ("eval", "root_cause", "decision", "execution", "other"))
        self.assertIn(rep.task_type_source, ("llm", "lexicon", "other"))


# ---------------- P-C3 新增 8 项(C1-C8, 段六冻结清单; mock 注入 task_type) ----------------

class C3TestBase(RouterTestBase):
    """C3 用例公共: 构造 weakest_link 后菜单含 f_reason+terminate 的标准场景。"""
    def _wl_adj(self):
        # weakest_link 菜单含 f_reason 与 terminate(邻接表真实形态)
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_f_reason"]["next_options"] = [
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [
            {"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)

    def _run_wl(self, replies, task_type=None, steps=None):
        llm = MockLLM(replies)
        r = RouterAtomic(llm, self.adj)
        if task_type is not None:
            r.task_type, r.task_type_source = task_type, "llm"   # mock 注入(段三解耦原则)
        if steps is not None:
            r._orig = None
        st = ThinkState(goal="评估类目标")
        return r.run(st), llm


class C1_PreemptFull(RouterTestBase):
    """C1: eval 全条件满足 LLM 选 terminate → 拦截走 f_reason"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_f_reason"]["next_options"] = [{"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "完成", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "收", "task_type": "eval"}'])
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="评估方案风险"))
        self.assertIn("skill_f_reason", [s["skill_id"] for s in rep.steps_log])
        pre = [s for s in rep.steps_log if s.get("preempt")]
        self.assertTrue(pre)
        self.assertEqual(pre[0]["reason"], "C3偏好拦截terminate→f_reason")
        self.assertTrue(all(s.get("fallback") is False for s in pre))   # 策略拦截≠故障回退
        self.assertIn(rep.exit_channel, ("llm_terminate", "terminate_suggest"))
        self.assertEqual(rep.fallback_count, 0)


class C2_SecondPassThrough(RouterTestBase):
    """C2: 拦一次后 LLM 再选 terminate → 无条件放行"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_f_reason"]["next_options"] = [{"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        # f_reason 菜单只剩 terminate → 拦截后下一轮 LLM 选 terminate → 放行
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "1", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "2", "task_type": "eval"}'])
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="评估方案风险"))
        self.assertEqual(rep.exit_channel, "llm_terminate")
        self.assertEqual(len([s for s in rep.steps_log if s.get("preempt")]), 1)  # 只拦一次


class C3_TypeOff(RouterTestBase):
    """C3: decision/other → 提示不追加、不拦截"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "decision"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "decision"}',
                      '{"chosen": "skill_terminate", "reason": "收", "task_type": "decision"}'])
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="该不该建仓"))
        self.assertEqual(rep.exit_channel, "llm_terminate")
        self.assertNotIn("skill_f_reason", [s["skill_id"] for s in rep.steps_log])
        for msgs in llm.calls:
            self.assertNotIn("竞争假设", msgs[1]["content"])          # 无提示注入


class C4_PromptHint(RouterTestBase):
    """C4: 窗口期内 prompt 含偏好提示; 窗口关闭后不含"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_f_reason"]["next_options"] = [{"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "x", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "y", "task_type": "eval"}'])
        RouterAtomic(llm, self.adj).run(ThinkState(goal="评估方案风险"))
        # 第3次调用(weakest_link后)应有提示; 第4次(拦截后窗口关)无
        self.assertIn("竞争假设", llm.calls[2][1]["content"])
        self.assertNotIn("竞争假设", llm.calls[3][1]["content"])


class C5_LexiconFallback(RouterTestBase):
    """C5: 词表兜底只归 root_cause, 不归 eval"""
    def test(self):
        # 场景1: LLM 无 task_type + goal 含"复盘" → root_cause/lexicon
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm1 = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r"}',
                       '{"chosen": "skill_weakest_link", "reason": "r"}',
                       '{"chosen": "skill_terminate", "reason": "s"}'])
        r1 = RouterAtomic(llm1, self.adj)
        st1 = ThinkState(goal="复盘零入库事故根因")
        rep1 = r1.run(st1)
        self.assertEqual(r1.task_type, "root_cause")
        self.assertEqual(r1.task_type_source, "lexicon")
        self.assertEqual(rep1.task_type, "root_cause")
        # 场景2: 仅"风险"(决策语境)不命中词表 → other
        llm2 = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r"}',
                       '{"chosen": "skill_weakest_link", "reason": "r"}',
                       '{"chosen": "skill_terminate", "reason": "s"}'])
        r2 = RouterAtomic(llm2, self.adj)
        r2.run(ThinkState(goal="建仓风险评估"))          # "分析"命中词表? 分析在词表内→root_cause
        # 词表词含"分析"——按裁决词集(根因/分析/审查/复盘)本goal命中; 换纯决策goal
        r3 = RouterAtomic(MockLLM(['{"chosen": "skill_terminate", "reason": "s"}']), self.adj)
        r3.run(ThinkState(goal="该不该买紫光国微"))
        self.assertEqual(r3.task_type, "other")


class C6_FirstRoundLock(RouterTestBase):
    """C6: task_type 首轮锁定, 后续漂移不改"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "decision"}',
                      '{"chosen": "skill_terminate", "reason": "s", "task_type": "other"}'])
        r = RouterAtomic(llm, self.adj)
        r.run(ThinkState(goal="评估方案"))
        self.assertEqual(r.task_type, "eval")            # 首轮锁定


class C7_BudgetGuard(RouterTestBase):
    """C7: steps=max_steps-1 → 预算不足放行, 无拦截无提示"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        self.adj["global"]["max_steps"] = 4
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_weakest_link", "reason": "r", "task_type": "eval"}',
                      '{"chosen": "skill_terminate", "reason": "收", "task_type": "eval"}'])
        # anchor(1)+scan(2)+wl(3); wl 后选 terminate 时 steps=3 > max-2=2 → 预算不足抑制, 放行
        rep = RouterAtomic(llm, self.adj).run(ThinkState(goal="评估方案"))
        self.assertEqual(rep.exit_channel, "llm_terminate")
        self.assertNotIn("skill_f_reason", [s["skill_id"] for s in rep.steps_log])


class C8_VisitCapOff(RouterTestBase):
    """C8: f_reason 已 2 次 → C3 关闭; RouteReport 观测字段类型合法"""
    def test(self):
        self.adj["entries"]["skill_weakest_link"]["next_options"] = [
            {"skill": "skill_f_reason", "gain": "g", "cost": "c"},
            {"skill": "skill_terminate", "gain": "g", "cost": "c"}]
        self.adj["entries"]["skill_scope_scan"]["next_options"] = [{"skill": "skill_weakest_link", "gain": "g", "cost": "c"}]
        _patch_registry(self.adj)
        r = RouterAtomic(MockLLM('{}'), self.adj)
        r.task_type, r.task_type_source = "eval", "llm"
        st = ThinkState(goal="g")
        st.trail = [{"skill_id": "skill_weakest_link"}, {"skill_id": "skill_f_reason"},
                    {"skill_id": "skill_weakest_link"}, {"skill_id": "skill_f_reason"}]
        st.steps = 4
        self.assertFalse(r._c3_should_preempt(st, [{"skill": "skill_f_reason"}]))
        # RouteReport 观测字段(非C3主逻辑)
        rep = RouterAtomic(MockLLM('{"chosen": "skill_terminate", "reason": "r", "task_type": "eval"}'), self.adj).run(ThinkState(goal="g"))
        self.assertIsInstance(rep.task_type, str)
        self.assertIsInstance(rep.task_type_source, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
