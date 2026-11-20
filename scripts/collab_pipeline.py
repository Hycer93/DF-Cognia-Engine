# -*- coding: utf-8 -*-
"""多子 Agent 协同推演管线 (P-collab, 2026-11-20)

同任务纵向扇出: N 路子 Agent(独立进程/独立 ChainExecutor/差异化视角软注入)
各跑一遍 run_atomic → F50 聚合层合并。

F50 契约(docs/formula_future_sim_v2_1.md §6, v2 重大修订):
- 禁止对分支终态 S_T^(k) 做加权平均——状态是原生完整结构, 平均会造出
  "既不是A也不是B的幻觉中间态"(r105 公式五硬伤)。
- 只允许聚合可插值的抽象指标(结论向量 𝒴): 𝒴 = Σ W_k · 𝒴_k
- 配套输出三件套: ①综合研判结论 𝒴 ②按权重降序全部原生分支(各自终态/
  前提/风险标签) ③参考基线 = 最高权重分支的原生终态。

W_k 打分(v2.1 软约束精神, Python 确定性派生, 零额外 LLM 调用):
  W_raw = ok × (0.4·收敛质量 + 0.3·证据密度 + 0.3·交付完整度) ± 惩罚
  归一化后 softmax 温度 τ 控制平坦度(默认偏平, 防单路独裁)。

视角注入通道: ThinkState.ref_material(P-C4 素材只读红线字段), 经
run_atomic 的 ---ref_material--- 入口分隔符切分——不碰路由器、不改
链路行为, anchor/skill 自然消费视角声明产生分歧。

产物: out_dir/branches/branch_<i>.json + collab_report.json。
纪律: 零 DB 写(子进程仅读记忆), 报告类认知产出走 judge 管道 pending,
本脚本自身绝不直写主库。

用法:
  python scripts/collab_pipeline.py --task t.txt [--branches 3]
      [--views v1.txt v2.txt v3.txt] [--out data/experiment/collab_xxx]
      [--mode parallel|single]
每份 view txt = 一段视角声明(如"风险审计官视角: 优先证伪..."), 与
--branches 一致; 缺省用内置三视角模板。
"""
import io, json, os, re, sys, time, math, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "---ref_material---"

# 内置三视角模板 v2(冒烟教训 20261120b: 立场对立→各说各话零共识;
# 改"共同中立目标 + 正交维度切面"——三路研究同一问题, 只是主责透镜不同)
DEFAULT_VIEWS = [
    "【协同分支 S1·机制边界】三路子分析师共研同一目标，你主责机制与边界切面：优先厘清该事物的内在机制、成立条件与作用边界（什么情况下成立/失效）。其他切面有队友负责，但你认可的机制结论要写明依据，便于跨路互证。",
    "【协同分支 S2·度量验证】三路子分析师共研同一目标，你主责度量与验证切面：优先识别现有结论缺哪些实证数据/指标才能成立，评估证据充分性与可测性。你认可的底层机制应与队友表述对齐而非另起炉灶，便于跨路互证。",
    "【协同分支 S3·依赖风险】三路子分析师共研同一目标，你主责依赖与风险切面：优先排查外部依赖、隐含假设与失效风险路径。风险判断仍须给证据支撑，不预设立场；与其他切面重叠的结论保持可对照的措辞，便于跨路互证。",
]


def load_cfg():
    cfg = yaml.safe_load(io.open(os.path.join(ROOT, "config", "config.yaml"), encoding="utf-8"))
    p = cfg.get("pipeline") or {}
    return int(p.get("workers", 4)), float(p.get("collab_tau", 0.5))


# ---------------- Worker(子进程入口, 复用 parallel_pipeline._run_one 模式) ----------------

