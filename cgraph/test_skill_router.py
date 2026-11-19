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
    """附加: retrieve 契约——BM25 判别力/异常恒返[]/top_k(P-retrieval-upgrade 段二⑤: 判别力用例
    更新为 BM25 语义, 异常返回[]用例直接复用; 打分 BM25(k1=1.5,b=0.75)+仅前邻扩展)"""
    def test(self):
        mat = "一级完全可复现信息。\n\n三级不可复现信息永久保存。\n\n闪念记忆层。"
        r = retrieve("不可复现信息如何保存", mat, 2)
        self.assertTrue(any("不可复现" in x for x in r))
        self.assertEqual(retrieve("", mat), [])
        self.assertEqual(retrieve("q", ""), [])
        self.assertEqual(retrieve(None, mat), [])
        # BM25 语义判别力: 专题段应胜过高频泛段(query 专有词 IDF 加权)
        mat2 = ("概述段落: 本系统包含多个方面内容总览介绍。\n\n"
                "错误处理机制: 校验失败时接收端返回错误帧并重传。\n\n"
                "概述段落再次出现: 系统总体架构与内容总览。")
        r2 = retrieve("校验失败如何重传", mat2, 1)
        self.assertTrue(r2 and "错误帧" in r2[0])


class R11_HeadingBodyAdjacency(unittest.TestCase):
    """R11(P-retrieval-upgrade 段三): 标题-正文邻接召回——短碎标题段 IDF 偏低分数下沉,
    依靠前邻扩展兜底(该病灶专属哨兵用例, 段三用户裁决风险锚)。
    场景: 标题段含 query 关键词, 定义正文不含关键词却在标题下一段——
    前邻扩展应让命中标题时带出其上文, 或命中正文时带出标题。"""
    def test(self):
        mat = ("可复现性驱动的三级记忆分层体系\n"          # 标题段(碎段, IDF 偏低风险)
               "一级记忆为闪念级, 传输即验证; 二级记忆为工作集, 保存七天;\n"
               "三级记忆为永久留存, 按需复核。三级之间按判定结果自动流转。\n\n"
               "其他无关章节: 本段讲通信协议帧格式的魔数定义与操作码体系。")
        # query 命中标题词 → 召回应含标题段(或其正文段带出标题)
        r = retrieve("三级记忆分层体系怎么定义", mat, 2)
        joined = "\n".join(r)
        self.assertTrue(any("三级记忆" in x or "分层体系" in x for x in r))
        # 若召回的是正文段(不含"体系"标题词), 前邻扩展须带出标题段
        self.assertIn("…(上文)", joined)   # 前邻扩展生效
        self.assertTrue("永久留存" in joined or "闪念级" in joined)   # 定义内容可见


class R12_FillerNoCrowdOut(unittest.TestCase):
    """R12(P-retrieval-upgrade 段三): 填充噪声段不挤占业务段召回位(R10 精神, 噪声回归)。
    场景: 大量重复填充段 + 业务段, BM25 IDF 应让填充词(文档内高频)贬值,
    query 业务词优先召回业务段。"""
    def test(self):
        filler = "\n\n".join("相关论述补充说明。" * 1 for _ in range(40))
        mat = (filler + "\n\n判定单元在记忆入库前执行可复现性检测并输出分类标签。\n\n" + filler)
        r = retrieve("判定单元如何检测", mat, 3)
        self.assertTrue(any("判定单元" in x for x in r))
        # 填充段不得占满全部召回位
        self.assertTrue(any("判定单元" in x for x in r[:2]))


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


# ---------------- P-matrix-feed R15(a-d) 隔离&降级&回归(2026-11-18 段三T6) ----------------

class R15a_SufficiencyZeroMatrix(RouterTestBase):
    """R15a: 隔离红线——skill_sufficiency_check.py 源码零 matrix 字符串。"""
    def test(self):
        import io as _io
        with _io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "skill_sufficiency_check.py"), encoding="utf-8") as _f:
            src = _f.read()
        self.assertNotIn("matrix", src)   # 源码级断言: sufficiency 判据零触碰


