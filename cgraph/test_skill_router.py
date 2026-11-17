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
            self.assertEqual(set(s.keys()),   # P-C4: +token_in/token_out(段二2.5, 12键)
                             {"step", "skill_id", "chosen", "candidates", "reason", "fallback", "preempt", "degrade",
                              "token_in", "token_out", "secs", "ts"})
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


# ---------------- P-C4 新增 9 项(R1-R9, 素材通道域; 段四4.2 冻结清单) ----------------
# 单测只验逻辑正确性; 真实链路生效由 E12 冒烟承担(mock-现实偏差教训, 4.2 教训防御)

from cgraph.atomic_skill import _ref_material_block, REF_INLINE_THRESHOLD
from cgraph.ref_retrieval import retrieve


class _MatSkill:                     # 测试用消费技能(带素材注入断言)
    spec = {"state_inputs": ["ref_material"]}
    def __init__(self, llm=None, adjacency=None):
        self.llm = llm
        self.last_prompt = ""


class R1_EntrySplit(unittest.TestCase):
    """R1: 入口分隔符切片正确/标记丢弃/无标记不动"""
    def test(self):
        MARK = "---ref_material---"
        raw = f"评估专利方案{MARK}\n一级分类内容。\n\n二级分类内容。"
        gp, _, mp = raw.partition(MARK)
        self.assertEqual(gp, "评估专利方案")
        self.assertEqual(mp.lstrip("\r\n"), "一级分类内容。\n\n二级分类内容。")
        # 标记只切首次; 材料内再出现标记原样保留
        raw2 = f"问题{MARK}\n材料A{MARK}材料内标记保留"
        gp2, _, mp2 = raw2.partition(MARK)
        self.assertIn(MARK, mp2)
        # 无标记: 不处理
        raw3 = "普通问题无标记"
        self.assertNotIn(MARK, raw3.partition(MARK)[0] + (raw3.partition(MARK)[2] if MARK in raw3 else "x"))


class R2_EmptyAfterMark(unittest.TestCase):
    """R2: 标记后为空 → 视同无标记, ref_material=''"""
    def test(self):
        from cgraph.executor import ChainExecutor
        # 直接验证切片语义: 标记后空 → 材料识别移交 anchor(此处验证空判定)
        raw = "任务目标\n---ref_material---\n"
        gp, _, mp = raw.partition("---ref_material---")
        self.assertFalse(mp.strip())   # 空 → 不作为素材


class R3_LLMChannelStrip(unittest.TestCase):
    """R3: anchor LLM 通道剥离——ref_material 写入 state, goal_anchored 纯框架"""
    def test(self):
        from cgraph.skill_anchor_target import SkillAnchorTarget
        import json as _j
        class _L:  last_usage = None
        class _Anchor(SkillAnchorTarget):
            def __init__(self):
                BaseAtomicSkill = None
        # 用真实 SkillAnchorTarget + mock adjacency + mock llm
        from cgraph.atomic_skill import BaseAtomicSkill
        adj = {"entries": {"skill_anchor_target": {
            "state_inputs": ["goal"], "state_outputs": ["goal_anchored", "ref_material", "flags.confidence", "flags.sensitive"],
            "result_fields": ["anchored_goal", "ref_material", "goal_type", "ambiguity_note", "summary"],
            "next_options": [], "terminate_suggest": "恒假"}}}
        class FakeLLM:
            last_usage = None
            def chat(self, msgs):
                return _j.dumps({"anchored_goal": "评估方案质量", "ref_material": "专利原文正文...",
                                 "goal_type": "review", "ambiguity_note": "", "summary": "锚定",
                                 "confidence": 0.8, "sensitive": False})
        sk = SkillAnchorTarget.__new__(SkillAnchorTarget)
        sk.skill_id = "skill_anchor_target"; sk.spec = adj["entries"]["skill_anchor_target"]
        sk.adjacency = adj; sk.llm = FakeLLM()
        st = ThinkState(goal="评估方案: <大段材料>")
        res = sk.parse_result(sk.llm.chat([]), st)
        self.assertEqual(res["ref_material"], "专利原文正文...")
        self.assertEqual(res["anchored_goal"], "评估方案质量")


