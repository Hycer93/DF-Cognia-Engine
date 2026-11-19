# -*- coding: utf-8 -*-
"""
run_p_skeleton_ab_20261119.py — P-skeleton-complete round6 A/B 复测放量

设定(用户指令 2026-11-19):
  A侧: 裸模型单次直呼(大上下文, 不走 cgraph 技能链)
  B侧: 元核管线全链——anchor→scan→wl→f_reason→Dk→En→Bd→G24→terminate;
       Gs 软挂 router 自主选路不强制; 只跑主线一遍, 不开回流补推演
  样本: ①round6 原样本(身份锚点专利, 历史基准) ②伦理安全新专利(泛化检验)
  通道: CodeChat glm-5.3-flash (config llm.active=codechat, agent_free=True)
产物: data/experiment/p_skeleton_round6_rerun_20261119/
  bare_<slug>.txt / pipeline_<slug>.json / 汇总 metrics.json
约束: 只观测不修改代码; 主线链路自然跑(router 选路含逃逸旁路)
"""
import io, json, os, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "experiment", "p_skeleton_round6_rerun_20261119")
os.makedirs(OUT, exist_ok=True)

SAMPLES = [
    ("anchor_patent", "基于唯一身份锚点的AI全域行为追溯与性格基线自治安全演化架构及方法"),
    ("ethics_patent", "基于职责单一型前置过滤模型的AI伦理安全系统及方法"),
]

TASK_TMPL = ("对专利《%s》进行授权前景审查：对照AI安全与身份认证现有技术评估"
             "新颖性/创造性/实用性最强驳回风险点、权利要求结构性缺陷、说明书公开"
             "充分性问题，并给出最可能被驳回的前3个具体理由及逐条依据。")

BARE_TMPL = ("你是资深专利审查员。任务: %s\n\n专利交底书全文如下:\n---\n%s\n---\n"
             "请输出完整审查意见书: 前3驳回理由(逐条含条款引用+证据) + "
             "权利要求缺陷 + 公开充分性问题 + 总体结论。")


def load_patent(title):
    p = os.path.join(HERE, "data", "patent_evidence", title + ".txt")
    return io.open(p, encoding="utf-8").read()


def run_bare(llm, slug, title, fulltext):
    from cgraph.llm_codechat import CodeChatLLM
    bare = CodeChatLLM(model=None, agent_free=False)     # 裸模型: agent化后端(round6 A侧同款)
    t0 = time.time()
    msg = BARE_TMPL % (TASK_TMPL % title, fulltext)
    out = bare.chat([{"role": "user", "content": msg}])
    secs = round(time.time() - t0, 1)
    f = os.path.join(OUT, "bare_%s.txt" % slug)
    io.open(f, "w", encoding="utf-8", newline="\n").write(out or "")
    return {"slug": slug, "secs": secs, "chars": len(out or "")}


def run_pipeline(llm, slug, title, fulltext):
    from cgraph.executor import ChainExecutor
    from cgraph.llm import LLMClient
    cl = LLMClient()                                     # codechat agent_free=True
    ex = ChainExecutor.__new__(ChainExecutor)
    ex.llm = cl
    task = TASK_TMPL % title
    user_input = task + "\n---ref_material---\n" + fulltext
    t0 = time.time()
    report, state, gate = ex.run_atomic(user_input)
    secs = round(time.time() - t0, 1)
    snap = {
        "slug": slug, "secs": secs,
        "ok": report.ok, "exit_channel": report.exit_channel,
        "fallback_count": report.fallback_count, "task_type": report.task_type,
        "steps": state.steps,
        "trail": state.trail,
        "skill_seq": [e.get("skill_id") for e in state.trail],
        "degraded_steps": [e.get("skill_id") for e in state.trail
                           if e.get("step_summary", "").find("降级") >= 0],
        "hypotheses": state.hypotheses,
        "weakest_link": state.weakest_link,
        "deduction_chains": state.deduction_chains,
        "evidence_net": state.evidence_net,
        "contradiction_pairs": state.contradiction_pairs,
        "bd_matrix": state.bd_matrix,
        "unaddressed": state.unaddressed,
        "topology": state.topology,
        "gaps": state.gaps,
        "gs_view": state.gs_view,
    }
    f = os.path.join(OUT, "pipeline_%s.json" % slug)
    io.open(f, "w", encoding="utf-8", newline="\n").write(
        json.dumps(snap, ensure_ascii=False, indent=1, default=str))
    return snap


def main():
    results = {"bare": [], "pipeline": []}
    for slug, title in SAMPLES:
        fulltext = load_patent(title)
        print("[AB] A侧裸模型 %s (%d字)..." % (slug, len(fulltext)), flush=True)
        results["bare"].append(run_bare(None, slug, title, fulltext))
        print("[AB] B侧管线 %s..." % slug, flush=True)
        results["pipeline"].append(run_pipeline(None, slug, title, fulltext))
    io.open(os.path.join(OUT, "metrics.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print("[AB] 完成, 产物:", OUT, flush=True)


if __name__ == "__main__":
    main()