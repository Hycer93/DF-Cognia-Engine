# -*- coding: utf-8 -*-
"""门槛③ 边覆盖率稳定性考核器（S3, docs/design_f40_threshold_234.md §2 冻结口径）

定位: eval 层只读检测工具, 不改任何业务文件/常量。
- 覆盖率代理: debt_ratio(§2.1), 与 f20_weight.factor_density 同口径
- 沙箱: 每场景 tempfile.mkdtemp() 内 sqlite backup 复制 corpus_store.db,
  全部扰动+推演只作用于副本, **严禁读写真实 corpus_store.db**, 结束删目录(§2.2)
- 场景: 复用 f20_nightly_guard.SCENARIOS 5 场景(不改原文件, import 常量)
- 扰动: 副本内对目标簇 core_hold_pool 插 hold(verdict IS NULL) + 删 active relation 边,
  改变簇 debt_ratio(§2.3)
- PASS: flip=0 且 逐场景 top-3 集合 Jaccard ≥0.6(§2.4)
- 产物: data/experiment/coverage_stability/<run_ts>/stability_summary.json

用法:
  python eval/audit/f20_coverage_stability.py            # 真实两轮(原始/扰动, 慢, 需 DB+embedding)
  python eval/audit/f20_coverage_stability.py --selftest # 快速自检(fake provider, 不碰 DB)
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # cognitive_engine_v3
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "eval", "experiment"),
           os.path.join(_ROOT, "memory")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

REAL_DB = os.path.join(_ROOT, "data", "memory", "corpus_store.db")
STABILITY_ROOT = os.path.join(_ROOT, "data", "experiment", "coverage_stability")

# ---- 冻结口径常量(§6) ----
C3_JACCARD_MIN = 0.6
C3_FLIP_MAX = 0


def _jaccard(a, b):
    A, B = set(a or []), set(b or [])
    if not A and not B:
        return 1.0
    return len(A & B) / len(A | B)


def _short(iid):
    return (iid or "")[:8]


# ---------------- 沙箱 DB 复制 ----------------

def _copy_db(src, dst):
    """sqlite backup API 一致性复制(含已提交 WAL 内容)。"""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
    d = sqlite3.connect(dst, timeout=5)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


# ---------------- 推演流程(复刻 minloop main, 沙箱 DB) ----------------

def _run_pipeline(snapshot, anchor_query, goal_prefix, now,
                  beta_profile="baseline", lambda_debt=0.5):
    """在已 patch 的沙箱 DB 上复刻 minloop 主流程, 返回 (verdict, top3_full_id)。
    F00 快照由调用方传入(冻结语义: 全流程一次, 不因扰动重算)。"""
    import f20_weight as f20
    import future_sim_minloop as m

    # F10 发散(锚定 top-1 中心卡, 防锚集混入近目标卡)
    _k, _n, all_branches = m.f10_diverge(
        {**snapshot, "anchor_cards": snapshot["anchor_cards"][:1]})
    # F20 重排(λ_debt 进程内临时覆盖)
    old_ld = f20.LAMBDA_DEBT
    f20.LAMBDA_DEBT = lambda_debt
    try:
        con = sqlite3.connect(f"file:{m.DB}?mode=ro", uri=True, timeout=5)
        try:
            scored = []
            for b in all_branches:
                w, _fac = f20.score_branch(con, b, step=1, profile=beta_profile, now=now)
                scored.append({**b, "weight": w})
            scored.sort(key=lambda x: (-x["weight"], x["to"] or ""))
        finally:
            con.close()
    finally:
        f20.LAMBDA_DEBT = old_ld
    top3 = [b["to"] for b in scored[:3]]
    all_sorted = scored

    # 真值链 BFS(锚→G, 双边)
    con = sqlite3.connect(f"file:{m.DB}?mode=ro", uri=True, timeout=5)
    try:
        g = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                        (goal_prefix + "%",)).fetchone()
        anchor0 = snapshot["anchor_cards"][0]["inst_id"]
        if not g:
            return "FAIL", top3
        goal_full = g[0]
        from collections import deque
        dq = deque([(anchor0, [anchor0])])
        seen = {anchor0}
        truth = None
        while dq and not truth:
            cur, p = dq.popleft()
            for o, _et, _w, _raw in m._edges_from(con, cur):
                if o in seen:
                    continue
                if o == goal_full:
                    truth = p + [o]
                    break
                seen.add(o)
                dq.append((o, p + [o]))
        truth = truth or [anchor0, goal_full]
    finally:
        con.close()

    # F40 逆向回溯(内部读 sandbox DB)
    path, _note = m.f40_backward(goal_prefix, [c["inst_id"] for c in snapshot["anchor_cards"][:1]])
    if path is None:
        verdict = "FAIL"
    else:
        v = m.verify(path, all_sorted, truth)
        verdict = v["verdict"]
    return verdict, top3


def _eval_scenario(name, anchor, goal, now):
    """单场景: 沙箱复制 -> 原始评估 -> 扰动 -> 扰动后评估 -> 销毁沙箱。
    返回 {"orig":{verdict,top3}, "perturbed":{...}, "debt":{before,after}, "perturb":{...}}"""
    import f20_weight as f20
    import future_sim_minloop as m
    import memory_query as mq

    tmpdir = tempfile.mkdtemp(prefix="covstab_%s_" % name)
    sandbox_db = os.path.join(tmpdir, "corpus_store.db")
    # 保存原常量以便 finally 还原
    old = {"m.DB": m.DB, "f20.DB": f20.DB, "mq._DB_PATH": mq._DB_PATH,
           "m.CONFIG.anchor_query": m.CONFIG.get("anchor_query"),
           "m.CONFIG.goal_prefix": m.CONFIG.get("goal_prefix")}
    try:
        _copy_db(REAL_DB, sandbox_db)
        # patch 全部 DB 指向沙箱
        m.DB = sandbox_db
        f20.DB = sandbox_db
        mq._DB_PATH = sandbox_db
        m.CONFIG["anchor_query"] = anchor
        m.CONFIG["goal_prefix"] = goal

        # F00 冻结快照(沙箱原始态, 全流程一次)
        snapshot = m.f00_anchor()
        # 原始评估
        v0, top0 = _run_pipeline(snapshot, anchor, goal, now)
        debt_before = _cluster_debt(sandbox_db, goal, f20)
        # 扰动
        pert = _perturb(sandbox_db, goal)
        debt_after = _cluster_debt(sandbox_db, goal, f20)
        # 扰动后评估(复用 F00 冻结快照: 扰动只作用于 F10/F40 图遍历, 不重算锚定)
        v1, top1 = _run_pipeline(snapshot, anchor, goal, now)
        return {
            "orig": {"verdict": v0, "top3": top0},
            "perturbed": {"verdict": v1, "top3": top1},
            "debt": {"before": debt_before, "after": debt_after},
            "perturb": pert,
        }
    finally:
        m.DB = old["m.DB"]
        f20.DB = old["f20.DB"]
        mq._DB_PATH = old["mq._DB_PATH"]
        if old["m.CONFIG.anchor_query"] is not None:
            m.CONFIG["anchor_query"] = old["m.CONFIG.anchor_query"]
        if old["m.CONFIG.goal_prefix"] is not None:
            m.CONFIG["goal_prefix"] = old["m.CONFIG.goal_prefix"]
        shutil.rmtree(tmpdir, ignore_errors=True)


def _cluster_debt(db_path, goal_prefix, f20):
    """目标簇成员 avg debt_ratio(factor_density 同口径), 用于记录扰动幅度。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        g = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                        (goal_prefix + "%",)).fetchone()
        if not g:
            return None
        topics = [t for (t,) in con.execute(
            "SELECT topic_id FROM topic_links WHERE inst_id=?", (g[0],))]
        if not topics:
            return None
        qm = ",".join("?" * len(topics))
        members = [i for (i,) in con.execute(
            "SELECT DISTINCT inst_id FROM topic_links WHERE topic_id IN (%s)" % qm, topics)]
        if not members:
            return None
        ds = [f20.factor_density(con, i) for i in members]
        return round(sum(ds) / len(ds), 6)
    finally:
        con.close()


