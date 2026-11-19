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
    results = [smoke_f_reason(llm), smoke_memory_gate(llm),
               # P-skeleton-complete 五技能冒烟(2026-11-19 段二编码完成后接入)
               smoke_dk_deduction(llm), smoke_en_evidence_net(llm),
               smoke_bd_crosscheck(llm), smoke_g24_topology(llm),
               smoke_gs_review(llm)]
    ok = all(r["ok"] for r in results)
    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "results": results,
              "usage": getattr(llm, "last_usage", None)}
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"[E11] {status} {r['skill']} ({r['secs']}s) "
              f"{'' if r['ok'] else '失败项: ' + ','.join(failed)}", flush=True)
    print(f"[E11] 总判定: {'PASS' if ok else 'FAIL'} | 明细 {LOG}")
    sys.exit(0 if ok else 1)


# ============ P-skeleton-complete 五技能冒烟(2026-11-19 段一定稿v1.0 §5) ============
# 共享冒烟语境: round6 专利审查真题同源(锚点安全专利), 保证五技能链路连贯:
# f_reason 产假设谱 -> Dk 织链 -> En 织网 -> Bd 交叉 -> G24 全局拓扑。

PSK_GOAL = "审查基于唯一身份锚点的AI行为追溯专利的授权风险"
PSK_SUBS = [{"q": "身份锚点不可伪造断言是否有说明书支撑?"},
            {"q": "行为追溯链路是否具备创造性?"}]
PSK_HYPS = [
    {"hypothesis": "H1 不可伪造断言缺实验数据支撑", "support": "说明书仅断言",
     "contradicting": [], "test_next": "核对实施例"},
    {"hypothesis": "H2 哈希链锚定构成创造性贡献", "support": "对比文件未覆盖",
     "contradicting": [], "test_next": "检索现有技术"}]
PSK_WL = {"location": "说明书-创新点一 不可伪造性公开充分性",
          "dimension": "公开充分性", "score": 0.25, "risk": "high",
          "evidence": "技术效果仅断言无实验数据"}
PSK_MAT = [{"matrix_id": "pa-004", "tech_name": "哈希链/不可篡改",
            "mapping_note": "锚点防篡改映射", "audit_risk": "创造性争辩点"},
           {"matrix_id": "pa-005", "tech_name": "区分度驳回锚",
            "mapping_note": "区分度不足驳回", "audit_risk": "驳回锚"}]


def smoke_dk_deduction(llm) -> dict:
    """Dk: 断言织链非空、单链<=4步、unsupported 派生可命中(样例含1条空引用)。"""
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_dk_deduction import SkillDkDeduction
    t0 = time.time()
    state = ThinkState(goal=PSK_GOAL, goal_anchored=PSK_GOAL,
                       sub_questions=PSK_SUBS, summary="专利审查冒烟语境",
                       hypotheses=PSK_HYPS, steps=3)
    state.weakest_link = PSK_WL
    state.ref_material = ("[pa-004] 哈希链提供不可篡改的时间有序记录; "
                          "[pa-005] 权利要求区分度不足时构成驳回锚。")
    state.matrix_knowledge = PSK_MAT
    r = SkillDkDeduction(llm=llm).run(state)
    res = r.result
    chains = res.get("chains") or []
    checks = {
        "non_degraded": not res.get("_degraded"),
        "applicable": res.get("applicable") is True,
        "chains_nonempty": bool(chains),
        "chains_cap_steps": all(len(c.get("steps", [])) <= 4 for c in chains),
        "full_coverage": len({c.get("target_ref") for c in chains}) >= 2,  # 全量织链
        "has_evidence_ref": any(s.get("evidence_ref") for c in chains for s in c.get("steps", [])),
        "unsupported_derived": all(
            isinstance(c.get("unsupported_premises"), list) for c in chains),
        "state_written": state.deduction_chains is not None,
    }
    return {"skill": "skill_dk_deduction", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_chains": len(chains),
            "n_unsupported": len(res.get("unsupported_premises") or []),
            "ok": all(checks.values())}


