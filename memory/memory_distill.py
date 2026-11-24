# memory_distill.py — 经验聚类层（三层职责之第三层，承接旧 Op2 经验合并）
#
# 职责：沿 entity_id 本体链归纳提炼稳定经验、构建 derived_from 溯源链、
#       生成可蒸馏思维轨迹。
#
# 关键规则（design_memory_v02 §4.5/§6）：
# - 经验是新增不是替代：经验条目带 derived_from=[原子ID列表]，原子层只打
#   referenced_by 计数，永不物理合并——错误经验死活不传染原子（两物理层）
# - 单经验 derived_from 上限 DERIVED_FROM_MAX=50，超限分裂子经验（防话题桶退化）
# - 合并产物初始权重 = 簇内均值 × MERGE_WEIGHT_FACTOR(0.85)，整理不制造虚假权重
# - negative 经验禁固化（tier 永不升 asset/knowledge）——本层只做聚类，固化门禁在上层
# - 经验簇锚：同 entity + 同行为码才算同簇（正负经验不同锚——polarity 虽裁撤，
#   由详情层"由来段"承载方向语义，聚类时按行为码+本体分桶）
# - 冲突条目（认知关联层 conflict 档）不参与聚类——先评审后入（接元思域矛盾对撞）

import json
import io
import os
import uuid
from datetime import datetime
from collections import defaultdict

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import constants as C
from memory_organize import load_memories, MEM_DIR, append_jsonl, atomic_store
from memory_relations import load_graph, _embed, _cosine, EMBEDDING_BASE, grams

EXPERIENCE_JSONL = os.path.join(MEM_DIR, "experiences.jsonl")       # 经验层（独立物理文件，与原子层分离）
DISTILL_LOG_JSONL = os.path.join(MEM_DIR, "distill_log.jsonl")      # 聚类操作日志
EXPERIENCE_DB = os.path.join(MEM_DIR, "corpus_store.db")            # 经验层 DB 主存（2026-11-24 #49 接线）


