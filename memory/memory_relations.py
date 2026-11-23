# memory_relations.py — 认知关联层（三层职责之第二层，用户第六轮裁决）
#
# 职责（承接旧 Op5 关联补链）：摘要向量 + DFCP 过滤、相似度召回、同源判定、
# 冲突打标、entity_id 本体图谱维护。
#
# 风险封闭：本层所有判定结果只写入独立的关系图谱文件，不修改原子层主记录的
# inst_id/full_hash/dfcp_tag；图谱可单独关闭、重算、回滚，不破坏底层账本。
#
# 认知不幂等对策（方案六·固有代价③）：每条关系强制带 computed_at + embedding_base，
# 判定结果可复现当时基线。
#
# Week1 向量实现：字符 2-gram TF 余弦（零依赖、可跑通全链路）；
# 接口设计为可替换——Week2 换真嵌入基座时只改 _embed()，图谱带 embedding_base
# 字段标记基线版本，换基座后老关系作废重算。

import json
import io
import os
import math
import uuid
import numpy as np
from collections import Counter
from datetime import datetime

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import constants as C
import embed_semantic
import topic_layer          # Topic锚层（规约4单向闸门; 开关默认关, 零行为变化）
from memory_organize import load_memories, MEM_DIR, append_jsonl

RELATION_GRAPH_JSONL = os.path.join(MEM_DIR, "relation_graph.jsonl")   # 本体图谱（软层）
CONFLICT_QUEUE_JSONL = os.path.join(MEM_DIR, "conflict_queue.jsonl")  # 冲突评审流水线入口

EMBEDDING_BASE = "bge-small-zh-v1.5-onnx-int8"   # v1: char2gram-tf-v0 → 语义基座（2026-11-11 §7.2 强制流程切换）
# 切换依据：专利 P37/P39/P43 模板句式假撞实验——char2gram 同物/跨物无分离带（0.57-0.68 重叠），
# bge 完整句 同物0.86 / 跨物0.48-0.51，0.70 附近有清晰边界（logs/embed_switch_20261111）


# ---- 向量接口（v1: bge-small-zh ONNX 语义嵌入，embed_semantic.py 独立实现）----
def _embed(text: str) -> np.ndarray:
    """中文语义向量（512 维归一化）。单条接口保持与 v0 同签名兼容（dict→ndarray 内部消化）。"""
    return embed_semantic.embed_cached(text)


def _cosine(a, b) -> float:
    """已归一化向量点积=余弦；空值防御与 v0 一致。"""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))


