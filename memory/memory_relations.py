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
    for other in cands:
        sim = _combined_sim(me, other, vec_me, _embed(other["summary"]))
        # 三级级联（§4.6）：摘要层疑似（过 conflict 线）→下钻 detail/source 校正
        sem_sum = _cosine(vec_me, _embed(other["summary"]))
        if sim >= th["conflict"]:
            sem_final, layer, _layers = _cascade_sem(me, other, sem_sum)
            sim = sem_final * 0.7 + _dfcp_proximity(me["dfcp_tag"], other["dfcp_tag"], sem=sem_final) * 0.3
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
    append_jsonl(RELATION_GRAPH_JSONL, graph_entry)

    # 回填 entity_id 到原子层主记录（关系回填不改 inst_id/full_hash/dfcp_tag——账本刚性不破）
    _backfill_entity(inst_id, entity_id)

    # 冲突条目送评审流水线（接元思域：分叉=潜在矛盾对撞素材）
    # 入队去重（2026-11-12 债③根因修复）：同卡对已有 pending 条目则跳过——
    # 此前 classify_and_link 每跑一次重复入队一次（305 条实为 103 对，同对最多重复 14 次）
    if rel == C.REL_FORK_CONFLICT and best is not None:
        if not _conflict_pair_pending(inst_id, best["inst_id"]):
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