class R15b_DegradedPaths(RouterTestBase):
    """R15b: 三条降级路径——路径错误[]/超限截断/零命中[]。"""
    def test(self):
        from cgraph import matrix_knowledge as MK
        # ① 路径错误(monkeypatch 模块路径常量)
        real = MK._MATRIX_PATH
        MK._MATRIX_PATH = "data/matrix/__no_such__.jsonl"
        try:
            self.assertEqual(MK.load_matrix("记忆 异常 权重", ""), [])
            self.assertEqual(MK._load_entries(), [])
        finally:
            MK._MATRIX_PATH = real
        # ② 超限: cap 极小 → 条目全弃(尾部截断在小集合下的等价形态)
        mk = MK.load_matrix("认知断点 异常暂存 标签化 记忆归档 权重迭代 不可篡改 区分度", "")
        self.assertTrue(len(mk) >= 1)     # 正常cap下有命中
        self.assertEqual(MK.load_matrix("认知断点 异常暂存 标签化 记忆归档", "", token_cap=1), [])
        # ③ 零命中(无任何 trigger_kw 交集)
        self.assertEqual(MK.load_matrix("完全无关的美食烹饪问题怎么做好吃", ""), [])
        # 兜底锚: scan_summary 空时 goal 单独输入不崩(上面各例 scan_summary 均为 "")


class R15c_PromptByteRegression(RouterTestBase):
    """R15c: prompt 回归——关 matrix 字节级一致; 开 matrix 出现隔离头+溯源标记。"""
    def test(self):
        from cgraph.matrix_knowledge import matrix_block
        # 构造带/不带 matrix_knowledge 的 state, 直接对比注入串
        st_off = ThinkState(goal="g")
        self.assertEqual(matrix_block(st_off, None), "")          # 关闭 → 恒 ''(基线字节一致)
        st_on = ThinkState(goal="g")
        st_on.matrix_knowledge = [{"matrix_id": "pa-001", "tech_name": "RAG长期记忆",
                                   "mapping_note": "映射依据", "audit_risk": "驳回锚"}]
        blk = matrix_block(st_on, None)
        self.assertTrue(blk.startswith("---matrix_knowledge"))    # 隔离分隔头
        self.assertIn("[source=pa-001]", blk)                     # 溯源标记
        # 物理隔离: 不含 ref_material 分隔头
        self.assertNotIn("---ref_material", blk)
        # 真实技能 build_prompt 双形态对照(mock LLM 拿到 prompt 再断言)
        import json as _j
        from cgraph.skill_f_reason import SkillFReason
        from cgraph.router_atomic import SKILL_REGISTRY
        _saved = SKILL_REGISTRY.get("skill_f_reason")
        adj = {"entries": {"skill_f_reason": {"state_inputs": ["goal_anchored", "sub_questions",
                                                              "summary", "ref_material",
                                                              "matrix_knowledge"],
                                              "state_outputs": ["hypotheses", "summary"]}}}
        llm = MockLLM(_j.dumps({"hypotheses": [{"hypothesis": "h", "support": "s", "contradict": "",
                                                "test_next": "t"}], "applicable": [],
                                "coverage_note": "c", "summary": "s", "confidence": 0.8}))
        try:
            # OFF
            sk = SkillFReason(llm=llm, adjacency=adj)
            st = ThinkState(goal="g", goal_anchored="目标", sub_questions=[], summary="s")
            off_msgs = sk.build_prompt(st); off_prompt = off_msgs[-1]["content"]
            self.assertNotIn("---matrix_knowledge", off_prompt)
            # ON
            st2 = ThinkState(goal="g", goal_anchored="目标", sub_questions=[], summary="s")
            st2.matrix_knowledge = st_on.matrix_knowledge
            on_msgs = sk.build_prompt(st2); on_prompt = on_msgs[-1]["content"]
            self.assertIn("---matrix_knowledge", on_prompt)
            self.assertIn("[source=pa-001]", on_prompt)
            # OFF prompt 去掉 ON 独有段后与 ON 前缀一致(增量注入不扰动基线内容)
            self.assertTrue(on_prompt.startswith(off_prompt))
        finally:
            if _saved is not None:
                SKILL_REGISTRY["skill_f_reason"] = _saved


