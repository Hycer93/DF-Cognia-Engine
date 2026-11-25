# -*- coding: utf-8 -*-
"""F40 晋升门槛②③④ 总回归（S5, docs/03_future_sim/design_f40_threshold_234.md 冻结口径集成）

定位: eval 层只读检测工具, 集成三块单元回归。
- 门槛②消融收敛: 网格完整性 / machine_converged 布尔 / 产物目录生成(委托 f20_ablation_convergence)
- 门槛③沙箱稳定性: 临时沙箱扰动 / 翻转计数 / Jaccard 校验 / 真实库零改动(委托 f20_coverage_stability)
- 门槛④降级分支: 四枚举触发矩阵(自包含 fixture DB, 调 f20_weight.degrade_path_candidates)
本期不实现 promote_check() 门面; 产物写审计目录, 不改业务库。
退出码: 全通过 0, 任一断言失败 1。

用法:
  python eval/audit/f40_promotion_gate_regression.py --selftest  # 快速自检(fake provider, 不依赖真实DB/embedding), CI 用
  python eval/audit/f40_promotion_gate_regression.py             # 真实模式(完整消融+稳定性+降级矩阵, 较慢)
"""
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "eval", "experiment"),
           os.path.join(_ROOT, "eval", "audit"), os.path.join(_ROOT, "memory")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


# ---------------- 门槛④ 降级分支: 四枚举触发矩阵(自包含 fixture) ----------------

def _make_fixture():
    """内存 DB fixture: 覆盖四枚举 + 提供锚点边界内候选。
    仅测试用, 不触碰任何真实库。"""
    con = sqlite3.connect(":memory:", timeout=5)
    con.row_factory = sqlite3.Row
    con.executescript(
        "CREATE TABLE core_memory_card(inst_id TEXT PRIMARY KEY, ts TEXT, event_ts TEXT, confidence TEXT);"
        "CREATE TABLE core_relation(me_inst TEXT, other_inst TEXT, relation TEXT, status TEXT);"
        "CREATE TABLE topic_links(inst_id TEXT, topic_id TEXT);"
        "CREATE TABLE core_hold_pool(inst_id TEXT, verdict TEXT);"
    )
    A = "a" * 32
    nb = ["b" * 32, "c" * 32, "d" * 32]        # 锚点邻居(边界内候选来源)
    iso = "1" * 32                              # goal_isolated: 无任何边
    stp = "2" * 32                              # step_exhausted: 仅 topic, 无 relation
    sen = "3" * 32                              # sensitive_blocked: 挂高敏簇
    gap = "4" * 32                              # edge_coverage_gap: 有 relation 边
    ts = "2026-11-22T00:00:00"
    for n in [A] + nb + [iso, stp, sen, gap]:
        con.execute("INSERT INTO core_memory_card(inst_id,ts,event_ts,confidence) VALUES(?,?,?,?)",
                    (n, ts, ts, "L1"))
    # 锚点 relation 邻居
    for n in nb:
        con.execute("INSERT INTO core_relation(me_inst,other_inst,relation,status) VALUES(?,?,?,?)",
                    (A, n, "rel", "active"))
    # 锚点簇(含邻居) topic 归属 → topic 弱边
    for n in [A] + nb:
        con.execute("INSERT INTO topic_links(inst_id,topic_id) VALUES(?,?)", (n, "topic_x"))
    # 各枚举目标
    con.execute("INSERT INTO topic_links(inst_id,topic_id) VALUES(?,?)", (stp, "topic_memory_ops"))
    con.execute("INSERT INTO topic_links(inst_id,topic_id) VALUES(?,?)", (sen, "topic_user_relation"))
    con.execute("INSERT INTO core_relation(me_inst,other_inst,relation,status) VALUES(?,?,?,?)",
                (gap, A, "rel", "active"))
    con.commit()
    return con, A