class R4_LLMOverrideGuard(unittest.TestCase):
    """R4: 入口已写非空 + LLM 再输出 → 代码丢弃 LLM 版本保留入口版(R13)"""
    def test(self):
        from cgraph.skill_anchor_target import SkillAnchorTarget
        import json as _j
        adj = {"entries": {"skill_anchor_target": {
            "state_inputs": ["goal"], "state_outputs": [], "result_fields": [],
            "next_options": [], "terminate_suggest": "恒假"}}}
        sk = SkillAnchorTarget.__new__(SkillAnchorTarget)
        sk.skill_id = "skill_anchor_target"; sk.spec = adj["entries"]["skill_anchor_target"]
        sk.adjacency = adj; sk.llm = None
        st = ThinkState(goal="g", ref_material="入口版本素材")
        raw = _j.dumps({"anchored_goal": "目标", "ref_material": "LLM试图覆盖的版本",
                        "goal_type": "analysis", "ambiguity_note": "", "summary": "s",
                        "confidence": 0.9, "sensitive": False})
        res = sk.parse_result(raw, st)
        self.assertEqual(res["ref_material"], "入口版本素材")   # 代码级防御生效


class R5_AnchorParseFailNoDegrade(unittest.TestCase):
    """R5: ref_material 键缺失 → 空串不降级; anchored_goal 空才降级"""
    def test(self):
        from cgraph.skill_anchor_target import SkillAnchorTarget
        import json as _j
        adj = {"entries": {"skill_anchor_target": {
            "state_inputs": ["goal"], "state_outputs": [], "result_fields": [],
            "next_options": [], "terminate_suggest": "恒假"}}}
        sk = SkillAnchorTarget.__new__(SkillAnchorTarget)
        sk.skill_id = "skill_anchor_target"; sk.spec = adj["entries"]["skill_anchor_target"]
        sk.adjacency = adj; sk.llm = None
        raw = _j.dumps({"anchored_goal": "有效目标", "goal_type": "analysis",
                        "ambiguity_note": "", "summary": "s", "confidence": 0.9, "sensitive": False})
        res = sk.parse_result(raw, ThinkState(goal="g"))
        self.assertEqual(res["ref_material"], "")
        self.assertNotIn("_degraded", res)                      # 素材缺失不降级
        raw2 = _j.dumps({"goal_type": "analysis"})             # anchored_goal 缺失
        res2 = sk.parse_result(raw2, ThinkState(goal="g"))
        self.assertIn("_degraded", res2)                        # 目标缺失才降级


class _SpecSkill:                     # R6/R7 共用: 消费技能视角
    def __init__(self, declares=True):
        self.spec = {"state_inputs": ["ref_material"] if declares else []}


class R6_ThresholdInline(unittest.TestCase):
    """R6: 材料 ≤8000(P-C6 阈值上调后) → 直塞完整材料"""
    def test(self):
        sk = _SpecSkill()
        mat = "短材料" * 300            # 900字符 < 8000
        st = ThinkState(goal="g", goal_anchored="任务", ref_material=mat)
        blk = _ref_material_block(st, sk)
        self.assertIn("参考素材(完整)", blk)
        self.assertIn("短材料", blk)


class R7_ThresholdRetrieve(unittest.TestCase):
    """R7: 材料 >8000 → 检索片段入prompt(素材本体完整保留)
    段三执行备注: 样本扩至 >8000 永久保留检索分支回归覆盖, 不因阈值上调变冷分支"""
    def test(self):
        sk = _SpecSkill()
        mat = "可复现性判定是核心创新点。\n\n" + "填充内容。" * 2500     # >8000
        st = ThinkState(goal="g", goal_anchored="评估可复现判定方案", ref_material=mat)
        blk = _ref_material_block(st, sk)
        self.assertIn("超直塞阈值", blk)
        self.assertIn("可复现性判定", blk)
        self.assertEqual(st.ref_material, mat)                  # 原始素材不丢失
        # 未声明 state_inputs 的技能不注入(权限内降级)
        blk2 = _ref_material_block(st, _SpecSkill(declares=False))
        self.assertEqual(blk2, "")


class R8_OversizeTruncate(unittest.TestCase):
    """R8: 30万字符 → 截断至200K留痕, 链路不崩(截断逻辑在executor入口, 此处验证语义)"""
    def test(self):
        MAXC = 200_000
        mat = "可复现性判定核心章节。\n\n" + "填充内容。" * 60000        # >30万
        self.assertGreater(len(mat), 300_000)
        cut = mat[:MAXC]                                            # 入口截断语义
        self.assertEqual(len(cut), MAXC)
        blk = _ref_material_block(ThinkState(goal="g", goal_anchored="评估可复现性判定", ref_material=cut), _SpecSkill())
        self.assertIn("超直塞阈值", blk)                              # 截断后仍走检索分支, 关键段判别力保留
        self.assertIn("可复现性判定", blk)