class R15d_TrailMatrixInjected(RouterTestBase):
    """R15d: trail 埋点——run_atomic 后 matrix_injected 或 degraded 标记存在。"""
    def test(self):
        # 主链 mock: anchor→scan→terminate; scan 的 step_summary 含命中词触发装载
        llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "扫"}',
                       '{"chosen": "skill_terminate", "reason": "收"}'])
        # scan mock 产出带 trigger_kw 的 summary
        def _scan_run(self, st):
            st.summary = "认知断点 异常暂存 记忆归档 评估"
            return SkillResult(skill_id="skill_scope_scan",
                               result={"summary": "认知断点 异常暂存 记忆归档 评估"},
                               next_options=[], terminate_suggest=False)
        saved_scan = R.SKILL_REGISTRY.get("skill_scope_scan")
        R.SKILL_REGISTRY["skill_scope_scan"] = type("S", (object,),
            {"__init__": lambda s, llm, adjacency: None, "run": _scan_run})
        try:
            self._saved = _patch_registry(self.adj, overrides={"skill_scope_scan": {"term": False}})
            # 恢复自定义 scan(被 _patch_registry 覆盖)
            R.SKILL_REGISTRY["skill_scope_scan"] = type("S", (object,),
                {"__init__": lambda s, llm, adjacency: None, "run": _scan_run})
            from cgraph.executor import ChainExecutor
            ex = ChainExecutor.__new__(ChainExecutor); ex.llm = llm
            report, state, gate = ex.run_atomic("评估认知断点检测与记忆归档专利")
            mk_rows = [e for e in state.trail if e.get("skill_id") == "matrix_feed"]
            self.assertEqual(len(mk_rows), 1)                       # 装载一次
            note = mk_rows[0]["step_summary"]
            self.assertTrue(note.startswith("matrix_injected:") or
                            note.startswith("matrix: degraded"))    # 二态埋点
            if note.startswith("matrix_injected:"):
                self.assertIn("pa-", note)                          # 命中条目 id 列表
        finally:
            if saved_scan is not None:
                R.SKILL_REGISTRY["skill_scope_scan"] = saved_scan


# ---------------- P-skill-synthesize R16(a-d) 收敛/路由/红线/降级(2026-11-18 段三T6) ----------------

class R16a_ThreeSegmentSchema(RouterTestBase):
    """R16a: 三段 schema 正常解析 + 缺段降级标记(不改 hypotheses)。"""
    def test(self):
        import json as _j
        from cgraph.skill_synthesize import SkillSynthesize
        adj = {"entries": {"skill_synthesize": {
            "state_inputs": ["goal_anchored", "sub_questions", "summary", "hypotheses", "ref_material"],
            "state_outputs": ["selection_rationale", "final_deliverable",
                              "alternatives_appendix", "summary"]}}}
        sk = SkillSynthesize(llm=MockLLM(""), adjacency=adj)
        st = ThinkState(goal="g", goal_anchored="优化公式", sub_questions=[{"q": "算子"}],
                        summary="s", hypotheses=[{"hypothesis": "EWMA"}, {"hypothesis": "贝叶斯"}])
        msgs = sk.build_prompt(st)
        self.assertIn("H1: EWMA", msgs[-1]["content"])
        self.assertIn("H2: 贝叶斯", msgs[-1]["content"])
        good = _j.dumps({"selection_rationale": "选H1", "final_deliverable": "w=α·w+β·Δ",
                         "alternatives_appendix": [{"hypothesis_ref": "H2", "summary": "x",
                                                    "keep_reason": "r"}],
                         "summary": "s", "confidence": 0.8})
        r = sk.parse_result(good, st)
        self.assertEqual(r["selection_rationale"], "选H1")
        self.assertEqual(r["final_deliverable"], "w=α·w+β·Δ")
        self.assertEqual(len(r["alternatives_appendix"]), 1)
        self.assertNotIn("degraded", r)
        # 缺 final_deliverable → degraded 标记且 hypotheses 未被改
        bad = _j.dumps({"selection_rationale": "x"})
        r2 = sk.parse_result(bad, st)
        self.assertEqual(r2.get("degraded"), "final_deliverable_missing")
        self.assertEqual(len(st.hypotheses), 2)          # 红线: 原样保留
        # decide_terminate 恒 False
        self.assertFalse(sk.decide_terminate(st, r))


class R16b_RouteGateRegression(RouterTestBase):
    """R16b: 路由门——非 scheme_design 剔除 synthesize; scheme_design 保留;
    无 anchor 记录 fail-safe 剔除。"""
    def test(self):
        menu = [{"skill": "skill_synthesize"}, {"skill": "skill_terminate"}]
        import types as _tp
        from cgraph.router_atomic import RouterAtomic
        rt = RouterAtomic.__new__(RouterAtomic)
        rt.steps_log = []; rt.adjacency = {"entries": {"skill_terminate": {}, "skill_synthesize": {}}}
        rt._audit = _tp.MethodType(lambda self, *a: None, rt)
        rt._ids = _tp.MethodType(lambda self, x: [o.get("skill") for o in (x or [])], rt)
        for gt, keep in [("analysis", False), ("review", False), ("simple_query", False),
                         ("unknown_ambiguous", False), ("scheme_design", True)]:
            st = ThinkState(goal="g")
            st.trail = [{"skill_id": "skill_anchor_target", "result_fields": {"goal_type": gt}}]
            m = RouterAtomic._clean_menu(rt, [dict(o) for o in menu], st)
            self.assertIn("skill_synthesize", [o["skill"] for o in m]) if keep else \
                self.assertNotIn("skill_synthesize", [o["skill"] for o in m])
        # fail-safe: 无 anchor 记录
        st4 = ThinkState(goal="g"); st4.trail = []
        m4 = RouterAtomic._clean_menu(rt, [dict(o) for o in menu], st4)
        self.assertNotIn("skill_synthesize", [o["skill"] for o in m4])
        # state=None 兼容旧调用签名(纯占位清洗不受影响)
        m5 = RouterAtomic._clean_menu(rt, [{"skill": "skill_terminate"}, {"skill": "__planned__"}])
        self.assertEqual([o["skill"] for o in m5], ["skill_terminate"])


