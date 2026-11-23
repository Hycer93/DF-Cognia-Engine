"""#20 Phase2 阈值命中率审计（只读）。

目的：给「分域四档阈值是否定得合理」提供量化证据，不改任何阈值。

数据源：data/memory/relation_graph.jsonl —— 每条判定都带
  best_sim（最高相似分）+ thresholds_used（该次比对用的三档阈值）+ relation（落档结果）。

产出：
  1. 档位命中数/占比（equivalent/augment/conflict/new）。
  2. **临界带（margin band）**：best_sim 落在某阈值下方 0~0.03 内 = 差一点就过线。
     临界带堆积多 → 阈值可能偏紧（或相似度分布天然双峰，需人工看样例）；
     临界带为空 → 阈值落在分布空档里，定得稳。
  3. 各域（thresholds_used 三元组）的命中分布，发现某域阈值与其他域偏离。

用法：
  python eval/audit/threshold_hit_audit.py
"""
from __future__ import annotations
import io
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAPH = os.path.join(HERE, "data", "memory", "relation_graph.jsonl")
BAND = 0.03   # 临界带宽：阈值下方 [th-BAND, th) 视为"差一点"


def _load():
    if not os.path.exists(GRAPH):
        return []
    rows = []
    with io.open(GRAPH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _domain_key(th: dict) -> str:
    """把阈值三元组压成可比较的 key（用于分域聚合）。"""
    return "eq%.2f/aug%.2f/cft%.2f" % (
        th.get("equivalent", 0), th.get("augment", 0), th.get("conflict", 0))


def audit(rows: list) -> dict:
    total = len(rows)
    rel_cnt = Counter(r.get("relation") for r in rows)

    # 去重：同一卡对 (inst_id, best_match) 多次比对只算一次（重跑 classify 会重复写图谱），
    # 否则重复比对把临界带数量虚高。
    seen = set()
    uniq = []
    for r in rows:
        key = (r.get("inst_id"), r.get("best_match"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    n_unique = len(uniq)

    # 临界带：对每条（去重后）记录，看 best_sim 是否落在三档阈值的下方 BAND 内
    band_hits = {"equivalent": 0, "augment": 0, "conflict": 0}
    band_samples = defaultdict(list)
    # conflict 线的 gap 分布（对最终落 new 的：离 conflict 线还差多少）
    new_gaps = []
    for r in uniq:
        sim = r.get("best_sim")
        th = r.get("thresholds_used")
        if sim is None or not th:
            continue
        for tier in ("equivalent", "augment", "conflict"):
            t = th.get(tier)
            if t is None:
                continue
            if (t - BAND) <= sim < t:
                band_hits[tier] += 1
                if len(band_samples[tier]) < 5:
                    band_samples[tier].append(
                        {"inst": r.get("inst_id", "")[:8], "sim": sim, "th": t,
                         "relation": r.get("relation")})
        if r.get("relation") == "new" and th.get("conflict") is not None:
            new_gaps.append(th["conflict"] - sim)

    # 分域命中（去重后）
    dom = defaultdict(lambda: Counter())
    for r in uniq:
        th = r.get("thresholds_used")
        if not th:
            continue
        dom[_domain_key(th)][r.get("relation")] += 1

    return {
        "total": total,
        "unique_pairs": n_unique,
        "relation_dist": dict(rel_cnt),
        "band_hits": band_hits,
        "band_samples": {k: v for k, v in band_samples.items()},
        "domain_dist": {k: dict(v) for k, v in sorted(dom.items())},
        "new_gap": {
            "n": len(new_gaps),
            "lt_0.03": sum(1 for g in new_gaps if g < 0.03),
            "lt_0.05": sum(1 for g in new_gaps if g < 0.05),
            "lt_0.10": sum(1 for g in new_gaps if g < 0.10),
        },
    }


def main():
    rows = _load()
    if not rows:
        print("无 relation_graph 数据（文件不存在或为空）")
        return
    a = audit(rows)
    print("=" * 60)
    print("阈值命中率审计（只读）  样本 %d 条 / 去重后 %d 对" % (a["total"], a["unique_pairs"]))
    print("=" * 60)
    print("\n[1] 档位命中:")
    for tier in ("equivalent", "augment", "conflict", "new"):
        n = a["relation_dist"].get(tier, 0)
        pct = (n / a["total"] * 100) if a["total"] else 0
        print("  %-11s %3d  (%.1f%%)" % (tier, n, pct))

    print("\n[2] 临界带（阈值下方 %.2f 内，差一点过线）:" % BAND)
    for tier in ("equivalent", "augment", "conflict"):
        n = a["band_hits"].get(tier, 0)
        flag = "  ← 堆积,阈值可能偏紧" if n >= 10 else ""
        print("  %-11s %3d%s" % (tier, n, flag))
        for s in a["band_samples"].get(tier, []):
            print("      样例 %s sim=%.4f th=%.2f → %s" % (
                s["inst"], s["sim"], s["th"], s["relation"]))

    print("\n[3] 分域命中（按阈值三元组）:")
    for key, dist in a["domain_dist"].items():
        print("  %s : %s" % (key, dist))

    print("\n[4] 落 new 的离 conflict 线还差多少（去重后）:")
    ng = a["new_gap"]
    print("  new 总数 %d | 差<0.03: %d | 差<0.05: %d | 差<0.10: %d" % (
        ng["n"], ng["lt_0.03"], ng["lt_0.05"], ng["lt_0.10"]))
    print("  （差<0.05 占比高 → conflict 判定线附近仍有一簇相似对，属正常灰区，非阈值失误）")

    print("\n[结论提示]")
    eq_band = a["band_hits"].get("equivalent", 0)
    aug_band = a["band_hits"].get("augment", 0)
    if eq_band + aug_band == 0:
        print("  合并/增补档临界带为空 → 阈值落在相似度分布空档，定得稳，无需调整。")
    elif eq_band + aug_band < 10:
        print("  合并/增补档临界带少量堆积（%d）→ 观察即可，暂不调阈值。" % (eq_band + aug_band))
    else:
        print("  合并/增补档临界带堆积偏多（%d）→ 建议人工看样例，评估阈值是否偏紧。" % (eq_band + aug_band))
    print("\n注：本审计只读不改阈值。规则收敛须拿多批数据后再议。")


if __name__ == "__main__":
    main()