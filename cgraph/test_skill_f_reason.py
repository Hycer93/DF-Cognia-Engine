# -*- coding: utf-8 -*-
"""
test_skill_f_reason.py — skill_f_reason(F20封装) 回归(七段定稿闭环, 分歧①-⑦)

用户裁决覆盖(2026-11-16 第二轮裁决 R1-R10 全保留):
  正常多假设 / 超限截断7→5+note / 空谱三态(true+空=降级, false+空=合法) /
  JSON畸形 / 条目缺hypothesis丢弃+note / 全清洗丢弃降级 / 非法state读 /
  证据字段单字符串包列表+非法类型丢整条 / 审计落库 / 邻接表契约
跑法: python cgraph/test_skill_f_reason.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table
from cgraph.skill_f_reason import SkillFReason, MAX_HYPOTHESES

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


class MockLLM:
    def __init__(self, raw): self.raw = raw
    def chat(self, messages): return self.raw


def run_skill(raw):
    sk = SkillFReason(adjacency=ADJ)
    sk.llm = MockLLM(raw)
    st = ThinkState(goal="g", goal_anchored="评估X原因", sub_questions=[{"q": "q1"}], summary="s")
    r = sk.run(st)
    return r, st


HYP = lambda h, s, c, t: {"hypothesis": h, "supporting": s, "contradicting": c, "test_next": t}

# R1 正常多假设(3条全有效)
GOOD3 = '''{"applicable": true,
 "hypotheses": [
  {"hypothesis": "H1: 基线快照未更新导致误判", "supporting": ["快照时间戳旧"], "contradicting": [], "test_next": "重跑基线对比"},
  {"hypothesis": "H2: 环境抖动", "supporting": ["跨夜时段"], "contradicting": ["同环境昨日正常"], "test_next": "查监控"},
  {"hypothesis": "H3: 代码变更引入", "supporting": [], "contradicting": ["无变更记录"], "test_next": "git diff"}],
 "coverage_note": "覆盖环境/数据/代码三类成因, 遗漏硬件故障方向",
 "summary": "3条竞争假设: 基线/环境/代码", "confidence": 0.7, "ambiguity_note": ""}'''
r1, st1 = run_skill(GOOD3)
check("R1① hypotheses落state(3条)", len(st1.hypotheses) == 3)
check("R1② 四字段结构完整", all(
    set(h) == {"hypothesis", "supporting", "contradicting", "test_next"} for h in st1.hypotheses))
check("R1③ 无plausibility字段(分歧①B案)", all("plausibility" not in h for h in st1.hypotheses))
check("R1④ state.summary被覆盖(分歧③惯例)", st1.summary == "3条竞争假设: 基线/环境/代码")
check("R1⑤ terminate恒False", r1.terminate_suggest is False)
check("R1⑥ 菜单⊇terminate+__planned__(分歧④B案: 无weakest_link)",
      {o.get("skill") for o in r1.next_options} >= {"skill_terminate", "__planned__"}
      and "skill_weakest_link" not in {o.get("skill") for o in r1.next_options})
check("R1⑦ coverage_note透传", "硬件" in r1.result.get("coverage_note", ""))

# R2 超限截断(7→5)
H7 = '''{"applicable": true,
 "hypotheses": [%s],
 "coverage_note": "", "summary": "7条", "confidence": 0.5, "ambiguity_note": ""}''' % ",".join(
    '{"hypothesis": "H%d", "supporting": ["s"], "contradicting": [], "test_next": "t%d"}' % (i, i)
    for i in range(1, 8))
r2, st2 = run_skill(H7)
check("R2① 保前5条(MAX_HYPOTHESES)", len(st2.hypotheses) == MAX_HYPOTHESES == 5)
check("R2② 顺序截断保H1-H5", [h["hypothesis"] for h in st2.hypotheses] == ["H%d" % i for i in range(1, 6)])
check("R2③ note写明截断信息", "仅保留前5条" in r2.result["ambiguity_note"]
      and "原始生成7条" in r2.result["ambiguity_note"])

# R3 applicable=true+空数组 → 降级(O2前置防线)
R3RAW = '''{"applicable": true, "hypotheses": [], "coverage_note": "",
 "summary": "有假设", "confidence": 0.5, "ambiguity_note": ""}'''
r3, _ = run_skill(R3RAW)
check("R3 声称适用+空数组 → 强制降级", bool(r3.result.get("_degraded"))
      and "假设谱全空" in str(r3.result.get("_degraded")), str(r3.result.get("_degraded")))

# R3b applicable=false+空 → 合法非降级+默认摘要
R3BRAW = '''{"applicable": false, "hypotheses": [], "coverage_note": "",
 "summary": "", "confidence": 0.9, "ambiguity_note": ""}'''
r3b, st3b = run_skill(R3BRAW)
check("R3b① 不适用+空 → 合法非降级", not r3b.result.get("_degraded"))
check("R3b② 默认摘要生成", "不适用" in r3b.result["summary"] or "无关键不确定点" in r3b.result["summary"])
check("R3b③ state.hypotheses为空列表", st3b.hypotheses == [])

# R4 JSON畸形(自然语言) → 降级+result_fields补空
r4, _ = run_skill("我认为可能是环境问题, 但没有证据支持, 建议进一步排查。")
check("R4① JSON畸形 → 降级", r4.result.get("_degraded") == "JSON解析失败")
check("R4② result_fields全补空(基类兜底)", all(r4.result.get(f) == "" for f in
      ("hypotheses", "applicable", "coverage_note", "summary", "confidence", "ambiguity_note"))
      or r4.result.get("_degraded") == "JSON解析失败")

# R5 条目缺hypothesis本体 → 静默丢弃+note计数
R5RAW = '''{"applicable": true,
 "hypotheses": [{"hypothesis": "", "supporting": ["x"], "contradicting": [], "test_next": "t"},
                {"hypothesis": "H_ok", "supporting": ["s"], "contradicting": [], "test_next": "t"}],
 "coverage_note": "", "summary": "", "confidence": 0.5, "ambiguity_note": ""}'''
r5, st5 = run_skill(R5RAW)
check("R5① 本体空条目丢弃", len(st5.hypotheses) == 1 and st5.hypotheses[0]["hypothesis"] == "H_ok")
check("R5② 丢弃计数入note", "丢弃: 1条" in r5.result["ambiguity_note"])

# R6 applicable=true+条目全被清洗丢弃 → 降级(清洗后空≠声称空, 两路径同拦)
# (注: 三条均为不可救条目——本体空×2 + 非 dict)
R6RAW = '''{"applicable": true,
 "hypotheses": [{"hypothesis": "", "supporting": ["x"], "contradicting": [], "test_next": "t"},
                {"hypothesis": "  ", "supporting": [], "contradicting": [], "test_next": "t"},
                "纯字符串非dict"],
 "coverage_note": "", "summary": "", "confidence": 0.5, "ambiguity_note": ""}'''
r6, _ = run_skill(R6RAW)
check("R6 全清洗丢弃 → 强制降级", bool(r6.result.get("_degraded"))
      and "假设谱全空" in str(r6.result.get("_degraded")))

# R7 非法state读取(goal未声明)
sk7 = SkillFReason(adjacency=ADJ)
try:
    sk7._in(ThinkState(goal="x"), "goal")
    check("R7 未声明键 goal 读取被拒", False, "未抛 PermissionError")
except PermissionError:
    check("R7 未声明键 goal 读取被拒", True)

# R8 证据字段清洗: 单字符串包列表 / 非法类型丢整条
R8RAW = '''{"applicable": true,
 "hypotheses": [
  {"hypothesis": "H_str", "supporting": "单字符串证据", "contradicting": "单字符串反证", "test_next": "t"},
  {"hypothesis": "H_bad", "supporting": 42, "contradicting": [], "test_next": "t"},
  {"hypothesis": "H_mix", "supporting": ["a", "  ", "b"], "contradicting": [], "test_next": "t"}],
 "coverage_note": "", "summary": "", "confidence": 0.5, "ambiguity_note": ""}'''
r8, st8 = run_skill(R8RAW)
hyps = {h["hypothesis"]: h for h in st8.hypotheses}
check("R8① supporting单字符串自动包列表", hyps.get("H_str", {}).get("supporting") == ["单字符串证据"])
check("R8② contradicting单字符串自动包列表", hyps.get("H_str", {}).get("contradicting") == ["单字符串反证"])
check("R8③ 证据非法类型(supporting=42)丢整条", "H_bad" not in hyps)
check("R8④ 非法丢弃入note", "证据字段非法丢弃整条: 1条" in r8.result["ambiguity_note"])
check("R8⑤ 列表内空串/空白项清洗", hyps.get("H_mix", {}).get("supporting") == ["a", "b"])

# R9 审计落库
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_f_reason'").fetchone()[0]
con.close()
check("R9 审计落库(op=atomic_skill:skill_f_reason, 独立归属)", n >= 8, str(n))

# R10 邻接表契约
spec = ADJ["entries"]["skill_f_reason"]
check("R10① state_inputs/outputs与实现一致",
      spec.get("state_inputs") == ["goal_anchored", "sub_questions", "summary", "ref_material"]  # P-C4素材通道增键
      and spec.get("state_outputs") == ["hypotheses", "summary"])
check("R10② dfcp_link=无(段二裁决, 不预留占位)", spec.get("dfcp_link") == "无")
check("R10③ 菜单契约=terminate+__planned__仅两项",
      [o.get("skill") for o in spec.get("next_options", [])] == ["skill_terminate", "__planned__"])
check("R10④ 上游入边存在(scope_scan/source_intent直连, 非孤岛)",
      any(o.get("skill") == "skill_f_reason" for o in ADJ["entries"]["skill_scope_scan"]["next_options"])
      and any(o.get("skill") == "skill_f_reason" for o in ADJ["entries"]["skill_source_intent"]["next_options"]))
check("R10⑤ 恒False非is_exit(exit口径不误判)", spec.get("terminate_suggest", "").startswith("恒False"))

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_f_reason 七段定稿回归全过(分歧①-⑦闭环)")