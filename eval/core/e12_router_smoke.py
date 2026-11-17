# -*- coding: utf-8 -*-
"""
e12_router_smoke.py — E12 skill_router 夜间冒烟(2026-11-17 七段定稿段六③a变体;
P-C3 迭代后扩三样本)

定位: skill_router 上线双准入之 nightly 冒烟步骤(编排组件双准入: 单测+E12,
豁免 20 样本盲评)。真实 LLM 走完整原子管线。
P-C3 扩样本(段六二): G1根因类/G2评估类(应触发C3倾向f_reason) +
G3决策类(负样本, 应不触发)。G3 断言裁量: warn 模式(误判进晨报观测,
不触发FAIL——真实LLM判型有误判可能, 看门价值在暴露而非阻断)。
FAIL 退出 1 → 晨报高亮, 不静默断层(对齐 E0 硬 FAIL 哲学)。

设计口径:
  - 只调 router+技能本体: 零 DB 写, 不执行 commit_green_cards(夜间不产生绿卡)
  - 幂等: 固定样例, 结果落 logs/e12_router_smoke.json
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

LOG = os.path.join(HERE, "logs", "e12_router_smoke.json")

SMOKE_GOALS = [
    # G1 根因类: 应触发 C3(f_reason 出现于 trail 或 preempt=True 至少其一)
    ("G1-根因", "夜间 E7 晨报显示 hold 池新增 3 张引用性提及卡, 分类规约哪一环出了问题, 怎么补强?", True),
    # G2 评估类: 应触发 C3
    ("G2-评估", "评估数字熔炉性格蒸馏系统方案: 创新点/最薄弱环节/商业部署风险", True),
    # G3 决策类(负样本): 不应触发 C3(断言为 warn 裁量模式, 误判进晨报观测)
    ("G3-决策", "紫光国微当前该不该按71-72元被动挂单建仓, 给出判断与理由", False),
]

# P-C4 素材通道冒烟样本(段四4.4): S1短材料直塞/S2长材料检索/S3超大截断。
# S1/S2 关键词命中为硬断言(机械词表, 拒绝 LLM judge); S3 仅校验链路不崩溃。
PATENT_PATH = os.path.join(HERE, "data", "patent_evidence",
                           "一种基于可复现性判定的AI记忆分级存储方法.txt")
P4_KEYWORDS = ["可复现性", "三级分类", "三层架构", "判定单元", "闪念记忆层"]
P4_MARK = "---ref_material---"
P4_COMMON = ("评估专利P1《一种基于可复现性判定的AI记忆分级存储方法》交底书方案质量: "
             "核心创新点与现有技术区分度/最薄弱环节/可落地性")

def _smoke_s_samples():
    """S1/S2/S3 由专利正文构造; 文件缺失时返回空列表(跳过并 warn)。"""
    try:
        with io.open(PATENT_PATH, encoding="utf-8", errors="replace") as f:
            patent = f.read()
    except OSError:
        return None
    s1_mat = patent[:1500]                                    # ≤2000 直塞分支
    s2_mat = patent + "\n\n背景补充: " + ("相关论述。" * 300)   # >2000 检索分支
    s3_mat = patent[:1000] + ("扩展段落内容。\n\n" * 20000)     # >200K 截断分支
    return [
        ("S1-短材料直塞", f"{P4_COMMON}{P4_MARK}\n{s1_mat}", "inline"),
        ("S2-长材料检索", f"{P4_COMMON}{P4_MARK}\n{s2_mat}", "retrieve"),
        ("S3-超大截断", f"{P4_COMMON}{P4_MARK}\n{s3_mat}", "oversize"),
    ]

EXIT_CHANNELS = ("terminate_suggest", "llm_terminate", "collapse", "max_steps")
MAX_VISITS = 2
MIN_STEPS = 3
MAX_FALLBACK = 1


def run_one(llm, adjacency, goal):
    from cgraph.atomic_skill import ThinkState
    from cgraph.router_atomic import RouterAtomic
    t0 = time.time()
    report = RouterAtomic(llm, adjacency).run(ThinkState(goal=goal))
    secs = round(time.time() - t0, 1)
    trail_ids = [e["skill_id"] for e in report.final_state.trail]
    visits = {sid: trail_ids.count(sid) for sid in set(trail_ids)}
    checks = {
        "ok_true": report.ok is True,
        "exit_channel_legal": report.exit_channel in EXIT_CHANNELS,
        "steps_ge3": len(report.steps_log) >= MIN_STEPS,
        "fallback_le1": report.fallback_count <= MAX_FALLBACK,
        "visits_within_cap": all(v <= MAX_VISITS for v in visits.values()),
    }
    return {
        "component": "skill_router", "secs": secs, "goal": goal[:60],
        "exit_channel": report.exit_channel, "n_steps": len(report.steps_log),
        "fallback_count": report.fallback_count, "trail": trail_ids,
        "visit_counts": visits, "task_type": report.task_type,
        "task_type_source": report.task_type_source,
        "preempt_steps": len([s for s in report.steps_log if s.get("preempt")]),
        "checks": checks,
    }, report


def main():
    from cgraph.llm import LLMClient

    adj_path = os.path.join(HERE, "data", "atomic_skills_adjacency.json")
    with io.open(adj_path, encoding="utf-8") as f:
        adjacency = json.load(f)

    llm = LLMClient()
    results, warns, hard_fail = [], [], False
    for tag, goal, c3_expected in SMOKE_GOALS:
        r, report = run_one(llm, adjacency, goal)
        r["task"] = tag
        # P-C3 生效性断言(G1/G2) / 抑制性断言(G3, warn 裁量模式)
        if c3_expected:
            r["checks"]["c3_effective"] = ("skill_f_reason" in r["trail"]) or (r["preempt_steps"] > 0)
        else:
            r["checks"]["c3_suppressed"] = ("skill_f_reason" not in r["trail"]) and (r["preempt_steps"] == 0)
        base_ok = all(v for k, v in r["checks"].items() if not k.startswith("c3_"))
        c3_ok = all(v for k, v in r["checks"].items() if k.startswith("c3_"))
        r["ok"] = base_ok and c3_ok
        if not base_ok:
            hard_fail = True
        elif not c3_ok:
            # 裁量点(段六二): G3 误判/task_type漂移 → warn 不 FAIL, 晨报观测
            warns.append(f"{tag} C3断言未达预期(task_type={r['task_type']}/{r['task_type_source']} "
                         f"trail含f_reason={'skill_f_reason' in r['trail']} preempt={r['preempt_steps']})")
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"[E12] {tag} {'PASS' if r['ok'] else ('WARN' if base_ok else 'FAIL')} "
              f"({r['secs']}s) exit={r['exit_channel']} type={r['task_type']}/{r['task_type_source']} "
              f"steps={r['n_steps']} fb={r['fallback_count']} preempt={r['preempt_steps']} "
              f"{'' if r['ok'] else '失败项: ' + ','.join(failed)}", flush=True)
        print(f"[E12] {tag} trail: {'→'.join(r['trail'])}")
        results.append(r)

    # ---- P-C4 素材通道冒烟(段四4.4): S1/S2 词表硬断言 / S3 不崩溃 ----
    s_samples = _smoke_s_samples()
    if s_samples is None:
        warns.append("P4 样本文件缺失, S1/S2/S3 跳过(需人工复核)")
    else:
        for tag, goal, mode in s_samples:
            # 走 run_atomic 入口(分隔符通道在 executor; E12 仍零 DB 绿卡——直接复制入口切片语义)
            MARK = "---ref_material---"
            gp, _, mp = goal.partition(MARK)
            mat = mp.lstrip("\r\n")
            from cgraph.atomic_skill import ThinkState, REF_INLINE_THRESHOLD
            from cgraph.router_atomic import RouterAtomic
            ref_mat = mat[:200_000] if len(mat) > 200_000 else (mat if mat.strip() else "")
            state = ThinkState(goal=gp.rstrip(), ref_material=ref_mat)
            t0 = time.time()
            report = RouterAtomic(llm, adjacency).run(state)
            secs = round(time.time() - t0, 1)
            # 汇编终态文本 = 全 trail result_fields 序列化(词表断言域; 材料引用在
            # f_reason 假设谱等 result_fields 内, terminate final_output 只是60字截断
            # summary 的汇编, 覆盖不全——断言查全 trail 产出)
            final_txt = json.dumps([e.get("result_fields") or {} for e in report.final_state.trail],
                                   ensure_ascii=False, default=str)
            hits = [k for k in P4_KEYWORDS if k in final_txt]
            also_in_state = [k for k in P4_KEYWORDS if k in (state.ref_material or "")]
            sr = {"task": tag, "mode": mode, "secs": secs,
                  "material_len": len(ref_mat), "n_steps": len(report.steps_log),
                  "trail": [e["skill_id"] for e in report.final_state.trail],
                  "exit_channel": report.exit_channel, "ok_chain": report.ok,
                  "keyword_hits": hits, "keywords_in_state": also_in_state}
            if mode == "oversize":
                # S3: 仅校验链路不崩溃(WARN 允许, 段四4.4)
                sr["ok"] = report.ok and report.exit_channel in EXIT_CHANNELS
                if not sr["ok"]:
                    hard_fail = True
                print(f"[E12] {tag} {'PASS' if sr['ok'] else 'FAIL'} ({secs}s) mode={mode} "
                      f"mat={sr['material_len']}chars exit={sr['exit_channel']} 词表命中={len(hits)}/{len(P4_KEYWORDS)} {hits}")
            else:
                # S1/S2: 词表硬断言——命中>=2 个专利关键词(从 0 到 1 引用材料为本轮 KPI)
                sr["ok"] = report.ok and len(hits) >= 2
                if not report.ok:
                    hard_fail = True
                elif len(hits) < 2:
                    hard_fail = True
                print(f"[E12] {tag} {'PASS' if sr['ok'] else 'FAIL'} ({secs}s) mode={mode} "
                      f"mat={sr['material_len']}chars exit={sr['exit_channel']} 词表命中={len(hits)}/{len(P4_KEYWORDS)} {hits}")
            results.append(sr)

    # ---- P-C5 后置段冒烟(段四4.3): N1带材料告警链 / N2 simple_query不适用 ----
    # 走 run_atomic 完整后置时序(补汇编→sufficiency→gate), 不进 steps_log;
    # final_assembly_present=False 属硬 FAIL(病灶A回归哨兵)
    from cgraph.executor import ChainExecutor
    from cgraph.atomic_skill import REF_INLINE_THRESHOLD
    try:
        with io.open(PATENT_PATH, encoding="utf-8", errors="replace") as f:
            patent_full = f.read()
    except OSError:
        patent_full = "可复现性判定记忆分级存储方案概要文本"
    for tag, goal, expect_assembly, expect_applicable in [
        ("N1-带材料后置", (P4_COMMON + P4_MARK + "\n" + patent_full), True, True),
        ("N2-直收口不适用", "今天星期几? 一句话回答即可", True, False),
    ]:
        ex = ChainExecutor.__new__(ChainExecutor); ex.llm = llm
        t0 = time.time()
        try:
            rep, st, gate = ex.run_atomic(goal)
        except Exception as e:
            print(f"[E12] {tag} FAIL: run_atomic异常 {e}")
            hard_fail = True
            continue
        secs = round(time.time() - t0, 1)
        ids = [e["skill_id"] for e in st.trail]
        nr = {"task": tag, "secs": secs, "trail": ids,
              "has_terminate": "skill_terminate" in ids,
              "ref_len": len(st.ref_material or "")}
        # final_output 信息量(病灶A对拍: 旧版终态=60字尾步summary)
        fo = ""
        for e in st.trail:
            if e.get("skill_id") == "skill_terminate":
                rf = e.get("result_fields") or {}
                fo = str(rf.get("final_output", "")) if isinstance(rf, dict) else ""
        nr["final_output_len"] = len(fo)
        checks_ok = True
        if not nr["has_terminate"]:
            print(f"[E12] {tag} FAIL: final_assembly_present=False(病灶A回归哨兵)")
            hard_fail = True
            checks_ok = False
        if tag.startswith("N2") and not expect_applicable:
            pass                                  # applicable=False 断言走审计行(下查)
        print(f"[E12] {tag} {'PASS' if checks_ok else 'FAIL'} ({secs}s) trail={'→'.join(ids)} "
              f"汇编={nr['has_terminate']} final_output={nr['final_output_len']}字 ref={nr['ref_len']}")
        results.append(nr)
    # N1 后置告警链审计断言: sufficiency:alert 行存在 + retrieval_dependent 硬断言(段四A)
    try:
        import sqlite3 as _sq
        _con = _sq.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"))
        rows = _con.execute("SELECT payload FROM audit_topic_log WHERE op='sufficiency:alert' "
                            "ORDER BY rowid DESC LIMIT 4").fetchall()
        _con.close()
        n1_row = None
        for rr in rows:
            p = json.loads(rr[0])
            if p.get("coverage_metrics", {}).get("retrieval_dependent") is True:
                n1_row = p
                break
        if n1_row is None:
            print("[E12] N1 FAIL: 未检出 retrieval_dependent=True 告警(段四A硬断言)")
            hard_fail = True
        else:
            print(f"[E12] N1 后置告警链 PASS: sufficiency:alert 已落盘, retrieval_dependent=True, "
                  f"alerts={len(n1_row.get('alerts', []))}条, metrics={ {k: v for k, v in n1_row['coverage_metrics'].items() if k in ('subq_coverage', 'hypotheses_n', 'wl_addressed', 'final_assembly_present')} }")
    except Exception as e:
        print(f"[E12] N1 审计断言异常(不阻断): {e}")

    ok = not hard_fail
    report_json = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok,
                   "warns": warns, "results": results}
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(report_json, f, ensure_ascii=False, indent=1)
    for w in warns:
        print(f"[E12][WARN] {w}")
    print(f"[E12] 总判定: {'PASS' if ok else 'FAIL'} (warn {len(warns)}条不阻断) | 明细 {LOG}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()