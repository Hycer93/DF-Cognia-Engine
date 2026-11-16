# -*- coding: utf-8 -*-
"""
test_skill_anchor_target.py — skill_anchor_target 试点回归(单独回归测试, 七段模板闭环最后一步)

不依赖真实 LLM 后端: 用 MockLLM 注入受控输出, 验证刚性框架与七段共识。
断言面:
  ① 权限控制: 读未声明 state 键 → PermissionError
  ② schema 强校验: result_fields 缺一补空, 脏枚举→unknown_ambiguous
  ③ 降级三路: JSON 解析失败 / anchored_goal 空 / llm 空返回
  ④ 终止判定: simple_query+无歧义+conf>=0.9 → True; 低置信强制继续(风险1兜底);
     歧义存在不终止; 降级态永不终止
  ⑤ state 写回白名单: goal_anchored/flags.confidence/flags.sensitive 正确落位
  ⑥ next_options 取自邻接表(模型不可生成): 两项且含 gain/cost
  ⑦ 审计轨迹落库(audit_topic_log op=atomic_skill:skill_anchor_target)
跑法: python cgraph/test_skill_anchor_target.py
"""
import io
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import BaseAtomicSkill, ThinkState, assert_loadable, load_table
from cgraph.skill_anchor_target import SkillAnchorTarget, GOAL_TYPES

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


class MockLLM:
    """受控 LLM 替身: 返回预置原文"""
    def __init__(self, raw): self.raw = raw
    def chat(self, messages): return self.raw


GOOD_SIMPLE = '''```json
{"anchored_goal": "查询当前 F20 守卫基线的五场景判定结果", "goal_type": "simple_query",
 "ambiguity_note": "", "summary": "查F20基线判定", "confidence": 0.95, "sensitive": false}
```'''

GOOD_LOWCONF = '''{"anchored_goal": "分析记忆系统最近一周的 hold 池流动趋势", "goal_type": "analysis",
 "ambiguity_note": "", "summary": "分析hold池趋势", "confidence": 0.6, "sensitive": false}'''

GOOD_AMBIGUOUS = '''{"anchored_goal": "优化检索排序", "goal_type": "unknown_ambiguous",
 "ambiguity_note": "未指明优化目标(准确率/时延/成本)与作用层(F10/F20/FTS)", "summary": "目标歧义",
 "confidence": 0.4, "sensitive": false}'''

DIRTY = '''{"anchored_goal": "复核 E7 分流逻辑", "goal_type": "xxx非法枚举", "ambiguity_note": "",
 "summary": "复核E7", "confidence": 1.7, "sensitive": "yes"}'''

print("== skill_anchor_target 试点回归(七段共识) ==")

def run_skill(raw, goal="测试目标文本"):
    sk = SkillAnchorTarget(llm=MockLLM(raw), adjacency=ADJ)
    st = ThinkState(goal=goal)
    return sk.run(st), st

# --- ① 权限控制 ---
sk = SkillAnchorTarget(llm=MockLLM(GOOD_SIMPLE), adjacency=ADJ)
try:
    sk._in(ThinkState(goal="x"), "trail")          # trail 不在 state_inputs
    check("未声明 state 键读取被拒", False, "未抛 PermissionError")
except PermissionError:
    check("未声明 state 键读取被拒", True)

# --- ②③④ 正常/低置信/歧义/脏数据/降级 ---
r, st = run_skill(GOOD_SIMPLE)
check("正常链: anchored_goal 落 state", st.goal_anchored == "查询当前 F20 守卫基线的五场景判定结果")
check("正常链: flags.confidence 写回", abs(st.flags.get("confidence", 0) - 0.95) < 1e-9)
check("正常链: simple_query+0.95+无歧义 → terminate=True",
      r.terminate_suggest is True and r.result["goal_type"] == "simple_query")
check("next_options 取自邻接表(2项含gain/cost)",
      len(r.next_options) == 2 and all(o.get("gain") and o.get("cost") for o in r.next_options))

r2, st2 = run_skill(GOOD_LOWCONF)
check("风险1兜底: 低置信(0.6)强制继续思考(terminate=False)",
      r2.terminate_suggest is False and r2.result["goal_type"] == "analysis")

r3, st3 = run_skill(GOOD_AMBIGUOUS)
check("风险2兜底: 歧义存在 → 不终止且 ambiguity_note 强制记录",
      r3.terminate_suggest is False and "未指明" in r3.result["ambiguity_note"])

r4, st4 = run_skill(DIRTY)
check("脏枚举→unknown_ambiguous; confidence 1.7 夹取 1.0; sensitive 'yes'严格清洗→False(只认布尔true)",
      r4.result["goal_type"] == "unknown_ambiguous"
      and r4.result["confidence"] == 1.0 and r4.result["sensitive"] is False)
check("脏数据不终止(unknown_ambiguous 永不 simple_query 路径)", r4.terminate_suggest is False)

r5, st5 = run_skill("这不是JSON格式的输出")
check("降级①: JSON解析失败 → _degraded 且 result_fields 补空",
      r5.result.get("_degraded") and r5.result.get("anchored_goal") == "")
check("降级态永不终止", r5.terminate_suggest is False)

r6, st6 = run_skill(None)
check("降级②: llm 空返回 → _degraded", bool(r6.result.get("_degraded")))

r7, st7 = run_skill('{"goal_type":"simple_query","confidence":0.99}')
check("降级③: anchored_goal 空 → 降级不透传半成品", bool(r7.result.get("_degraded")))

# --- ⑥ state_outputs 白名单(trail 未被技能污染) ---
check("state 写回白名单: 技能未写 trail/steps", st.goal_anchored != "" and len(st.trail) == 0)

# --- ⑦ 审计落库 ---
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_anchor_target'").fetchone()[0]
con.close()
check("审计轨迹落库(audit_topic_log 独立归属, 不进E7晨报)", n >= 7, str(n))

# --- 邻接表契约 ---
spec = ADJ["entries"]["skill_anchor_target"]
check("邻接表七段字段全声明(role/inputs/outputs/result/menu/terminate/dfcp)",
      all(k in spec for k in ("role", "state_inputs", "state_outputs",
                              "result_fields", "next_options", "terminate_suggest", "dfcp_link")))

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_anchor_target 试点回归全过(七段共识闭环)")