def grams(text: str) -> set:
    """字符 2-gram 集合（锚点词匹配专用——语义向量无 keys()，关键词层保留旧法）。
    检索锚点/停用词过滤是词级操作，语义向量不适配，独立保留 2-gram 实现。"""
    t = text.replace(" ", "").replace("\n", "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


# ---- DFCP 过滤：编码坐标近邻度（四维权重里的编码匹配项，先验过滤用）----
def _dfcp_proximity(mine: dict, other: dict, sem: float | None = None) -> float:
    """DFCP 先验只压不抬（2026-11-11 用户裁决修复①，二修）：
    同域=返回当前语义分本身（dfcp 项=sem×0.3 → combined=sem，语义自负其责，
    一修的固定 0.5 曾致同域真 augment/equivalent 全被压成 new）；
    同域不同子域 0.5 / 跨域同行为 0.3 / 跨域 0.15——异域硬压是本职。
    sem 仅为同域回声用，不影响异域判定。"""
    d1, d2 = mine["domain"], other["domain"]
    if d1 == d2:
        return sem if sem is not None else 0.5
    if d1.split("-")[0] == d2.split("-")[0]:
        return 0.5
    if mine["behavior"] == other["behavior"]:   # 行为检字法：跨域同骨架仍可比较
        return 0.3
    return 0.15


def _combined_sim(mine: dict, other: dict, vec_mine: dict, vec_other: dict) -> float:
    """加权相似度 = 语义余弦 × 0.7 + DFCP 先验 × 0.3（语义为主、编码护航）。
    常量在此内联标注：[先验-业务设定] 0.7/0.3 语义/编码配比，盲测数据后入常量表。
    注意：同域时 _dfcp_proximity 回声 sem → combined == sem（只压不抬）。"""
    sem = _cosine(vec_mine, vec_other)
    dfcp = _dfcp_proximity(mine["dfcp_tag"], other["dfcp_tag"], sem=sem)
    return sem * 0.7 + dfcp * 0.3


# ---- 三级级联判定（§4.6 用户裁决2026-11-06：摘要→详情→原文逐层下钻；2026-11-11落码）----
# 设计：摘要层过线（≥conflict）的候选对，自动下钻 detail 比对；detail 仍过线才下钻
# source_ref/原文；任一层语义显著回落（CASCADE_DROP=0.15）即判"非同源"——模板句式在
# 摘要层制造的假相似，会在信息量更高的层被稀释。实验依据：P17×P42 摘要0.702→详情0.559。
CASCADE_DROP = 0.15          # [先验-可校准] 下钻后回落幅度判定"显著"


# ---- 冲突判定前置过滤器（用户裁决2026-11-12 ②④：同entity折叠+占位分流）----
# 过滤器顺序（规约）：先查entity归属 → 再做占位检测 → 再执行三层级联嵌入比对。
# ① 同entity互撞折叠：两张卡已挂同一entity → 不进冲突判定流水线，仅留审计日志。
#    例外：演化链关系（evolved_into/derive）的卡对不同entity，照常走冲突判定——
#    防演化分支后继推翻前驱时漏抓（P10→P22口径）。
# ② 占位检测前置：任一方detail判定为占位 → 级联停在summary层，不做detail嵌入比对；
#    不是dismiss而是分流到摘要-only可疑对队列（summary_only_suspects.jsonl），
#    防止无效detail拉高分数制造假分，同时保留真实分叉的告警通道。
# ③ 实体锚增强（中期，未落码）：命中专利编号/股票标的等强标识符 → 路由到谱系/交易
#    事件核查分支，实体特征是裁决线索非最终判据。
# ④ 换嵌入基座（BGE）：后置大手术，触发指标主看检索召回/gate命中率/别名匹配质量，
#    不为冲突误报驱动；换基座须整套重标（conflict/augment/equivalent阈值+CASCADE_DROP
#    +全库向量+E8回归集），级联逻辑本身保留。
SUMMARY_ONLY_SUSPECTS_JSONL = os.path.join(MEM_DIR, "summary_only_suspects.jsonl")
ENTITY_COLLISION_SKIP_JSONL = os.path.join(MEM_DIR, "entity_collision_skip.jsonl")
_PLACEHOLDER_HINTS_OK = ("极简规约豁免", "强谓词/指针", "高相似但有差异化")


def _is_placeholder_card(m: dict) -> bool:
    """卡片 detail 是否为占位（复用质检门禁 detect_placeholder 复合判定）。

    两类占位形态（2026-11-12 冲突终审实证）：
    a) 复读型（detect_placeholder 原口径）：detail≈summary 复读且无新增实体/谓词/指针；
    b) 无信息短文本型（本处补充）：detail 极短（<15字，如"复盘详情"/"d1"）或为
       领域路径标签（"详情层：A·B·C"模板，载体是分类路径非内容）。
    b 类不走质检门禁（门禁判据依赖 detail≈summary 相似度，对短文本恒不触发），
    在此处独立判定——口径：len<15 且不含数字实体/文件指针/谓词标点=占位。"""
    try:
        from memory_quality_gate import detect_placeholder
        ph = detect_placeholder(m.get("summary", ""), m.get("detail", ""), m.get("source_ref"))
        if ph.get("is_placeholder"):
            return True
    except Exception:
        pass
    d = (m.get("detail") or "").strip()
    if not d:
        return False
    # 领域路径标签模板（载体是分类路径非内容）——定位式拦截，不限长度
    if d.startswith("详情层：") or d.startswith("详情层:"):
        return True
    if len(d) >= 15:
        return False
    # 短文本豁免条件：含量词型规约数字（"20条"式）、文件指针、或谓词标点+长度≥10
    import re as _re
    if _re.search(r"\d+[条个次字天%倍张份]", d):
        return False
    if _re.search(r"[\w\-/\\]+\.(txt|md|jsonl|py)\b", d):
        return False
    if len(d) >= 10 and any(p in d for p in (":", "：", "=", "→", "，")):
        return False
    if d.startswith("详情层：") or d.startswith("详情层:"):
        return True   # 领域路径标签模板（载体是分类路径非内容）
    return True


def _cascade_sem(mine: dict, other: dict, sem_summary: float) -> tuple:
    """级联语义分：返回 (最终语义分, 走到第几层, 每层分数)。
    层1=summary（必算）；层2=detail（摘要过 conflict 线才下钻）；
    层3=source_ref（detail 仍过线才下钻）。文本缺失的层跳过。"""
    layers = {"summary": sem_summary}
    sem = sem_summary
    def _text_of(m, k):
        v = m.get(k)
        return v if isinstance(v, str) and len(v.strip()) >= 8 else ""
    d_a, d_b = _text_of(mine, "detail"), _text_of(other, "detail")
    # 下钻门槛=conflict线本身（修复③回声先验后 combined==sem，白送分时代的
    # conflict/0.7=0.97~1.14 不可达，级联曾永不触发，2026-11-11 审查修正）
    gate = C.DOMAIN_SIM_THRESHOLDS.get(
        mine["dfcp_tag"]["domain"].split("-")[0],
        C.DOMAIN_SIM_THRESHOLDS["000"])["conflict"]
    # 前置过滤②（用户裁决2026-11-12）：占位卡不做detail嵌入比对，级联停在summary。
    # 规约：任意一方detail为占位 → 该层相似度不计入、不触发下钻、不强行拉高整体分数。
    if _is_placeholder_card(mine) or _is_placeholder_card(other):
        return sem_summary, 1, {"summary": sem_summary, "note": "placeholder_blocked"}
    if sem >= gate and d_a and d_b:
        va, vb = embed_semantic.embed([d_a[:512]]), embed_semantic.embed([d_b[:512]])
        sem_d = float(np.dot(va[0], vb[0]))
        layers["detail"] = sem_d
        if sem_summary - sem_d >= CASCADE_DROP:
            return sem_d, 2, layers          # 显著回落→采信 detail（非同源信号）
        s_a, s_b = _text_of(mine, "source_ref"), _text_of(other, "source_ref")
        if sem_d >= 0.60 and s_a and s_b and s_a != s_b:
            va, vb = embed_semantic.embed([s_a[:256]]), embed_semantic.embed([s_b[:256]])
            sem_s = float(np.dot(va[0], vb[0]))
            layers["source"] = sem_s
            return sem_s, 3, layers
        return sem_d, 2, layers
    return sem, 1, layers


# ---- #30 P2：corpus↔runtime 跨池疑似等价告警（仅 E4 可见，不阻断不降级）----
def _cross_pool_warn(corpus_card: dict):
    """corpus 卡写入时，跨 runtime 池做 summary 相似度扫描。

    达 SUMMARY_EQUIV_THRESHOLD → 写 warn_corpus_runtime_equivalent 到 gate_log。
    纯旁路告警：任何异常静默跳过，绝不影响 corpus 写入/返回。
    P2 与 P0 共用阈值口径（0.95），避免第二套阈值。
    """
    try:
        import memory_organize as _mo
        if _mo.SUMMARY_EQUIV_MODE == "off":
            return
        from memory_organize import load_active_pool
        sum_c = corpus_card.get("summary")
        if not sum_c:
            return
        vc = _embed(sum_c)
        best = 0.0
        best_id = None
        for e in load_active_pool():
            if e.get("asset_class") != "runtime":     # 只与 runtime 池比对
                continue
            s = e.get("summary")
            if not s:
                continue
            sim = _cosine(vc, _embed(s))
            if sim > best:
                best, best_id = sim, e.get("inst_id")
        if best >= _mo.SUMMARY_EQUIV_THRESHOLD:
            append_jsonl(os.path.join(MEM_DIR, "gate_log.jsonl"), {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "op": "warn_corpus_runtime_equivalent",
                "corpus_inst": corpus_card.get("inst_id"),
                "runtime_inst": best_id, "best_sim": round(best, 4),
            })
    except Exception:
        pass   # 告警非关键路径，任何失败静默


# ---- 四档判定 + entity_id 回填 ----
def classify_and_link(inst_id: str, top_k: int = 5) -> dict:
    """对新入库条目执行四档判定，写入关系图谱。返回判定报告。

    四档（constants.DOMAIN_SIM_THRESHOLDS 分域阈值）：
    - equivalent 高重合 → 复用本体仅计数，不新增本体
    - augment    中高相似 → 同源增补挂同一 entity_id
    - conflict   中等相似 → 同源分叉打冲突标，送评审流水线（接元思域矛盾对撞）
    - new        低相似 → 新建 entity_id
    """
    all_mem = load_memories()
    me = next((e for e in all_mem if e["inst_id"] == inst_id), None)
    if me is None:
        return {"status": "not_found"}
    if me.get("asset_class") == "corpus":
        # #30 P2（2026-11-23）：corpus 仍硬跳过冲突比对（教材冻结底座，语义不变），
        # 但旁路加一次跨池疑似等价告警（仅 E4 晨报可见，不阻断、不降级）。
        _cross_pool_warn(me)
        return {"status": "skipped_protected"}   # 教材硬跳过（§4.1 整理机制不碰 corpus）
    if me.get("multi_topic"):
        return {"status": "skipped_multi_topic"} # 混主题冻结条目不参与同源/冲突比对（2026-11-08 裁决：漏拆预案）

    domain_root = me["dfcp_tag"]["domain"].split("-")[0]
    th = C.DOMAIN_SIM_THRESHOLDS.get(domain_root, C.DOMAIN_SIM_THRESHOLDS["000"])
    vec_me = _embed(me["summary"])

    # 候选：排除自己、无摘要条目、教材（裁决A：corpus 与 runtime 彻底隔离——
    # 教材是冻结证据底座，runtime 与教材的关系是引用/学习，不是同源/冲突；
    # 引用走 origin_ids 前后件关联，认知关联层四档判定只在 runtime 内部发生）。
    # superseded 条目同样排除（§7.7：已被校准单取代的旧口径不参与判定——
    # 专利新旧编号错位时跨号互撞的根源，2026-11-11 修复③）
    cands = [e for e in all_mem
             if e["inst_id"] != inst_id and e.get("summary")
             and e.get("asset_class") != "corpus"
             and not e.get("superseded_by")]

    # entity 末值表：图谱是 entity_id 的事实源（回填走变更日志，主记录字段可能滞后）
    ent_latest = {}
    for g in load_graph():
        if g.get("inst_id") not in ent_latest:   # 图谱按时间追加，首条即创建值——末值需倒序找
            pass
    for g in load_graph():                        # 顺序遍历：后写覆盖=末值
        ent_latest[g["inst_id"]] = g.get("entity_id")

    scored = []
    # 前置过滤⓪（Topic层单向闸门，规约4，2026-11-13 design_topic_layer.md）：
    #   topic 不同（双端已定且不等）→ 跳过冲突比对（落审计日志不静默）
    #   topic 相同（无论 entity）  → 照常全级联（含演化链例外——同主题演化对绝不能漏）
    #   任一端 null               → 放行（hold 池记忆照常全流程）
    # 顺序：topic → entity → 占位 → 级联。白名单开关关闭时零行为变化。
    _topic_gate = None
    if topic_layer.topic_enabled():
        _topic_gate = topic_layer.conflict_gate
    for other in cands:
        # 前置过滤①（用户裁决2026-11-12）：同entity互撞折叠——
        # 双方已挂同一entity_id（图谱末值为准）→ 不参与冲突判定，留审计日志。
        # 例外：演化链卡对（evolved_into/derive 关系）不折叠，照常比对。
        # entity 折叠以主记录为事实源（2026-11-12 终审回填直写主记录；图谱 ent_latest
        # 是历史判定值，会覆盖今晚人工归并——冲突折叠加一层：主记录相同直接折叠）
        my_ent_latest = me.get("entity_id") or ent_latest.get(inst_id)
        other_ent_latest = other.get("entity_id") or ent_latest.get(other["inst_id"])
        if (my_ent_latest and other_ent_latest and my_ent_latest == other_ent_latest):
            append_jsonl(ENTITY_COLLISION_SKIP_JSONL, {
                "ts": datetime.now().isoformat(),
                "inst_id": inst_id, "conflict_with": other["inst_id"],
                "entity_id": my_ent_latest,
                "rule": "entity_collision_skip",
                "note": "同entity互撞折叠, 不进冲突判定(演化链例外见冲突时照常比对)",
            })
            continue
        # 前置过滤⓪执行：topic 单向拦截（只拦不同topic；同topic/含null 一律放行）
        if _topic_gate is not None:
            _g = _topic_gate(me, other)
            if _g["gate"] == "skip":
                continue   # 审计日志已在 conflict_gate 内落盘（conflict_skip），不静默
        sim = _combined_sim(me, other, vec_me, _embed(other["summary"]))
        # 占位卡档位封顶+可疑分流标记（2026-11-12 终审次生发现+规约②）：
        # 占位对白送分通道有二——combined 的 dfcp_proximity（同域同标签占位对会被
        # 抬过 equivalent 线假合并）与级联层。规约②：占位对不做合并/增补采信，
        # 档位封顶 conflict 之下；但**不 dismiss 只分流**——封顶前 sim 过 conflict 线
        # 的占位对标记 `_ph_suspect`，入队段送摘要-only可疑队列保留告警通道。
        if (_is_placeholder_card(me) or _is_placeholder_card(other)) and sim >= th["conflict"]:
            other["_ph_suspect_with"] = me["inst_id"]      # 封顶前过线 → 分流标记
            sim = th["conflict"] - 0.01
        # 三级级联（§4.6）：摘要层疑似（过 conflict 线）→下钻 detail/source 校正
        sem_sum = _cosine(vec_me, _embed(other["summary"]))
        if sim >= th["conflict"]:
            sem_final, layer, _layers = _cascade_sem(me, other, sem_sum)
            sim = sem_final * 0.7 + _dfcp_proximity(me["dfcp_tag"], other["dfcp_tag"], sem=sem_final) * 0.3
            # #20 Phase2：把级联命中层挂到候选上，供 best 落图谱供 E4 聚合（纯观测，不改判定）
            other["_cascade_layer"] = layer
            other["_cascade_layers"] = _layers
            # fragile 采信降级（四缺口裁决2026-11-11缺口2）：会话闪念类卡
            # 不参与高权重冲突判定——equivalent/augment 档一律降为 new（轻量detail
            # 语义薄, 高分多为表面撞词; fragile 只能被引用, 不能当合并/增补依据）
            if (me.get("fragile") or other.get("fragile")) and sim >= th["augment"]:
                sim = th["conflict"] - 0.01 if sim >= th["conflict"] else sim
        scored.append((sim, other))
    scored.sort(key=lambda x: -x[0])
    scored = scored[:top_k]

    best_sim, best = scored[0] if scored else (0.0, None)

    if best is not None and best_sim >= th["equivalent"]:              # 高重合：独立阈值（按基座标定）
        rel = C.REL_HIGH_OVERLAP
    elif best is not None and best_sim >= th["augment"]:
        rel = C.REL_SAME_SOURCE_ADD
    elif best is not None and best_sim >= th["conflict"]:
        rel = C.REL_FORK_CONFLICT
    else:
        rel = C.REL_NEW_ENTITY

    # entity_id 决定
    best_ent = ent_latest.get(best["inst_id"]) if best else None
    if rel in (C.REL_HIGH_OVERLAP, C.REL_SAME_SOURCE_ADD):
        entity_id = best_ent or _new_entity()
    elif rel == C.REL_FORK_CONFLICT:
        entity_id = best_ent or _new_entity()   # 分叉仍挂同本体（方案三：同本体下多分叉）
    else:
        entity_id = _new_entity()

    # 关系图谱条目（软层，独立可回滚；带基线可复现字段）
    graph_entry = {
        "ts": datetime.now().isoformat(),
        "computed_at": datetime.now().isoformat(),       # 固有代价③对策
        "embedding_base": EMBEDDING_BASE,
        "inst_id": inst_id,
        "entity_id": entity_id,
        "relation": rel,
        "best_match": best["inst_id"] if best else None,
        "best_sim": round(best_sim, 4),
        "topk": [{"inst_id": o["inst_id"], "sim": round(s, 4)} for s, o in scored],
        "thresholds_used": th,
    }
    # #20 Phase2 观测：记录 best 对的三级级联命中层（1=summary/2=detail/3=source）
    # 纯观测字段，下游 E4 聚合冲突分级分布；旧条目无此字段不影响读取。
    if best is not None and best.get("_cascade_layer") is not None:
        graph_entry["cascade_layer"] = best["_cascade_layer"]
        graph_entry["cascade_layers"] = best.get("_cascade_layers")
    append_jsonl(RELATION_GRAPH_JSONL, graph_entry)

    # 清理本轮临时观测标记（避免污染原卡 dict，_ph_suspect_with 同理在下方 pop）
    for _s, _o in scored:
        _o.pop("_cascade_layer", None)
        _o.pop("_cascade_layers", None)

    # 回填 entity_id 到原子层主记录（关系回填不改 inst_id/full_hash/dfcp_tag——账本刚性不破）
    _backfill_entity(inst_id, entity_id)

    # 摘要-only可疑分流（规约②：封顶钳线下后 rel 判 new，靠标记补送告警——
    # 占位对不 dismiss，进 summary_only_suspects.jsonl，detail 回填后复审）
    for s, o in scored:
        if o.pop("_ph_suspect_with", None) is not None:
            append_jsonl(SUMMARY_ONLY_SUSPECTS_JSONL, {
                "ts": datetime.now().isoformat(),
                "inst_id": inst_id, "conflict_with": o["inst_id"],
                "sim_summary_combined": round(s + 0.01, 4),
                "summary_a": me["summary"], "summary_b": o["summary"],
                "status": "summary_only_suspect",
                "note": "占位对封顶钳线→分流; 真实分叉待detail回填后复审, 不自动dismiss",
            })

    # 冲突条目送评审流水线（接元思域：分叉=潜在矛盾对撞素材）
    # 入队去重（2026-11-12 债③根因修复）：同卡对已有 pending 条目则跳过——
    # 此前 classify_and_link 每跑一次重复入队一次（305 条实为 103 对，同对最多重复 14 次）
    if rel == C.REL_FORK_CONFLICT and best is not None:
        if not _conflict_pair_pending(inst_id, best["inst_id"]):
            # 前置过滤②分流（用户裁决2026-11-12）：任一方detail占位 → 不进正常冲突队列，
            # 分流到摘要-only可疑对（保留告警通道，不自动dismiss，不进元思域对撞）
            if _is_placeholder_card(me) or _is_placeholder_card(best):
                append_jsonl(SUMMARY_ONLY_SUSPECTS_JSONL, {
                    "ts": datetime.now().isoformat(),
                    "inst_id": inst_id, "conflict_with": best["inst_id"],
                    "entity_id": entity_id, "sim": round(best_sim, 4),
                    "summary_a": me["summary"], "summary_b": best["summary"],
                    "status": "summary_only_suspect",
                    "note": "detail占位→级联停在summary层; 真实分叉待detail回填后复审",
                })
            else:
                append_jsonl(CONFLICT_QUEUE_JSONL, {
                    "ts": datetime.now().isoformat(),
                    "inst_id": inst_id, "conflict_with": best["inst_id"],
                    "entity_id": entity_id, "sim": round(best_sim, 4),
                    "summary_a": me["summary"], "summary_b": best["summary"],
                    "status": "pending_review",
                })

    return {"status": "ok", "relation": rel, "entity_id": entity_id,
            "best_match": best["inst_id"][:8] if best else None, "best_sim": round(best_sim, 4)}


def _conflict_pair_pending(a: str, b: str) -> bool:
    """同卡对（无序）是否已有 pending 冲突条目——入队去重用（2026-11-12 债③修复）。
    dismissed 的对允许重新入队（复审后再次分叉是新事件）。"""
    if not os.path.exists(CONFLICT_QUEUE_JSONL):
        return False
    pair = {a, b}
    with io.open(CONFLICT_QUEUE_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("status") == "pending_review"
                    and {r.get("inst_id"), r.get("conflict_with")} == pair):
                return True
    return False


def _new_entity() -> str:
    return "ent-" + uuid.uuid4().hex[:12]


def _backfill_entity(inst_id: str, entity_id: str):
    """回填只改 entity_id 字段。为保账本 append-only 特性，回填以追加变更日志实现；
    图谱可全量重算，主记录文件不动（entity_id 以图谱末值为准——与编码变更日志同机制）。"""
    append_jsonl(os.path.join(MEM_DIR, "code_changelog.jsonl"), {
        "ts": datetime.now().isoformat(), "inst_id": inst_id,
        "field": "entity_id", "old": None, "new": entity_id,
        "reason": "认知关联层四档判定回填",
    })


# ---- 图谱重建（软层可全量重算；换 embedding 基座后触发）----
def rebuild_graph():
    """全量重算关系图谱。原子层不动；旧图谱归档后新建。
    触发场景：换嵌入基座 / 阈值调整 / 图谱污染回滚。"""
    if os.path.exists(RELATION_GRAPH_JSONL):
        archived = RELATION_GRAPH_JSONL + f".archived-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        os.rename(RELATION_GRAPH_JSONL, archived)
        print(f"旧图谱归档: {archived}")
    n = 0
    for e in load_memories():
        if e.get("asset_class") != "corpus":   # 教材跳过
            if e.get("superseded_by"):
                continue                        # 被校准取代的旧口径不产生新判定（§7.7）
            r = classify_and_link(e["inst_id"])
            n += (r.get("status") == "ok")
    print(f"图谱重算完成: {n} 条关系")


def load_graph() -> list:
    if not os.path.exists(RELATION_GRAPH_JSONL):
        return []
    out = []
    with io.open(RELATION_GRAPH_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


if __name__ == "__main__":
    # 自检：对库里 runtime 条目跑四档判定（corpus 教材应被跳过）
    print("=== 认知关联层自检 ===")
    for e in load_memories():
        if e.get("asset_class") == "corpus":
            continue
        r = classify_and_link(e["inst_id"])
        print(f"{e['inst_id'][:8]} {r.get('status')}/{r.get('relation','-')} sim={r.get('best_sim','-')}")

    # 场景测试：构造一对"同源分叉冲突"（同主题相反结论）
    from memory_organize import atomic_store
    st1, _ = atomic_store(
        "复盘结论：非农超预期金价不涨反跌，归因流动性拐点，市场已price in。",
        "复盘详情", "sim-test-a",
        {"domain": "001-004", "behavior": "Jw", "relation": "P", "confidence": "L2"})
    st2, _ = atomic_store(
        "复盘结论：非农超预期金价不涨反跌，归因央行购金支撑对冲了流动性冲击。",
        "复盘详情2", "sim-test-b",
        {"domain": "001-004", "behavior": "Jw", "relation": "P", "confidence": "L2"})
    if st1 == "stored" and st2 == "stored":
        mems = [e for e in load_memories() if e["source_ref"] in ("sim-test-a", "sim-test-b")]
        for m in mems:
            r = classify_and_link(m["inst_id"])
            print(f"冲突场景 {m['source_ref']}: {r.get('relation')} sim={r.get('best_sim')}")
    print("图谱条数:", len(load_graph()))