def smoke_en_evidence_net(llm) -> dict:
    """En: 断言织网只消费 Dk 链引用、矛盾对独立字段、孤立检出。"""
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_en_evidence_net import SkillEnEvidenceNet
    t0 = time.time()
    # 预置 Dk 链产物(直接构造, 单技能冒烟不串真实 Dk——链路联动归 E12)
    chains = [
        {"target_ref": PSK_HYPS[0]["hypothesis"], "steps": [
            {"premise": "断言无实验数据", "evidence_ref": "pa-005", "note": ""},
            {"premise": "哈希链可支撑不可篡改", "evidence_ref": "pa-004", "note": ""}],
         "unsupported_premises": []},
        {"target_ref": "创造性", "steps": [
            {"premise": "对比文件未覆盖锚定", "evidence_ref": "pa-004", "note": ""}],
         "unsupported_premises": []}]
    state = ThinkState(goal=PSK_GOAL, goal_anchored=PSK_GOAL,
                       summary="专利审查冒烟语境", steps=4)
    state.hypotheses = PSK_HYPS
    state.deduction_chains = chains
    state.ref_material = "[pa-004] 哈希链不可篡改; [pa-005] 区分度驳回锚。"
    state.matrix_knowledge = PSK_MAT
    r = SkillEnEvidenceNet(llm=llm).run(state)
    res = r.result
    net = res.get("net") or []
    refs = {"pa-004", "pa-005"}
    checks = {
        "non_degraded": not res.get("_degraded"),
        "applicable": res.get("applicable") is True,
        "net_whitelist": all(e["src_a"] in refs and e["src_b"] in refs for e in net),
        "relations_valid": all(e["relation"] in ("印证", "矛盾", "补充") for e in net),
        "contradiction_indep": (len(res.get("contradiction_pairs") or []) ==
                                sum(1 for e in net if e["relation"] == "矛盾")),
        "state_written": state.evidence_net is not None,
    }
    return {"skill": "skill_en_evidence_net", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_edges": len(net),
            "n_contradictions": len(res.get("contradiction_pairs") or []),
            "n_isolated": len(res.get("isolated_evidence") or []),
            "ok": all(checks.values())}


def smoke_bd_crosscheck(llm) -> dict:
    """Bd: 断言矩阵非空、relation 三档、unaddressed 派生一致。"""
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_bd_crosscheck import SkillBdCrosscheck
    t0 = time.time()
    state = ThinkState(goal=PSK_GOAL, goal_anchored=PSK_GOAL,
                       summary="专利审查冒烟语境", steps=4)
    state.hypotheses = PSK_HYPS
    state.weakest_link = PSK_WL
    state.deduction_chains = [{"target_ref": PSK_HYPS[0]["hypothesis"],
                               "steps": [{"premise": "断言无数据", "evidence_ref": "pa-005", "note": ""}]}]
    state.evidence_net = [{"src_a": "pa-004", "src_b": "pa-005", "relation": "补充", "note": ""}]
    r = SkillBdCrosscheck(llm=llm).run(state)
    res = r.result
    m = res.get("cross_matrix") or []
    wl_loc = PSK_WL["location"]
    checks = {
        "non_degraded": not res.get("_degraded"),
        "applicable": res.get("applicable") is True,
        "matrix_nonempty": bool(m),
        "relations_valid": all(e["relation"] in ("回应", "竞争解释", "无关") for e in m),
        "unaddressed_consistent": (
            (wl_loc in [e["wl_location"] for e in m if e["relation"] in ("回应", "竞争解释")])
            == (wl_loc not in (res.get("unaddressed_points") or []))),
        "state_written": state.bd_matrix is not None,
    }
    return {"skill": "skill_bd_crosscheck", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_pairs": len(m),
            "n_unaddressed": len(res.get("unaddressed_points") or []),
            "ok": all(checks.values())}