def _perturb(db_path, goal_prefix, max_isolate=2):
    """副本内温和扰动(仅沙箱副本, 不碰真实库), 改变目标簇 debt_ratio:
      (a) hold 注入: 簇内非 goal 成员按 1/3(向上取整) 插入 hold(verdict IS NULL);
      (b) 孤立化: 仅对"度数≤2 且非 goal 自身 且非 goal 一阶邻居"的叶子成员删其
          全部 relation 边使其孤立(最多 max_isolate 个)。
    设计原则: 扰动必须可归因于 debt 变化, 绝不断主链(goal 及其一阶邻居边不动),
    避免"删光整簇=摧毁图谱"式的过度扰动造成 flip 假阳性。
    返回扰动计数。"""
    con = sqlite3.connect(db_path, timeout=5)
    try:
        g = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                        (goal_prefix + "%",)).fetchone()
        if not g:
            return {"error": "goal 卡不存在"}
        gid = g[0]
        topics = [t for (t,) in con.execute(
            "SELECT topic_id FROM topic_links WHERE inst_id=?", (gid,))]
        if topics:
            qm = ",".join("?" * len(topics))
            members = [i for (i,) in con.execute(
                "SELECT DISTINCT inst_id FROM topic_links WHERE topic_id IN (%s)" % qm, topics)]
        else:
            members = [gid]
        members = list(dict.fromkeys(members))
        # goal 一阶 relation 邻居(主链首跳, 绝不删其边/不孤立)
        goal_nbrs = {r[0] for r in con.execute(
            "SELECT other_inst FROM core_relation WHERE status='active' AND me_inst=?", (gid,))}
        goal_nbrs |= {r[0] for r in con.execute(
            "SELECT me_inst FROM core_relation WHERE status='active' AND other_inst=?", (gid,))}
        protected = {gid} | goal_nbrs
        probe = [m for m in members if m not in protected]

        # (a) hold 注入: 1/3
        ts = datetime.now().isoformat(timespec="seconds")
        n_hold = max(1, (len(probe) + 2) // 3) if probe else 0
        hold_ins = 0
        for mem in probe[:n_hold]:
            con.execute(
                "INSERT INTO core_hold_pool(inst_id, reason, reference_mention, "
                "enqueued_ts, verdict) VALUES(?,?,?,?,NULL)",
                (mem, "stability_probe", 0, ts))
            hold_ins += 1

        # (b) 孤立化低度叶子(不影响主链)
        #     统计每个 probe 成员的 relation 度数, 选度数≤2 的孤立化
        deg = {}
        for mem in probe:
            deg[mem] = con.execute(
                "SELECT COUNT(*) FROM core_relation WHERE status='active' "
                "AND (me_inst=? OR other_inst=?)", (mem, mem)).fetchone()[0]
        leaves = [m for m in probe if 0 < deg[m] <= 2]
        leaves.sort(key=lambda m: (deg[m], m))   # 确定性: 度数小者优先, 按 id 定序
        rel_del = 0
        isolated = 0
        for mem in leaves[:max_isolate]:
            rel_del += con.execute(
                "DELETE FROM core_relation WHERE status='active' "
                "AND (me_inst=? OR other_inst=?)", (mem, mem)).rowcount
            isolated += 1
        con.commit()
        return {"hold_inserted": hold_ins, "rel_deleted": rel_del,
                "isolated_members": isolated, "members": len(members),
                "protected_goal_nbrs": len(goal_nbrs)}
    finally:
        con.close()


# ---------------- 稳定性主流程 ----------------

def run_stability(out_base=None, provider=None, scenarios=None):
    """跑 5 场景稳定性。provider 可注入(selftest fake)。返回 (summary, run_dir)。"""
    out_base = out_base or STABILITY_ROOT
    now = datetime.now()
    run_ts = now.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(out_base, run_ts)
    os.makedirs(run_dir, exist_ok=True)

    if scenarios is None:
        import f20_nightly_guard as g
        scenarios = g.SCENARIOS
    if provider is None:
        provider = lambda name, anchor, goal: _eval_scenario(name, anchor, goal, now)  # noqa: E731

    rows = []
    flip_total = 0
    min_jac = 1.0
    for name, anchor, goal, _exp_v, _exp_top in scenarios:
        res = provider(name, anchor, goal)
        v0, v1 = res["orig"]["verdict"], res["perturbed"]["verdict"]
        flip = (v0 != v1)
        jac = round(_jaccard(res["orig"]["top3"], res["perturbed"]["top3"]), 4)
        if flip:
            flip_total += 1
        min_jac = min(min_jac, jac)
        # 扰动有效性: debt 变化幅度(接近 0 说明该场景扰动未实质改变簇状态, 该场景的 PASS 说服力弱)
        db = res.get("debt") or {}
        b, a = db.get("before"), db.get("after")
        debt_delta = round(abs((b or 0) - (a or 0)), 6) if (b is not None and a is not None) else None
        perturb_effective = (debt_delta is not None and debt_delta > 1e-6)
        rows.append({
            "name": name, "orig_verdict": v0, "perturbed_verdict": v1, "flip": flip,
            "orig_top3": [_short(x) for x in res["orig"]["top3"]],
            "perturbed_top3": [_short(x) for x in res["perturbed"]["top3"]],
            "jaccard": jac, "debt": res.get("debt"),
            "debt_delta": debt_delta, "perturb_effective": perturb_effective,
            "perturb": res.get("perturb"),
        })

    stable = (flip_total <= C3_FLIP_MAX) and (min_jac >= C3_JACCARD_MIN)
    ineffective = [r["name"] for r in rows if not r.get("perturb_effective")]
    summary = {
        "generated_ts": now.isoformat(timespec="seconds"),
        "spec": "docs/design_f40_threshold_234.md §2",
        "thresholds": {"flip_max": C3_FLIP_MAX, "jaccard_min": C3_JACCARD_MIN},
        "scenarios": rows,
        "flip_total": flip_total,
        "min_jaccard": round(min_jac, 4),
        "stable": stable,
        "verdict": "PASS" if stable else "FAIL",
        "perturb_ineffective_scenarios": ineffective,
        "note": "沙箱临时库运行; 真实 corpus_store.db 零改动; flip=0 且逐场景 Jaccard>=0.6 判 PASS。"
                "perturb_ineffective_scenarios 列出 debt 未被实质扰动(≈D_FLOOR 下限)的场景, 其 PASS 说服力弱, 人工复核需留意",
    }
    io.open(os.path.join(run_dir, "stability_summary.json"), "w", encoding="utf-8").write(
        json.dumps(summary, ensure_ascii=False, indent=1))
    return summary, run_dir


# ---------------- 自检(selftest: fake provider, 不碰 DB) ----------------

def selftest():
    """单元用例: ① 稳定(PASS)判定 ② 翻转(FAIL)判定 ③ Jaccard 不足(FAIL) ④ 产物生成。
    fake provider 完全脱离真实 DB。"""
    scen = [("s1", "a", "g1", "PASS", []), ("s2", "a", "g2", "PASS", [])]

    def fake_stable(name, anchor, goal):
        return {"orig": {"verdict": "PASS", "top3": ["aaaaaaaa1", "bbbbbbbb2", "cccccccc3"]},
                "perturbed": {"verdict": "PASS", "top3": ["aaaaaaaa1", "bbbbbbbb2", "cccccccc3"]},
                "debt": {"before": 0.4, "after": 0.7}, "perturb": {"hold_inserted": 1}}

    def fake_flip(name, anchor, goal):
        return {"orig": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "perturbed": {"verdict": "FAIL", "top3": ["a1", "b2", "c3"]},
                "debt": {"before": 0.4, "after": 0.8}, "perturb": {}}

    def fake_lowjac(name, anchor, goal):
        return {"orig": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "perturbed": {"verdict": "PASS", "top3": ["x1", "y2", "z3"]},
                "debt": {"before": 0.4, "after": 0.9}, "perturb": {}}

    def fake_ineffective(name, anchor, goal):
        # debt 未变化(模拟 D_FLOOR 下限场景扰动无效)
        return {"orig": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "perturbed": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "debt": {"before": 0.3, "after": 0.3}, "perturb": {}}

    tmp = tempfile.mkdtemp(prefix="covstab_selftest_")
    checks = []

    s_stable, d_stable = run_stability(out_base=tmp, provider=fake_stable, scenarios=scen)
    checks.append(("稳定场景 flip=0/Jaccard=1.0 -> PASS",
                   s_stable["stable"] is True and s_stable["verdict"] == "PASS"
                   and s_stable["flip_total"] == 0))

    s_flip, _ = run_stability(out_base=tmp, provider=fake_flip, scenarios=scen)
    checks.append(("判定翻转 -> FAIL",
                   s_flip["stable"] is False and s_flip["flip_total"] == 2))

    s_low, _ = run_stability(out_base=tmp, provider=fake_lowjac, scenarios=scen)
    checks.append(("Jaccard=0 <0.6 -> FAIL",
                   s_low["stable"] is False and s_low["min_jaccard"] < C3_JACCARD_MIN))

    files = os.listdir(d_stable)
    checks.append(("产物 stability_summary.json 生成",
                   os.path.exists(os.path.join(d_stable, "stability_summary.json"))))

    # ⑤ 扰动无效场景被显式标注(不误判为已充分验证)
    s_ineff, _ = run_stability(out_base=tmp, provider=fake_ineffective, scenarios=scen)
    checks.append(("扰动无效场景(debt Δ=0)被标注",
                   s_ineff["stable"] is True
                   and set(s_ineff["perturb_ineffective_scenarios"]) == {"s1", "s2"}))

    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print("  %s %s" % ("✅" if passed else "❌", name))
    print("[门槛③稳定性自检] %d/%d %s" % (sum(1 for _, p in checks if p), len(checks),
                                        "通过" if ok else "失败"))
    return ok


def _db_fingerprint(db_path):
    """真实库指纹(大小+mtime), 用于外部核对零改动。"""
    st = os.stat(db_path)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def main(argv):
    if "--selftest" in argv:
        return 0 if selftest() else 1
    fp_before = _db_fingerprint(REAL_DB)
    summary, run_dir = run_stability()
    fp_after = _db_fingerprint(REAL_DB)
    print("[门槛③稳定性] flip=%d min_jaccard=%.4f -> %s"
          % (summary["flip_total"], summary["min_jaccard"], summary["verdict"]))
    print("[门槛③稳定性] 产物: %s" % run_dir)
    print("[门槛③稳定性] 真实库指纹 before=%s after=%s %s"
          % (fp_before, fp_after, "未改动" if fp_before == fp_after else "!! 已改动 !!"))
    return 0 if summary["stable"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))