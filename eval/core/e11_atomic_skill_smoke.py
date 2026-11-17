# -*- coding: utf-8 -*-
"""
e11_atomic_skill_smoke.py — E11 元核原子技能夜间冒烟(2026-11-17 立项)

定位: f_reason 与 memory_gate 的上线双准入之 nightly 冒烟步骤(f_reason
2026-11-16 七段定稿遗留; memory_gate 2026-11-17 七段定稿同口径)。
每夜真实 LLM 最小样本各跑一次, 断言: 非降级 + schema 合法 + 关键红线
(f_reason 无 plausibility 档位 / memory_gate 绿档类目封闭)。
FAIL 退出 1 → 晨报高亮, 不静默断层(对齐 E0 开关守卫的硬 FAIL 哲学)。

设计口径(对齐 E 系主链原则):
  - 纯本地无人值守: 只依赖 Engine 同源 LLM(config.yaml active 后端)
  - 只读校验: f_reason 全程零落库; memory_gate 走技能本体零 DB 写(段七红线1),
    本冒烟只调技能 run() 不调编排器 commit_green_cards——夜间不产生绿卡写入
  - 幂等: 每次跑同样的固定冒烟样例, 结果落 logs/e11_atomic_smoke.json
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

LOG = os.path.join(HERE, "logs", "e11_atomic_smoke.json")

# f_reason 冒烟样例(多解可能型问题——applicable 应为 true 且产出假设谱)
FR_GOAL = ("8397 车机昨夜 E9 对话归档数为 0, 但当日确实有长时间对话。"
           "可能的原因有哪些?")
# memory_gate 冒烟样例(含外部工具+踩坑素材——应盘出记忆资产)
MG_USER_INPUT = ("部署 vhal_test 时发现设备未预置, 跑 deploy_vhal_test.sh 解决; "
                 "踩坑: 注入后 dumpsys 回读 status=ERROR 但 UI 实际有反应。")


def _llm():
    from cgraph.llm import LLMClient
    return LLMClient()


def smoke_f_reason(llm) -> dict:
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_f_reason import SkillFReason
    t0 = time.time()
    state = ThinkState(
        goal=FR_GOAL, goal_anchored=FR_GOAL,
        sub_questions=["归档管道哪一环可能断了?", "有无环境侧因素?"],
        summary="E9 归档零入库现象复盘", steps=3)
    r = SkillFReason(llm=llm).run(state)
    res = r.result
    checks = {
        "non_degraded": not res.get("_degraded"),
        "applicable": res.get("applicable") is True,
        "hypotheses_nonempty": bool(res.get("hypotheses")),
        "hypotheses_cap5": len(res.get("hypotheses") or []) <= 5,
        "no_plausibility": all("plausibility" not in h for h in res.get("hypotheses") or []),
        "has_test_next": all(h.get("test_next") for h in res.get("hypotheses") or []),
    }
    return {"skill": "skill_f_reason", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_hypotheses": len(res.get("hypotheses") or []),
            "ok": all(checks.values())}


def smoke_memory_gate(llm) -> dict:
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_memory_gate import SkillMemoryGate
    t0 = time.time()
    trail = [
        {"skill_id": "node_Ch", "step_summary": "vhal_test 未预置, deploy 脚本解决",
         "result_fields": []},
        {"skill_id": "node_Dk", "step_summary": "dumpsys 回读 ERROR 但 UI 有反应",
         "result_fields": []},
        {"skill_id": "SUMMARY", "step_summary": "部署+注入闭环完成", "result_fields": []},
    ]
    state = ThinkState(goal=MG_USER_INPUT, goal_anchored=MG_USER_INPUT,
                       summary="部署与注入冒烟链路", trail=trail, steps=3)
    g = SkillMemoryGate(llm=llm)
    g.jw_review_text = "复盘: 部署前置校验缺失是系统性遗漏; 回读判据不可信是新踩坑"
    r = g.run(state)
    res = r.result
    gl = res.get("gate_list") or []
    checks = {
        "non_degraded": not res.get("_degraded"),
        "gate_schema_legal": all(
            x["tier"] in ("green", "yellow", "red")
            and x["proposed_action"] in ("写卡", "建议立卡", "仅标记")
            for x in gl),
        "green_types_closed": all(
            x["asset_type"] in ("观测", "测试结果", "踩坑")
            for x in gl if x["tier"] == "green"),
        "exit_contract": r.next_options == [] and r.terminate_suggest is True,
    }
    return {"skill": "skill_memory_gate", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_gate_items": len(gl),
            "gate_stats": {"green": sum(1 for x in gl if x["tier"] == "green"),
                           "yellow": sum(1 for x in gl if x["tier"] == "yellow"),
                           "red": sum(1 for x in gl if x["tier"] == "red")},
            "ok": all(checks.values())}


def main():
    llm = _llm()
    results = [smoke_f_reason(llm), smoke_memory_gate(llm)]
    ok = all(r["ok"] for r in results)
    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "results": results}
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"[E11] {status} {r['skill']} ({r['secs']}s) "
              f"{'' if r['ok'] else '失败项: ' + ','.join(failed)}", flush=True)
    print(f"[E11] 总判定: {'PASS' if ok else 'FAIL'} | 明细 {LOG}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()