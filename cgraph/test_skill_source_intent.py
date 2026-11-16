# -*- coding: utf-8 -*-
"""
test_skill_source_intent.py — skill_source_intent 试点③回归(七段定稿闭环)

用户指定重点覆盖:
  有外部信源正常产出 / 纯计算任务 applicable=false 非降级 / 推测超限截断
  is_inference 标记 / JSON失败与LLM空返回进 _degraded(与 applicable=false 区分)
  / terminate_suggest 恒 False / state 读写白名单校验
外加: 条目爆炸上限截断 / applicable=false 反向清空 source_intents /
菜单取自邻接表(scope_scan 三项化) / 审计落库 / 邻接表契约。
跑法: python cgraph/test_skill_source_intent.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table
from cgraph.skill_source_intent import SkillSourceIntent, MAX_SOURCES

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

ADJ = load_table(os.path.join(HERE, "data", "atomic_skills_adjacency.json"))


class MockLLM:
    def __init__(self, raw): self.raw = raw
    def chat(self, messages): return self.raw


GOOD_WITH_SOURCE = '''{"applicable": true,
 "source_intents": [
  {"source": "供应商A的报价单", "actor": "供应商A销售部", "timing_motive": "招标前一周放出低价锚点, 意图锁定入围", "is_inference": false},
  {"source": "竞对官网公告", "actor": "竞对市场部", "timing_motive": "我方发布会前一天发布对标稿, 抢占叙事", "is_inference": true}],
 "summary": "两处信息源均带时机性动机", "confidence": 0.8, "ambiguity_note": ""}'''

NOT_APPLICABLE = '''{"applicable": false, "source_intents": [],
 "summary": "纯计算任务无外部信息源", "confidence": 0.9, "ambiguity_note": ""}'''

# 3 推测 + 0 显式 → 截断至 0 推测(不得超过显式线索数)
ALL_INFER = '''{"applicable": true,
 "source_intents": [
  {"source": "源1", "actor": "甲", "timing_motive": "推测动机1", "is_inference": true},
  {"source": "源2", "actor": "乙", "timing_motive": "推测动机2", "is_inference": true},
  {"source": "源3", "actor": "丙", "timing_motive": "推测动机3", "is_inference": true}],
 "summary": "纯脑补", "confidence": 0.5, "ambiguity_note": ""}'''

# 1 显式 + 3 推测 → 推测截断至 1
OVER_INFER = '''{"applicable": true,
 "source_intents": [
  {"source": "白皮书", "actor": "官方", "timing_motive": "版本发布配套", "is_inference": false},
  {"source": "论坛帖", "actor": "匿名", "timing_motive": "推测", "is_inference": true},
  {"source": "博客", "actor": "博主", "timing_motive": "推测", "is_inference": true},
  {"source": "社群消息", "actor": "群友", "timing_motive": "推测", "is_inference": true}],
 "summary": "混合", "confidence": 0.6, "ambiguity_note": ""}'''

EXPLODE_SOURCES = '''{"applicable": true, "source_intents": [''' + ",".join(
    '{"source": "信源%d", "actor": "主体%d", "timing_motive": "m%d", "is_inference": false}' % (i, i, i)
    for i in range(10)) + '''],
 "summary": "爆炸", "confidence": 0.7, "ambiguity_note": ""}'''

# applicable=false 但 LLM 还是吐了条目 → 反向强制清空
APPLICABLE_FALSE_WITH_ITEMS = '''{"applicable": false,
 "source_intents": [{"source": "不该出现的源", "actor": "x", "timing_motive": "y", "is_inference": false}],
 "summary": "", "confidence": 0.9, "ambiguity_note": ""}'''

print("== skill_source_intent 试点③回归(七段定稿) ==")

def run_skill(raw):
    sk = SkillSourceIntent(llm=MockLLM(raw), adjacency=ADJ)
    st = ThinkState(goal="g", goal_anchored="评估报价与竞对动向",
                    sub_questions=[{"q": "报价单可信吗", "type": "factual"},
                                   {"q": "竞对为何此时发声", "type": "analytical"}],
                    summary="扫描摘要", flags={"confidence": 0.9, "sensitive": False})
    return sk.run(st), st

# --- 正常产出 ---
r, st = run_skill(GOOD_WITH_SOURCE)
check("有信源: 2条画像落 state(含 actor/timing_motive/is_inference)",
      len(st.source_intents) == 2 and all(
          {"source", "actor", "timing_motive", "is_inference"} <= set(x) for x in st.source_intents))
check("有信源: 项内无 trust 字段(分歧①裁决移出)",
      all("trust" not in x for x in st.source_intents))
check("有信源: is_inference 标记保留(false/true 各一)",
      [x["is_inference"] for x in st.source_intents] == [False, True])
check("有信源: 正常产出不终止(恒False)", r.terminate_suggest is False)

# --- 分歧②: applicable=false 语义不适用(非降级) ---
r2, st2 = run_skill(NOT_APPLICABLE)
check("纯计算: applicable=false 落 result", r2.result["applicable"] is False)
check("纯计算: 空 source_intents + 非 _degraded(严格区分语义不适用与执行异常)",
      st2.source_intents == [] and not r2.result.get("_degraded"))
check("纯计算: 默认摘要生成, 非降级", "不适用" in r2.result["summary"] or "无外部信息源" in r2.result["summary"])

# --- 风险1: 推测超限截断 ---
r3, st3 = run_skill(ALL_INFER)
check("风险1: 纯推测3条全截断(显式线索0条)", len(st3.source_intents) == 0)
check("风险1: 超限写入 ambiguity_note", "推测条目超限截断" in r3.result["ambiguity_note"])

r4, st4 = run_skill(OVER_INFER)
n_infer = sum(1 for x in st4.source_intents if x["is_inference"])
n_explicit = sum(1 for x in st4.source_intents if not x["is_inference"])
check("风险1: 推测数(1)不超过显式数(1)", n_infer <= n_explicit and n_infer == 1 and n_explicit == 1)
check("风险1: 截断说明写入 note", "推测条目超限截断" in r4.result["ambiguity_note"])

# --- 风险1辅: 条目爆炸上限 ---
r5, st5 = run_skill(EXPLODE_SOURCES)
check("爆炸: 10条截断至%d条" % MAX_SOURCES, len(st5.source_intents) == MAX_SOURCES)
check("爆炸: 截断说明写入 note", "超上限截断" in r5.result["ambiguity_note"] and "10" in r5.result["ambiguity_note"])

# --- applicable=false 反向清空 ---
r6, st6 = run_skill(APPLICABLE_FALSE_WITH_ITEMS)
check("不适用反向强制: applicable=false 时条目清空",
      st6.source_intents == [] and r6.result["applicable"] is False and not r6.result.get("_degraded"))

# --- 风险2/降级区分: _degraded 只留给解析/LLM 异常 ---
r7, st7 = run_skill("不是json")
check("降级: JSON失败 → _degraded 且不终止",
      bool(r7.result.get("_degraded")) and r7.terminate_suggest is False)
r8, st8 = run_skill(None)
check("降级: llm空返回 → _degraded", bool(r8.result.get("_degraded")))
r9, st9 = run_skill('{"applicable": true, "source_intents": "非列表字符串"}')
check("降级: source_intents 非列表 → _degraded(与 applicable=false 是两回事)",
      bool(r9.result.get("_degraded")))
r10, st10 = run_skill('{"applicable": true, "source_intents": [], "summary": "", "confidence": 0.5, "ambiguity_note": ""}')
check("降级: 声称适用但画像全空 → _degraded", bool(r10.result.get("_degraded")))

# --- 终止恒 False(七段定稿) ---
for rr, label in ((r, "正常"), (r2, "不适用"), (r7, "降级")):
    check("终止恒False(%s态)" % label, rr.terminate_suggest is False)

# --- 权限白名单(基类复验) ---
sk = SkillSourceIntent(llm=MockLLM(GOOD_WITH_SOURCE), adjacency=ADJ)
try:
    sk._in(ThinkState(goal="x"), "goal")            # goal 不在 state_inputs
    check("未声明键 goal 读取被拒", False, "未抛 PermissionError")
except PermissionError:
    check("未声明键 goal 读取被拒", True)

# --- 菜单取自邻接表 ---
check("next_options = terminate + __planned__(禁自环, 模型不可生成)",
      {o.get("skill") for o in r.next_options} == {"skill_terminate", "__planned__"})
check("scope_scan 菜单已三项化(分歧④: terminate+source_intent+__planned__)",
      {o.get("skill") for o in ADJ["entries"]["skill_scope_scan"]["next_options"]}
      == {"skill_terminate", "skill_source_intent", "__planned__"})

# --- 审计落库 ---
con = sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
n = con.execute("SELECT COUNT(*) FROM audit_topic_log WHERE op='atomic_skill:skill_source_intent'").fetchone()[0]
con.close()
check("审计落库(op=atomic_skill:skill_source_intent, 独立归属)", n >= 7, str(n))

# --- 邻接表七段契约 ---
spec = ADJ["entries"]["skill_source_intent"]
check("邻接表契约: state_inputs/outputs/dfcp_link=无 与实现一致",
      spec.get("state_inputs") == ["goal_anchored", "sub_questions", "summary"]
      and spec.get("state_outputs") == ["source_intents", "summary"]
      and spec.get("dfcp_link") == "无")

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("skill_source_intent 试点③回归全过(七段定稿闭环)")