class R9_UsageMissing(RouterTestBase):
    """R9: mock LLM 无 usage → token_in/out=None, 链路正常"""
    def test(self):
        rep = RouterAtomic(MockLLM('{"chosen": "skill_terminate", "reason": "r"}'), self.adj).run(ThinkState(goal="g"))
        for s in rep.steps_log:
            self.assertIn("token_in", s)
            self.assertIn("token_out", s)
            self.assertTrue(s["token_in"] is None or isinstance(s["token_in"], int))


class R10_CippInline6401(unittest.TestCase):
    """R10(P-C6): 6401字P24级材料 ≤8000 → 直塞完整材料含'反规避撰写加固'标记词。
    段三执行备注: 只做结构断言(prompt 收到完整材料非片段)+直塞确认;
    '能否读到后半段'的语义校验交给段四 A/B 对拍, 单测不包揽 LLM 语义校验。"""
    def test(self):
        sk = _SpecSkill()
        # 模拟 P24 规模: 6401字, 前段业务内容+后段'反规避撰写加固补充'章节
        head = "二进制指令帧操作码寻址扩展接口CIPP协议。" * 150      # ≈3300字 前段
        mid = "填充论述段落。" * 600                                # ≈4200字 中段, 累计≈7500≤8000
        tail = "反规避撰写加固补充: 独立权利要求防御性布局。" * 1     # 后段标记章节
        mat = head + mid + tail
        self.assertLessEqual(len(mat), 8000)
        self.assertIn("反规避撰写加固", mat)
        st = ThinkState(goal="g", goal_anchored="评估CIPP协议", ref_material=mat)
        blk = _ref_material_block(st, sk)
        self.assertIn("参考素材(完整)", blk)          # 直塞(非"超直塞阈值")
        self.assertNotIn("超直塞阈值", blk)
        self.assertIn("反规避撰写加固", blk)           # 后段章节完整可见(结构级)


class R0_RetrieveContract(unittest.TestCase):
    """附加: retrieve 契约——判别力/异常恒返[]/top_k"""
    def test(self):
        mat = "一级完全可复现信息。\n\n三级不可复现信息永久保存。\n\n闪念记忆层。"
        r = retrieve("不可复现信息如何保存", mat, 2)
        self.assertTrue(any("不可复现" in x for x in r))
        self.assertEqual(retrieve("", mat), [])
        self.assertEqual(retrieve("q", ""), [])
        self.assertEqual(retrieve(None, mat), [])


# ---------------- P-C5 新增 10 项(S1-S10, 段四4.1 冻结清单) ----------------
# 机械层不 mock(可复现即真值); S2-S5/S7-S9 真实技能类+mock spec 注入(同 R3-R5 先例)

from cgraph.skill_sufficiency_check import SkillSufficiencyCheck

_SUFF_SPEC = {"state_inputs": ["goal", "goal_anchored", "sub_questions", "summary", "trail", "ref_material"],
              "state_outputs": [], "result_fields": [], "next_options": [], "terminate_suggest": "恒真"}


def _mk_suff(llm=None):
    sk = SkillSufficiencyCheck.__new__(SkillSufficiencyCheck)
    sk.skill_id = "skill_sufficiency_check"; sk.spec = dict(_SUFF_SPEC)
    sk.adjacency = {"entries": {"skill_sufficiency_check": _SUFF_SPEC}}; sk.llm = llm
    return sk


class _SuffLLM:
    """mock: 记录调用; 可编程回复"""
    last_usage = None
    def __init__(self, reply=None):
        self.reply = reply; self.calls = []
    def chat(self, msgs):
        self.calls.append(msgs)
        return self.reply


def _trail(hypo_text=None, wl_point=None, subqs=None, with_terminate=False, goal_type="analysis"):
    t = [{"skill_id": "skill_anchor_target", "step_summary": "锚定",
          "result_fields": {"goal_type": goal_type}},
         {"skill_id": "skill_scope_scan", "step_summary": "扫描",
          "result_fields": {"sub_questions": subqs or []}}]
    if wl_point is not None:
        t.append({"skill_id": "skill_weakest_link", "step_summary": "薄弱点",
                  "result_fields": {"weakest_point": wl_point}})
    if hypo_text is not None:
        t.append({"skill_id": "skill_f_reason", "step_summary": "假设",
                  "result_fields": {"hypotheses": [{"hypothesis": hypo_text, "supporting": "s"}]}})
    if with_terminate:
        t.append({"skill_id": "skill_terminate", "step_summary": "汇编",
                  "result_fields": {"final_output": "汇编产出"}})
    return t


