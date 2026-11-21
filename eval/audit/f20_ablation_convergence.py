# -*- coding: utf-8 -*-
"""门槛② 消融收敛考核器（S2, docs/design_f40_threshold_234.md §1 冻结口径）

定位: eval 层只读检测工具, 不改任何业务文件/常量。
- 网格: BETA[0.6,1.0,1.6] × LAMBDA_DEBT[0.25,0.5,0.75] 共 9 点(§1.1)
- β 经 profile 名注入(conservative/baseline/exploratory, 值恰好 == 网格值)
- λ_debt 经进程内 setattr(f20_weight, "LAMBDA_DEBT", v) 临时覆盖, finally 还原(§1.1)
- 评测: 门槛①真跨簇题库命中率 + 五守卫场景 top-3(复用 f40_cross_cluster_eval /
  future_sim_minloop / f20_weight, 全部 mode=ro 只读)
- 机器收敛: 相邻网格点 命中率变化≤5% 且 五场景 top-3 Jaccard≥0.7(§1.3)
- 机器达标≠晋升: ablation_summary.json 强制 human_accept/reviewer/review_ts 人工签字(§1.4)
- 产物: data/experiment/ablation_f40/<run_ts>/ 每组参数快照+指标(§1.5)

用法:
  python eval/audit/f20_ablation_convergence.py            # 真实消融(慢, 需 DB+embedding)
  python eval/audit/f20_ablation_convergence.py --selftest # 快速自检(注入 fake provider, 不碰 DB/LLM)
"""
import io
import json
import os
import sys
import tempfile
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # cognitive_engine_v3

# 控制台编码兜底: GBK 终端遇 ✅/⚠️ 会 UnicodeEncodeError, 统一降级替换不中断脚本
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "eval", "experiment"),
           os.path.join(_ROOT, "memory")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---- 冻结口径常量(§6) ----
BETA_GRID = [0.6, 1.0, 1.6]
LAMBDA_DEBT_GRID = [0.25, 0.5, 0.75]
BETA_PROFILE_OF = {0.6: "conservative", 1.0: "baseline", 1.6: "exploratory"}
C2_HIT_DELTA_MAX = 0.05
C2_JACCARD_MIN = 0.7

ABLATION_ROOT = os.path.join(_ROOT, "data", "experiment", "ablation_f40")


def _jaccard(a, b):
    """集合 Jaccard; 双空集视作 1.0(无分支场景按"一致"处理)。"""
    A, B = set(a or []), set(b or [])
    if not A and not B:
        return 1.0
    return len(A & B) / len(A | B)


def _grid_points():
    """9 点网格(β 外层, λ 内层), 顺序确定。"""
    return [(beta, ld) for beta in BETA_GRID for ld in LAMBDA_DEBT_GRID]


def _adjacent_pairs(points):
    """3×3 正交相邻边: 同 λ 相邻 β + 同 β 相邻 λ, 共 12 条。"""
    idx = {p: (BETA_GRID.index(p[0]), LAMBDA_DEBT_GRID.index(p[1])) for p in points}
    coord2pt = {(v): k for k, v in idx.items()}
    pairs = []
    for beta, ld in points:
        i, j = idx[(beta, ld)]
        right = coord2pt.get((i + 1, j))
        down = coord2pt.get((i, j + 1))
        if right:
            pairs.append(((beta, ld), right))
        if down:
            pairs.append(((beta, ld), down))
    return pairs


# ---------------- 真实 provider（只读 DB） ----------------

def _real_hit_rate():
    """复用门槛①口径复算跨簇命中率(纯图可达, 与 β/λ 无关, 作库态一致性参照)。"""
    import f40_cross_cluster_eval as f40
    adj, topic_of, _summ = f40.load_graph()
    bank = f40.load_bank()
    if not bank:
        return 0.0, {"note": "题库为空"}
    detail = []
    for q in bank:
        excluded = bool(f40.SENSITIVE & set(q["hop_topics"]) | f40.SENSITIVE & set(q["src_dst"]))
        path = f40.bfs(adj, topic_of, q["src"], q["dst"])
        if path is None:
            r = "FAIL"
        elif len(path) - 1 != q["truth_len"]:
            r = "FAIL"
        else:
            r = "PASS"
        if excluded:
            r = "EXCLUDED"
        detail.append({"qid": q["qid"], "result": r})
    denom = [d for d in detail if d["result"] != "EXCLUDED"]
    passed = [d for d in denom if d["result"] == "PASS"]
    rate = len(passed) / len(denom) if denom else 0.0
    return rate, {"n": len(detail), "denom": len(denom), "passed": len(passed), "detail": detail}