def _run_branch(args):
    idx, task_path, view_text, out_dir = args[:4]
    synth_override = args[4] if len(args) > 4 else True   # A/B: 旧 4 元组调用默认开门
    from cgraph.executor import ChainExecutor
    from cgraph.llm import LLMClient
    snap_path = os.path.join(out_dir, "branches", f"branch_{idx}.json")
    prog_path = os.path.join(out_dir, "branches", f"branch_{idx}.progress.json")

    def _prog(ev, **kw):
        io.open(prog_path, "w", encoding="utf-8").write(json.dumps(
            {"branch": idx, "ts": time.strftime("%H:%M:%S"), "ev": ev, **kw},
            ensure_ascii=False))
    try:
        task = io.open(task_path, encoding="utf-8").read()
        # 视角软注入: 走 ref_material 只读通道, goal 本体一字不动
        user_input = task + ("\n" + MARK + "\n" + view_text if view_text else "")
        cl = LLMClient()
        # 硬超时护栏(冒烟教训 2026-11-20): urlopen 的 timeout 只管 socket 读间隔,
        # SSE 慢速涓流可无限挂 → 每次调用包一层墙钟上限, 超时抛 TimeoutError 交路由降级链
        _orig_chat = cl.chat
        def _chat_timeout(messages, retries=3, _wall=420):
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TE
            with ThreadPoolExecutor(1) as pool:
                fut = pool.submit(_orig_chat, messages, retries)
                try:
                    return fut.result(timeout=_wall)
                except _TE:
                    raise TimeoutError(f"LLM调用超墙钟{_wall}s(网关挂起防护)")
        cl.chat = _chat_timeout
        ex = ChainExecutor.__new__(ChainExecutor)
        ex.llm = cl
        _prog("start")
        t0 = time.time()
        # #9 collab synthesize 旁路(2026-11-20 用户裁决): 仅本脚本注入, 普通任务不开放。
        # 让多路推演分支能穿过 goal_type 门产出 final_deliverable 供 F50 has_deliverable 打分;
        # F60 聚类只取 hypotheses/evidence, 不读 final_deliverable, 共识判定不受干扰。
        report, state, gate = ex.run_atomic(
            user_input, collab_synthesize_override=synth_override)
        secs = round(time.time() - t0, 1)
        _prog("done", secs=secs, steps=state.steps)
        snap = {
            "branch": idx, "view": (view_text or "(无视角)")[:200],
            "secs": secs, "ok": report.ok,
            "exit_channel": report.exit_channel, "fallback_count": report.fallback_count,
            "task_type": report.task_type, "steps": state.steps,
            "trail": [e.get("skill_id") for e in state.trail],
            "summary": state.summary,
            "hypotheses": state.hypotheses, "weakest_link": state.weakest_link,
            "deduction_chains": state.deduction_chains, "gaps": state.gaps,
            "bd_matrix": state.bd_matrix, "unaddressed": state.unaddressed,
            "evidence_net": state.evidence_net,
            "isolated_evidence": state.isolated_evidence,
            "contradiction_pairs": state.contradiction_pairs,
            "selection_rationale": state.selection_rationale,
            "final_deliverable": state.final_deliverable,
            "alternatives_appendix": state.alternatives_appendix,
            "flags": state.flags,
        }
        io.open(snap_path, "w", encoding="utf-8").write(
            json.dumps(snap, ensure_ascii=False, indent=1, default=str))
        return {"branch": idx, "ok": True, "secs": secs, "error": None}
    except Exception as e:
        err = {"branch": idx, "ok": False, "error": f"{type(e).__name__}: {e}"}
        io.open(snap_path, "w", encoding="utf-8").write(
            json.dumps(err, ensure_ascii=False, default=str))
        return err


# ---------------- F50 聚合层(纯函数, 可单测) ----------------