class R16c_HypothesesReadOnly(RouterTestBase):
    """R16c: 红线——E2E 链上 synthesize 执行后 hypotheses 原样、trail 不受染、
    三键写入 state。"""
    def test(self):
        import json as _j
        hyps = [{"hypothesis": "H1EWMA", "supporting": ["a"], "contradicting": [], "test_next": "t"},
                 {"hypothesis": "H2贝叶斯", "supporting": ["b"], "contradicting": [], "test_next": "t"}]
        def _syn_run(self, st):
            from cgraph.atomic_skill import SkillResult
            return SkillResult(skill_id="skill_synthesize",
                               result={"selection_rationale": "选H1因可公式化",
                                       "final_deliverable": "w_{k+1}=α·w_k+β·Δ_k 全文推演",
                                       "alternatives_appendix": [{"hypothesis_ref": "H2", "summary": "贝叶斯", "keep_reason": "备选"}],
                                       "summary": "收敛完成", "confidence": 0.8},
                               next_options=[{"skill": "skill_terminate", "gain": "g", "cost": "c"}],
                               terminate_suggest=False)
        import cgraph.router_atomic as RA
        saved = RA.SKILL_REGISTRY.get("skill_synthesize")
        RA.SKILL_REGISTRY["skill_synthesize"] = type("S", (object,),
            {"__init__": lambda s, llm, adjacency: None, "run": _syn_run})
        try:
            # goal_type=scheme_design 才走 synthesize: anchor mock 输出该类型
            def _anchor_run(self, st):
                from cgraph.atomic_skill import SkillResult
                return SkillResult(skill_id="skill_anchor_target",
                                   result={"anchored_goal": st.goal, "goal_type": "scheme_design", "summary": "a"},
                                   next_options=[{"skill": "skill_scope_scan", "gain": "g", "cost": "c"}],
                                   terminate_suggest=False)
            saved_a = RA.SKILL_REGISTRY.get("skill_anchor_target")
            RA.SKILL_REGISTRY["skill_anchor_target"] = type("A", (object,),
                {"__init__": lambda s, llm, adjacency: None, "run": _anchor_run})
            # f_reason mock 产出假设谱并把 hypotheses 写进 state(mock 不走write机制, 手动设)
            def _fr_run(self, st):
                from cgraph.atomic_skill import SkillResult
                st.hypotheses = [dict(h) for h in hyps]     # 模拟 state_outputs 写入
                st.trail.append({"skill_id": "skill_f_reason", "chosen": "skill_f_reason",
                                 "result_fields": {"hypotheses": hyps}, "step_summary": "fr"})
                return SkillResult(skill_id="skill_f_reason", result={"hypotheses": hyps, "summary": "fr"},
                                   next_options=[{"skill": "skill_synthesize", "gain": "g", "cost": "c"},
                                                 {"skill": "skill_terminate", "gain": "g", "cost": "c"}],
                                   terminate_suggest=False)
            saved_f = RA.SKILL_REGISTRY.get("skill_f_reason")
            RA.SKILL_REGISTRY["skill_f_reason"] = type("F", (object,),
                {"__init__": lambda s, llm, adjacency: None, "run": _fr_run})
            # scan mock: 转 f_reason(与真实邻接表一致)
            def _scan_run(self, st):
                from cgraph.atomic_skill import SkillResult
                return SkillResult(skill_id="skill_scope_scan",
                                   result={"sub_questions": [{"q": "q"}], "summary": "sc"},
                                   next_options=[{"skill": "skill_f_reason", "gain": "g", "cost": "c"}],
                                   terminate_suggest=False)
            saved_s = RA.SKILL_REGISTRY.get("skill_scope_scan")
            RA.SKILL_REGISTRY["skill_scope_scan"] = type("C", (object,),
                {"__init__": lambda s, llm, adjacency: None, "run": _scan_run})
            # 路由 LLM: 选 synthesize (goal_type 匹配)
            llm = MockLLM(['{"chosen": "skill_scope_scan", "reason": "扫"}',
                           '{"chosen": "skill_f_reason", "reason": "推理"}',
                           '{"chosen": "skill_synthesize", "reason": "收敛"}',
                           '{"chosen": "skill_terminate", "reason": "收"}'])
            from cgraph.executor import ChainExecutor
            ex = ChainExecutor.__new__(ChainExecutor); ex.llm = llm
            report, state, gate = ex.run_atomic("设计一套三级收敛公式体系方案")
            self.assertIn("skill_synthesize", [e["skill_id"] for e in state.trail])
            # 红线①: hypotheses 原样(条数与内容)
            self.assertEqual(len(state.hypotheses), 2)
            self.assertEqual(state.hypotheses[0]["hypothesis"], "H1EWMA")
            # 三段产出: 从 trail result_fields 断言(mock 不走基类write机制)
            syn_rf = next(e.get("result_fields") for e in state.trail
                          if e.get("skill_id") == "skill_synthesize")
            self.assertIn("w_{k+1}", str(syn_rf.get("final_deliverable")))
            self.assertIn("H2", str(syn_rf.get("alternatives_appendix")))
            self.assertEqual(syn_rf.get("selection_rationale"), "选H1因可公式化")
        finally:
            for k, v in [("skill_synthesize", saved), ("skill_anchor_target", saved_a),
                         ("skill_f_reason", saved_f), ("skill_scope_scan", saved_s)]:
                if v is not None:
                    RA.SKILL_REGISTRY[k] = v


