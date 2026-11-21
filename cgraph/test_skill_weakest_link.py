# -*- coding: utf-8 -*-
"""
test_skill_weakest_link.py — skill_weakest_link 试点④回归(七段定稿闭环)

用户指定重点覆盖:
  正常产出 weakest_point 单点 + others 次级截断 / applicable=false 非降级 /
  修复建议文本被解析层过滤+note / score 与 min 不一致代码修正+note /
  dimension_scores 空或非法触发降级 / terminate 恒 False 三态 /
  state 读写白名单 / 菜单取自邻接表 / 审计落库
跑法: python cgraph/test_skill_weakest_link.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table
from cgraph.skill_weakest_link import SkillWeakestLink, MAX_OTHERS, map_risk_level

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


class MockLLM:
    def __init__(self, raw): self.raw = raw
    def chat(self, messages): return self.raw


GOOD = '''{"applicable": true,
 "weakest_point": {"location": "夜间回归守卫", "dimension": "可复现性", "score": 0.15,
                   "evidence": "基线快照依赖人工消融更新, 数值浮动误判风险"},
 "others": [{"location": "ID迁移兼容层", "dimension": "隐性bug", "score": 0.5, "evidence": "存量短id残留"},
            {"location": "E7豁免链", "dimension": "绕过风险", "score": 0.7, "evidence": "user-retain前缀可被伪造"}],
 "dimension_scores": {"可复现性": 0.15, "隐性bug": 0.5, "绕过风险": 0.7},
 "summary": "最脆弱在夜间守卫基线陈旧", "confidence": 0.8, "ambiguity_note": ""}'''

NOT_APPLICABLE = '''{"applicable": false, "weakest_point": null, "others": [],
 "dimension_scores": {}, "summary": "纯事实查询无系统面", "confidence": 0.9, "ambiguity_note": ""}'''

# 修复建议混在 evidence/summary 文本里 → 过滤+note(分数口径: 越低越脆弱)
REMEDY = '''{"applicable": true,
 "weakest_point": {"location": "迁移脚本", "dimension": "数据安全", "score": 0.1,
                   "evidence": "无回滚校验, 建议尽快修复该脚本并加双写保护"},
 "others": [],
 "dimension_scores": {"数据安全": 0.1},
 "summary": "最弱在迁移脚本", "confidence": 0.7, "ambiguity_note": ""}'''

# score 与 min 失配 → 修正+note(健壮分口径: min=最弱)
MISMATCH = '''{"applicable": true,
 "weakest_point": {"location": "守卫", "dimension": "可复现性", "score": 0.05, "evidence": "依据"},
 "others": [],
 "dimension_scores": {"可复现性": 0.7, "覆盖": 0.9},
 "summary": "失配案例", "confidence": 0.6, "ambiguity_note": ""}'''

# dimension_scores 空 → 降级
EMPTY_SCORES = '''{"applicable": true,
 "weakest_point": {"location": "x", "dimension": "y", "score": 0.5, "evidence": ""},
 "others": [], "dimension_scores": {}, "summary": "空分", "confidence": 0.5, "ambiguity_note": ""}'''

# 全非法数值(字符串分) → 清洗后空 → 降级
BAD_SCORES = '''{"applicable": true,
 "weakest_point": {"location": "x", "dimension": "y", "score": 0.5, "evidence": ""},
 "others": [], "dimension_scores": {"a": "高", "b": None}, "summary": "坏分", "confidence": 0.5, "ambiguity_note": ""}'''

# others 爆炸 6 项 → 截 4(健壮分口径: 主弱点最低分)
EXPLODE_OTHERS = '''{"applicable": true,
 "weakest_point": {"location": "主弱点", "dimension": "d0", "score": 0.2, "evidence": ""},
 "others": [''' + ",".join(
    '{"location": "次级%d", "dimension": "d%d", "score": 0.6, "evidence": ""}' % (i, i)
    for i in range(6)) + '''],
 "dimension_scores": {"d0": 0.2}, "summary": "爆炸", "confidence": 0.6, "ambiguity_note": ""}'''

# weakest_point 缺失 → min 维度构造+note(健壮分: 容量0.75最低=最弱)
NO_WP = '''{"applicable": true, "weakest_point": null, "others": [],
 "dimension_scores": {"容量": 0.75, "延迟": 0.95}, "summary": "缺主点", "confidence": 0.6, "ambiguity_note": ""}'''

print("== skill_weakest_link 试点④回归(七段定稿) ==")

def run_skill(raw):
    sk = SkillWeakestLink(llm=MockLLM(raw), adjacency=ADJ)
    st = ThinkState(goal="g", goal_anchored="评估记忆系统夜间链路健壮性",
                    sub_questions=[{"q": "哪一环最脆弱", "type": "analytical"}],
                    summary="扫描摘要", flags={"confidence": 0.9, "sensitive": False})
    return sk.run(st), st

# --- 正常产出 ---
r, st = run_skill(GOOD)
wp = st.weakest_link
check("正常: weakest_point 单点落 state(含 location/dimension/score/evidence)",
      isinstance(wp, dict) and {"location", "dimension", "score", "evidence"} <= set(wp)
      and wp["location"] == "夜间回归守卫")
check("正常: others 2项次级列表", len(r.result["others"]) == 2)
check("分歧①: risk_level 由 Python min 映射(0.15→critical)",
      r.result["risk_level"] == "critical" and isinstance(r.result["risk_level"], str))
check("正常: 不终止(恒False)", r.terminate_suggest is False)

# --- 分歧②: 阈值表独立验证(健壮分口径: 越低风险越高) ---
check("分歧②: 阈值表五档映射 0.15/0.3/0.5/0.7/0.85",
      [map_risk_level(x) for x in (0.15, 0.3, 0.5, 0.7, 0.85)]
      == ["critical", "high", "medium", "low", "negligible"])

# --- 风险2: 处置建议过滤 ---
r2, st2 = run_skill(REMEDY)
check("风险2: 修复建议文本被剔除(evidence 无建议残留)",
      "建议" not in st2.weakest_link["evidence"] and "修复" not in st2.weakest_link["evidence"])
check("风险2: 过滤写入 ambiguity_note", "处置建议已被过滤" in r2.result["ambiguity_note"])

# --- 风险1: 评分自洽修正 ---
r3, st3 = run_skill(MISMATCH)
check("风险1: score(0.05) 失配 min(0.7) → 修正为 0.7",
      abs(st3.weakest_link["score"] - 0.7) < 1e-9)
check("风险1: 修正写入 note", "评分自洽修正" in r3.result["ambiguity_note"])
check("风险1: 修正后 risk_level=low(0.7 健壮分档)", r3.result["risk_level"] == "low")

# --- 降级: dimension_scores 空/全非法 ---
r4, st4 = run_skill(EMPTY_SCORES)
check("降级: dimension_scores 空 → _degraded", bool(r4.result.get("_degraded")))
r5, st5 = run_skill(BAD_SCORES)
check("降级: 全非法数值清洗后空 → _degraded", bool(r5.result.get("_degraded")))

# --- 分歧③: others 爆炸截断 ---
r6, st6 = run_skill(EXPLODE_OTHERS)
check("分歧③: others 6项截断至%d项" % MAX_OTHERS, len(r6.result["others"]) == MAX_OTHERS)
check("分歧③: 截断写入 note", "超上限截断" in r6.result["ambiguity_note"] and "6" in r6.result["ambiguity_note"])

# --- weakest_point 缺失: min 维度构造 ---
r7, st7 = run_skill(NO_WP)
check("主点缺失: min 维度(容量0.75)构造补齐", st7.weakest_link["dimension"] == "容量"
      and abs(st7.weakest_link["score"] - 0.75) < 1e-9)
check("主点缺失: 构造写入 note", "已由 min 维度构造" in r7.result["ambiguity_note"])

# --- 分歧④a: applicable=false 非降级 ---
r8, st8 = run_skill(NOT_APPLICABLE)
check("不适用: applicable=false + 空 weakest_link + 非 _degraded",
      r8.result["applicable"] is False and st8.weakest_link in (None, "")
      and not r8.result.get("_degraded"))
check("不适用: 正常路径 note 记录", "不适用" in r8.result["ambiguity_note"])
check("不适用: risk_level 空串(无评分)", r8.result["risk_level"] == "")

# --- JSON 失败 / llm 空 → 降级 ---
r9, st9 = run_skill("不是json")
check("降级: JSON失败 → _degraded", bool(r9.result.get("_degraded")))
r10, st10 = run_skill(None)
check("降级: llm空返回 → _degraded", bool(r10.result.get("_degraded")))

# --- 终止恒 False 三态 ---
for rr, label in ((r, "正常"), (r8, "不适用"), (r9, "降级")):
    check("终止恒False(%s态)" % label, rr.terminate_suggest is False)

# --- 权限白名单(基类复验) ---
sk = SkillWeakestLink(llm=MockLLM(GOOD), adjacency=ADJ)
try:
    sk._in(ThinkState(goal="x"), "goal")
    check("未声明键 goal 读取被拒", False, "未抛 PermissionError")
except PermissionError:
    check("未声明键 goal 读取被拒", True)

# --- 菜单取自邻接表 ---
check("next_options = terminate + __planned__ + f_reason(模型不可生成, P-C3路由偏好增边)",
      {o.get("skill") for o in r.next_options} == {"skill_terminate", "__planned__", "skill_f_reason"})
check("上游登记: scope_scan 与 source_intent menu 均含 weakest_link(无孤岛)",
      "skill_weakest_link" in {o.get("skill") for o in ADJ["entries"]["skill_scope_scan"]["next_options"]}
      and "skill_weakest_link" in {o.get("skill") for o in ADJ["entries"]["skill_source_intent"]["next_options"]})

# --- 审计落库 ---
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"), timeout=5)
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_weakest_link'").fetchone()[0]
con.close()
check("审计落库(op=atomic_skill:skill_weakest_link, 独立归属)", n >= 7, str(n))

# --- 邻接表七段契约 ---
spec = ADJ["entries"]["skill_weakest_link"]
check("邻接表契约: state_inputs/outputs/dfcp_link=无 与实现一致",
      spec.get("state_inputs") == ["goal_anchored", "sub_questions", "summary", "ref_material"]  # P-C4素材通道增键
      and spec.get("state_outputs") == ["weakest_link", "summary"]
      and spec.get("dfcp_link") == "无")

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_weakest_link 试点④回归全过(七段定稿闭环)")