def degrade_matrix():
    """门槛④ 四枚举触发矩阵。返回 list[(name, passed)]。"""
    import f20_weight as f20
    con, A = _make_fixture()
    checks = []
    try:
        cases = [
            ("goal_isolated", "1" * 32, 6, True),
            ("step_exhausted", "2" * 32, 3, True),
            ("sensitive_blocked", "3" * 32, 6, False),
            ("edge_coverage_gap", "4" * 32, 1, False),
        ]
        for reason, prefix, ms, expect_degrade in cases:
            r = f20.degrade_path_candidates(con, prefix, [A], ms)
            ok = (r["degraded"] is expect_degrade
                  and r["failure_reason"] == reason
                  and isinstance(r["low_confidence_candidates"], list))
            if expect_degrade:
                ok = ok and len(r["low_confidence_candidates"]) > 0
                ok = ok and all(c["factors"]["step"] <= ms for c in r["low_confidence_candidates"])
                ok = ok and all(set(c.keys()) == {"branch", "w", "factors"}
                                for c in r["low_confidence_candidates"])
            else:
                ok = ok and r["low_confidence_candidates"] == []
            checks.append(("降级矩阵[%s] degraded=%s" % (reason, expect_degrade), ok))
    finally:
        con.close()
    return checks


# ---------------- 门槛②消融收敛(委托 S2) ----------------