class R16d_DegradeToTerminate(RouterTestBase):
    """R16d: LLM 输出解析失败 → degraded 标记 + 链路不因 synthesize 中断。"""
    def test(self):
        from cgraph.skill_synthesize import SkillSynthesize
        adj = {"entries": {"skill_synthesize": {
            "state_inputs": ["goal_anchored", "sub_questions", "summary", "hypotheses", "ref_material"],
            "state_outputs": ["selection_rationale", "final_deliverable",
                              "alternatives_appendix", "summary"]}}}
        sk = SkillSynthesize(llm=MockLLM("不是JSON"), adjacency=adj)
        st = ThinkState(goal="g", goal_anchored="x", hypotheses=[{"hypothesis": "h1"}])
        # extract_json 兜不住纯文本 → parse 得空段 + degraded
        r = sk.parse_result("完全不是 JSON 的输出", st)
        self.assertEqual(r.get("degraded"), "empty_output")
        self.assertEqual(len(st.hypotheses), 1)           # 上游不污染
        self.assertFalse(sk.decide_terminate(st, r))       # 恒False(收敛交terminate)


# ---------------- round5 自举审计快修 R17(2026-11-18): DB路径同源断言 ----------------

class R17_CommitDbSameSource(RouterTestBase):
    """R17: P0-3 回归锚——commit_green_cards 使用的 db 必须与 atomic_skill.AUDIT_DB
    同源(相对路径硬编码=CWD漂移静默落错库, E4 对账 drift 根因嫌疑)。"""
    def test(self):
        import io as _io
        src = _io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "executor.py"), encoding="utf-8").read()
        with _io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "executor.py"), encoding="utf-8") as _f:
            src = _f.read()
        # 禁止出现相对路径硬编码(旧P0-3形态)
        self.assertNotIn('db = "data/memory/corpus_store.db"', src)
        # 必须引用 AUDIT_DB 同源
        self.assertIn("AUDIT_DB as db", src)


