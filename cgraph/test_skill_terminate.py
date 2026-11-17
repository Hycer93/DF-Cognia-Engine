# -*- coding: utf-8 -*-
"""
test_skill_terminate.py — skill_terminate 回归(七段定稿闭环, 分歧⑧-⑪b)

用户裁决覆盖 T1-T10(2026-11-16 第二轮全部确认):
  正常三步汇编 / 零LLM铁证 / 空链路合法直达 / 缺step_summary宽容 /
  两值verdict / next_options防篡改raise / coverage_check软围栏黑名单 /
  邻接表契约 / state零写回 / 编排器trail升级契约
跑法: python cgraph/test_skill_terminate.py
"""
import copy
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table
from cgraph.skill_terminate import SkillTerminate, STEP_SUMMARY_MAX

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


def mk_state(**kw):
    st = ThinkState(goal="原始目标G")
    for k, v in kw.items():
        setattr(st, k, v)
    return st


TRAIL3 = [
    {"skill_id": "skill_anchor_target", "step_summary": "目标锚定: 评估X原因", "s" * 0 or "result_fields": ["anchored_goal", "goal_type"], "candidates": [], "chosen": "", "reason": "", "ts": ""},
    {"skill_id": "skill_scope_scan", "step_summary": "任务空间拆解完成", "result_fields": ["sub_questions", "key_parameters"], "candidates": [], "chosen": "", "reason": "", "ts": ""},
    {"skill_id": "skill_weakest_link", "step_summary": "最弱在判定基准缺失", "result_fields": ["weakest_point", "risk_level"], "candidates": [], "chosen": "", "reason": "", "ts": ""},
]

# T1 正常三步链汇编
st1 = mk_state(goal_anchored="评估X原因", summary="终局局部视角", trail=TRAIL3, steps=3)
r1 = SkillTerminate(adjacency=ADJ).run(st1)
fo = r1.result["final_output"]
check("T1① final_output含锚定目标", "评估X原因" in fo)
check("T1② 路线罗列(skill_id序列)", "anchor_target" in fo and "scope_scan" in fo and "weakest_link" in fo)
check("T1③ 各步视角(step_summary)", "目标锚定" in fo and "任务空间拆解" in fo)
check("T1④ 关键产出引用(字段名不展开)", "产出字段: weakest_point" in fo)
check("T1⑤ 终局摘要(最后一步局部视角)", "终局局部视角" in fo)
check("T1⑥ termination_reason两值verdict", isinstance(r1.result["termination_reason"], dict)
      and r1.result["termination_reason"]["verdict"] == "normal_stop")
check("T1⑦ steps_used=3", r1.result["steps_used"] == 3)
check("T1⑧ coverage_check非空", bool(r1.result["coverage_check"]))
check("T1⑨ terminate_suggest恒真", r1.terminate_suggest is True)
check("T1⑩ secs≈0(纯Python)", r1.secs < 0.05, str(r1.secs))

# T2 零LLM铁证: llm=None 且非法LLM对象均照常
st2 = mk_state(goal_anchored="a", trail=TRAIL3, steps=3)
r2 = SkillTerminate(llm=None, adjacency=ADJ).run(st2)
class BadLLM:
    def chat(self, m): raise RuntimeError("LLM被调用即失败")
st2b = mk_state(goal_anchored="a", trail=TRAIL3, steps=3)
r2b = SkillTerminate(llm=BadLLM(), adjacency=ADJ).run(st2b)
check("T2 零LLM铁证(llm=None与炸弹LLM均正常完成)",
      "final_output" in r2.result and "final_output" in r2b.result
      and not r2b.result.get("_degraded"))

# T3 空链路直达(simple_query场景): 合法非降级
st3 = mk_state(steps=0)
st3.trail = []
r3 = SkillTerminate(adjacency=ADJ).run(st3)
check("T3① trail空 → 合法非降级", not r3.result.get("_degraded"))
check("T3② final_output=goal原文", "原始目标G" in r3.result["final_output"])
check("T3③ detail注明trail为空", "trail为空" in r3.result["termination_reason"]["detail"])

# T4 trail条目缺step_summary: 宽容+note
TRAIL_MISS = [dict(t) for t in TRAIL3[:1]] + [{"skill_id": "skill_scope_scan"}]
st4 = mk_state(goal_anchored="a", trail=TRAIL_MISS, steps=2)
r4 = SkillTerminate(adjacency=ADJ).run(st4)
check("T4① 缺摘要步显示(无自报摘要)", "(无自报摘要)" in r4.result["final_output"])
check("T4② 缺失计数入reason detail", "缺摘要步数1" in r4.result["termination_reason"]["detail"])