def conclusion_vector(snap: dict) -> dict:
    """从分支快照提取可插值抽象指标(S_T 原生态不进向量, 留清单)。"""
    hyps = snap.get("hypotheses") or []
    chains = snap.get("deduction_chains") or []
    gaps = snap.get("gaps") or []
    en = snap.get("evidence_net") or []
    contra = snap.get("contradiction_pairs") or []
    unaddr = snap.get("unaddressed") or []
    flags = snap.get("flags") or {}
    n = lambda x: len(x) if isinstance(x, list) else (1 if x else 0)
    return {
        "has_deliverable": 1.0 if snap.get("final_deliverable") else 0.0,
        "n_hypotheses": float(n(hyps)),
        "n_chains": float(n(chains)),
        "n_gaps": float(n(gaps)),
        "n_evidence": float(n(en)),
        "n_contradictions": float(n(contra)),
        "n_unaddressed": float(n(unaddr)),
        "confidence": float(flags.get("confidence") or 0.0),
        "fallback_rate": float(snap.get("fallback_count") or 0) / max(snap.get("steps") or 1, 1),
        "converged": 1.0 if snap.get("exit_channel") in ("terminate_suggest", "llm_terminate") else 0.0,
    }


def branch_weight(cv: dict, ok: bool) -> float:
    """确定性打分: 收敛质量/证据密度/交付完整度三分项 + 矛盾与回退惩罚。"""
    if not ok:
        return 0.0
    quality = 0.4 * cv["converged"] + 0.3 * min(
        (cv["n_evidence"] + cv["n_chains"]) / 10.0, 1.0) + 0.3 * cv["has_deliverable"]
    penalty = min(0.1 * cv["n_contradictions"] + 0.5 * cv["fallback_rate"], 0.5)
    return max(quality - penalty, 0.01)


def softmax_weights(raws: list, tau: float) -> list:
    if not raws:
        return []
    m = max(raws)
    exps = [math.exp((r - m) / max(tau, 1e-6)) for r in raws]
    s = sum(exps)
    return [e / s for e in exps]


def aggregate(snaps: list, tau: float = 0.5) -> dict:
    """F50: 𝒴=Σ W_k·𝒴_k 仅聚合指标向量; 原生终态全量保留降序清单。"""
    live = [s for s in snaps if s.get("ok") and "branch" in s]
    dead = [s for s in snaps if s not in live]
    cvs = [conclusion_vector(s) for s in live]
    raws = [branch_weight(cv, True) for cv in cvs]
    ws = softmax_weights(raws, tau)
    keys = ["converged", "has_deliverable", "confidence", "n_gaps", "n_contradictions"]
    y_hat = {k: round(sum(w * cv[k] for w, cv in zip(ws, cvs)), 4)
             for k in keys}
    order = sorted(range(len(live)), key=lambda i: ws[i], reverse=True)
    branches_out = [{
        "rank": r + 1, "weight": round(ws[i], 4), "raw_score": round(raws[i], 4),
        "branch": live[i]["branch"], "view": live[i].get("view"),
        "exit_channel": live[i].get("exit_channel"), "secs": live[i].get("secs"),
        "S_native": {  # 原生终态 S_T^(k): 禁平均, 整支保留
            "summary": live[i].get("summary"),
            "hypotheses": live[i].get("hypotheses"),
            "weakest_link": live[i].get("weakest_link"),
            "gaps": live[i].get("gaps"),
            "final_deliverable": live[i].get("final_deliverable"),
            "alternatives_appendix": live[i].get("alternatives_appendix"),
        },
    } for r, i in enumerate(order)]
    baseline = branches_out[0] if branches_out else None
    # 分歧信号: 同一 gap 主题被几路独立提到(词面粗匹配, 观测用非裁决)
    divergence = _divergence_notes(cvs, live)
    return {
        "formula": "F50 v2.1: Y=Σ W_k·Y_k(仅抽象指标); S_T 禁平均→原生分支清单",
        "tau": tau, "n_live": len(live), "n_dead": len(dead),
        "dead_errors": [{"branch": d.get("branch"), "error": d.get("error")} for d in dead],
        "y_hat": y_hat,                       # ① 综合研判结论向量
        "branches": branches_out,             # ② 权重降序原生分支清单
        "reference_baseline_branch": baseline["branch"] if baseline else None,  # ③
        "divergence_notes": divergence,
    }