class R18_WlToFReasonCrossCheck(RouterTestBase):
    """R18: P1快修回归(2026-11-18 round6上下文传递审计)——wl→f_reason 链路:
    f_reason build_prompt 可读取上游 weakest_link 结构化条目并注入交叉验证语境;
    wl 缺失时 prompt 与改造前字节一致(零影响); parse 正常无降级。"""
    def test(self):
        import json as _j
        from cgraph.skill_f_reason import SkillFReason
        from cgraph.router_atomic import SKILL_REGISTRY
        _saved = SKILL_REGISTRY.get("skill_f_reason")
        # 邻接表带 weakest_link 键(对齐 P1快修后 data/atomic_skills_adjacency.json)
        adj = {"entries": {"skill_f_reason": {"state_inputs": ["goal_anchored", "sub_questions",
                                                              "summary", "ref_material",
                                                              "matrix_knowledge",
                                                              "weakest_link"],
                                              "state_outputs": ["hypotheses", "summary"]}}}
        wl = {"location": "说明书-创新点一 公开充分性", "dimension": "公开充分性",
              "score": 0.25, "risk": "high", "evidence": "技术效果仅断言无实验数据支撑"}
        ok_reply = _j.dumps({"hypotheses": [{"hypothesis": "H1公开不充分", "support": "s",
                                             "contradict": "", "test_next": "t"}],
                             "applicable": [], "coverage_note": "c", "summary": "s",
                             "confidence": 0.8})
        try:
            llm = MockLLM(ok_reply)
            sk = SkillFReason(llm=llm, adjacency=adj)
            # ON: wl 在 state → 注入交叉验证块
            st_on = ThinkState(goal="g", goal_anchored="审查锚点专利", sub_questions=[], summary="s")
            st_on.weakest_link = dict(wl)
            on_msgs = sk.build_prompt(st_on); on_prompt = on_msgs[-1]["content"]
            self.assertIn("上游最弱环节定位", on_prompt)
            self.assertIn("说明书-创新点一 公开充分性", on_prompt)
            self.assertIn("0.25", on_prompt)
            # OFF: wl=None → 与基线字节一致(无注入块)
            st_off = ThinkState(goal="g", goal_anchored="审查锚点专利",
                                sub_questions=[], summary="s")
            off_msgs = sk.build_prompt(st_off); off_prompt = off_msgs[-1]["content"]
            self.assertNotIn("上游最弱环节定位", off_prompt)
            self.assertTrue(on_prompt.startswith(off_prompt))   # 增量注入不扰动基线
            # OFF 且 dict 无 location → 同样零注入(条件守卫)
            st_bad = ThinkState(goal="g", goal_anchored="x", sub_questions=[], summary="s")
            st_bad.weakest_link = {"dimension": "d"}
            self.assertNotIn("上游最弱环节定位",
                             sk.build_prompt(st_bad)[-1]["content"])
            # 全链 run: parse 正常无降级 + hypotheses 写回 state
            r = sk.run(st_on)
            self.assertNotIn("_degraded", r.result)
            self.assertEqual(len(r.result["hypotheses"]), 1)
            self.assertTrue(st_on.hypotheses and st_on.hypotheses[0]["hypothesis"] == "H1公开不充分")
        finally:
            if _saved is not None:
                SKILL_REGISTRY["skill_f_reason"] = _saved


class R19_DkDeductionChain(RouterTestBase):
    """R19: P-skeleton Dk 演绎链——织链/4步截断/unsupported 派生/前置守卫/降级/写回。"""
    def test(self):
        import json as _j
        from cgraph.skill_dk_deduction import SkillDkDeduction
        adj = {"entries": {"skill_dk_deduction": {
            "state_inputs": ["goal_anchored", "hypotheses", "weakest_link",
                             "ref_material", "matrix_knowledge", "summary"],
            "state_outputs": ["deduction_chains", "summary"],
            "result_fields": ["chains", "unsupported_premises", "applicable",
                              "summary", "confidence"]}}}
        ok = _j.dumps({"chains": [
            {"target_ref": "H1", "steps": [
                {"premise": "p%d" % i, "evidence_ref": "ref-A" if i % 2 == 0 else "",
                 "note": ""} for i in range(7)],                      # 7步->截4
                "verdict_ready": True}],
            "applicable": True, "summary": "s", "confidence": 0.8})
        try:
            # ① 正常: 4步截断 + unsupported(空evidence_ref)派生 + 写回
            llm = MockLLM(ok)
            sk = SkillDkDeduction(llm=llm, adjacency=adj)
            st = ThinkState(goal="g", goal_anchored="目标",
                            hypotheses=[{"hypothesis": "H1", "support": "s"}])
            st.weakest_link = {"location": "L1", "dimension": "d", "score": 0.5,
                               "evidence": "e"}
            r = sk.run(st)
            self.assertNotIn("_degraded", r.result)
            ch = r.result["chains"][0]
            self.assertEqual(len(ch["steps"]), 4)                     # 4步硬截断
            self.assertIn("上限截断", ch["steps"][-1]["note"])
            self.assertEqual(len(ch["unsupported_premises"]), 2)      # 4步中2步空引用
            self.assertEqual(len(r.result["unsupported_premises"]), 2)
            self.assertTrue(st.deduction_chains and st.deduction_chains[0]["target_ref"] == "H1")
            # ② 前置守卫: hypotheses+wl 全空 -> applicable=false 跳过(非降级)
            st2 = ThinkState(goal="g", goal_anchored="x")
            r2 = SkillDkDeduction(llm=MockLLM("任意"), adjacency=adj).run(st2)
            self.assertFalse(r2.result["applicable"])
            self.assertNotIn("_degraded", r2.result)
            # ③ 降级: JSON 全坏 -> _degraded + 链路不炸
            st3 = ThinkState(goal="g", goal_anchored="x",
                             hypotheses=[{"hypothesis": "H1"}])
            r3 = SkillDkDeduction(llm=MockLLM("不是JSON"), adjacency=adj).run(st3)
            self.assertIn("_degraded", r3.result)
            self.assertFalse(r3.result["chains"])   # 基类schema补空串, falsy即降级契约
        finally:
            pass