def _build_scenario_snapshots(scenarios):
    """每场景 F00 锚定快照(与 β/λ 无关), 只算一次缓存复用。"""
    import future_sim_minloop as m
    cache = {}
    for name, anchor, goal, _exp_v, _exp_top in scenarios:
        old_aq, old_gp = m.CONFIG["anchor_query"], m.CONFIG["goal_prefix"]
        m.CONFIG["anchor_query"], m.CONFIG["goal_prefix"] = anchor, goal
        try:
            cache[name] = m.f00_anchor()
        finally:
            m.CONFIG["anchor_query"], m.CONFIG["goal_prefix"] = old_aq, old_gp
    return cache


def _real_top3(snaps, beta_profile, lambda_debt, now):
    """给定 β 档位 + λ_debt, 对 5 场景做 F10 发散 + F20 重排, 取 top-3 短 id。
    λ_debt 进程内临时覆盖 f20_weight.LAMBDA_DEBT, finally 还原。"""
    import sqlite3
    import f20_weight as f20
    import future_sim_minloop as m
    old_ld = f20.LAMBDA_DEBT
    f20.LAMBDA_DEBT = lambda_debt
    try:
        out = {}
        con = sqlite3.connect(f"file:{m.DB}?mode=ro", uri=True, timeout=5)
        try:
            for name, snap in snaps.items():
                _k, _n, all_branches = m.f10_diverge(
                    {**snap, "anchor_cards": snap["anchor_cards"][:1]})
                scored = []
                for b in all_branches:
                    w, _fac = f20.score_branch(con, b, step=1, profile=beta_profile, now=now)
                    scored.append({**b, "weight": w})
                scored.sort(key=lambda x: (-x["weight"], x["to"] or ""))
                out[name] = [b["to"] for b in scored[:3]]
        finally:
            con.close()
        return out
    finally:
        f20.LAMBDA_DEBT = old_ld


def _load_guard_scenarios():
    """复用 nightly 守卫 5 场景常量(不改原文件)。"""
    import f20_nightly_guard as g
    return g.SCENARIOS


class _real_sandbox:
    """真实模式沙箱: 复制真实 corpus_store.db 到临时目录, patch 全部模块 DB 路径,
    产出 (hit_provider, top3_provider); 退出时还原路径并删除临时目录。
    目的: memory_query.ask() 会向 trace_cited 回写引用统计(真实写库),
    必须让所有读写落在副本上, 保证真实库零改动(红线)。"""

    def __enter__(self):
        import shutil
        import tempfile
        import sqlite3
        import memory_query as mq
        import future_sim_minloop as m
        import f20_weight as f20
        import f40_cross_cluster_eval as f40

        self._tmpdir = tempfile.mkdtemp(prefix="ablation_sandbox_")
        self._sandbox_db = os.path.join(self._tmpdir, "corpus_store.db")
        # 一致性复制(含已提交 WAL)
        s = sqlite3.connect("file:%s?mode=ro" % f40.DB, uri=True, timeout=5)
        d = sqlite3.connect(self._sandbox_db, timeout=5)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()

        self._saved = {
            "mq._DB_PATH": mq._DB_PATH,
            "m.DB": m.DB,
            "f20.DB": f20.DB,
            "f40.DB": f40.DB,
        }
        mq._DB_PATH = self._sandbox_db
        m.DB = self._sandbox_db
        f20.DB = self._sandbox_db
        f40.DB = self._sandbox_db

        now = datetime.now()
        scenarios = _load_guard_scenarios()
        snaps = _build_scenario_snapshots(scenarios)

        def hit_provider():
            return _real_hit_rate()

        def top3_provider(profile, ld):
            return _real_top3(snaps, profile, ld, now)

        return hit_provider, top3_provider

    def __exit__(self, *exc):
        import shutil
        import memory_query as mq
        import future_sim_minloop as m
        import f20_weight as f20
        import f40_cross_cluster_eval as f40
        mq._DB_PATH = self._saved["mq._DB_PATH"]
        m.DB = self._saved["m.DB"]
        f20.DB = self._saved["f20.DB"]
        f40.DB = self._saved["f40.DB"]
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