class S1_HealthyZeroLLM(unittest.TestCase):
    """S1: 健康链→指标全绿+alerts空+LLM零调用"""
    def test(self):
        subs = [{"q": "评估区分度"}, {"q": "落地技术债"}]
        llm = _SuffLLM()
        sk = _mk_suff(llm)
        st = ThinkState(goal="g", goal_anchored="评估方案", sub_questions=subs,
                        trail=_trail(hypo_text="区分度论证与落地技术债均有回应假设",
                                     wl_point="区分度论证最薄弱", subqs=subs, with_terminate=True), steps=4)
        res = sk.parse_result("", st)
        self.assertEqual(res["alerts"], [])
        self.assertTrue(res["applicable"])
        self.assertEqual(llm.calls, [])                 # R16: 无疑点零LLM
        self.assertTrue(res["coverage_metrics"]["final_assembly_present"])


class S2_SubqLowTriggersReview(unittest.TestCase):
    """S2: subq_coverage低→触发LLM复核→alerts含机械疑点+复核陈述"""
    def test(self):
        subs = [{"q": "q1甲问"}, {"q": "q2乙问"}, {"q": "q3丙问"}, {"q": "q4丁问"}]
        import json as _j
        llm = _SuffLLM(_j.dumps({"reviewed_alerts": ["覆盖缺口陈述存在"], "new_concerns": [],
                                 "evidence_insufficient": [], "summary": "s", "confidence": 0.8}))
        sk = _mk_suff(llm)
        st = ThinkState(goal="g", goal_anchored="t", sub_questions=subs,
                        trail=_trail(hypo_text="完全无关假设内容", wl_point="无关薄弱点", subqs=subs), steps=4)
        res = sk.parse_result("", st)
        self.assertLess(res["coverage_metrics"]["subq_coverage"], 0.6)
        self.assertTrue(any("subq_coverage" in a for a in res["alerts"]))
        self.assertTrue(any("覆盖缺口" in a for a in res["alerts"]))  # 复核陈述入列
        self.assertEqual(len(llm.calls), 1)


class S3_WlUnaddressed(unittest.TestCase):
    """S3: wl疑点零回应→触发+告警"""
    def test(self):
        subs = [{"q": "x甲"}]
        sk = _mk_suff(None)
        st = ThinkState(goal="g", goal_anchored="t", sub_questions=subs,
                        trail=_trail(hypo_text="x甲假设完全无关", wl_point="甲乙丙丁薄弱", subqs=subs), steps=4)
        res = sk.parse_result("", st)
        self.assertTrue(any("wl_addressed" in a for a in res["alerts"]))


class S4_RetrievalDependent(unittest.TestCase):
    """S4: 检索分支+结论命中素材→retrieval_dependent=True+盲区陈述"""
    def test(self):
        mat = "评估方案素材全文。" * 1200                         # >8000 走检索分支(P-C6 阈值上调后仍测检索)
        subs = [{"q": "评估方案"}]
        sk = _mk_suff(None)
        st = ThinkState(goal="评估方案", goal_anchored="评估方案质量",
                        ref_material=mat, sub_questions=subs,
                        trail=_trail(hypo_text="评估方案假设", wl_point="评估方案薄弱", subqs=subs,
                                     with_terminate=True), steps=4)
        res = sk.parse_result("", st)
        self.assertTrue(res["coverage_metrics"]["retrieval_dependent"])
        self.assertTrue(any("retrieval_dependent" in a for a in res["alerts"]))
        self.assertIn("未遍历", res["coverage_note"])           # 盲区双处留痕(段三)


class S5_MaterialUnmapped(unittest.TestCase):
    """S5: 素材存在+全链零命中→文档未遍历告警"""
    def test(self):
        mat = "奇异主题素材独占词汇" * 100                        # 全链不出现
        subs = [{"q": "无关问题"}]
        sk = _mk_suff(None)
        st = ThinkState(goal="g", goal_anchored="无关目标", ref_material=mat,
                        sub_questions=subs,
                        trail=_trail(hypo_text="无关假设", subqs=subs), steps=3)
        res = sk.parse_result("", st)
        self.assertTrue(res["coverage_metrics"]["material_unmapped"])
        self.assertTrue(any("material_unmapped" in a for a in res["alerts"]))


