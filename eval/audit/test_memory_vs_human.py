"""test_memory_vs_human_20261123.py — 记忆系统 vs 人脑存取不足对照测试

目的：用可量化探针，把"记忆系统相比人脑在存取上的短板"打成事实清单。
只读：不改任何记忆卡、不写 gate_log（本脚本不调 atomic_store）。

六个维度对照：
  D1 检索召回   —— 人脑语义联想 vs 系统 ask() 的召回面
  D2 细节保真   —— 人脑可重构场景 vs 系统 detail 平均 270 字截断
  D3 关联扩散   —— 人脑概念网扩散 vs 系统 entity 覆盖 0.85% + 无图遍历
  D4 时序记忆   —— 人脑时间线叙事 vs 系统 event_ts 覆盖 236/353
  D5 遗忘曲线   —— 人脑自然遗忘(重要性加权) vs 系统无自动衰减(仅手动demote)
  D6 冗余/去重  —— 人脑模式归纳 vs 系统 353 卡 30 条重复 summary

运行：
  python eval/audit/test_memory_vs_human_20261123.py
"""
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "memory"))
DB = os.path.join(_ROOT, "data", "memory", "corpus_store.db")


def _conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def d1_recall():
    """检索召回：对已知存在的目标，ask() 能否 top_k 内召回。"""
    import memory_query as MQ
    probes = [
        ("记忆入库断档事故", "10764d3b"),      # 已知卡
        ("专利 动态拓维", "b0f8c008"),
        ("紫光国微 建仓", "ef3ef53f"),
        ("G22 决策整合", None),
        ("复现率门禁", None),
    ]
    hits = 0
    detail = []
    for q, expect in probes:
        try:
            r = MQ.ask(q, top_k=5)
            ids = [x[1]["inst_id"][:8] for x in r if isinstance(x, tuple)]
            ok = (expect in ids) if expect else (len(r) > 0)
            hits += 1 if ok else 0
            detail.append((q, ok, len(r), ids[:3]))
        except Exception as e:
            detail.append((q, False, 0, f"ERR:{e}"[:40]))
    return {"score": f"{hits}/{len(probes)}", "hits": hits, "total": len(probes),
            "detail": detail}