def ablation_checks():
    """复用 f20_ablation_convergence.selftest() 内部四个单元断言, 返回 granular 结果。
    为避免重复实现, 直接调用其 run_ablation(fake provider) 并断言输出结构。"""
    import f20_ablation_convergence as ab
    import tempfile
    import shutil

    def fake_hit():
        return 0.83, {"note": "s5-stub"}

    def fake_top3(profile, ld):
        return {"smoke": ["a1", "b2", "c3"], "explore1": ["d4"],
                "explore2": ["e5", "f6"], "explore3": ["g7"], "explore4": ["h8", "i9"]}

    tmp = tempfile.mkdtemp(prefix="s5_abl_")
    checks = []
    try:
        summary, run_dir = ab.run_ablation(out_base=tmp, hit_provider=fake_hit,
                                           top3_provider=fake_top3, scenarios=[])
        checks.append(("门槛② 网格遍历完整性(9点)",
                       len(summary["grid"]) == 9
                       and {(g["beta"], g["lambda_debt"]) for g in summary["grid"]}
                       == set(ab._grid_points())))
        checks.append(("门槛② 机器收敛布尔输出",
                       isinstance(summary["machine_converged"], bool)
                       and summary["machine_converged"] is True
                       and len(summary["adjacent_checks"]) == 12))
        grid_files = [f for f in os.listdir(run_dir) if f.startswith("grid_b") and f.endswith(".json")]
        checks.append(("门槛② 产物目录生成(summary+9组)",
                       os.path.exists(os.path.join(run_dir, "ablation_summary.json"))
                       and len(grid_files) == 9))
        checks.append(("门槛② 人工签字字段骨架(默认未签)",
                       summary.get("human_accept") is False
                       and summary.get("reviewer") == "" and summary.get("review_ts") == ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return checks


# ---------------- 门槛③沙箱稳定性(委托 S3) ----------------

def stability_checks():
    """复用 f20_coverage_stability: fake provider 验证 PASS/FAIL 判定 + 产物生成。"""
    import f20_coverage_stability as cs
    import tempfile
    import shutil

    scen = [("s1", "a", "g1", "PASS", []), ("s2", "a", "g2", "PASS", [])]

    def fake_stable(name, anchor, goal):
        return {"orig": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "perturbed": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "debt": {"before": 0.4, "after": 0.7}, "perturb": {"hold_inserted": 1}}

    def fake_flip(name, anchor, goal):
        return {"orig": {"verdict": "PASS", "top3": ["a1", "b2", "c3"]},
                "perturbed": {"verdict": "FAIL", "top3": ["a1", "b2", "c3"]},
                "debt": {"before": 0.4, "after": 0.8}, "perturb": {}}

    tmp = tempfile.mkdtemp(prefix="s5_stab_")
    checks = []
    try:
        s_ok, d_ok = cs.run_stability(out_base=tmp, provider=fake_stable, scenarios=scen)
        checks.append(("门槛③ 稳定(flip=0,Jaccard=1)->PASS",
                       s_ok["stable"] is True and s_ok["verdict"] == "PASS"
                       and s_ok["flip_total"] == 0))
        s_fl, _ = cs.run_stability(out_base=tmp, provider=fake_flip, scenarios=scen)
        checks.append(("门槛③ 判定翻转->FAIL(flip>0)",
                       s_fl["stable"] is False and s_fl["flip_total"] == 2))
        checks.append(("门槛③ 产物 stability_summary.json 生成",
                       os.path.exists(os.path.join(d_ok, "stability_summary.json"))))
        checks.append(("门槛③ PJaccard阈值常量=0.6", cs.C3_JACCARD_MIN == 0.6))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return checks


# ---------------- 编排 ----------------

def _fingerprint(db_path):
    try:
        st = os.stat(db_path)
        return {"size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return None


def _report(title, checks):
    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print("  %s %s" % ("✅" if passed else "❌", name))
    print("[%s] %d/%d %s" % (title, sum(1 for _, p in checks if p), len(checks),
                             "通过" if ok else "失败"))
    return ok


def run_selftest():
    """CI 快速自检: 三块全 fake, 不依赖真实 DB/embedding。"""
    print("=== 门槛④ 降级分支四枚举矩阵(fixture) ===")
    ok4 = _report("门槛④降级", degrade_matrix())
    print("=== 门槛② 消融收敛(fake provider) ===")
    ok2 = _report("门槛②消融", ablation_checks())
    print("=== 门槛③ 沙箱稳定性(fake provider) ===")
    ok3 = _report("门槛③稳定性", stability_checks())
    all_ok = ok2 and ok3 and ok4
    print("\n[F40晋升门禁总回归-selftest] %s" % ("全部通过" if all_ok else "存在失败"))
    return all_ok


def run_real():
    """真实模式: 完整消融(真实DB) + 稳定性(沙箱) + 降级矩阵(fixture); 校验真实库零改动。"""
    import f20_ablation_convergence as ab
    import f20_coverage_stability as cs

    real_db = cs.REAL_DB
    fp_before = _fingerprint(real_db)

    print("=== 门槛④ 降级分支四枚举矩阵(fixture) ===")
    ok4 = _report("门槛④降级", degrade_matrix())

    print("=== 门槛② 消融收敛(真实DB, 9点网格) ===")
    abl_summary, abl_dir = ab.run_ablation()
    ok2 = _report("门槛②消融", [
        ("网格9点", len(abl_summary["grid"]) == 9),
        ("相邻边12条", len(abl_summary["adjacent_checks"]) == 12),
        ("machine_converged 布尔", isinstance(abl_summary["machine_converged"], bool)),
        ("产物目录", os.path.isdir(abl_dir)),
    ])

    print("=== 门槛③ 沙箱稳定性(真实DB沙箱副本) ===")
    stab_summary, stab_dir = cs.run_stability()
    ok3 = _report("门槛③稳定性", [
        ("flip<=0", stab_summary["flip_total"] <= cs.C3_FLIP_MAX),
        ("min_jaccard>=0.6", stab_summary["min_jaccard"] >= cs.C3_JACCARD_MIN),
        ("产物目录", os.path.isdir(stab_dir)),
    ])

    fp_after = _fingerprint(real_db)
    ok_db = (fp_before == fp_after)
    _report("真实库零改动", [("corpus_store.db 指纹前后一致", ok_db)])

    all_ok = ok2 and ok3 and ok4 and ok_db
    print("\n[F40晋升门禁总回归-真实模式] %s" % ("全部通过" if all_ok else "存在失败"))
    print("  消融产物: %s" % abl_dir)
    print("  稳定性产物: %s" % stab_dir)
    return all_ok


def main(argv):
    if "--selftest" in argv:
        return 0 if run_selftest() else 1
    return 0 if run_real() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))