def _divergence_notes(cvs: list, live: list) -> list:
    notes = []
    agree_conv = all(c["converged"] > 0.5 for c in cvs) if cvs else False
    if cvs:
        notes.append(f"各路收敛一致性: {'全收敛' if agree_conv else '存在未收敛分支'}")
        spread = max(c["n_gaps"] for c in cvs) - min(c["n_gaps"] for c in cvs)
        notes.append(f"缺口数极差={spread:.0f}(大=视角正交性好, 小=同质冗余)")
        deliv = [i for i, c in enumerate(cvs) if c["has_deliverable"] > 0.5]
        notes.append(f"有终稿分支: {[live[i]['branch'] for i in deliv] or '无'}")
    return notes


def _task_topic_words(task_path) -> list:
    """目标 G 的主题词代理: ASCII 术语 + CJK n-gram(2~4字窗口)候选池, 上限 6。"""
    import re
    from collections import Counter
    try:
        text = io.open(task_path, encoding="utf-8").read()
    except Exception:
        return []
    ascii_terms = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_\-\.]{2,}", text)]
    cjk_ngrams = [r[i:i+n] for r in re.findall(r"[\u4e00-\u9fff]{3,}", text)
                  for n in (4, 3, 2) for i in range(len(r)-n+1)]
    stop = {"请", "分析", "以下", "进行", "给出", "一个", "我们", "如何", "什么",
            "为什么", "然后", "以及", "可以", "需要", "问题", "背景", "要求", "说明",
            "用一", "一段", "回答", "主要", "下一步", "建议"}
    cnt = Counter(t for t in ascii_terms if t not in stop)
    cnt.update(g for g in cjk_ngrams if g not in stop)
    return [w for w, _ in cnt.most_common(6)]


# ---------------- F60 决策层: ΔX 候选派生 + 正演校验 ----------------

def _norm(s) -> str:
    return "".join(str(s or "").split())[:16]


def _bigrams(s: str) -> set:
    return {s[i:i+2] for i in range(len(s)-1)} if len(s) > 1 else ({s} if s else set())