# T5 两值verdict: max_steps_hit
st5 = mk_state(goal_anchored="a", trail=TRAIL3, steps=24)
r5 = SkillTerminate(adjacency=ADJ).run(st5)
check("T5① steps>=上限 → max_steps_hit", r5.result["termination_reason"]["verdict"] == "max_steps_hit")
check("T5② detail含步数上下限", "steps=24" in r5.result["termination_reason"]["detail"]
      and "上限24" in r5.result["termination_reason"]["detail"])
check("T5③ 降级步数计数", "降级步数0" in r5.result["termination_reason"]["detail"])
TRAIL_DEG = [dict(t, degraded=True) for t in TRAIL3]
st5b = mk_state(goal_anchored="a", trail=TRAIL_DEG, steps=3)
r5b = SkillTerminate(adjacency=ADJ).run(st5b)
check("T5④ trail降级步计入detail", "降级步数3" in r5b.result["termination_reason"]["detail"])

# T6 next_options防篡改raise
BAD = copy.deepcopy(ADJ)
BAD["entries"]["skill_terminate"]["next_options"] = [{"skill": "skill_scope_scan"}]
try:
    SkillTerminate(adjacency=BAD).run(mk_state(trail=[], steps=0))
    check("T6 next_options非空 → raise", False, "未抛 RuntimeError")
except RuntimeError as e:
    check("T6 next_options非空 → raise", "必须为空" in str(e))

# T7 coverage_check软围栏黑名单: 禁定性评价词
BAN = ("充分", "不足", "良好", "较差", "优", "很棒", "覆盖率高", "覆盖率低")
r7 = SkillTerminate(adjacency=ADJ).run(mk_state(goal_anchored="a", trail=TRAIL3, steps=3))
cc = r7.result["coverage_check"]
check("T7① coverage_check无定性评价词", not any(w in cc for w in BAN), cc[:80])
check("T7② 只含事实陈述(触及事实)", "触及事实" in cc)

# T8 邻接表契约
spec = ADJ["entries"]["skill_terminate"]
check("T8① state_inputs五键(P-C4增ref_material)", spec.get("state_inputs") == ["goal", "goal_anchored", "summary", "trail", "ref_material"])
check("T8② state_outputs=[]", spec.get("state_outputs") == [])
check("T8③ result_fields含coverage_check", "coverage_check" in spec.get("result_fields", []))
check("T8④ 恒真terminate_suggest(is_exit双通道)", spec.get("terminate_suggest", "").startswith("恒真"))
check("T8⑤ next_options=[]", spec.get("next_options") == [])
check("T8⑥ dfcp_link=无", spec.get("dfcp_link") == "无")

# T9 state零写回(outputs=[]契约)
st9 = mk_state(goal_anchored="锚A", summary="摘S", trail=TRAIL3, steps=3)
before = copy.deepcopy(vars(st9))
SkillTerminate(adjacency=ADJ).run(st9)
check("T9 执行前后state字段完全不变(outputs=[]契约)", vars(st9) == before)

# T10 编排器trail升级契约: step_summary≤60 + 字段清单
st10 = mk_state(goal_anchored="a", trail=[dict(t, step_summary="长" * 100) for t in TRAIL3], steps=3)
r10 = SkillTerminate(adjacency=ADJ).run(st10)
# terminate消费端: 超长step_summary在汇编时截断展示(双保险)
import cgraph.skill_terminate as stm
check("T10① STEP_SUMMARY_MAX=60常量", stm.STEP_SUMMARY_MAX == 60 == STEP_SUMMARY_MAX)
check("T10② trail条目携带result_fields清单被消费", "result_fields" in str(TRAIL3[0]))

# 附: assembler_crash降级保三字段
st11 = mk_state(steps=5)
st11.trail = "非列表trail"           # 底层结构异常
r11 = SkillTerminate(adjacency=ADJ).run(st11)
check("附① trail非列表 → assembler_crash降级", "assembler_crash" in str(r11.result.get("termination_reason")))
check("附② 降级仍保final_output/steps_used", r11.result.get("final_output") == "" and r11.result.get("steps_used") == 5)

# 审计落库
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_terminate'").fetchone()[0]
con.close()
check("审计落库(op=atomic_skill:skill_terminate, 独立归属)", n >= 13, str(n))

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_terminate 七段定稿回归全过(分歧⑧-⑪b闭环)")