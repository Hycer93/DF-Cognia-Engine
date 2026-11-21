# memory_judge.py — 三件套件②：judge 判定挂 Jw-G21 钩子位（2026-11-09 定案 5013c410）
#
# 考古来源：Jw-G21"新认知评估固化形式"行为（mind_graph.json 骨架级触发）+
#           前身 judge_flash_value 方向（corpus2：对话结束后异步调小模型判定）
# 定位：入库时机从"LLM 自觉"变"拓扑强制"——Jw trigger=执行完成，本模块消费该触发。
#
# 判定卡五问（门槛链非评分，ALL 语义对齐别名六条件风格）：
#   Q1 这轮有新认知产生吗？（没有→discard 记日志，不算积压）
#   Q2 有定义本体吗？（EEA 教训：空壳不入）
#   Q3 归五类哪一类？（消费 schema.MEMORY_CLASS_ENUM）
#   Q4 血缘？（孤条降权；有实体链/同源先例升权；同时跑冲突检测——选A轻量：只打
#      conflict 疑似标记不拦截，仲裁归既有冲突队列，judge 不接管全局冲突仲裁）
#   Q5 分池：知识层（可复现方法论）→六标准加验去个体化/普适性；
#           经验层（runtime事故/裁决/里程碑）→豁免（个体性时间性是 v3 特性，19d9406f 裁定）
#
# 三态输出与契约（2026-11-09 用户裁决）：
#   store   → 免人工确认（Jw 自主闭环定位），直接 memory_hook.add；但 store≠免审计——
#             每条附带完整五问判定日志快照（judge_log.jsonl），任何记忆可回溯当时判定理由
#   hold    → 候选池 store_pending.jsonl。放行契约（严格，防门槛软化）：
#             同一条 hold 在同实体链/同会话上下文再次命中 Jw 触发时，judge 重跑全套五问
#             + 追加第六问"相比上次 hold，是否补充了缺失证据/消除了信息缺口？"——
#             证据补齐→store；依然不足→维持 hold 并记二次评审；出现反向证据→discard。
#             单纯"又触发了一次"不等于转正。
#   discard → 记判定日志（append-only），理由可审计
#
# 健壮性（2026-11-09 用户四隐患修订）：
#   - 单次触发批量上限 JUDGE_BATCH_LIMIT=8 条候选，超限截断+E4 告警（防会话收尾风暴）
#   - LLM 判定调用异常/超时/畸形 → 自动 hold + judge_failure 标记，E4 计入积压。不丢、不强行 discard
#
# 文件职责边界：judge 不动 mind_graph.json（消费 Jw trigger，不改它）；memory_hook.add 是唯一落库口

import json
import os
import sys
import io
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_organize import append_jsonl, load_memories

MEM_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(MEM_DIR), "data", "memory")
STORE_PENDING_JSONL = os.path.join(DATA_DIR, "store_pending.jsonl")
JUDGE_LOG_JSONL = os.path.join(DATA_DIR, "judge_log.jsonl")

JUDGE_BATCH_LIMIT = 8          # [先验-业务设定] 单次触发批量上限，超限截断+E4告警
HOLD_MAX_AGE_DAYS = 14        # [先验-可校准→calib] hold 沉睡债务线，E4 老化报警

# LLM 判定卡（2026-11-21 P1-1 转正）。五问契约原样继承，输出严格 JSON。
_JUDGE_SYSTEM_PROMPT = """你是记忆入库判定卡。对每条候选记忆回答五问，只输出 JSON，不要任何解释文字或 markdown 围栏。

五问（门槛链，任一不过即降级）：
Q1 新认知：这轮是否产生了新的、可复用的认知？占位/寒暄/重复表述 → 不算。
Q2 定义本体：是否有实质内容（具体做法/结论/证据），而非空壳标题？
Q3 归类：归入且仅归入以下五类之一，或 null（普通记录）：
   skill_refine（方法/工程改进）、scar_case（踩坑/失效教训）、
   strategy_evolve（路线/裁决演进）、relation_milestone（实体血缘打通）、
   key_pact（规约/契约/边界约束）。
Q4 血缘：与已有记忆是否有同源/实体链关系？candidates 里 bloodline_hint 给出本地预计算的
   best_overlap（2-gram 重叠数值）与 conflict_suspect 作辅助参考；数值证据以代码为准，
   你只做语义综合。孤条无血缘 → 降权；疑似高度同源重复/冲突 → conflict_suspect=true。
Q5 分池：知识层（可复现方法论，须去个体化）还是经验层（事故/裁决/里程碑，可含个体性）？

输出 JSON（严格）：
{"verdicts":[{"idx":0,"verdict":"store","memory_class":"scar_case","conflict_suspect":false,"reasons":["Q1...","Q2..."],"evidence_gap":null}]}
verdict 取值规则：五问全过=store；有可补缺口=hold（evidence_gap 写清缺什么）；无新认知=discard。
memory_class 取值必须是上述五类之一或 null。"""


