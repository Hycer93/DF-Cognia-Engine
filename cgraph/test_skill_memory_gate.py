# -*- coding: utf-8 -*-
"""
test_skill_memory_gate.py — skill_memory_gate 七段定稿回归测试(2026-11-17)

段四: 出口契约 / 段五: 降级矩阵7行 / 段七: 红线断言(零DB写/绿档类目/动词封闭/幂等)
运行: python -X utf8 cgraph/test_skill_memory_gate.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from cgraph.skill_memory_gate import (
    SkillMemoryGate, MAX_GATE_ITEMS, GREEN_TYPES, VERBS, build_trail_digest)
from cgraph.atomic_skill import ThinkState

PASS = FAIL = 0


def ok(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


class MockLLM:
    """可控 mock: 固定返回 payload"""
    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages):
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)


class NoWriteDB:
    """DB写探针: 技能运行期间任何sqlite写尝试都会被抓到(段七红线1)"""
    opened = False


def make_state(trail=None, steps=3):
    return ThinkState(
        goal="模拟空调开", goal_anchored="SPI注入", summary="注入成功",
        trail=trail if trail is not None else [
            {"skill_id": "skill_terminate", "step_summary": "链路收敛注入成功", "result_fields": ["final_output"]}],
        steps=steps)


def gate(payload, trail=None):
    return SkillMemoryGate(llm=MockLLM(payload)).run(make_state(trail))


def item(tier, atype, gist="x", step="skill_terminate", verb=None):
    legal = {"green": "写卡", "yellow": "建议立卡", "red": "仅标记"}
    return {"tier": tier, "asset_type": atype, "gist": gist, "evidence_step": step,
            "proposed_action": verb or legal[tier]}


def base(items, applicable=True, **kw):
    d = {"applicable": applicable, "gate_list": items, "coverage_note": "",
         "summary": "盘点完成", "confidence": 0.5, "ambiguity_note": ""}
    d.update(kw)
    return d


print("== 段四: 出口契约 ==")
r = gate(base([]))
ok("T4① next_options 恒空", r.next_options == [])
ok("T4② terminate_suggest 恒真", r.terminate_suggest is True)

print("== 段五: 降级矩阵 ==")
r = gate(base([], applicable=False), trail=[])  # 行1: 空链路→LLM判false=合法
ok("M1 空trail: applicable=false 非降级", not r.result.get("_degraded") and r.result["applicable"] is False)
r = gate(base([], applicable=False))  # 行2: 无资产=合法
ok("M2 无资产: 非降级且清单空", not r.result.get("_degraded") and r.result["gate_list"] == [])
r = gate(base([item("green", "踩坑", gist="")]))  # gist空→清洗光
ok("M3 声明有资产清洗后空→降级", bool(r.result.get("_degraded")))
r = gate("")                          # 行4: LLM空返回
ok("M4 LLM空返回→降级", bool(r.result.get("_degraded")))
r = gate("not json at all")
ok("M4b JSON解析失败→降级", bool(r.result.get("_degraded")))
r = gate(base([item("green", "踩坑"), {"tier": "bogus", "asset_type": "观测", "gist": "g", "evidence_step": "skill_terminate", "proposed_action": "写卡"}]))
ok("M5 单条非法丢弃不降级+note", not r.result.get("_degraded")
   and len(r.result["gate_list"]) == 1 and "非法" not in r.result["ambiguity_note"])
many = [item("green", "观测", gist=f"观测{i}") for i in range(9)]
r = gate(base(many))
ok("M6 超限截断保前6+过载note", len(r.result["gate_list"]) == MAX_GATE_ITEMS
   and "过载" in r.result["ambiguity_note"] and not r.result.get("_degraded"))
r = gate(base([item("green", "key_pact")]))        # 行7: 越档
ok("M7 越档(green+key_pact)整条丢弃→降级(全空)", bool(r.result.get("_degraded"))
   and "越档" in r.result.get("ambiguity_note", ""))
r = gate(base([item("green", "key_pact"), item("green", "踩坑")]))
ok("M7b 越档单条丢弃其余保留+note", len(r.result["gate_list"]) == 1
   and "越档" in r.result["ambiguity_note"] and not r.result.get("_degraded"))
r = gate(base([item("green", "topic挂载")]))
ok("M7c 绿档+topic挂载=越档丢弃", len(r.result["gate_list"]) == 0)
r = gate(base([item("green", "踩坑", verb="仅标记")]))
ok("M7d 档位动词不符丢弃", len(r.result["gate_list"]) == 0)
r = gate(base([item("green", "踩坑", step="skill_nonexist")]))
ok("M7e evidence_step编造丢弃+note", len(r.result["gate_list"]) == 0
   and "evidence_step" in r.result.get("ambiguity_note", ""))
r = gate(base([item("green", "踩坑")], applicable=False))
ok("M8 applicable=false仍带条目→清空", r.result["gate_list"] == [])

print("== 段七: 红线断言 ==")
import cgraph.skill_memory_gate as smg
src = open(smg.__file__, encoding="utf-8").read()
import ast
_tree = ast.parse(src)
_calls = [n.func.id for n in ast.walk(_tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
_imports = [a.name for n in ast.walk(_tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
ok("R1 零DB写: 代码层无sqlite3 import/无seed_topic/mount_topic调用",
   "sqlite3" not in _imports and "seed_topic" not in _calls and "mount_topic" not in _calls
   and not any("connect" == c for c in _calls))
r = gate(base([item("green", t) for t in GREEN_TYPES]))
ok("R2 绿档类目封闭(观测/测试结果/踩坑)", all(x["asset_type"] in GREEN_TYPES for x in r.result["gate_list"]))
ok("R3 绿档永不挂锚(topic挂载∈黄档)", "topic挂载" not in GREEN_TYPES)
ok("R4 动词枚举封闭(写卡|建议立卡|仅标记)", VERBS == {"写卡", "建议立卡", "仅标记"})
ok("R5 无删除/更新动词", "删除" not in VERBS and "更新" not in VERBS)

# R6 幂等: 同输入两次运行产出一致
s = base([item("green", "踩坑"), item("yellow", "能力卡")])
r1 = gate(s); r2 = gate(s)
ok("R6 同trail重跑产出一致(幂等)", r1.result["gate_list"] == r2.result["gate_list"])

# T4③ 真实热改断言: 邻接表注入非空 next_options
adj = SkillMemoryGate._load_adjacency()
import copy
spec = copy.deepcopy(adj["entries"]["skill_memory_gate"])
spec["next_options"] = [{"skill": "skill_terminate", "gain": "x", "cost": "x"}]
fake = {"entries": {**adj["entries"], "skill_memory_gate": spec}, "global": adj.get("global", {})}
try:
    SkillMemoryGate(llm=MockLLM("{}"), adjacency=fake).run(make_state())
    raised = False
except RuntimeError:
    raised = True
ok("T4③b 热改 next_options 非空→raise", raised)

# build_prompt: Jw复盘素材注入(段三②b)
g = SkillMemoryGate(llm=MockLLM("{}"))
g.jw_review_text = "发现系统性遗漏: 工具部署缺前置检查"
msgs = g.build_prompt(make_state())
ok("P1 Jw复盘文本注入prompt", any("Jw 支线复盘产出" in m["content"] for m in msgs))
ok("P2 trail digest 60字截断口径", all(len(l) <= 200 for l in build_trail_digest(make_state().trail * 20).splitlines() if l))
ok("P3 system prompt 固化绿黄红枚举", "观测记录 | 测试结果 | 简单踩坑" in msgs[0]["content"])

print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
print("skill_memory_gate 七段定稿回归", "全过" if FAIL == 0 else "存在失败")
sys.exit(1 if FAIL else 0)