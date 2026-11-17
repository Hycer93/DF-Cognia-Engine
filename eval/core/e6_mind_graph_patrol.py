# eval/e6_mind_graph_patrol.py — 思维图巡逻（只读，只报警，不自动处理图）
# 对策来源：2026-11-09 主AI提案用户确认——记忆侧有E4/E5，思维侧无专项体检；
#           上次赫布漂移(Aa→Eo顶2.0主干坍缩)靠E1事后暴露，损失一轮8h夜跑。
# 原则：与E4同纪律——机器不私自改图；饱和/坍缩/死边/定义-执行缺口/回滚守护只报警。
# 产物：logs/e6_mind_graph_patrol.json + stdout 摘要；nightly_run.py STEPS 第6步。
# 阈值：全部收拢 calibration/nightly_calib.toml [mind_graph_patrol]，代码内不出现判定数字。

import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAPH = os.path.join(HERE, "data", "mind_graph.json")
LOGS = os.path.join(HERE, "logs")

# 定义-执行缺口检查清单：节点behaviors里声明的钩子/固化动作 → 期望绑定的执行器。
# 已落地（从缺口清单移除）：
#   Jw"新认知评估固化形式" → memory_judge.judge_and_store（2026-11-10 三件套件①落地，
#   CLAUDE.md Jw钩子段 + judge_log 审计链，E4 第10项消费）
DECLARED_EXECUTORS = {
}


def _load_calib():
    path = os.path.join(HERE, "calibration", "nightly_calib.toml")
    if os.path.exists(path):
        try:
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            out, section = {}, None
            with io.open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if line.startswith("["):
                        section = line.strip("[]"); out[section] = {}
                    elif "=" in line and section:
                        k, v = [x.strip() for x in line.split("=", 1)]
                        try:
                            out[section][k] = int(v)
                        except ValueError:
                            try:
                                out[section][k] = float(v)
                            except ValueError:
                                out[section][k] = v
            return out
    return {}  # 无calib则跳过阈值判定（只输出统计，不报警——宁漏报不误报）


def patrol():
    calib = _load_calib().get("mind_graph_patrol", {})
    g = json.load(io.open(GRAPH, encoding="utf-8"))
    nodes, edges = g["nodes"], g["edges"]
    report = {"ts": datetime.now().isoformat(),
              "nodes": len(nodes), "edges": len(edges), "alerts": [], "items": {}}

    # 1. 饱和预警（上次漂移直接特征：ε软上限1.904附近/≥2.0顶死）
    weights = {k: e["weight"] for k, e in edges.items()}
    over_2 = [k for k, w in weights.items() if w >= calib.get("weight_hard_cap", 10.0)]
    near_cap = [k for k, w in weights.items()
                if w >= calib.get("weight_near_cap", 10.0)]
    report["items"]["edges_over_hard_cap"] = len(over_2)
    report["items"]["edges_near_soft_cap"] = len(near_cap)
    if over_2:
        report["alerts"].append(
            f"图饱和漂移复发：{len(over_2)} 条边≥硬上限 {calib.get('weight_hard_cap')}（示例 {over_2[0]}={weights[over_2[0]]:.3f}）"
            f"——对照 mind_graph_backup，必要时回滚")
    if near_cap:
        report["alerts"].append(
            f"[warning] {len(near_cap)} 条边逼近ε软上限（≥{calib.get('weight_near_cap')}，"
            f"示例 {near_cap[0]}={weights[near_cap[0]]:.3f}）——观察衰减是否失效")

    # 2. 主干坍缩度（贪心首跳集中度：上次坍缩为[Aa,Eo,Jw]承接主干）
    first_hits = {}
    for k, e in edges.items():
        src = k.split("->")[0]
        first_hits[src] = first_hits.get(src, 0) + e.get("success_count", 0) + e.get("failure_count", 0)
    total_traversals = sum(first_hits.values())
    if total_traversals > 0:
        top3 = sorted(first_hits.values(), reverse=True)[:3]
        concentration = sum(top3) / total_traversals
        report["items"]["first_hop_concentration_top3"] = round(concentration, 3)
        if concentration > calib.get("trunk_concentration_max", 1.0):
            report["alerts"].append(
                f"[warning] 主干坍缩倾向：top3 首跳承接 {concentration:.0%} > 阈值"
                f" {calib.get('trunk_concentration_max'):.0%}——检查贪心路由是否退化")

    # 3. 死边（声明了但从未被路径使用）
    dead = [k for k, e in edges.items()
            if e.get("success_count", 0) + e.get("failure_count", 0) == 0]
    report["items"]["dead_edges"] = len(dead)
    if len(dead) > calib.get("dead_edge_max", 999999):
        report["alerts"].append(
            f"[warning] 死边 {len(dead)} 条超阈值 {calib.get('dead_edge_max')}（零success/failure计数）"
            f"——图结构与真实路径脱节，需重估边合理性")

    # 4. 定义-执行缺口（节点声明了钩子/固化动作但未绑执行器——Jw类缺口例行化）
    gaps = []
    for code, declared in DECLARED_EXECUTORS.items():
        n = nodes.get(code)
        if not n:
            gaps.append(f"{code}: 节点缺失（DECLARED_EXECUTORS 陈旧，清单待清理）")
            continue
        actions = {b.get("action", "") for b in n.get("behaviors", [])}
        for action, executor in declared.items():
            if action in actions:
                gaps.append(f"{code}『{action}』已声明 behaviors 但未绑 {executor}")
            else:
                gaps.append(f"{code}: behaviors 中已无『{action}』——三件套已落地或图已改，从清单移除")
    if gaps:
        report["items"]["defined_not_bound"] = len(gaps)
        for gp in gaps:
            report["alerts"].append(f"定义-执行缺口: {gp}")

    # 5. 回滚守护：夜跑前自动快照 + 与最近备份的 diff 摘要（漂移不再靠人工比对）
    snaps = sorted(f for f in os.listdir(os.path.dirname(GRAPH))
                   if f.startswith("mind_graph_backup_"))
    report["items"]["backup_snapshots"] = len(snaps)
    report["items"]["latest_backup"] = snaps[-1] if snaps else "无"
    if snaps:
        b = json.load(io.open(os.path.join(os.path.dirname(GRAPH), snaps[-1]), encoding="utf-8"))
        bmax = max((e["weight"] for e in b["edges"].values()), default=0)
        report["items"]["drift_vs_backup"] = {
            "edges": len(b["edges"]) - len(edges),
            "nodes": len(b["nodes"]) - len(nodes),
            "max_weight": round(max(weights.values()) - bmax, 3),
        }
    report["ok"] = len(report["alerts"]) == 0
    return report


def snapshot_for_nightly():
    """夜跑前快照（由 nightly_run.py 在 E1 前调用；本函数写备份文件，不碰主图——只读纪律指主图）。"""
    dst = os.path.join(os.path.dirname(GRAPH),
                       f"mind_graph_backup_{datetime.now():%Y%m%d}.json")
    if os.path.exists(dst):
        return dst  # 当日已有快照，不覆盖（保留当天首次状态）
    import shutil
    shutil.copy(GRAPH, dst)
    return dst


def main():
    r = patrol()
    path = os.path.join(LOGS, "e6_mind_graph_patrol.json")
    os.makedirs(LOGS, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    print("E6 思维图巡逻:", "全绿" if r["ok"] else f"{len(r['alerts'])} 项报警")
    for a in r["alerts"]:
        print("  [warn]", a)
    print("计数:", json.dumps(r["items"], ensure_ascii=False))
    print("报告:", path)


if __name__ == "__main__":
    main()