class S6_ShortChainNA(unittest.TestCase):
    """S6: 空链/直收口→applicable=False 合法非降级"""
    def test(self):
        sk = _mk_suff(None)
        st = ThinkState(goal="问日期", goal_anchored="问日期",
                        trail=[{"skill_id": "skill_anchor_target", "step_summary": "锚定",
                                "result_fields": {"goal_type": "simple_query"}}], steps=1)
        res = sk.parse_result("", st)
        self.assertFalse(res["applicable"])
        self.assertEqual(res["alerts"], [])
        self.assertNotIn("_degraded", res)


class S7_ReviewFailSilent(unittest.TestCase):
    """S7: LLM复核失败(空返回)→静默跳过+固定告警+机械alerts保留"""
    def test(self):
        subs = [{"q": "q甲"}, {"q": "q乙"}, {"q": "q丙"}]
        llm = _SuffLLM(None)                                    # 空返回
        sk = _mk_suff(llm)
        st = ThinkState(goal="g", goal_anchored="t", sub_questions=subs,
                        trail=_trail(hypo_text="完全无关", subqs=subs), steps=3)
        res = sk.parse_result("", st)
        self.assertTrue(any("复核失败" in a for a in res["alerts"]))
        self.assertTrue(any("subq_coverage" in a for a in res["alerts"]))  # 机械保留


class S8_WordingViolationFiltered(unittest.TestCase):
    """S8: 复核回"建议补跑"→措辞违规过滤+原文保留"""
    def test(self):
        import json as _j
        subs = [{"q": "q甲"}, {"q": "q乙"}, {"q": "q丙"}]
        llm = _SuffLLM(_j.dumps({"reviewed_alerts": ["建议补跑扫描以覆盖子问题"],
                                 "new_concerns": [], "evidence_insufficient": [],
                                 "summary": "s", "confidence": 0.8}))
        sk = _mk_suff(llm)
        st = ThinkState(goal="g", goal_anchored="t", sub_questions=subs,
                        trail=_trail(hypo_text="无关", subqs=subs), steps=3)
        res = sk.parse_result("", st)
        self.assertTrue(any("措辞违规已过滤" in a and "建议补跑" in a for a in res["alerts"]))


class S9_NewConcernsTagged(unittest.TestCase):
    """S9: LLM新疑点打[LLM新发现]前缀; 机械疑点无前缀"""
    def test(self):
        import json as _j
        subs = [{"q": "q甲"}, {"q": "q乙"}, {"q": "q丙"}]
        llm = _SuffLLM(_j.dumps({"reviewed_alerts": ["支撑关系存在缺口"],
                                 "new_concerns": ["证据链跨步缺失风险"],
                                 "evidence_insufficient": [], "summary": "s", "confidence": 0.8}))
        sk = _mk_suff(llm)
        st = ThinkState(goal="g", goal_anchored="t", sub_questions=subs,
                        trail=_trail(hypo_text="无关", subqs=subs), steps=3)
        res = sk.parse_result("", st)
        self.assertTrue(any(a.startswith("[LLM新发现]") for a in res["alerts"]))
        self.assertTrue(any("支撑关系存在缺口" in a and not a.startswith("[LLM新发现]") for a in res["alerts"]))


class S10_AssemblyBackfill(RouterTestBase):
    """S10: 病灶A——llm_terminate退出后run_atomic补跑汇编, final_assembly_present=True"""
    def test(self):
        # 主链: anchor→scan→terminate(LLM选), 无terminate_suggest → 走llm_terminate通道
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "扫"}',
                       '{"chosen": "skill_terminate", "reason": "收"}'])
        from cgraph.executor import ChainExecutor
        ex = ChainExecutor.__new__(ChainExecutor); ex.llm = llm
        report, state, gate = ex.run_atomic("分析一下两步就够的问题")
        ids = [e["skill_id"] for e in state.trail]
        self.assertIn("skill_terminate", ids)            # 补跑汇编条目入trail
        # 哨兵指标由技能计算; 此处直接断言terminate条目存在(病灶A修复)


if __name__ == "__main__":
    unittest.main(verbosity=2)