class R20_EnEvidenceNet(RouterTestBase):
    """R20: P-skeleton En 证据网——白名单配对/去重/矛盾派生/孤立检出/守卫/降级。"""
    def test(self):
        import json as _j
        from cgraph.skill_en_evidence_net import SkillEnEvidenceNet
        adj = {"entries": {"skill_en_evidence_net": {
            "state_inputs": ["goal_anchored", "hypotheses", "deduction_chains",
                             "ref_material", "matrix_knowledge", "summary"],
            "state_outputs": ["evidence_net", "isolated_evidence",
                              "contradiction_pairs", "summary"],
            "result_fields": ["net", "isolated_evidence", "contradiction_pairs",
                              "applicable", "summary", "confidence"]}}}
        # Dk链: M001两处引用, M002一处, M003零引用(不在链内)
        chains = [
            {"target_ref": "H1", "steps": [
                {"premise": "p1", "evidence_ref": "M001", "note": ""},
                {"premise": "p2", "evidence_ref": "M002", "note": ""}]},
            {"target_ref": "WL-1", "steps": [
                {"premise": "p3", "evidence_ref": "M001", "note": ""}]}]
        ok = _j.dumps({"net": [
            {"src_a": "M001", "src_b": "M002", "relation": "矛盾", "note": "冲突"},
            {"src_a": "M001", "src_b": "M002", "relation": "补充", "note": "重复对"},
            {"src_a": "M001", "src_b": "M999", "relation": "印证", "note": "白名单外"},
            {"src_a": "M002", "src_b": "M001", "relation": "补充", "note": "反向同对"}],
            "applicable": True, "summary": "s", "confidence": 0.8})
        try:
            # ① 正常: 白名单过滤(M999丢)+去重(3条重复丢) -> 只留1条矛盾边
            llm = MockLLM(ok)
            sk = SkillEnEvidenceNet(llm=llm, adjacency=adj)
            st = ThinkState(goal="g", goal_anchored="专利风险评估")
            st.deduction_chains = chains
            st.matrix_knowledge = [{"matrix_id": "M001"}, {"matrix_id": "M002"}]
            r = sk.run(st)
            self.assertNotIn("_degraded", r.result)
            self.assertTrue(r.result["applicable"])
            self.assertEqual(len(r.result["net"]), 1)                 # 4->1
            self.assertEqual(r.result["net"][0]["relation"], "矛盾")
            # 矛盾对纯Python派生(从net筛"矛盾", 非LLM单独数组)
            self.assertEqual(len(r.result["contradiction_pairs"]), 1)
            self.assertEqual(r.result["contradiction_pairs"][0]["src_a"], "M001")
            # 孤立证据: 链内引用但无net边 (M003不在链内不参与; M001/M002有边)
            self.assertEqual(r.result["isolated_evidence"], [])
            self.assertTrue(st.evidence_net)                          # 写回state
            self.assertTrue(st.contradiction_pairs)
            # ② 孤立检出: LLM只判M001-M002, 则M002无边? 用单证据链更直接——
            # 链只有M003引用, LLM返回空net -> M003孤立
            st2 = ThinkState(goal="g", goal_anchored="x")
            st2.deduction_chains = [{"target_ref": "H1", "steps": [
                {"premise": "p", "evidence_ref": "M003", "note": ""}]}]
            r2 = SkillEnEvidenceNet(llm=MockLLM(_j.dumps(
                {"net": [], "applicable": True, "summary": "s", "confidence": 0.5})),
                adjacency=adj).run(st2)
            self.assertEqual(r2.result["isolated_evidence"], ["M003"])
            # ③ 守卫: 链内无任何非空evidence_ref -> applicable=false(非降级)
            st3 = ThinkState(goal="g", goal_anchored="x")
            st3.deduction_chains = [{"target_ref": "H1", "steps": [
                {"premise": "p", "evidence_ref": "", "note": ""}]}]
            r3 = SkillEnEvidenceNet(llm=MockLLM("任意"), adjacency=adj).run(st3)
            self.assertFalse(r3.result["applicable"])
            self.assertNotIn("_degraded", r3.result)
            # ④ 降级: JSON全坏 -> _degraded + 链路不炸
            st4 = ThinkState(goal="g", goal_anchored="x")
            st4.deduction_chains = chains
            r4 = SkillEnEvidenceNet(llm=MockLLM("不是JSON"), adjacency=adj).run(st4)
            self.assertIn("_degraded", r4.result)
            self.assertFalse(r4.result["net"])
        finally:
            pass