def smoke_g24_topology(llm) -> dict:
    """G24: 断言四图非空、gaps 三类合并、矛盾/漂移不入 gaps。"""
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_g24_topology import SkillG24Topology
    t0 = time.time()
    state = ThinkState(goal=PSK_GOAL, goal_anchored=PSK_GOAL,
                       sub_questions=PSK_SUBS, summary="专利审查冒烟语境",
                       hypotheses=PSK_HYPS, steps=5)
    state.weakest_link = PSK_WL
    state.trail = [{"skill_id": "skill_anchor_target", "reason": "锚定审查目标"},
                   {"skill_id": "skill_scope_scan", "reason": "拆解子问题"},
                   {"skill_id": "skill_f_reason", "reason": "生成假设谱"},
                   {"skill_id": "skill_dk_deduction", "reason": "织演绎链"}]
    state.matrix_knowledge = PSK_MAT
    state.unaddressed = [PSK_WL["location"]]          # 模拟 Bd 已产
    state.deduction_chains = [{"target_ref": "创造性",
                               "steps": [{"premise": "p", "evidence_ref": "", "note": ""}],
                               "unsupported_premises": [{"premise": "p"}]}]
    r = SkillG24Topology(llm=llm).run(state)
    res = r.result
    topo = res.get("topology") or {}
    gaps = res.get("gaps") or []
    types = [g.get("gap_type") for g in gaps]
    checks = {
        "non_degraded": not res.get("_degraded"),
        "four_maps": all(k in topo for k in
                         ("skeleton_family", "tool_topology", "knowledge_map", "path_status")),
        "gaps_merged": ("unaddressed_weakpoint" in types and "unsupported_premise" in types),
        "gaps_clean": all(t in ("uncovered_subquestion", "unaddressed_weakpoint",
                                "unsupported_premise") for t in types),
        "state_written": state.topology is not None and state.gaps is not None,
    }
    return {"skill": "skill_g24_topology", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_gaps": len(gaps), "gap_types": types,
            "n_uncovered": len(res.get("uncovered_subquestions") or []),
            "ok": all(checks.values())}


def smoke_gs_review(llm) -> dict:
    """Gs: 断言检查点产出、resume_view 截断保护、漂移告警形态合法。"""
    from cgraph.atomic_skill import ThinkState
    from cgraph.skill_gs_review import SkillGsReview
    t0 = time.time()
    state = ThinkState(goal=PSK_GOAL, goal_anchored=PSK_GOAL,
                       sub_questions=PSK_SUBS, summary="专利审查冒烟语境", steps=5)
    state.trail = [{"skill_id": "skill_anchor_target", "reason": "锚定审查目标"},
                   {"skill_id": "skill_scope_scan", "reason": "拆解子问题"},
                   {"skill_id": "skill_f_reason", "reason": "生成假设谱"},
                   {"skill_id": "skill_dk_deduction", "reason": "织演绎链"},
                   {"skill_id": "skill_en_evidence_net", "reason": "织证据网"}]
    r = SkillGsReview(llm=llm).run(state)
    res = r.result
    drifts = res.get("drift_warnings") or []
    view = res.get("resume_view") or ""
    checks = {
        "non_degraded": not res.get("_degraded"),
        "applicable": res.get("applicable") is True,
        "view_nonempty": bool(view),
        "view_capped": len(view) <= 3100,
        "drift_schema": all("warning" in d for d in drifts),
        "state_written": state.gs_view is not None,
    }
    return {"skill": "skill_gs_review", "secs": round(time.time() - t0, 1),
            "degraded": res.get("_degraded"), "checks": checks,
            "n_drifts": len(drifts),
            "view_chars": len(view),
            "checkpoint": (state.gs_view or {}).get("last_checkpoint_step"),
            "ok": all(checks.values())}


if __name__ == "__main__":
    main()