def _sig_tokens(txt: str) -> set:
    """结构化签名 token: 括号内枚举项(顿号/逗号/斜杠切分, ≥2字整词入集,
    其中≥3字再剥常见后缀产出词干) + ASCII 术语。"""
    s = set()
    for m in re.findall(r"[（(]([^）)]{2,80})[）)]", txt):
        for tok in re.split(r"[、,，/和与或\s]+", m):
            tok = tok.strip().lower()
            if len(tok) >= 2:
                s.add(tok)
            if len(tok) >= 3:   # 词干化: "容器配额"→"容器配额"+"容器"
                for suf in ("配额", "限制", "约束", "开销", "资源"):
                    if tok.endswith(suf) and len(tok) > len(suf):
                        s.add(tok[:-len(suf)])
    s.update(t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", txt))
    return s


# 主题范畴词典(顺序即优先级, 首个命中生效; 通用词表, 不随具体任务定制)
_TOPIC_LEX = [
    ("资源约束", ["cpu", "内存", "核数", "资源", "配额", "文件描述符", "cgroup", "物理"]),
    ("硬编码默认", ["硬编码", "常量", "默认值", "写死"]),
    ("配置驱动", ["配置", "config", "yaml"]),
    ("串行结构瓶颈", ["串行", "汇聚", "合并", "同步", "阶段间"]),
    ("通信开销", ["序列化", "通信", "管道", "传输", "ipc", "队列"]),
    ("网关外部限流", ["网关", "限流", "qps", "rpm", "429", "服务端"]),
    ("并发安全写回", ["锁", "并发写", "竞态", "database is locked", "sqlite"]),
    ("降级容错", ["超时", "重试", "退避", "降级", "容错"]),
]

# 双主题歧义虚词(假共识教训 20261120c: 「依赖」一词贯通"外部依赖决定"与
# "串行依赖瓶颈"两主题致误聚)。此类词不得单独构成共享证据。
_STOP_SHARED = {"依赖", "约束", "限制", "开销", "资源", "组件", "模式", "机制"}


def _hyp_topic(txt: str) -> str:
    """首个命中主题; 多主题命中时取命中关键词数最多者(消歧)。"""
    low = "".join(str(txt).split()).lower()
    best, bestn = "", 0
    for name, kws in _TOPIC_LEX:
        n = sum(1 for k in kws if k.lower() in low)
        if n > bestn:
            best, bestn = name, n
    return best


def _kw_tokens(txt: str) -> set:
    """词典命中集: 与 _sig_tokens 并集做共享证据——纯陈述句(无括号枚举)
    靠词典词兜底共享(冒烟反例: 「硬编码常量」vs「硬编码…经验数字」零词面交集)。"""
    low = "".join(str(txt).split()).lower()
    hits = set()
    for _, kws in _TOPIC_LEX:
        hits.update(k.lower() for k in kws if k.lower() in low)
    return hits


def _bigrams_seg(s: str) -> set:
    """宽松匹配集: 字符串全部 2~4 字滑窗, 供中文短语互相包含判定。"""
    s = "".join(str(s).split())
    return {s[i:i+n] for n in (2, 3, 4) for i in range(max(len(s)-n+1, 0))}


def derive_dx(report: dict, min_votes: int = 2) -> dict:
    """F40 式粗解(搜索/共识近似, 非数值逆算): 从原生分支清单派生候选调整 ΔX。
    只读 S_native——聚合层输出即输入, 不重碰 ThinkState。
    共识判据(20261120c 重写)=证据锚定: 同主题范畴 ∧ ≥1 共享签名 token(词干化)
    ∧ ≥2 条独立假设来自不同分支。整句 bigram-Jaccard 已证伪(LLM 跨路改写
    自由度高, 实测 0.05~0.08, commit_green_cards >0.6 阈值仅适用短摘要)。
    hypotheses 的 supporting/contradicting 证据文本并入签名池。"""
    hyp_pool, weak_pool, actions = [], [], []
    for b in report["branches"]:
        sn = b["S_native"]
        for h in (sn.get("hypotheses") or []):
            # 真实 schema(skill_f_reason): {hypothesis, supporting, contradicting, test_next}
            stmt = h.get("hypothesis") if isinstance(h, dict) else str(h)
            ev = ("；".join(map(str, h.get("supporting") or []))
                  + "；" + "；".join(map(str, h.get("contradicting") or []))
                  ) if isinstance(h, dict) else ""
            if stmt:
                hyp_pool.append((b["branch"], str(stmt), str(ev)))
        wl = sn.get("weakest_link")
        # 真实 schema(skill_weakest_link): {location, dimension, score, evidence}
        wk = (wl.get("location") if isinstance(wl, dict) else (str(wl) if wl else ""))
        if wk:
            weak_pool.append((b["branch"], str(wk), ""))
        fd = sn.get("final_deliverable")
        act = fd.get("action") if isinstance(fd, dict) else (fd if isinstance(fd, str) else None)
        if not act and isinstance(fd, dict):
            act = json.dumps(fd, ensure_ascii=False)[:120]
        if not act:                       # 无终稿退到 summary(观测级动作)
            act = sn.get("summary") or None
        if act:
            actions.append({"weight": b["weight"], "branch": b["branch"],
                            "action": str(act)[:160]})

    groups = []                           # 证据锚定聚类
    for br, txt, ev in hyp_pool:
        topic = _hyp_topic(txt)
        sg = _sig_tokens(txt + "；" + ev) | _kw_tokens(txt + ev)
        hit = None
        if topic:
            for g in groups:
                if g["topic"] != topic or br in g["branches"]:
                    continue
                shared = (sg & g["sig"]) - _STOP_SHARED
                if shared:
                    hit = (g, shared)
                    break
        if hit:
            g, shared = hit
            g["branches"].append(br)
            g["sig"] |= sg
            g["shared_evidence"] = sorted(set(g["shared_evidence"]) | shared)
            if len(txt) > len(g["statement"]):
                g["statement"] = txt[:120]   # 取信息量最大表述为代表
        else:
            groups.append({"topic": topic, "statement": txt[:120],
                           "branches": [br], "sig": sg, "shared_evidence": []})
    weak_groups = []
    for br, txt, ev in weak_pool:         # 薄弱点同法: 主题+sig
        topic = _hyp_topic(txt)
        sg = _sig_tokens(txt + "；" + ev) | _kw_tokens(txt + ev)
        hit = next((g for g in weak_groups if topic and g["topic"] == topic
                    and br not in g["branches"] and len(sg & g["sig"]) >= 1), None)
        if hit:
            hit["branches"].append(br); hit["sig"] |= sg
        else:
            weak_groups.append({"topic": topic, "statement": txt[:100],
                                "branches": [br], "sig": sg})

    n = report["n_live"]
    consensus = [{"statement": g["statement"], "topic": g["topic"],
                  "branches": g["branches"], "votes": len(g["branches"]),
                  "shared_evidence": g["shared_evidence"][:8]}
                 for g in groups if len(g["branches"]) >= min_votes]
    shared_weak = [g["statement"] for g in weak_groups if len(g["branches"]) >= min_votes]
    divergent_hyp = [g["statement"] for g in groups if len(g["branches"]) == 1]
    # Ω(x) 代理: 动作按权重降序去重保留全部不同动作(禁平均)
    uniq_actions, seen = [], set()
    for a in sorted(actions, key=lambda x: -x["weight"]):
        k = _norm(a["action"])
        if k not in seen:
            seen.add(k); uniq_actions.append(a)
    return {
        "dx_candidates": {
            "consensus_hypotheses": consensus,          # ≥min_votes 路共证 → 可信前提
            "shared_weak_points": shared_weak,          # 多路同指薄弱点 → 优先修补位
            "candidate_actions_dedup": uniq_actions,    # 按权重降序保留全部不同动作(禁平均)
            "divergent_hypotheses": divergent_hyp,      # 单票假设 = 视角独产, 留人裁
        },
        "vote_stats": {"n_live": n, "hyp_clusters": len(groups),
                       "consensus_clusters": len(consensus)},
        "single_branch_mode": n < 2,                    # 支数不足时"共识"仅为单票自证
        "note": "ΔX 为候选(F40 粗解语义), 未经正演校验不得直接执行",
    }


def forward_check(dx: dict, goal_keywords: list) -> dict:
    """F60 正演校验第一环(零 LLM 静态版): 用目标关键词反查候选是否仍对准 G。
    F(S₀+ΔX,T)−G 的图语义近似 = 共识假设+共同薄弱点词面覆盖目标主题的程度
    (divergent 单票项不计入——未共证不构成方向证据)。全不覆盖→FAIL；部分→PASS_WITH_GAPS。"""
    corpus_parts = [json.dumps(dx["dx_candidates"]["consensus_hypotheses"], ensure_ascii=False),
                    json.dumps(dx["dx_candidates"]["shared_weak_points"], ensure_ascii=False)]
    if dx.get("single_branch_mode"):        # 单路模式退化: 全部候选计入
        corpus_parts.append(json.dumps(
            {"actions": dx["dx_candidates"]["candidate_actions_dedup"],
             "divergent": dx["dx_candidates"]["divergent_hypotheses"]}, ensure_ascii=False))
    corpus = "".join(corpus_parts)
    segset = _bigrams_seg(corpus)
    hits = {kw: (kw in corpus or _bigrams_seg(kw) & segset != set())
            for kw in goal_keywords if kw.strip()}
    covered = sum(hits.values())
    verdict = ("NO_CONSENSUS" if dx["vote_stats"]["consensus_clusters"] == 0
               and not dx.get("single_branch_mode") else
               "FAIL" if not covered else
               "PASS" if covered == len(hits) else "PASS_WITH_GAPS")
    return {"goal_keyword_coverage": hits,
            "covered": f"{covered}/{len(hits)}", "verdict": verdict}


def run_f60(out_dir: str, goal_keywords: list) -> dict:
    """离线入口: 对已落盘 collab_report.json 做 ΔX 派生 + 正演初筛。"""
    rep = json.loads(io.open(os.path.join(out_dir, "collab_report.json"),
                             encoding="utf-8").read())
    dx = derive_dx(rep)
    fc = forward_check(dx, goal_keywords)
    result = {"f60": dx, "forward_check_static": fc}
    io.open(os.path.join(out_dir, "f60_dx.json"), "w", encoding="utf-8").write(
        json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


# ---------------- 编排入口 ----------------

def run_collab(task_path, views, out_dir, mode="parallel", goal_keywords=None,
               synth_override=True):
    workers, tau = load_cfg()
    os.makedirs(os.path.join(out_dir, "branches"), exist_ok=True)
    jobs = [(i, task_path, views[i] if i < len(views) else "", out_dir, synth_override)
            for i in range(len(views) or 1)]
    print(f"[collab] branches={len(jobs)} mode={mode} tau={tau} "
          f"synth_override={synth_override}", flush=True)
    t0 = time.time()
    if mode == "parallel":
        from multiprocessing import get_context
        with get_context("spawn").Pool(min(workers, len(jobs))) as pool:
            results = pool.map(_run_branch, jobs)
    else:
        results = [_run_branch(j) for j in jobs]
    wall = round(time.time() - t0, 1)
    snaps = []
    for r in results:
        p = os.path.join(out_dir, "branches", f"branch_{r['branch']}.json")
        if os.path.exists(p):
            snaps.append(json.loads(io.open(p, encoding="utf-8").read()))
    report = aggregate(snaps, tau)
    # F60 第一环: ΔX 候选派生 + 静态正演初筛(零 LLM; 深度正演复用 minloop.verify)
    try:
        dx = derive_dx(report)
        gk = goal_keywords or _task_topic_words(task_path)
        report["f60"] = dx
        report["forward_check_static"] = forward_check(dx, gk)
    except Exception as e:
        report["f60_error"] = f"{type(e).__name__}: {e}"
    report["meta"] = {"wall_secs": wall, "task_file": task_path,
                      "results": results}
    out = os.path.join(out_dir, "collab_report.json")
    io.open(out, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print(f"[collab] done wall={wall}s report={out}", flush=True)
    print(f"[F50] Y_hat={report['y_hat']} baseline=branch{report['reference_baseline_branch']}",
          flush=True)
    for b in report["branches"]:
        print(f"  #{b['rank']} branch{b['branch']} W={b['weight']} exit={b['exit_channel']}",
              flush=True)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="任务txt(全文)")
    ap.add_argument("--branches", type=int, default=3)
    ap.add_argument("--views", nargs="*", default=None,
                    help=f"视角txt列表, 数量={ '--branches' }; 缺省用内置三视角")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["single", "parallel"], default=None,
                    help="缺省跟随 config pipeline.mode 逻辑(collab 恒并行优先)")
    ap.add_argument("--synth-override", dest="synth_override", action="store_true", default=True,
                    help="collab synthesize 旁路(#9), 默认开; A/B 对照组用 --no-synth-override")
    ap.add_argument("--no-synth-override", dest="synth_override", action="store_false",
                    help="关闭 synthesize 旁路(对照组, 复现老行为)")
    a = ap.parse_args()
    views = ([io.open(v, encoding="utf-8").read() for v in a.views]
             if a.views else DEFAULT_VIEWS[:a.branches])
    mode = a.mode or "parallel"
    run_collab(a.task, views, a.out, mode=mode, synth_override=a.synth_override)
