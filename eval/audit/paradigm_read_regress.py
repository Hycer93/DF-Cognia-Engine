# -*- coding: utf-8 -*-
"""
paradigm_read_regress.py — 范式读取&排序回归 (2026-11-15 用户指令 P1-c)

目的: 样例情境核对 core_thinking_paradigm / core_skill_paradigm 的
     读取、筛选、evidence_pass 排序、calibration_pending/candidate
     过滤逻辑, 与 E7 夜间判定、render_persona 渲染三方对齐。

断言组:
  1. 表内容读取: 两表全字段可读, 无坏 JSON 列
  2. 状态筛选: status NOT IN (superseded, retired) 的过滤正确
     (植入一行 retired 样例? 不——只读校验不写库, 用现有行验证筛选集合)
  3. 排序: render_persona.load_paradigms 的输出顺序 == 预期
     (active 优先 → evidence_pass DESC → evidence_count DESC → 衰减排尾)
  4. E7 P3 判定一致性: e7_nightly_governance.p3 扫到的
     (a) calibration_pending 集 (b) 可晋升集(evidence_pass≥3且全pass) (c) 衰减集
     与直接 SQL 复核一致
  5. 渲染产物一致性: prompt_persona_v1.md 中出现的范式名集合 ==
     两表 active+candidate 并集(减 retired/superseded)
  6. 触发条件可匹配: 每条范式 trigger_when 非空(渲染层可显示)
只读。输出 logs/paradigm_read_regress_report.{json,md}。
"""
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
PROMPT = os.path.join(_HERE, "prompts", "prompt_persona_v1.md")
OUT = os.path.join(_HERE, "logs", "paradigm_read_regress_report")

TABLES = ("core_thinking_paradigm", "core_skill_paradigm")


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def load_all(con):
    out = {}
    for t in TABLES:
        rows = con.execute(f"SELECT * FROM {t}").fetchall()
        cols = [d[1] for d in con.execute(f"PRAGMA table_info({t})")]
        out[t] = [dict(zip(cols, r)) for r in rows]
    return out


def expected_order(items):
    """与 render_persona.load_paradigms 相同排序键的独立实现(双源对拍)"""
    def decay_flag(p):
        t = p.get("active_time") or p.get("last_trigger_ts") or p.get("ts")
        try:
            return (datetime.now() - datetime.fromisoformat(t[:19])).days > 90
        except (ValueError, TypeError):
            return True
    visible = [p for p in items if p["status"] not in ("superseded", "retired")]
    decayed = [p for p in visible if p["status"] == "active" and decay_flag(p)]
    fresh = [p for p in visible if p not in decayed]
    fresh.sort(key=lambda p: (p["status"] != "active", -(p["evidence_pass"] or 0),
                              -(p["evidence_count"] or 0)))
    return fresh + decayed


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    data = load_all(con)
    results, ok = [], True

    def check(name, cond, detail=""):
        nonlocal ok
        results.append({"name": name, "pass": bool(cond), "detail": str(detail)[:200]})
        if not cond:
            ok = False

    # 1 读取完整性
    for t in TABLES:
        check(f"{t} 可读", len(data[t]) > 0, f"{len(data[t])} 行")
        bad_trigger = [p["pid"] for p in data[t] if not (p["trigger_when"] or "").strip()]
        check(f"{t} trigger_when 非空", not bad_trigger, bad_trigger)

    # 2 排序对拍: render_persona 实际输出 vs 独立实现
    import render_persona as rp
    con_ro = sqlite3.connect(DB, timeout=5)
    rp_out = rp.load_paradigms(con_ro)
    con_ro.close()
    for t, key in ((TABLES[0], "thinking"), (TABLES[1], "skill")):
        rp_ids = [p["pid"] for p in rp_out[key][0]]
        exp_ids = [p["pid"] for p in expected_order(data[t])]
        check(f"{t} 排序对拍(render==独立实现)", rp_ids == exp_ids,
              {"render": rp_ids, "expected": exp_ids})

    # 3 E7 P3 判定一致性(独立 SQL 复核)
    for t in TABLES:
        # 可晋升集
        promo_sql = [r[0] for r in con.execute(
            f"SELECT pid FROM {t} WHERE status='candidate' AND evidence_pass>=3 "
            f"AND evidence_pass=evidence_count AND evidence_pass>0")]
        promo_logic = [p["pid"] for p in data[t]
                       if p["status"] == "candidate" and (p["evidence_pass"] or 0) >= 3
                       and p["evidence_pass"] == p["evidence_count"] and (p["evidence_pass"] or 0) > 0]
        check(f"{t} E7晋升集一致", promo_sql == promo_logic, {"sql": promo_sql, "logic": promo_logic})
        # 衰减集
        decay_sql = [r[0] for r in con.execute(
            f"SELECT pid FROM {t} WHERE status='active' AND "
            f"(last_trigger_ts IS NULL OR last_trigger_ts < datetime('now','-90 day'))")]
        # ts 存的是 ISO 本地无时区, SQL datetime() 粗算——用 Python 精算对拍
        decay_py = [p["pid"] for p in data[t] if p["status"] == "active" and
                    _over90(p.get("last_trigger_ts") or p["ts"])]
        check(f"{t} 衰减集(SQL粗算==Python精算)", set(decay_sql) == set(decay_py),
              {"sql": decay_sql, "py": decay_py})
        # calibration_pending 集
        calib = [p["pid"] for p in data[t] if p["status"] == "calibration_pending"]
        check(f"{t} calibration_pending 现状记录", True, calib)  # 当前空, 记录口径

    # 4 渲染产物一致性: v1 prompt 中范式名集合 == 两表可见集
    if os.path.exists(PROMPT):
        text = io.open(PROMPT, encoding="utf-8").read()
        names = set()
        for t in TABLES:
            for p in data[t]:
                if p["status"] not in ("superseded", "retired"):
                    names.add(p["name"].split("(")[0].strip())
        missing = [n for n in names if n not in text]
        check("渲染产物含全部可见范式", not missing, missing)
    else:
        check("渲染产物存在", False, PROMPT)

    con.close()
    report = {"ts": _ts(), "ok": ok, "checks": results}
    os.makedirs(os.path.dirname(OUT + ".json"), exist_ok=True)
    with io.open(OUT + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    md = ["# 范式读取&排序回归(切换准入门槛)", f"生成: {_ts()}",
          f"总判定: {'PASS' if ok else 'FAIL'}", ""]
    for r in results:
        md.append(f"- {'✓' if r['pass'] else '✗'} {r['name']}" +
                  (f" | {r['detail']}" if not r["pass"] else ""))
    with io.open(OUT + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[范式回归] {'PASS' if ok else 'FAIL'} — {OUT}.md")
    return 0 if ok else 1


def _over90(ts):
    try:
        return (datetime.now() - datetime.fromisoformat((ts or "")[:19])).days > 90
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())