def d2_fidelity():
    """细节保真：detail 长度分布，人脑可重构场景细节 vs 系统截断。"""
    c = _conn()
    rows = c.execute("SELECT LENGTH(COALESCE(detail,'')) FROM core_memory_card").fetchall()
    lens = sorted(r[0] for r in rows)
    n = len(lens)
    empty = sum(1 for x in lens if x == 0)
    truncated_risk = sum(1 for x in lens if 0 < x < 100)  # detail<100字=信息很薄
    avg = sum(lens) / n if n else 0
    c.close()
    return {"total": n, "empty_detail": empty, "thin_detail_lt100": truncated_risk,
            "avg_len": round(avg, 1), "p50": lens[n // 2] if n else 0,
            "max": lens[-1] if n else 0}


def d3_association():
    """关联扩散：entity_id 覆盖率 + 关联表规模，无图遍历。"""
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    ent = c.execute("SELECT COUNT(*) FROM core_memory_card WHERE COALESCE(entity_id,'')!=''").fetchone()[0]
    try:
        timeline = c.execute("SELECT COUNT(*) FROM core_entity_timeline").fetchone()[0]
    except Exception:
        timeline = "N/A"
    try:
        rel = c.execute("SELECT COUNT(*) FROM core_relation").fetchone()[0]
    except Exception:
        rel = "N/A"
    c.close()
    return {"entity_coverage": f"{ent}/{total}({round(ent/total*100,2)}%)",
            "entity_timeline_rows": timeline, "core_relation_rows": rel,
            "graph_traversal": "无（仅 entity_id 同本体互斥去重）"}


def d4_temporal():
    """时序记忆：event_ts 覆盖率。"""
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    ev = c.execute("SELECT COUNT(*) FROM core_memory_card "
                   "WHERE COALESCE(event_ts,'')!=''").fetchone()[0]
    ts = c.execute("SELECT COUNT(*) FROM core_memory_card WHERE COALESCE(ts,'')!=''").fetchone()[0]
    c.close()
    return {"total": total, "event_ts_filled": ev,
            "event_ts_coverage": f"{round(ev/total*100,1)}%",
            "ts_filled": ts,
            "note": "event_ts=事件真实发生时间(缺=只能按入库时刻回忆)"}


def d5_forgetting():
    """遗忘曲线：有无自动衰减。tier 分布反映人工/规则降级。"""
    c = _conn()
    tiers = {r[0]: r[1] for r in c.execute(
        "SELECT tier, COUNT(*) FROM core_memory_card GROUP BY tier")}
    c.close()
    # 是否有自动老化：查代码已知无 access_count 自动降级
    return {"tier_dist": tiers,
            "auto_decay": "无（仅 demote() 手动/校准触发，无 access_count 老化扫描）",
            "human_contrast": "人脑按情绪强度+复述频率+重要性自动遗忘，系统无此机制"}


def d6_redundancy():
    """冗余/去重：重复 summary 情况。"""
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    uniq = c.execute("SELECT COUNT(DISTINCT summary) FROM core_memory_card").fetchone()[0]
    # 找出重复的 summary 样例
    dups = c.execute(
        "SELECT summary, COUNT(*) c FROM core_memory_card GROUP BY summary "
        "HAVING c>1 ORDER BY c DESC LIMIT 5").fetchall()
    dup_list = [(r[0][:45], r[1]) for r in dups]
    c.close()
    return {"total": total, "unique_summary": uniq, "dup_groups": total - uniq,
            "top_dups": dup_list,
            "human_contrast": "人脑自动归纳合并相似经历为模式，系统靠 P0(阈值0.95)+全量卡并存"}


def main():
    print("=" * 68)
    print("记忆系统 vs 人脑 存取不足 对照测试")
    print("时间:", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 68)

    print("\n[D1] 检索召回（人脑凭模糊线索联想 vs 系统 ask 检索）")
    r1 = d1_recall()
    print(f"  召回命中: {r1['score']}")
    for q, ok, n, ids in r1["detail"]:
        print(f"    {'✅' if ok else '❌'} '{q}' → {n}条已召回 {ids}")

    print("\n[D2] 细节保真（人脑可重构场景 vs 系统 detail 截断）")
    r2 = d2_fidelity()
    print(f"  总卡 {r2['total']} | detail空 {r2['empty_detail']} | 薄(<100字) {r2['thin_detail_lt100']}")
    print(f"  detail长度 avg={r2['avg_len']} p50={r2['p50']} max={r2['max']}")
    print(f"  不足: 平均{r2['avg_len']}字难以支撑场景级重构；{r2['thin_detail_lt100']}张<100字几乎只剩骨架")

    print("\n[D3] 关联扩散（人脑概念网 vs 系统实体覆盖）")
    r3 = d3_association()
    print(f"  entity_id 覆盖: {r3['entity_coverage']}")
    print(f"  entity_timeline 行: {r3['entity_timeline_rows']} | core_relation 行: {r3['core_relation_rows']}")
    print(f"  图遍历: {r3['graph_traversal']}")
    print(f"  不足: 实体覆盖<1%，『想起A带出B』的联想扩散几乎不存在")

    print("\n[D4] 时序记忆（人脑时间线叙事 vs 系统 event_ts）")
    r4 = d4_temporal()
    print(f"  event_ts 覆盖: {r4['event_ts_filled']}/{r4['total']} ({r4['event_ts_coverage']})")
    print(f"  不足: {r4['total']-r4['event_ts_filled']}张只有入库时刻无事件时刻，无法还原真实时间线")

    print("\n[D5] 遗忘曲线（人脑重要性加权遗忘 vs 系统衰减）")
    r5 = d5_forgetting()
    print(f"  tier分布: {r5['tier_dist']}")
    print(f"  自动衰减: {r5['auto_decay']}")
    print(f"  不足: 系统只增不减(除手动demote)，旧噪声永久占位；人脑自动淡忘次要信息")

    print("\n[D6] 冗余/去重（人脑归纳 vs 系统并存）")
    r6 = d6_redundancy()
    print(f"  总 {r6['total']} | 唯一summary {r6['unique_summary']} | 重复组 {r6['dup_groups']}")
    print(f"  Top重复:")
    for s, c in r6["top_dups"]:
        print(f"    ×{c} {s}")
    print(f"  不足: {r6['dup_groups']}条冗余summary并存；人脑归纳为模式，系统靠阈值0.95去重但存留多版本")

    print("\n" + "=" * 68)
    print("总结：六大不足（详见 docs/08_patrol_review/memory_vs_human_gap_20261123.md）")
    print("=" * 68)
    return {"d1": r1, "d2": r2, "d3": r3, "d4": r4, "d5": r5, "d6": r6}


if __name__ == "__main__":
    main()