def load_experiences() -> list:
    """经验层读取（DB 优先，jsonl 兜底）。

    2026-11-24 #49 接线：core_experience 表成为主真相，jsonl 降为存档。
    返回结构保持与原 jsonl 完全一致（exp_id/cluster_key/... 全字段），下游零改动。
    表无数据或不可读时回退 jsonl（保证旧环境/未迁移库仍可用）。
    """
    # ---- DB 优先 ----
    try:
        if os.path.exists(EXPERIENCE_DB):
            import sqlite3
            con = sqlite3.connect(f"file:{EXPERIENCE_DB}?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute("SELECT * FROM core_experience").fetchall()
            finally:
                con.close()
            if rows:  # 表有数据 → 以表为准（列=真相，meta 仅补缺）
                out = []
                for r in rows:
                    d = dict(r)
                    # 列优先重建（列是主数据，改列即生效）；meta 只补列里没有的字段
                    try:
                        meta = json.loads(d["meta"]) if d.get("meta") else {}
                    except Exception:
                        meta = {}
                    def _j(v, default):
                        if v is None:
                            return default
                        try:
                            return json.loads(v)
                        except Exception:
                            return default
                    base = {
                        "exp_id": d.get("xid"),
                        "cluster_key": d.get("cluster_key"),
                        "entity_id": d.get("entity_id"),
                        "behavior": d.get("behavior"),
                        "dfcp_tag": _j(d.get("dfcp_tag"), None),
                        "summary": d.get("summary"),
                        "detail": d.get("detail"),
                        "derived_from": _j(d.get("derived_from"), []),
                        "atom_count": d.get("atom_count"),
                        "weight": d.get("weight"),
                        "tier": d.get("tier"),
                        "created": d.get("created"),
                        "last_updated": d.get("last_updated"),
                        "embedding_base": d.get("embedding_base"),
                        "split_pending": bool(d.get("split_pending")),
                    }
                    # 列缺失(空)时用 meta 兜底，保证不丢信息
                    for k, v in base.items():
                        if v is None and k in meta:
                            base[k] = meta[k]
                    out.append(base)
                return out
    except Exception:
        pass  # 任何 DB 异常 → 回退 jsonl，绝不因接线中断主流程

    # ---- jsonl 兜底 ----
    if not os.path.exists(EXPERIENCE_JSONL):
        return []
    out = []
    with io.open(EXPERIENCE_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---- 簇锚键：entity_id + 行为码（同本体同骨架才算同簇）----
def _cluster_key(entry: dict) -> str:
    return f"{entry.get('entity_id')}|{entry['dfcp_tag']['behavior']}"


def distill_all(min_cluster: int = 2, sim_threshold: float = 0.5) -> dict:
    """全量归纳：沿 entity 链分桶 → 桶内聚类 → 达门槛成经验。
    min_cluster=同簇最少原子数（[先验-业务设定] 2起步，即两个独立原子可成经验雏形）。
    返回统计报告。"""
    mems = [m for m in load_memories() if m.get("asset_class") != "corpus"]
    graph = load_graph()
    # entity_id 以图谱末值为准（回填走变更日志，主文件 entity_id 可能滞后）
    ent_latest = {}
    for g in graph:
        ent_latest[g["inst_id"]] = g.get("entity_id")
    for m in mems:
        if m["inst_id"] in ent_latest:
            m["entity_id"] = ent_latest[m["inst_id"]]

    # 排除：无本体链（认知关联层未跑）、冲突档待评审条目
    conflict_pending = {q["inst_id"] for q in _load_conflict_queue() if q.get("status") == "pending_review"}
    clusters = defaultdict(list)
    # fragile 隔离（四缺口裁决2026-11-11缺口4）：级联三级≠全局图谱基石——
    # 会话闪念类卡(fragile)不参与自动聚类归纳, 否则大量轻量短卡会让蒸馏持续
    # 产出错误归纳。全局蒸馏护栏独立于局部判定链。
    skipped = {"no_entity": 0, "conflict_pending": 0, "fragile": 0}
    for m in mems:
        if m["inst_id"] in conflict_pending:
            skipped["conflict_pending"] += 1
            continue
        if m.get("fragile"):
            skipped["fragile"] += 1
            continue
        if not m.get("entity_id"):
            skipped["no_entity"] += 1
            continue
        clusters[_cluster_key(m)].append(m)

    # 现有经验（幂等：已有同簇经验则增量更新，不重建）
    existing = {e["cluster_key"]: e for e in load_experiences()}

    created, updated, untouched = 0, 0, 0
    for key, atoms in clusters.items():
        if len(atoms) < min_cluster:
            continue
        # 桶内相似度复核（防本体链误挂把异质条目聚进来）
        base = _embed(atoms[0]["summary"])
        coherent = [a for a in atoms if _cosine(base, _embed(a["summary"])) >= sim_threshold]
        if len(coherent) < min_cluster:
            continue

        if key in existing:
            # 增量：新原子并入（derived_from 追加 + referenced_by 计数），不覆盖旧经验
            exp = existing[key]
            new_atoms = [a for a in coherent if a["inst_id"] not in exp["derived_from"]]
            if not new_atoms:
                untouched += 1
                continue
            # 上限分裂：超限不再并入（保持经验锐度），标记待分裂
            room = C.DERIVED_FROM_MAX - len(exp["derived_from"])
            added = new_atoms[:room]
            if not added:
                exp["split_pending"] = True
                untouched += 1
                continue
            exp["derived_from"] += [a["inst_id"] for a in added]
            exp["atom_count"] = len(exp["derived_from"])
            exp["last_updated"] = datetime.now().isoformat()
            exp["weight"] = round((exp["weight"] * exp["atom_count"] + _mean_w(coherent)) / (exp["atom_count"] + len(added)) * 1.0, 4)
            _rewrite_experience(exp)
            _log({"op": "augment", "cluster_key": key, "added": len(added)})
            updated += 1
        else:
            # 新经验
            exp = _new_experience(key, coherent)
            append_jsonl(EXPERIENCE_JSONL, exp)
            _upsert_experience_db(exp)   # #49 双写：jsonl + DB 同步
            _log({"op": "create", "cluster_key": key, "atoms": len(coherent)})
            created += 1

    report = {
        "clusters_scanned": len(clusters), "created": created, "updated": updated,
        "untouched": untouched, "skipped": skipped,
        "atom_total": sum(len(v) for v in clusters.values()),
    }
    _log({"op": "distill_all_report", **report})
    return report


def _mean_w(atoms: list) -> float:
    vals = [a.get("weight", {}).get("match", 0.0) for a in atoms]
    return sum(vals) / len(vals) if vals else 0.0


def _new_experience(key: str, atoms: list) -> dict:
    ent, beh = key.split("|")
    summary = _synthesize_summary(atoms)
    return {
        "exp_id": "exp-" + uuid.uuid4().hex[:12],
        "cluster_key": key,
        "entity_id": ent,
        "behavior": beh,
        "dfcp_tag": dict(atoms[0]["dfcp_tag"]),        # 代表性坐标（经验继承主原子域码）
        "summary": summary,                            # 归纳摘要（Week1=原子摘要拼接压缩；Week2 接 LLM 提炼）
        "detail": "\n---\n".join(a["summary"] for a in atoms),   # 原子摘要串=由来段
        "derived_from": [a["inst_id"] for a in atoms],
        "atom_count": len(atoms),
        "weight": round(_mean_w(atoms) * C.MERGE_WEIGHT_FACTOR, 4),   # 簇内均值×0.85
        "tier": "merged",                                # 固化门禁在上层：merged→asset 需命中≥3 人工门禁
        "created": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "embedding_base": EMBEDDING_BASE,
        "split_pending": False,
    }


def _synthesize_summary(atoms: list) -> str:
    """Week1 归纳：取共同前缀主题 + 原子数。真归纳（提炼模式名/触发条件/标准流程）
    属 Week2 LLM 接入——那是'可蒸馏思维轨迹'的正式形态。"""
    summaries = [a["summary"] for a in atoms]
    # 公共 2-gram 提主题
    gram_set = grams(summaries[0])
    for s in summaries[1:]:
        gram_set &= grams(s)
    theme = "".join(sorted(gram_set)[:6]) if gram_set else summaries[0][:12]
    return f"[经验]{theme}（{len(atoms)}例：{summaries[0][:30]}…等）"


def _load_conflict_queue() -> list:
    p = os.path.join(MEM_DIR, "conflict_queue.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with io.open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _rewrite_experience(exp: dict):
    """经验层更新：读改写（经验层是软层产物，允许重写；溯源靠 distill_log）。"""
    exps = load_experiences()
    out = [e if e["exp_id"] != exp["exp_id"] else exp for e in exps]
    with io.open(EXPERIENCE_JSONL, "w", encoding="utf-8", newline="\n") as f:
        for e in out:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    _upsert_experience_db(exp)   # #49 双写：jsonl + DB 同步


def _upsert_experience_db(exp: dict):
    """经验层 DB 双写（2026-11-24 #49 接线）。

    core_experience 表与 jsonl 同步：按 exp_id 存在则 UPDATE，否则 INSERT。
    表不可用时静默跳过（不阻断 jsonl 主流程）。
    """
    if not os.path.exists(EXPERIENCE_DB):
        return
    try:
        import sqlite3
        row = {
            "xid": exp.get("exp_id"), "summary": exp.get("summary"), "detail": exp.get("detail"),
            "polarity": exp.get("polarity"),
            "referenced_by": json.dumps(exp.get("referenced_by", []), ensure_ascii=False),
            "tier": exp.get("tier"), "status": exp.get("status", "active"),
            "ts": exp.get("created") or exp.get("last_updated"), "event_ts": exp.get("event_ts"),
            "cluster_key": exp.get("cluster_key"), "entity_id": exp.get("entity_id"),
            "behavior": exp.get("behavior"),
            "dfcp_tag": json.dumps(exp.get("dfcp_tag"), ensure_ascii=False),
            "weight": exp.get("weight"), "atom_count": exp.get("atom_count"),
            "derived_from": json.dumps(exp.get("derived_from", []), ensure_ascii=False),
            "embedding_base": exp.get("embedding_base"), "created": exp.get("created"),
            "last_updated": exp.get("last_updated"),
            "split_pending": 1 if exp.get("split_pending") else 0,
            "meta": json.dumps(exp, ensure_ascii=False),
        }
        if not row["xid"]:
            return
        con = sqlite3.connect(EXPERIENCE_DB, timeout=10)
        try:
            exists = con.execute("SELECT 1 FROM core_experience WHERE xid=?", (row["xid"],)).fetchone()
            if exists:
                sets = ",".join("%s=?" % k for k in row if k != "xid")
                vals = [row[k] for k in row if k != "xid"] + [row["xid"]]
                con.execute("UPDATE core_experience SET %s WHERE xid=?" % sets, vals)
            else:
                ks = list(row)
                con.execute("INSERT INTO core_experience(%s) VALUES(%s)" % (
                    ",".join(ks), ",".join("?" * len(ks))), [row[k] for k in ks])
            con.commit()
        finally:
            con.close()
    except Exception:
        pass  # DB 双写失败不影响 jsonl 主流程


def _log(obj: dict):
    append_jsonl(DISTILL_LOG_JSONL, {**obj, "ts": datetime.now().isoformat()})


if __name__ == "__main__":
    print("=== 经验聚类层自检 ===")
    report = distill_all()
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("\n经验条目:")
    for e in load_experiences():
        print(f"  {e['exp_id']} [{e['cluster_key'][:30]}] w={e['weight']} atoms={e['atom_count']}")
        print(f"    {e['summary'][:70]}")