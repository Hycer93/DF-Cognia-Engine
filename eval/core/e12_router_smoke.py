# -*- coding: utf-8 -*-
"""
e12_router_smoke.py — E12 skill_router 夜间冒烟(2026-11-17 七段定稿段六③a变体)

定位: skill_router 上线双准入之 nightly 冒烟步骤(编排组件双准入: 单测+E12,
豁免 20 样本盲评——无假设谱等语义输出面)。
真实 LLM 跑一条标准故障根因目标走完整原子管线, 断言:
  ok=True / exit_channel 合法 / steps≥3 / fallback_count≤1 / trail 单技能≤2 次。
FAIL 退出 1 → 晨报高亮, 不静默断层(对齐 E0 硬 FAIL 哲学)。

设计口径:
  - 只调 router+技能本体: 零 DB 写, 不执行 commit_green_cards(夜间不产生绿卡)
  - mock-LLM 四通道场景归单测; E12 只做真实 LLM 行为的夜间看门
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

# 标准目标: 故障根因型(语料真题同型, 应走 anchor→scan→深挖→terminate 自然收敛)
SMOKE_GOAL = ("夜间 E7 晨报显示 hold 池新增 3 张引用性提及卡, 这是分类规约的哪一环"
              "出了问题, 该怎么补强?")

EXIT_CHANNELS = ("terminate_suggest", "llm_terminate", "collapse", "max_steps")
MAX_VISITS = 2
MIN_STEPS = 3
MAX_FALLBACK = 1


def main():
    from cgraph.llm import LLMClient
    from cgraph.atomic_skill import ThinkState
    from cgraph.router_atomic import RouterAtomic

    adj_path = os.path.join(HERE, "data", "atomic_skills_adjacency.json")
    with io.open(adj_path, encoding="utf-8") as f:
        adjacency = json.load(f)

    t0 = time.time()
    llm = LLMClient()
    report = RouterAtomic(llm, adjacency).run(ThinkState(goal=SMOKE_GOAL))
    secs = round(time.time() - t0, 1)

    trail_ids = [e["skill_id"] for e in report.final_state.trail]
    visit_counts = {sid: trail_ids.count(sid) for sid in set(trail_ids)}
    checks = {
        "ok_true": report.ok is True,
        "exit_channel_legal": report.exit_channel in EXIT_CHANNELS,
        "steps_ge3": len(report.steps_log) >= MIN_STEPS,
        "fallback_le1": report.fallback_count <= MAX_FALLBACK,
        "visits_within_cap": all(v <= MAX_VISITS for v in visit_counts.values()),
    }
    result = {
        "component": "skill_router", "secs": secs, "ok": all(checks.values()),
        "exit_channel": report.exit_channel, "n_steps": len(report.steps_log),
        "fallback_count": report.fallback_count,
        "trail": trail_ids, "visit_counts": visit_counts, "checks": checks,
        "steps_log": report.steps_log,
    }
    ok = result["ok"]
    report_json = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "results": [result]}
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(report_json, f, ensure_ascii=False, indent=1)
    failed = [k for k, v in checks.items() if not v]
    print(f"[E12] {'PASS' if ok else 'FAIL'} skill_router ({secs}s) "
          f"exit={report.exit_channel} steps={len(report.steps_log)} "
          f"fallback={report.fallback_count} "
          f"{'' if ok else '失败项: ' + ','.join(failed)}", flush=True)
    print(f"[E12] trail: {'→'.join(trail_ids)}")
    print(f"[E12] 总判定: {'PASS' if ok else 'FAIL'} | 明细 {LOG}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()