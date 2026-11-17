# eval/e5_consolidation.py — 夜间自动整理步（幂等软层操作，只追加不删除）
# 与 E4 的分工（2026-11-09 用户裁决后补）：
#   E4 = 只读巡逻报警（多主题/000超期/watchlist 等——必须人工）
#   E5 = 机器可安全自动做的整理：四档判定补挂 + 经验聚类增量归纳
# 安全边界（为什么不越权）：
#   - classify_and_link / distill_all 都只写软层文件（relation_graph/experiences），
#     永不改原子层主记录（inst_id/full_hash/dfcp_tag 不可触碰）
#   - 教材 corpus 硬跳过、multi_topic 冻结跳过、保护清单 skip——都在函数内部生效
#   - 全程幂等：跑两遍结果一致（cluster_key 去重、图谱末值事实源）
#   - 不做任何删除/降级/拆分/归类决策——那些是人的活（E4 报警项）
# 对比原项目 memory_organizer.py（cognitive_engine）后的取舍：
#   取：夜间自动整理的触发节奏（原心跳 idle-30min；v3 用 schtasks 定时，更强）
#   取：报告 stats 结构（merged/cards/skipped → v3 already has，保持）
#   不取：平面标签分组、硬编码别名表、关键词推断 category（DFCP 树内码已替代）
#   别名桥接（alias_tags 思路）另立设计提案待用户裁决，不入本步。

import json
import io
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))
os.environ.setdefault("NO_MEMORY", "1")

import memory_organize as mo    # noqa: E402
import memory_relations as mr   # noqa: E402
import memory_distill as md     # noqa: E402


def consolidate():
    now = datetime.now()
    report = {"ts": now.isoformat(), "ops": {}, "ok": False}
    mems = mo.load_memories()
    graph_ids = {g["inst_id"] for g in mr.load_graph()}

    # 1. 补挂四档判定：runtime有 summary、未跑过的（E4 报的 unlinked 消化路径）
    to_link = [m["inst_id"] for m in mems
               if m.get("asset_class") != "corpus"
               and m.get("summary")
               and m["inst_id"] not in graph_ids]
    linked = {"ok": 0, "skipped": 0, "conflict": 0}
    for iid in to_link:
        try:
            r = mr.classify_and_link(iid)
        except Exception as e:
            report["ops"]["link_error"] = report["ops"].get("link_error", 0) + 1
            report.setdefault("link_errors", []).append({"inst_id": iid[:8], "err": str(e)})
            continue
        st = r.get("status")
        if st == "ok":
            linked["ok"] += 1
            if r.get("relation") == "conflict":
                linked["conflict"] += 1
        else:
            linked["skipped"] += 1  # skipped_protected / skipped_multi_topic
    report["ops"]["classify"] = {"candidates": len(to_link), **linked}

    # 2. 经验聚类增量归纳（幂等：已有同簇经验只增量追加 derived_from）
    try:
        d = md.distill_all()
        report["ops"]["distill"] = d
    except Exception as e:
        report["ops"]["distill"] = {"error": str(e)}

    # 2.5 memory_class 存量回填提案（件② 2026-11-10 用户终裁：E5批量提案+judge确认，禁止机器直写；
#     不属五类提案=null 不硬塞——S015：无other兜底，null=普通记录）
    try:
        import memory_class_backfill as mcb
        report["ops"]["memory_class_backfill"] = mcb.propose_batch(limit=8)
    except Exception as e:
        report["ops"]["memory_class_backfill"] = {"error": str(e)}

# 3. 置信度动态兜底扫描（2026-11-10 用户裁决②：升降级事件统计+待重审积压，只读）
    try:
        sys.path.insert(0, os.path.join(HERE, "memory"))
        from confidence_dynamics import confidence_dynamics_scan
        report["ops"]["confidence_dynamics"] = confidence_dynamics_scan()
    except Exception as e:
        report["ops"]["confidence_dynamics"] = {"error": str(e)}

    # 3. 快照前后状态（晨报可读）
    report["after"] = {
        "memories": len(mo.load_memories()),
        "graph_edges": len(mr.load_graph()),
        "experiences": len(md.load_experiences()),
    }
    report["ok"] = True
    return report


def main():
    r = consolidate()
    path = os.path.join(HERE, "logs", "e5_consolidation.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    c = r["ops"].get("classify", {})
    d = r["ops"].get("distill", {})
    print(f"E5 整理: 补挂 {c.get('ok',0)}/{c.get('candidates',0)}"
          f"（conflict {c.get('conflict',0)} skipped {c.get('skipped',0)}） | "
          f"经验 created={d.get('created',0)} updated={d.get('updated',0)}")
    print(f"库态: {r['after']}")
    print("报告:", path)


if __name__ == "__main__":
    main()