# ---------------- 消融主流程 ----------------

def run_ablation(out_base=None, hit_provider=None, top3_provider=None, scenarios=None):
    """跑 9 点网格消融。provider 可注入(selftest 用 fake, 生产用真实)。
    返回 summary dict, 并落盘到 <out_base>/<run_ts>/。
    真实模式(provider=None)全程在**临时沙箱副本**上跑, 绝不写真实 corpus_store.db:
    memory_query.ask() 每次检索会向 trace_cited 回写引用统计(真实写库),
    故必须 patch mq._DB_PATH 等指向沙箱, 否则污染真实库(本项目历史事故)。"""
    out_base = out_base or ABLATION_ROOT
    now = datetime.now()
    run_ts = now.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(out_base, run_ts)
    os.makedirs(run_dir, exist_ok=True)

    scenarios = scenarios if scenarios is not None else _load_guard_scenarios()
    if top3_provider is not None and hit_provider is not None:
        # fake provider(selftest): 不碰真实库, 直接跑
        return _run_grid(out_base, run_dir, now, hit_provider, top3_provider, scenarios)

    # 真实模式: 沙箱副本 + patch 全部 DB 路径
    with _real_sandbox() as (hit_provider, top3_provider):
        return _run_grid(out_base, run_dir, now, hit_provider, top3_provider, scenarios)


def _run_grid(out_base, run_dir, now, hit_provider, top3_provider, scenarios):
    """网格主循环(与 provider 解耦, 供 fake/真实共用)。"""
    points = _grid_points()
    grid = []
    for beta, ld in points:
        rate, hit_detail = hit_provider()
        top3 = top3_provider(BETA_PROFILE_OF[beta], ld)
        rec = {"beta": beta, "lambda_debt": ld, "profile": BETA_PROFILE_OF[beta],
               "cross_hit_rate": round(rate, 4), "top3": top3, "hit_detail": hit_detail}
        grid.append(rec)
        # 每组参数快照落盘(审计溯源)
        fname = "grid_b%s_l%s.json" % (("%.2f" % beta).replace(".", ""),
                                       ("%.2f" % ld).replace(".", ""))
        io.open(os.path.join(run_dir, fname), "w", encoding="utf-8").write(
            json.dumps(rec, ensure_ascii=False, indent=1))

    by_pt = {(g["beta"], g["lambda_debt"]): g for g in grid}
    adjacent_checks = []
    for pa, pb in _adjacent_pairs(points):
        ga, gb = by_pt[pa], by_pt[pb]
        hit_delta = abs(ga["cross_hit_rate"] - gb["cross_hit_rate"])
        jac = {name: round(_jaccard(ga["top3"].get(name), gb["top3"].get(name)), 4)
               for name in {**ga["top3"], **gb["top3"]}}
        jac_min = min(jac.values()) if jac else 1.0
        # 辅助观测(不影响 pass 判据): top-3 顺序是否逐位一致, 供人工识别"集合同但位次变"
        order_same = {name: (list(ga["top3"].get(name) or []) == list(gb["top3"].get(name) or []))
                      for name in {**ga["top3"], **gb["top3"]}}
        ok = (hit_delta <= C2_HIT_DELTA_MAX) and (jac_min >= C2_JACCARD_MIN)
        adjacent_checks.append({
            "from": "b%s_l%s" % (pa[0], pa[1]), "to": "b%s_l%s" % (pb[0], pb[1]),
            "hit_delta": round(hit_delta, 4), "jaccard": jac, "jaccard_min": jac_min,
            "top3_order_identical": order_same,
            "pass": ok})

    machine_converged = bool(adjacent_checks) and all(c["pass"] for c in adjacent_checks)

    summary = {
        "generated_ts": now.isoformat(timespec="seconds"),
        "spec": "docs/design_f40_threshold_234.md §1",
        "grid_def": {"beta": BETA_GRID, "lambda_debt": LAMBDA_DEBT_GRID},
        "thresholds": {"hit_delta_max": C2_HIT_DELTA_MAX, "jaccard_min": C2_JACCARD_MIN},
        "grid": grid,
        "adjacent_checks": adjacent_checks,
        "machine_converged": machine_converged,
        # ---- 强制人工签字字段(脚本只出骨架, 由人填) ----
        "human_accept": False,
        "reviewer": "",
        "review_ts": "",
        "note": "机器收敛仅辅助；human_accept=true 且 reviewer/review_ts 非空，方视为消融收敛验收完成",
    }
    io.open(os.path.join(run_dir, "ablation_summary.json"), "w", encoding="utf-8").write(
        json.dumps(summary, ensure_ascii=False, indent=1))
    return summary, run_dir


