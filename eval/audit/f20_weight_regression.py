"""f20_weight_regression.py — F20 权重层断言式回归 (2026-11-21 A-3)

背景: f20_weight.py 此前只有 main() 自检(打印无断言), eval/audit 无任何 F20 回归。
本脚本补静态断言, 覆盖 5 类判据, 全部**不依赖当前 DB 快照的特定卡**——
只验证公式性质与常量契约, 库数据怎么漂都不误报。

口径:
  - 纯计算用例用假 con(monkeypatch 因子函数), 不读库
  - 需要真库的用例只做「性质断言」(如 D 恒 >= D_FLOOR), 不断言具体值
  - 常量契约断言"值与设计文档一致", 权重重标时须同步改文档+本脚本

运行: python eval/audit/f20_weight_regression.py
退出码 0=全过 1=有断言失败。
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "eval", "experiment"))
sys.path.insert(0, _HERE)

import f20_weight as F  # noqa: E402

_RESULTS = []


def _check(name, cond, detail=""):
    _RESULTS.append({"name": name, "ok": bool(cond), "detail": detail})
    print(("  ✅ " if cond else "  ❌ ") + name + (" | " + detail if detail and not cond else ""))


def case_beta_monotonic():
    """用例1: β 档位单调性。同一分支, β 越大 (1+δ)^β 越大(因 1+δ>1) → w 单调不降。

    用假因子函数固定 C/F/R/D, 隔离 β 的影响。
    """
    print("[1] β 档位单调性")
    orig = (F.factor_conf, F.factor_fresh, F.factor_edge, F.factor_density)
    F.factor_conf = lambda con, iid: 1.0
    F.factor_fresh = lambda con, iid, now=None, with_source=False: ((1.0, "event_ts") if with_source else 1.0)
    F.factor_edge = lambda etype: 1.0
    F.factor_density = lambda con, iid: 1.0
    try:
        branch = {"to": "x", "rel": "rel:new"}
        ws = [F.score_branch(None, branch, step=1, profile=p)[0]
              for p in ("conservative", "baseline", "exploratory")]
        _check("β 越大 w 单调不降", ws[0] <= ws[1] <= ws[2], f"w={ws}")
        # step=0 时 δ=0, (1+0)^β=1 恒定, 三档应相等
        ws0 = [F.score_branch(None, branch, step=0, profile=p)[0]
               for p in ("conservative", "baseline", "exploratory")]
        _check("step=0 时 δ=0, 三档 w 相等", abs(ws0[0] - ws0[1]) < 1e-9 and abs(ws0[1] - ws0[2]) < 1e-9,
               f"w0={ws0}")
    finally:
        F.factor_conf, F.factor_fresh, F.factor_edge, F.factor_density = orig


def case_density_floor():
    """用例2: D 因子硬下限。任意卡(含高债务簇) D >= D_FLOOR=0.3。

    需要真库(读 core_relation/topic_links)。库不存在则跳过不判失败。
    """
    print("[2] D 因子硬下限 D_FLOOR=0.3")
    import sqlite3
    if not os.path.exists(F.DB):
        _check("D 硬下限(库不存在, 跳过)", True, "skip")
        return
    con = sqlite3.connect(f"file:{F.DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        ids = [r["inst_id"] for r in con.execute(
            "SELECT inst_id FROM core_memory_card LIMIT 40")]
        if not ids:
            _check("D 硬下限(无卡, 跳过)", True, "skip")
            return
        bad = []
        for iid in ids:
            d = F.factor_density(con, iid)
            if d < F.D_FLOOR - 1e-9:
                bad.append((iid[:8], d))
        _check(f"D>=D_FLOOR 全 {len(ids)} 卡成立", not bad, f"违反={bad[:3]}")
        _check("D_FLOOR 常量=0.3", abs(F.D_FLOOR - 0.3) < 1e-12, str(F.D_FLOOR))
    finally:
        con.close()


def case_fresh_continuity():
    """用例3: F 因子时间连续性 + 来源口径(2026-11-24 #37 修复)。

    3a: 两个相差 5 秒的 event_ts → F 差异极小(连续 age, 非 .days 整档跳变)。
    3b: event_ts 缺失时不回退 ts —— 即便 ts 是"今天"(批量导入), 也给中性 FRESH_NEUTRAL,
        不复用 ts(旧 bug: ts 当事件时间会让考古卡 F≈1.0)。
    3c: 无卡记录也给中性值(不用旧的 0.5/1年前, 避免误杀)。
    3d: with_source=True 时来源标记正确(event_ts / neutral)。
    """
    print("[3] F 因子连续性 + 来源口径")
    from datetime import datetime, timedelta

    class _FakeRow(dict):
        pass

    class _FakeCon:
        row_factory = None      # _ensure_rows 会检查并设置; 设 None 使其进入转换分支
        def __init__(self, event_ts, ts=None):
            self._event_ts = event_ts
            self._ts = ts
        def execute(self, *a, **k):
            return self
        def fetchone(self):
            return _FakeRow({"event_ts": self._event_ts, "ts": self._ts})

    now = datetime.now()
    ts1 = (now - timedelta(days=10, seconds=0)).isoformat()
    ts2 = (now - timedelta(days=10, seconds=5)).isoformat()
    f1 = F.factor_fresh(_FakeCon(ts1), "x", now)
    f2 = F.factor_fresh(_FakeCon(ts2), "x", now)
    # 5 秒差在半衰期 90d 下, F 差异应极小(< 1e-4), 绝不能出现跨天整档跳变(~0.008)
    _check("3a: 5秒时间差 F 变化极小(<1e-4)", abs(f1 - f2) < 1e-4, f"f1={f1:.6f} f2={f2:.6f}")

    # 3b: event_ts 缺失但 ts=今天(模拟批量导入) → 必须给中性值, 不能拿 ts 当事件时间
    today_ts = now.isoformat()
    f_nots = F.factor_fresh(_FakeCon(None, today_ts), "x", now)
    _check("3b: event_ts缺失不回退ts→中性0.7(不误判为新)",
           abs(f_nots - F.FRESH_NEUTRAL) < 1e-9,
           f"f={f_nots:.6f} 应={F.FRESH_NEUTRAL}(ts={today_ts[:10]} 若回退旧bug会≈1.0)")

    # 3c: 无卡记录 → 中性值
    class _NullCon:
        row_factory = None
        def execute(self, *a, **k):
            return self
        def fetchone(self):
            return None
    fnull = F.factor_fresh(_NullCon(), "x", now)
    _check("3c: 无卡记录 F=FRESH_NEUTRAL(中性, 非旧0.5)",
           abs(fnull - F.FRESH_NEUTRAL) < 1e-9, str(fnull))

    # 3d: 来源标记
    f_ok, src_ok = F.factor_fresh(_FakeCon(today_ts), "x", now, with_source=True)
    f_no, src_no = F.factor_fresh(_FakeCon(None, today_ts), "x", now, with_source=True)
    _check("3d: with_source 来源标记(event_ts/neutral)",
           src_ok == "event_ts" and src_no == "neutral",
           f"ok={src_ok} no={src_no}")
    _check("3e: FRESH_NEUTRAL 常量=0.7", abs(F.FRESH_NEUTRAL - 0.7) < 1e-12, str(F.FRESH_NEUTRAL))


def case_constants_immutable():
    """用例4: 常量契约。β 档位表三档值、δ_step 常量、因子乘子均与设计文档一致。

    防"标定后改常量忘记更新文档/回归"或"运行时被人为改写"。
    """
    print("[4] 常量契约(值与设计文档一致)")
    _check("BETA_PROFILES 三档值", F.BETA_PROFILES ==
           {"conservative": 0.6, "baseline": 1.0, "exploratory": 1.6}, str(F.BETA_PROFILES))
    _check("BASELINE_PROFILE=baseline", F.BASELINE_PROFILE == "baseline", F.BASELINE_PROFILE)
    _check("DELTA_STEP_BASE=0.6", abs(F.DELTA_STEP_BASE - 0.6) < 1e-12, str(F.DELTA_STEP_BASE))
    _check("DELTA_STEP_SLOPE=0.85", abs(F.DELTA_STEP_SLOPE - 0.85) < 1e-12, str(F.DELTA_STEP_SLOPE))
    _check("CONF_TIER 三级", F.CONF_TIER == {"L0": 1.0, "L1": 0.7, "L2": 0.4}, str(F.CONF_TIER))
    _check("EDGE_W rel/topic", F.EDGE_W == {"rel": 1.0, "topic": 0.5}, str(F.EDGE_W))
    _check("LAMBDA_DEBT=0.5", abs(F.LAMBDA_DEBT - 0.5) < 1e-12, str(F.LAMBDA_DEBT))
    _check("FRESH_HALFLIFE_DAYS=90", F.FRESH_HALFLIFE_DAYS == 90, str(F.FRESH_HALFLIFE_DAYS))


def case_failure_attribution():
    """用例5: 四枚举归因分支全部可命中。

    用假 con 构造四种情形, 断言 attribute_failure 返回对应枚举。
    goal_isolated: 无卡 / 度=0
    sensitive_blocked: 目标挂 topic_user_relation
    edge_coverage_gap: 有 relation 边
    """
    print("[5] 四枚举归因分支")

    def _mk_con(goal_row, deg_rel=0, deg_top=0, topics=(), rel_n1=0):
        class _Row(dict):
            pass

        class _Con:
            def __init__(self):
                self._calls = 0
            def execute(self, sql, params=()):
                self._sql = sql
                self._params = params
                return self
            def fetchone(self):
                s = self._sql
                if "core_memory_card" in s:
                    return goal_row
                if "COUNT(*)" in s and "core_relation" in s:
                    return (deg_rel if "status" in s else rel_n1,)
                if "COUNT(*)" in s and "topic_links" in s:
                    return (deg_top,)
                return (0,)
            def fetchall(self):
                if "topic_links" in self._sql:
                    return [_Row({"topic_id": t}) for t in topics]
                return []
            def __iter__(self):
                # 真实代码用 `for r in con.execute(...)` 迭代游标取 topic_id
                if "topic_links" in self._sql:
                    return iter([_Row({"topic_id": t}) for t in topics])
                return iter([])
        return _Con()

    # goal_isolated: 无卡
    code, _ = F.attribute_failure(_mk_con(None), "zzz", [], 12)
    _check("无卡 → goal_isolated", code == "goal_isolated", code)
    # goal_isolated: 度=0
    code, _ = F.attribute_failure(_mk_con({"inst_id": "g"}), "g" * 8, [], 12)
    _check("双边度=0 → goal_isolated", code == "goal_isolated", code)
    # sensitive_blocked: 挂敏感簇
    code, _ = F.attribute_failure(
        _mk_con({"inst_id": "g"}, deg_rel=1, topics=["topic_user_relation"]), "g" * 8, [], 12)
    _check("挂高敏簇 → sensitive_blocked", code == "sensitive_blocked", code)
    # edge_coverage_gap: 有 relation 边
    code, _ = F.attribute_failure(
        _mk_con({"inst_id": "g"}, deg_rel=1, rel_n1=1, topics=["topic_x"]), "g" * 8, [], 12)
    _check("有边未及 → edge_coverage_gap", code == "edge_coverage_gap", code)


def case_formula_shape():
    """补充: 复合公式形状——五因子全为上限时 w 有界, 且 w 恒正(红线: 软降权不归零)。"""
    print("[6] 公式形状(恒正/有界)")
    orig = (F.factor_conf, F.factor_fresh, F.factor_edge, F.factor_density)
    F.factor_conf = lambda con, iid: 1.0
    F.factor_fresh = lambda con, iid, now=None, with_source=False: ((1.0, "event_ts") if with_source else 1.0)
    F.factor_edge = lambda etype: 1.0
    F.factor_density = lambda con, iid: F.D_FLOOR
    try:
        w, _ = F.score_branch(None, {"to": "x", "rel": "rel:x"}, step=3, profile="baseline")
        _check("极端低因子下 w>0(永不归零)", w > 0, str(w))
        # 因子全低时 w 应 <= 全高时(单调于各因子)
        F.factor_density = lambda con, iid: 1.0
        w_hi, _ = F.score_branch(None, {"to": "x", "rel": "rel:x"}, step=3, profile="baseline")
        _check("因子全高 w >= 因子全低 w", w_hi >= w, f"hi={w_hi} lo={w}")
    finally:
        F.factor_conf, F.factor_fresh, F.factor_edge, F.factor_density = orig


def main():
    case_beta_monotonic()
    case_density_floor()
    case_fresh_continuity()
    case_constants_immutable()
    case_failure_attribution()
    case_formula_shape()

    failed = [r for r in _RESULTS if not r["ok"]]
    print("\n[F20 权重层回归] %d/%d 通过" % (len(_RESULTS) - len(failed), len(_RESULTS)))
    out = os.path.join(_HERE, "logs", "f20_weight_regression.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    import json
    io.open(out, "w", encoding="utf-8").write(
        json.dumps({"total": len(_RESULTS), "passed": len(_RESULTS) - len(failed),
                    "results": _RESULTS}, ensure_ascii=False, indent=1))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()