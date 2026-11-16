# -*- coding: utf-8 -*-
"""
test_skill_scope_scan.py — skill_scope_scan 试点②回归(七段定稿闭环)

用户指定重点覆盖:
  子问题超限截断 / 降级状态禁止触发终止 / scan_confidence 独立写入不覆盖旧
  confidence / domains_touched 纯文本 / 新歧义识别
外加基类刚性框架复验(权限白名单/降级三路/审计落库)。
跑法: python cgraph/test_skill_scope_scan.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table
from cgraph.skill_scope_scan import SkillScopeScan, MAX_SUB_QUESTIONS

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


class MockLLM:
    def __init__(self, raw): self.raw = raw
    def chat(self, messages): return self.raw


GOOD_OPEN = '''{"sub_questions": [{"q": "F20五场景基线判定有哪些", "type": "factual"},
 {"q": "权重因子C/F/D各贡献多少", "type": "analytical"}],
 "key_parameters": ["beta_profile=baseline", "lambda_debt=0.5"],
 "domains_touched": ["记忆系统", "推演实验"], "summary": "F20基线体检拆解",
 "confidence": 0.85, "ambiguity_note": ""}'''

GOOD_CLOSED = '''{"sub_questions": [], "key_parameters": [], "domains_touched": [],
 "summary": "单点闭合", "confidence": 0.95, "ambiguity_note": ""}'''

CLOSED_LOWCONF = '''{"sub_questions": [], "key_parameters": [], "domains_touched": [],
 "summary": "闭合但没把握", "confidence": 0.7, "ambiguity_note": ""}'''

EXPLODE_12 = '''{"sub_questions": [''' + ",".join(
    '{"q": "子问题%d是关于什么", "type": "analytical"}' % i for i in range(12)) + '''],
 "key_parameters": ["k"], "domains_touched": ["d"], "summary": "爆炸",
 "confidence": 0.8, "ambiguity_note": ""}'''

NEW_AMBIGUOUS = '''{"sub_questions": [{"q": "对比两条路线", "type": "analytical"}],
 "key_parameters": [], "domains_touched": ["工程"], "summary": "路线对比",
 "confidence": 0.8, "ambiguity_note": "未指明对比维度(时延/准确率/维护成本)"}'''

print("== skill_scope_scan 试点回归(七段定稿) ==")

def run_skill(raw, anchor_conf=0.95):
    sk = SkillScopeScan(llm=MockLLM(raw), adjacency=ADJ)
    st = ThinkState(goal="原始目标", goal_anchored="已锚定目标文本",
                    flags={"confidence": anchor_conf, "sensitive": False})
    return sk.run(st), st

# --- 正常开放链 ---
r, st = run_skill(GOOD_OPEN)
check("开放链: sub_questions 2项带类型标注落 state",
      len(st.sub_questions) == 2 and st.sub_questions[0]["type"] == "factual")
check("开放链: domains_touched 纯文本列表(无任何id映射痕迹)",
      st and r.result["domains_touched"] == ["记忆系统", "推演实验"]
      and all("topic" not in str(x) for x in r.result["domains_touched"]))
check("开放链: 非闭合不终止(terminate=False)", r.terminate_suggest is False)

# --- 分歧④: scan_confidence 独立键, 不覆盖 flags.confidence ---
check("分歧④: flags.scan_confidence=0.85 独立写入", abs(st.flags.get("scan_confidence", 0) - 0.85) < 1e-9)
check("分歧④: flags.confidence 锚定值0.95 未被覆盖", abs(st.flags.get("confidence", 0) - 0.95) < 1e-9)

# --- 闭合链终止 ---
r2, st2 = run_skill(GOOD_CLOSED)
check("闭合链: 空子问题+0.95+非降级 → terminate=True", r2.terminate_suggest is True)

r2b, st2b = run_skill(CLOSED_LOWCONF)
check("闭合但低置信(0.7) → 不终止(阈值兜底)", r2b.terminate_suggest is False)

# --- 风险1: 爆炸截断 ---
r3, st3 = run_skill(EXPLODE_12)
check("风险1: 12项截断至%d项" % MAX_SUB_QUESTIONS, len(st3.sub_questions) == MAX_SUB_QUESTIONS)
check("风险1: 截断写入 ambiguity_note 说明", "截断" in r3.result["ambiguity_note"] and "12" in r3.result["ambiguity_note"])
check("截断后仍开放链不终止", r3.terminate_suggest is False)

# --- 新歧义识别 ---
r4, st4 = run_skill(NEW_AMBIGUOUS)
check("新歧义识别: ambiguity_note 记录且不终止", "对比维度" in r4.result["ambiguity_note"] and r4.terminate_suggest is False)

# --- 风险2: 降级态永不终止 ---
r5, st5 = run_skill("不是json")
check("风险2: JSON失败降级 → _degraded 且不终止", bool(r5.result.get("_degraded")) and r5.terminate_suggest is False)
r6, st6 = run_skill(None)
check("风险2: llm空返回降级 → 不终止", bool(r6.result.get("_degraded")) and r6.terminate_suggest is False)
# 降级态 result_fields 补空, sub_questions 为空也不触发终止
r7, st7 = run_skill('{"sub_questions": "非列表字符串"}')
check("风险2: sub_questions 非列表 → 降级不终止", bool(r7.result.get("_degraded")) and r7.terminate_suggest is False)

# --- 权限白名单(基类复验) ---
sk = SkillScopeScan(llm=MockLLM(GOOD_OPEN), adjacency=ADJ)
try:
    sk._in(ThinkState(goal="x"), "goal")            # goal 不在 state_inputs(读的是 goal_anchored)
    check("未声明键 goal 读取被拒", False, "未抛 PermissionError")
except PermissionError:
    check("未声明键 goal 读取被拒", True)

# --- 菜单取自邻接表 ---
check("next_options 含 skill_terminate 与 skill_f_reason 实边(模型不可生成)",
      # f_reason 登记后上游 __planned__ 换成实边(2026-11-16), 菜单随邻接表演进
      {o.get("skill") for o in r.next_options} >= {"skill_terminate", "skill_f_reason"}
      and "skill_source_intent" in {o.get("skill") for o in r.next_options})  # 分歧④: 试点③登记后多项化

# --- 审计落库 ---
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_scope_scan'").fetchone()[0]
con.close()
check("审计落库(op=atomic_skill:skill_scope_scan, 独立归属)", n >= 7, str(n))

# --- 邻接表七段契约 ---
spec = ADJ["entries"]["skill_scope_scan"]
check("邻接表条目与本实现键名一致(state_outputs 含 flags.scan_confidence)",
      "flags.scan_confidence" in spec.get("state_outputs", [])
      and spec.get("dfcp_link") == "无")

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_scope_scan 试点回归全过(七段定稿闭环)")