def _load_pending():
    if not os.path.exists(STORE_PENDING_JSONL):
        return []
    with io.open(STORE_PENDING_JSONL, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _bloodline(c):
    """Q4 血缘本地预计算（2026-11-21 P1-1：桩与 LLM 路径共用）。

    返回 (gram_set, conflict_suspect, best_overlap)。
    数值证据由代码算出（不交 LLM 猜），LLM 只做语义综合。
    """
    from memory_relations import grams  # 词级锚点判定走 2-gram（bge 切换适配 2026-11-11）
    summary = (c.get("summary") or "").strip()
    gram_set = grams(summary)
    suspect, best_overlap = False, 0
    for m in load_memories():
        if (m.get("confidence") or "") in ("L0", "L1"):
            ov = len(gram_set & grams(m.get("summary") or ""))
            best_overlap = max(best_overlap, ov)
            if ov >= 6:
                suspect = True; break
    return gram_set, suspect, best_overlap


def _stub_one(c):
    """规则桩单条判定（原 _judge_call 逻辑逐字搬迁，2026-11-21 P1-1）。

    供 _judge_call_stub 全量降级 与 _judge_call_llm 单条降级 复用。
    P2-7（2026-11-21）：Q1 命中即 early return discard，阻断后续 Q2-Q4。
    历史缺陷：Q1 置 discard 后无 return，孤条场景被 Q4 覆写为 hold；A/B 评估
    现存 hold 池 13 条零影响、22 条样本 1 条 hold→discard（4.55%），已裁决修复。
    """
    import schema
    summary = (c.get("summary") or "").strip()
    detail = (c.get("detail") or "").strip()
    reasons, verdict = [], "store"
    # Q1 新认知（桩口径：summary 非占位非空）——命中即一票否决 discard，不再往下判
    if not summary or len(summary) < 8:
        reasons.append("Q1fail: summary空/占位，无新认知")
        return {"verdict": "discard", "memory_class": c.get("memory_class"),
                "conflict_suspect": False, "reasons": reasons,
                "evidence_gap": None}
    # Q2 定义本体（桩口径：detail 提供实质内容或明确定义）
    elif not detail or len(detail) < 30:
        verdict = "hold"; reasons.append("Q2fail: 定义本体不足，缺 detail 实质内容")
        return {"verdict": verdict, "memory_class": c.get("memory_class"),
                "conflict_suspect": False, "reasons": reasons,
                "evidence_gap": "detail 过薄，等补定义/来源/血缘"}
    # Q3 归类（件② 2026-11-10 用户终裁改版：null 是合法值不是缺失）
    mc = c.get("memory_class")
    if mc not in schema.MEMORY_CLASS_ENUM and mc is not None:
        verdict = "hold"; reasons.append(f"Q3fail: memory_class 非法字符串({mc})；null 合法=普通记录")
        return {"verdict": verdict, "memory_class": mc, "conflict_suspect": False,
                "reasons": reasons, "evidence_gap": "分类取值非法（白名单外字符串）"}
    # behavior 软先验表（件②：仅可疑提示非硬拦截——字面枚举不可靠，结构性判据才可靠）
    _PRIOR = {"scar_case": ("Dl", "Bc"), "skill_refine": ("Jw", "Cf"),
              "strategy_evolve": ("Jw", "Ht"), "relation_milestone": ("Em", "Eo"),
              "key_pact": ("Ht", "Iv")}
    beh = c.get("behavior")
    if mc in _PRIOR and beh not in _PRIOR[mc]:
        reasons.append(f"Q3hint: class_behavior_mismatch_suspect（{mc} 先验 behavior={_PRIOR[mc]}，实际 {beh}）——软提示不拦截")
    if mc is None:
        # 内容五类特征词粗筛（结构性判断的桩版；LLM 判定卡上线后语义化）
        _FEATURES = {
            "strategy_evolve": ("路线", "范式", "架构切换", "终裁", "裁决定案"),
            "skill_refine": ("阈值", "调优", "微调", "优化", "工程改进"),
            "scar_case": ("事故", "bug", "失效", "错误方案", "踩坑", "误"),
            "relation_milestone": ("实体", "血缘", "关联打通", "链路"),
            "key_pact": ("规约", "契约", "准入", "边界约束", "口径"),
        }
        hit = next((k for k, ws in _FEATURES.items() if any(w in summary for w in ws)), None)
        if hit:
            reasons.append(f"Q3hint: class_missing_suspect——内容特征疑似 {hit} 但 memory_class=null，已放行待 E4 high_value_content_without_class 复核")
    # Q4 血缘+冲突疑似（桩口径：与库内高置信条目 2-gram 重叠——重叠高即疑似同源，
    #        标 conflict_suspect 不拦截；零重叠=孤条降权→hold 而非 discard）
    _gram_set, suspect, best_overlap = _bloodline(c)
    if best_overlap == 0:
        verdict = "hold"; reasons.append("Q4fail: 孤条零血缘，无实体链/同源先例")
        return {"verdict": verdict, "memory_class": mc, "conflict_suspect": False,
                "reasons": reasons, "evidence_gap": "缺血缘互指，等实体链或同源先例"}
    reasons.append(f"Q1-4 pass（血缘重叠gram={best_overlap}，conflict_suspect={suspect}）")
    # Q5 分池（桩口径：memory_class∈{skill_refine} 且无个体指涉词 → 知识层六标准弱验；
    #        其余经验层豁免。LLM 卡上线后语义化）
    if mc == "skill_refine" and any(w in summary for w in ("我", "用户", "主人", "厂长")):
        verdict = "hold"; reasons.append("Q5fail: 疑似知识层但含个体指涉，需去个体化或改经验层")
        return {"verdict": verdict, "memory_class": mc, "conflict_suspect": suspect,
                "reasons": reasons, "evidence_gap": "知识层/经验层归属存疑"}
    reasons.append("Q5 pass")
    return {"verdict": verdict, "memory_class": mc,
            "conflict_suspect": suspect, "reasons": reasons, "evidence_gap": None}


def _judge_call_stub(candidates, context):
    """纯规则桩全量判定（JUDGE_USE_LLM=0 或 LLM 整体不可用时）。"""
    return {i: _stub_one(c) for i, c in enumerate(candidates)}


def _judge_call_llm(candidates, context):
    """LLM 五问判定主路径。成功返回 {idx: verdict_dict}；任何整体性失败返回 None。

    整体失败（→ 上层切桩）：token 获取失败 / chat 返回 None / JSON 整批解析失败。
    单条畸形：仅该条 _stub_one 降级，其余保留 LLM 结果。
    """
    from cgraph.json_utils import extract_json_obj
    from cgraph.llm_codechat import CodeChatLLM

    # 构造 payload（含 Q4 本地预计算 bloodline_hint 作辅助）
    items = []
    for i, c in enumerate(candidates):
        _gs, suspect, overlap = _bloodline(c)
        items.append({
            "idx": i,
            "summary": (c.get("summary") or "")[:200],
            "detail": (c.get("detail") or "")[:800],
            "memory_class": c.get("memory_class"),
            "domain": c.get("domain"), "behavior": c.get("behavior"),
            "relation": c.get("relation"), "confidence": c.get("confidence"),
            "bloodline_hint": {"best_overlap": overlap, "conflict_suspect": suspect},
        })
    payload = json.dumps({"context": context, "candidates": items}, ensure_ascii=False)

    llm = CodeChatLLM(model=os.environ.get("JUDGE_LLM_MODEL") or None,
                      agent_free=True, timeout=120)
    resp = llm.chat([{"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                     {"role": "user", "content": payload}])
    if not resp:
        return None
    obj = extract_json_obj(resp)
    if not obj or not isinstance(obj.get("verdicts"), list):
        return None

    # 逐条校验 + 单条降级
    import schema
    out = {}
    for v in obj["verdicts"]:
        if not isinstance(v, dict):
            continue
        try:
            idx = int(v.get("idx"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)):
            continue
        verdict = v.get("verdict")
        if verdict not in ("store", "hold", "discard"):
            continue                         # 非法 verdict → 留给下方补齐时降桩
        mc = v.get("memory_class")
        if mc is not None and mc not in schema.MEMORY_CLASS_ENUM:
            mc = candidates[idx].get("memory_class")   # 非法分类 → 回退候选原值
        reasons = v.get("reasons")
        if not isinstance(reasons, list):
            reasons = ["llm_no_reason"]
        out[idx] = {"verdict": verdict, "memory_class": mc,
                    "conflict_suspect": bool(v.get("conflict_suspect")),
                    "reasons": [str(r) for r in reasons],
                    "evidence_gap": (v.get("evidence_gap") if verdict == "hold" else None)}
    # 漏答 / 畸形条 → 单条降桩补齐
    for i in range(len(candidates)):
        if i not in out:
            out[i] = _stub_one(candidates[i])
            out[i]["_fallback"] = "stub_one"
    return out


def _judge_call(candidates, context):
    """判定调度器（2026-11-21 P1-1）。

    优先级：JUDGE_USE_LLM=0 → 纯桩；否则 LLM 优先，整体失败切桩。
    返回 {idx: verdict_dict}；每条 verdict_dict 额外带 _source: "llm"|"stub"。
    异常仍由调用方 judge_and_store 捕获走 fallback-hold（契约不变）。
    """
    if os.environ.get("JUDGE_USE_LLM", "1") == "0":
        out = _judge_call_stub(candidates, context)
        for v in out.values():
            v["_source"] = "stub"
        return out
    try:
        out = _judge_call_llm(candidates, context)
        if out is None:
            raise RuntimeError("judge_llm_unavailable")
        for v in out.values():
            v.setdefault("_source", "llm")
            if v.get("_fallback") == "stub_one":
                v["_source"] = "stub"
        return out
    except Exception as e:  # noqa: BLE001  LLM 不可用（token/网络/解析）→ 整体切桩，不阻断
        print(f"[memory_judge] LLM 判定不可用，切规则桩: {str(e)[:120]}", file=sys.stderr)
        out = _judge_call_stub(candidates, context)
        for v in out.values():
            v["_source"] = "stub"
        return out


def judge_and_store(candidates, context=None):
    """Jw 钩子入口：执行完成后调用（裁决落地/修复验证完成/会话收尾盘点）。

    candidates: [{summary, detail, source, event_ts, memory_class, domain, behavior,
                  relation, confidence, asset_class, repro}, ...]
    context: {"trigger": "decision|fix|session_end", "session_id": str, "entity_hint": [inst_id..]}
    返回报告 dict（供调用方/E4 消费）。
    """
    now = datetime.now().isoformat()
    context = context or {"trigger": "manual"}
    report = {"ts": now, "trigger": context.get("trigger"),
              "received": len(candidates), "stored": 0, "held": 0, "discarded": 0,
              "truncated": 0, "judge_failures": 0}

    # 批量上限：截断告警不静默丢（截断条目留候选由下次触发消化，记日志）
    if len(candidates) > JUDGE_BATCH_LIMIT:
        report["truncated"] = len(candidates) - JUDGE_BATCH_LIMIT
        for c in candidates[JUDGE_BATCH_LIMIT:]:
            append_jsonl(JUDGE_LOG_JSONL, {"ts": now, "op": "batch_truncated",
                                           "summary": (c.get("summary") or "")[:200],
                                           "reason": "超出单次批量上限，留待下次触发"})
        candidates = candidates[:JUDGE_BATCH_LIMIT]

    # LLM/规则桩判定 + 异常 fallback（用户裁决：异常→hold 不丢不强discard）
    try:
        verdicts = _judge_call(candidates, context)
    except Exception as e:
        verdicts = {}
        for i in range(len(candidates)):
            append_jsonl(JUDGE_LOG_JSONL, {"ts": now, "op": "judge_failure",
                                           "summary": (candidates[i].get("summary") or "")[:200],
                                           "error": str(e)[:200]})
        for c in candidates:
            append_jsonl(STORE_PENDING_JSONL, {**c, "hold_ts": now,
                                               "hold_reason": "judge_failure", "reviews": []})
        report["judge_failures"] = len(candidates); report["held"] = len(candidates)
        return report

    import memory_hook
    for i, c in enumerate(candidates):
        v = verdicts.get(i, {"verdict": "hold", "memory_class": c.get("memory_class"),
                             "conflict_suspect": False, "reasons": ["no_verdict"],
                             "evidence_gap": "判定未返回，fallback hold"})
        log_entry = {"ts": now, "op": "judge", "trigger": context.get("trigger"),
                     # 2026-11-21 P1-1：judge_source 强制记录判定路径(llm/stub)，供 A/B 口径审计
                     "judge_source": v.get("_source", "stub"),
                     "summary": (c.get("summary") or "")[:200],
                     "verdict": v["verdict"], "memory_class": v.get("memory_class"),
                     "conflict_suspect": v.get("conflict_suspect"), "reasons": v.get("reasons"),
                     "evidence_gap": v.get("evidence_gap"), "snapshot": c}
        if v["verdict"] == "store":
            # store 免人确认（Jw 自主闭环）；conflict_suspect 只打标签入 detail，不拦截
            detail = c.get("detail") or ""
            if v.get("conflict_suspect"):
                detail += "\n[judge标注] conflict_suspect=true——与库内高置信条目疑似同源，待冲突队列仲裁。"
            r = memory_hook.add(
                domain=c.get("domain", "000-001"), behavior=c.get("behavior", "Jw"),
                relation=c.get("relation", "P"), confidence=c.get("confidence", "L2"),
                summary=c["summary"], detail=detail, source=c.get("source", "judge-hook"),
                asset_class=c.get("asset_class", "runtime"), repro=c.get("repro"),
                event_ts=c.get("event_ts"), memory_class=v.get("memory_class"))
            log_entry["store_result"] = r.get("store", {})
            report["stored"] += 1
        elif v["verdict"] == "hold":
            append_jsonl(STORE_PENDING_JSONL, {**c, "hold_ts": now,
                                               "hold_reason": v.get("evidence_gap") or "held",
                                               "reviews": [{"ts": now, "reasons": v.get("reasons")}]})
            report["held"] += 1
        else:
            report["discarded"] += 1
        append_jsonl(JUDGE_LOG_JSONL, log_entry)   # store≠免审计：全判定快照 append-only
    return report

def review_holds(context=None):
    """hold 转正契约（2026-11-09 用户裁决，防门槛软化）：

    同一条 hold 在后续同实体链/同会话上下文再次命中 Jw 触发时调用本接口。
    重跑全套五问 + 追加第六问：'相比上次 hold，是否补充了缺失证据/消除了信息缺口？'
      证据补齐（detail 扩充/血缘出现/分类确定）→ store
      依然不足 → 维持 hold，reviews 追加二次评审记录
      出现反向证据 → discard（记日志）
    单纯'又触发了一次'不等于转正——每次 review 都要回答第六问，无证据不得放行。
    """
    import schema
    from memory_relations import grams
    now = datetime.now().isoformat()
    pending = _load_pending()
    survivors, report = [], {"ts": now, "reviewed": len(pending),
                             "promoted": 0, "kept": 0, "dropped": 0}
    import memory_hook
    for p in pending:
        summary = (p.get("summary") or "").strip()
        detail = (p.get("detail") or "").strip()
        gap = p.get("hold_reason", "")
        reviews = p.get("reviews", [])
        # 第六问：证据缺口是否补齐（规则桩：以 hold_reason 对应的缺口逐项复查）
        promoted, dropped, why = False, False, ""
        if p.get("hold_reason") == "judge_failure":
            why = "judge_failure 不走自动转正——必须修复判定通道后由人复核（E4 报警项）"
        elif len(summary) < 8:
            dropped = True; why = "Q1 反向证据：summary 仍空/占位，discard"
        elif len(detail) < 30:
            why = "Q6 证据仍不足：detail 依旧过薄"
        elif p.get("memory_class") not in schema.MEMORY_CLASS_ENUM:
            why = "Q6 分类仍未定"
        else:
            gram_set = grams(summary)
            linked = any(len(gram_set & grams(m.get("summary") or "")) > 0
                         for m in load_memories())
            if linked:
                promoted = True; why = "Q6 血缘已出现/证据补齐，转正"
            else:
                why = "Q6 仍是孤条零血缘，维持 hold"
        if promoted:
            r = memory_hook.add(
                domain=p.get("domain", "000-001"), behavior=p.get("behavior", "Jw"),
                relation=p.get("relation", "P"), confidence=p.get("confidence", "L2"),
                summary=summary, detail=detail, source=p.get("source", "judge-hook"),
                asset_class=p.get("asset_class", "runtime"), repro=p.get("repro"),
                event_ts=p.get("event_ts"), memory_class=p.get("memory_class"))
            append_jsonl(JUDGE_LOG_JSONL, {"ts": now, "op": "hold_promoted",
                                           "summary": summary[:200],
                                           "prior_reviews": reviews, "why": why,
                                           "store_result": r.get("store", {})})
            report["promoted"] += 1
        elif dropped:
            append_jsonl(JUDGE_LOG_JSONL, {"ts": now, "op": "hold_dropped",
                                           "summary": summary[:200], "why": why})
            report["dropped"] += 1
        else:
            reviews = reviews + [{"ts": now, "why": why}]
            survivors.append({**p, "reviews": reviews})
            report["kept"] += 1
    with io.open(STORE_PENDING_JSONL, "w", encoding="utf-8", newline="\n") as f:
        for p in survivors:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return report
