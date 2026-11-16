# -*- coding: utf-8 -*-
"""
e_x_multi_skill_probe.py — 多原子技能集成思维测试脚手架(experiment 实验层)

定位(2026-11-16 用户确认):
  探索型脚手架, 真实 LLM(CodeChatLLM/TEST_MODEL)下把已落地四技能按固定路线
  串成完整链, 收集语义层问题(字段幻觉/解析异常/state上下文丢失/菜单异常/
  语义与置信问题), 供人工区分 "prompt/解析 bug" vs "业务范式待迭代非bug",
  挑选案例回补单元 Mock 回归。

硬约束(用户对齐, 编码时守住):
  - 只落盘 data/experiment/multi_skill_probe_<ts>/, 禁止任何业务 DB 写入
    (基类自带 _audit_log 照常输出, 不作改动)
  - 最小编排=硬编码固定路线消费邻接表 next_options, 脚本不含任何
    LLM 驱动的分支决策; 智能编排器留元核后续节点讨论, 不入 cgraph
  - 每步完整持久化: skill_id/raw_response/parsed_result/next_options/secs
    + ThinkState 全量快照 → jsonl
  - 防护: max_steps=24 硬兜底; terminate_suggest 命中即停
  - 入参: --tasks 子集(T1..T4), --route 分支路线(scan_si/scan_wl/full)
  - 输出: 汇总 md 含「观测问题清单」分类节 + 「待回补 Mock 回归」节

跑法:
  python eval/experiment/e_x_multi_skill_probe.py                # 全部4任务
  python eval/experiment/e_x_multi_skill_probe.py --tasks 1,4    # 子集
  python eval/experiment/e_x_multi_skill_probe.py --route scan_wl
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

from cgraph.atomic_skill import ThinkState, load_table, assert_loadable
from cgraph.skill_anchor_target import SkillAnchorTarget
from cgraph.skill_scope_scan import SkillScopeScan
from cgraph.skill_source_intent import SkillSourceIntent
from cgraph.skill_weakest_link import SkillWeakestLink
from cgraph.llm_codechat import CodeChatLLM

ADJACENCY_PATH = os.path.join(HERE, "data", "atomic_skills_adjacency.json")
OUT_ROOT = os.path.join(HERE, "data", "experiment")

MAX_STEPS = 24                     # 全局硬兜底(与邻接表 global.max_steps 同值, 双保险)

SKILL_REGISTRY = {
    "skill_anchor_target": SkillAnchorTarget,
    "skill_scope_scan": SkillScopeScan,
    "skill_source_intent": SkillSourceIntent,
    "skill_weakest_link": SkillWeakestLink,
}

# ---------------- 探索任务集(2026-11-16 用户评审确认) ----------------
# route: anchor -> scan -> 深挖分支(source_intent=si / weakest_link=wl / both) -> 终止
TASKS = {
    "T1": {
        "goal": "跨夜跑E7晨报显示hold池有3张卡, 这是什么问题?",
        "route": ["skill_anchor_target", "skill_scope_scan", "skill_source_intent"],
        "intent": "业务运维类查询: 探测 goal_type 分型是否跑偏(simple_query vs analysis)、歧义识别",
    },
    "T2": {
        "goal": "评估当前夜间流水线的最弱环节",
        "route": ["skill_anchor_target", "skill_scope_scan", "skill_weakest_link"],
        "intent": "定向评估弱点: 验证 weakest_link 依赖上游 scan summary/sub_questions 的 state 传递链路",
    },
    "T3": {
        "goal": "数据库 schema 演进该走什么流程?",
        "route": ["skill_anchor_target", "skill_scope_scan", "skill_source_intent"],
        "intent": "方案设计类: 测试 scheme_design 分型 + scope_scan 生成子问题质量",
    },
    "T4": {
        "goal": "昨天那个 bug 修好了吗?",
        "route": ["skill_anchor_target", "skill_scope_scan", "skill_source_intent"],
        "intent": "指代缺失模糊问句: 压测歧义识别/低置信分支; 「答非所问」属预期观测目标非缺陷",
    },
}

ROUTE_ALIASES = {
    "scan_si": ["skill_anchor_target", "skill_scope_scan", "skill_source_intent"],
    "scan_wl": ["skill_anchor_target", "skill_scope_scan", "skill_weakest_link"],
    "full":    ["skill_anchor_target", "skill_scope_scan", "skill_source_intent", "skill_weakest_link"],
}


def snap_state(state: ThinkState) -> dict:
    """ThinkState 全量快照。
    注意: weakest_link/source_intents 等是 state.write 动态 setattr 的键
    (不在 dataclass 声明字段内), asdict 会丢——必须用 vars() 全量捕获。
    (2026-11-16 脚手架实测发现并修复)"""
    d = {k: copy.deepcopy(v) for k, v in vars(state).items()}
    return d


def run_task(llm: CodeChatLLM, adjacency: dict, task_id: str, spec: dict,
             steps_log: list) -> dict:
    """跑单任务固定路线。返回任务级汇总 dict(问题观测由人工事后审 md 标注)"""
    state = ThinkState(goal=spec["goal"])
    route = list(spec["route"])
    route_pos = 0
    terminated = False
    term_reason = ""
    observations = []              # 脚本层自动观测(结构性, 语义问题留人工)

    for step in range(MAX_STEPS):
        if route_pos >= len(route):
            # 固定路线走完: 视为正常终止(菜单含 skill_terminate 即合法收敛)
            terminated = True
            term_reason = "route_exhausted(固定路线走完)"
            break
        skill_id = route[route_pos]

        if skill_id not in SKILL_REGISTRY:
            observations.append({"kind": "menu_anomaly",
                                 "detail": f"路线引用未注册技能: {skill_id}"})
            break
        skill = SKILL_REGISTRY[skill_id](llm=llm, adjacency=adjacency)

        t0 = time.time()
        try:
            sr = skill.run(state)
        except Exception as e:
            observations.append({"kind": "skill_crash", "skill": skill_id,
                                 "detail": f"{type(e).__name__}: {e}"})
            terminated = True
            term_reason = f"skill_crash:{skill_id}"
            break
        wall = round(time.time() - t0, 2)

        # 结构性自动观测(不动语义判断)
        if sr.result.get("_degraded"):
            observations.append({"kind": "degraded", "skill": skill_id,
                                 "detail": sr.result.get("_degraded")})
        declared = adjacency["entries"][skill_id].get("result_fields", [])
        missing = [f for f in declared if f not in sr.result]
        if missing:
            observations.append({"kind": "schema_missing_fields", "skill": skill_id,
                                 "detail": f"result_fields 缺: {missing}"})
        for opt in sr.next_options:
            nxt = opt.get("skill")
            if nxt not in adjacency["entries"] and nxt != "__planned__":
                observations.append({"kind": "menu_anomaly", "skill": skill_id,
                                     "detail": f"next_options 引用邻接表外技能: {nxt}"})
        if skill_id in ("skill_scope_scan", "skill_source_intent", "skill_weakest_link") \
                and not (state.summary or state.sub_questions):
            # 深挖技能应消费上游产出(结构性检查; 语义质量留人工)
            observations.append({"kind": "state_context_thin", "skill": skill_id,
                                 "detail": "上游 summary/sub_questions 为空, prompt 上下文将退化为无锚"})

        steps_log.append({
            "task": task_id, "step": step, "skill_id": skill_id,
            # raw 抓取: _RawCaptureLLM 包装最近一次 llm.chat 原文(零侵入)
            "raw_response": getattr(llm, "last_raw", None) if llm else None,
            "parsed_result": sr.result, "next_options": sr.next_options,
            "terminate_suggest": sr.terminate_suggest, "secs": sr.secs,
            "wall_secs": wall, "state_snapshot": snap_state(state),
        })

        # 分歧⑧C案配套(2026-11-16 skill_terminate七段): trail条目携带
        # step_summary(≤60截断)+result字段名清单——terminate汇编素材源;
        # 完整原始载荷在 steps.jsonl, trail只存汇编视图, 防体积膨胀
        _adj_fields = adjacency["entries"].get(skill_id, {}).get("result_fields", [])
        state.trail.append({
            "skill_id": skill_id, "chosen": skill_id,
            "candidates": [o.get("skill") for o in sr.next_options],
            "reason": "probe固定路线", "ts": datetime.now().isoformat(timespec="seconds"),
            "step_summary": str(sr.result.get("summary", "") or "")[:60],
            "result_fields": _adj_fields,
            "degraded": bool(sr.result.get("_degraded")),
        })

        if sr.terminate_suggest:
            terminated = True
            term_reason = "terminate_suggest命中(技能自身判定收敛)"
            break
        route_pos += 1
    else:
        terminated = True
        term_reason = f"max_steps={MAX_STEPS} 硬兜底强制终止"

    return {"task": task_id, "goal": spec["goal"], "intent": spec["intent"],
            "route": route, "steps_used": len(steps_log), "terminated": terminated,
            "term_reason": term_reason, "auto_observations": observations,
            "final_state": snap_state(state)}





def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="", help="子集如 1,4 (T1..T4)")
    ap.add_argument("--route", default="", choices=["", "scan_si", "scan_wl", "full"],
                    help="覆盖全部任务的深挖分支路线")
    args = ap.parse_args()

    assert_loadable(ADJACENCY_PATH)            # 双触发规约①: 加载时校验
    adjacency = load_table(ADJACENCY_PATH)

    if args.tasks.strip():
        wanted = {f"T{x.strip()}" for x in args.tasks.split(",") if x.strip()}
        unknown = wanted - set(TASKS)
        if unknown:
            print(f"未知任务: {unknown} (可用: {list(TASKS)})"); return
    else:
        wanted = set(TASKS)

    if args.route:
        for tid in wanted:
            TASKS[tid]["route"] = list(ROUTE_ALIASES[args.route])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(OUT_ROOT, f"multi_skill_probe_{ts}")
    os.makedirs(outdir, exist_ok=True)

    # agent_free=True: 纯文本直返路径(2026-11-16 探针实测定位——agent 化后端
    # 会自行调工具且工具全挂/无视JSON约束, 双消息触发编排, 见 llm_codechat 注释)
    # _RawCaptureLLM: 包装 chat 抓最近一次 LLM 原文(零侵入, 供 steps.jsonl 落盘)
    llm_base = CodeChatLLM(agent_free=True)
    class _RC:
        def __init__(self, base): self.base, self.last_raw = base, None
        def chat(self, messages):
            self.last_raw = self.base.chat(messages)
            return self.last_raw
    llm = _RC(llm_base)

    all_steps: list = []
    task_reports = []
    for tid in sorted(wanted):
        print(f"\n=== {tid}: {TASKS[tid]['goal']}")
        print(f"    路线: {' -> '.join(TASKS[tid]['route'])}")
        n_before = len(all_steps)
        rep = run_task(llm, adjacency, tid, TASKS[tid], all_steps)
        # 回填 raw(脚手架零侵入: run_task 内 steps_log 已含 _RawCaptureLLM 抓到的最近原文)
        task_reports.append(rep)
        print(f"    完成: steps={rep['steps_used']} steps(全局第{n_before+1}..{len(all_steps)}条)"
              f" | 终止={rep['terminated']} ({rep['term_reason']})")
        if rep["auto_observations"]:
            print(f"    自动观测: {len(rep['auto_observations'])} 条")

    # jsonl: 每步一行(含 raw/parsed/state 快照)
    steps_path = os.path.join(outdir, "steps.jsonl")
    with open(steps_path, "w", encoding="utf-8") as f:
        for s in all_steps:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")

    # 汇总 md
    md = ["# 多原子技能集成思维测试·观测报告",
          f"- 时间: {ts} | 后端: CodeChatLLM(TEST_MODEL) | 任务数: {len(task_reports)}",
          f"- 产物: steps.jsonl (每步 raw/parsed/next_options/secs/ThinkState快照)",
          f"- 邻接表: {os.path.basename(ADJACENCY_PATH)} (skills={len(adjacency['entries'])})", ""]

    md += ["## 一、任务执行总览", "",
           "| 任务 | goal | 路线 | 实际步数 | 终止 | 终止原因 |", "|---|---|---|---|---|---|"]
    for r in task_reports:
        md.append(f"| {r['task']} | {r['goal'][:30]} | {'→'.join(s.replace('skill_','') for s in r['route'])}"
                  f" | {r['steps_used']} | {'✓' if r['terminated'] else '✗'} | {r['term_reason']} |")
    md += ["", "各任务探测意图(设计口径, 人工审阅时对照):", ""]
    for r in task_reports:
        md.append(f"- **{r['task']}**: {r['intent']}")

    md += ["", "## 二、每任务终态摘要(goal_anchored / summary / 终态 state)", ""]
    for r in task_reports:
        fs = r["final_state"]
        md += [f"### {r['task']}",
               f"- goal_anchored: {fs.get('goal_anchored','(空)')}",
               f"- summary: {fs.get('summary','(空)')}",
               f"- sub_questions 数: {len(fs.get('sub_questions') or [])}",
               f"- flags: {json.dumps(fs.get('flags',{}), ensure_ascii=False)}", ""]

    md += ["## 三、脚本层自动观测(结构性, 语义问题需人工读 steps.jsonl 判断)", ""]
    any_obs = False
    for r in task_reports:
        for o in r["auto_observations"]:
            any_obs = True
            md.append(f"- **{r['task']}** [{o['kind']}] {o.get('skill','')}: {o['detail']}")
    if not any_obs:
        md.append("- (无结构性观测: 无降级/无schema缺字段/无菜单异常/无state上下文退化)")

    md += ["", "## 四、观测问题清单(人工审阅标注区)", "",
           "> 每条观测请人工标注: **[bug]** prompt/解析缺陷(需修) 或 **[范式待迭代]** 属业务范式本身未成熟(非bug, 记入技能七段草案迭代池)",
           "",
           "| # | 任务/步骤 | 现象 | 分类(字段幻觉/JSON解析异常/state上下文丢失/菜单异常/语义与置信) | 标注 | 处置 |",
           "|---|---|---|---|---|---|",
           "| 1 | (待填) | | | | |"]

    md += ["", "## 五、待回补 Mock 回归用例(人工挑选区)", "",
           "> 从第四节挑出可复现、可断言的现象, 转成 cgraph/test_skill_*.py 的 Mock 用例:",
           "",
           "- (待填)", ""]

    md_path = os.path.join(outdir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"\n产物目录: {outdir}")
    print(f"  steps.jsonl: {len(all_steps)} 步 | report.md 已生成(含人工标注区)")
    print("下一步: 人工审阅 report.md 第四节标注 bug/范式待迭代, 第五节挑 Mock 用例")


if __name__ == "__main__":
    main()