# ---------------- 自检(selftest: fake provider, 不碰 DB/LLM) ----------------

def selftest():
    """单元用例: ① 网格遍历完整性(9 点) ② 机器收敛布尔输出 ③ 产物目录生成。
    注入确定性 fake provider: 全网格 top-3 一致 + 命中率一致 → machine_converged=True。"""
    def fake_hit():
        return 0.83, {"note": "selftest-stub"}

    def fake_top3(profile, ld):
        # 全网格一致 → Jaccard=1.0 ≥0.7; 命中率一致 → delta=0 ≤0.05 → 收敛 True
        return {"smoke": ["aaaaaaa1", "bbbbbbb2", "ccccccc3"],
                "explore1": ["ddddddd4"],
                "explore2": ["eeeeeee5", "fffffff6"],
                "explore3": ["11111117"],
                "explore4": ["22222228", "33333339"]}

    tmp = tempfile.mkdtemp(prefix="ablation_selftest_")
    summary, run_dir = run_ablation(out_base=tmp, hit_provider=fake_hit,
                                    top3_provider=fake_top3, scenarios=[])
    checks = []
    # ① 网格完整性
    checks.append(("网格遍历完整性: 9 点",
                   len(summary["grid"]) == 9
                   and {(g["beta"], g["lambda_debt"]) for g in summary["grid"]}
                   == set(_grid_points())))
    # ② 机器收敛布尔
    checks.append(("机器收敛布尔输出 True",
                   isinstance(summary["machine_converged"], bool)
                   and summary["machine_converged"] is True
                   and len(summary["adjacent_checks"]) == 12))
    # ③ 产物目录与文件生成
    summary_path = os.path.join(run_dir, "ablation_summary.json")
    grid_files = [f for f in os.listdir(run_dir) if f.startswith("grid_b") and f.endswith(".json")]
    checks.append(("产物目录生成: summary + 9 组 grid",
                   os.path.exists(summary_path) and len(grid_files) == 9))
    # ④ 人工签字字段骨架存在且默认未签
    checks.append(("人工签字字段骨架(默认未签)",
                   summary.get("human_accept") is False
                   and summary.get("reviewer") == "" and summary.get("review_ts") == ""))
    # 清理
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print("  %s %s" % ("✅" if passed else "❌", name))
    print("[门槛②消融自检] %d/%d %s" % (sum(1 for _, p in checks if p), len(checks),
                                       "通过" if ok else "失败"))
    return ok


def main(argv):
    if "--selftest" in argv:
        return 0 if selftest() else 1
    summary, run_dir = run_ablation()
    print("[门槛②消融] machine_converged=%s" % summary["machine_converged"])
    print("[门槛②消融] 产物: %s" % run_dir)
    print("[门槛②消融] 注意: 机器达标!=晋升, 需人工填 human_accept/reviewer/review_ts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))