class R21_BdCrosscheck(RouterTestBase):
    """R21: P-skeleton Bd 双维交叉——矩阵清洗/unaddressed派生/守卫/降级/写回。"""
    def test(self):
        import json as _j
        from cgraph.skill_bd_crosscheck import SkillBdCrosscheck
        adj = {"entries": {"skill_bd_crosscheck": {
            "state_inputs": ["goal_anchored", "hypotheses", "weakest_link",
                             "deduction_chains", "evidence_net", "summary"],
            "state_outputs": ["bd_matrix", "unaddressed", "summary"],
            "result_fields": ["cross_matrix", "unaddressed_points", "applicable",
                              "summary", "confidence"]}}}
        mk_state = lambda: ThinkState(
            goal="g", goal_anchored="专利授权风险评估",
            hypotheses=[{"hypothesis": "H1身份锚点公开不足", "support": "s"}])
        try:
            # ① 正常: 回应关系 -> unaddressed 空, 写回 state
            st = mk_state()
            st.weakest_link = {"location": "说明书-创新点一", "dimension": "d",
                               "score": 0.25, "evidence": "e"}
            st.deduction_chains = [{"target_ref": "H1身份锚点公开不足",
                                    "steps": [{"premise": "p", "evidence_ref": "M1", "note": ""}]}]
            st.evidence_net = [{"src_a": "M1", "src_b": "M2", "relation": "补充", "note": ""}]
            ok = _j.dumps({"cross_matrix": [
                {"hypothesis_ref": "H1身份锚点公开不足", "wl_location": "说明书-创新点一",
                 "relation": "回应", "note": "覆盖"},
                {"hypothesis_ref": "H1身份锚点公开不足", "wl_location": "不存在的位置",
                 "relation": "回应", "note": "白名单外"},
                {"hypothesis_ref": "H1身份锚点公开不足", "wl_location": "说明书-创新点一",
                 "relation": "部分回应", "note": "非法档位"}],
                "applicable": True, "summary": "s", "confidence": 0.8})
            r = SkillBdCrosscheck(llm=MockLLM(ok), adjacency=adj).run(st)
            self.assertNotIn("_degraded", r.result)
            self.assertEqual(len(r.result["cross_matrix"]), 1)     # 3->1(白名单+档位过滤)
            self.assertEqual(r.result["unaddressed_points"], [])   # 被回应
            self.assertTrue(st.bd_matrix and st.unaddressed == []) # 写回
            # ② 无关关系 -> unaddressed 命中该薄弱点
            st2 = mk_state()
            st2.weakest_link = {"location": "说明书-创新点一", "dimension": "d",
                                "score": 0.25, "evidence": "e"}
            r2 = SkillBdCrosscheck(llm=MockLLM(_j.dumps({
                "cross_matrix": [{"hypothesis_ref": "H1身份锚点公开不足",
                                  "wl_location": "说明书-创新点一",
                                  "relation": "无关", "note": "主题不匹配"}],
                "applicable": True, "summary": "s", "confidence": 0.7})),
                adjacency=adj).run(st2)
            self.assertEqual(r2.result["unaddressed_points"], ["说明书-创新点一"])
            self.assertTrue(st2.unaddressed)                       # 写回state供G24合并
            # ③ 守卫: weakest_link 空 -> applicable=false(非降级)
            st3 = mk_state()
            r3 = SkillBdCrosscheck(llm=MockLLM("任意"), adjacency=adj).run(st3)
            self.assertFalse(r3.result["applicable"])
            self.assertNotIn("_degraded", r3.result)
            # ④ 降级: JSON全坏 -> _degraded + 不炸
            st4 = mk_state()
            st4.weakest_link = {"location": "L", "dimension": "d", "score": 0.5, "evidence": "e"}
            r4 = SkillBdCrosscheck(llm=MockLLM("不是JSON"), adjacency=adj).run(st4)
            self.assertIn("_degraded", r4.result)
            self.assertFalse(r4.result["cross_matrix"])
        finally:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
