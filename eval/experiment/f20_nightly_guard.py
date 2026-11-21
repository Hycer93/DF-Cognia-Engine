# -*- coding: utf-8 -*-
"""F20 夜间守卫(experiment 层, 2026-11-16 用户裁决)

裁决全文归档:
  挂主链尾部, 归属 experiment 不升级进 core; 仅跑 baseline β=1.0 守卫基线,
  保守/激进档位只在手动消融启用, 夜间不跑; 基线 PASS/FAIL 翻转→晨报高亮
  告警, 但不阻塞 E 系 core 主链; 日志摘要纳入晨报, 完整链路快照保留独立
  f20 日志目录。

夜间回归规约(配套, 一并归档):
  只校验基线链路可达性(判定)与排序相对位次是否稳定; 权重具体数值允许
  小幅浮动(F 因子 90d 半衰天然逐晚衰减, 属正常), 只要不发生链路出局
  (判定翻转)、座次颠倒(top-k 位次变化), 不判定为故障。

退出码语义: 守卫自身永远退出 0(吞错), 翻转信息只写 guard_latest.json
供晨报高亮——experiment 层不允许用非零退出码惊扰 core 主链的步骤状态。
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(_HERE, "logs", "f20")
GUARD_JSON = os.path.join(_HERE, "logs", "f20", "guard_latest.json")
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")

# A-2 数据漂移归因阈值: 锚邻边数较上次下降超过此比例 → 标注为数据漂移可疑
# (与判定逻辑解耦: 只影响 summary 的 drift_suspect 标注, 不改变 flip/rank 判定)
DRIFT_EDGE_DROP_RATIO = 0.3

# 五场景(与 2026-11-16 四轮消融同参数): (名称, anchor_query, goal_prefix,
# 基线判定, 基线 top-k 短id位次)
# 基线位次 = 确定真序(2026-11-16 两项修正后实测):
#   ① minloop sort 次级键 inst_id 定序并列;
#   ② f20 factor_fresh 连续 age(消 .days 跨日截断跳变)——同权并列基本消失,
#      排序由 ts 真实微差决定, 随时间单调演化, 不会随机抖动。
# 上午消融报告的并列序(DB 行序偶然序)已废弃, 不可作位次基线。
SCENARIOS = [
    ("smoke", "记忆入库断档事故", "a65a500c", "PASS",
     ["87f66cc1", "f1577a38", "f4198bf7"]),
    ("explore1", "误判入库教训 运行时状态快照", "9e23b6ee", "PASS",
     ["04353a35"]),
    ("explore2", "消费侧教训 检索命中正确卡却回答不知情", "89cb3db1", "PASS",
     ["87f66cc1", "f1577a38", "f4198bf7"]),
    ("explore3", "P28专利 多认知头协同神经网络推理系统", "21b35e3f", "PASS",
     ["287e892d", "b178d1f9", "296e4981"]),
    ("explore4", "黄金ETF对冲 地缘风险 仓位", "322560be", "FAIL",
     ["4304794d", "6ed8586c", "fb674356"]),
]

_VERDICT_RE = re.compile(r"^## 总判定: (PASS|FAIL)", re.M)
_TOP_RE = re.compile(r"^\s*-\s+\(?.*?\)?\s*→\s+([0-9a-f]{8})", re.M)


def run_scenario(name, anchor, goal, outdir):
    """跑单场景 baseline 档, 返回 (verdict, top_ids, secs) 或异常上抛"""
    t0 = time.time()
    out_md = os.path.join(outdir, name + ".md")
    # 快照 json 由 minloop 全程同名覆盖——每场景先挪入独立目录再跑,
    # 避免五场景互相覆盖 snapshot(守卫保留完整链路快照, 晨报只放摘要)
    cmd = [sys.executable, os.path.join("eval", "experiment", "future_sim_minloop.py"),
           "--anchor", anchor, "--goal", goal, "--profile", "baseline",
           # minloop --out 拼 join(_HERE,'logs',arg): 此处只传 logs/ 之后的相对段
           "--out", os.path.relpath(out_md, os.path.join(_HERE, "logs"))]
    r = subprocess.run(cmd, cwd=_HERE, capture_output=True, timeout=600)
    txt = io.open(out_md, encoding="utf-8").read() if os.path.exists(out_md) else ""
    m = _VERDICT_RE.search(txt)
    if not m:
        raise RuntimeError("无判定行: rc=%d %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:200]))
    # 位次取 F10 段 top-k(在"## F40"之前的分支行), 只比位次不比数值
    f10_seg = txt.split("## F40")[0]
    top = _TOP_RE.findall(f10_seg)
    return m.group(1), top, round(time.time() - t0, 1)


def _data_drift_snapshot(goal_prefix):
    """A-2 数据漂移快照(只读, 不影响判定)。

    根因可辨性: 守卫的 flipped/rank_shifted 只能表达"结果变了", 无法区分
    "F20 权重异常" vs "库数据变动"。本函数采集足以判别数据侧的客观量:
      - goal_card_edge_count: 目标卡有效关系边数(relation active, 双向)
      - goal_card_topic_ids : 目标卡 topic 归属(改挂会变)
      - goal_neighbor_count : 目标卡 F40 视角出边数(relation+topic, 同 _edges_from 口径)
    另附全库 topic_links 行数作规模参照。采集失败不抛出(返回 error 字段)。
    """
    snap = {"goal_prefix": goal_prefix}
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        g = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                        (goal_prefix + "%",)).fetchone()
        if not g:
            snap["error"] = "goal 卡不存在"
            con.close()
            return snap
        gid = g["inst_id"]
        snap["goal_inst_id"] = gid
        snap["goal_card_edge_count"] = con.execute(
            "SELECT COUNT(*) FROM core_relation WHERE status='active' "
            "AND (me_inst=? OR other_inst=?)", (gid, gid)).fetchone()[0]
        snap["goal_card_topic_ids"] = sorted(
            r["topic_id"] for r in con.execute(
                "SELECT topic_id FROM topic_links WHERE inst_id=?", (gid,)))
        # F40 视角出边数(与 future_sim_minloop._edges_from 同口径: relation 双向 + topic 同簇)
        rel_n = con.execute(
            "SELECT COUNT(*) FROM core_relation WHERE status='active' "
            "AND (me_inst=? OR other_inst=?)", (gid, gid)).fetchone()[0]
        top_n = con.execute(
            "SELECT COUNT(*) FROM topic_links a JOIN topic_links b "
            "ON a.topic_id=b.topic_id WHERE b.inst_id=? AND a.inst_id<>?",
            (gid, gid)).fetchone()[0]
        snap["goal_neighbor_count"] = rel_n + top_n
        snap["db_topic_links_total"] = con.execute(
            "SELECT COUNT(*) FROM topic_links").fetchone()[0]
        con.close()
    except Exception as e:
        snap["error"] = f"{type(e).__name__}: {e}"
    return snap


def _drift_suspect(cur, prev):
    """对比前后两次快照, 判断是否疑似数据漂移。
    规则(仅标注, 不裁决): goal 卡关系边数或邻居数较上次下降超过 DRIFT_EDGE_DROP_RATIO。"""
    if not isinstance(cur, dict) or not isinstance(prev, dict):
        return False, "无上次快照可比"
    for key in ("goal_card_edge_count", "goal_neighbor_count"):
        a, b = prev.get(key), cur.get(key)
        if isinstance(a, int) and isinstance(b, int) and a > 0:
            if (a - b) / a > DRIFT_EDGE_DROP_RATIO:
                return True, f"{key} 由 {a} 降至 {b}(降幅{(a-b)/a:.0%})"
    # topic 归属变更为更强信号
    if prev.get("goal_card_topic_ids") != cur.get("goal_card_topic_ids"):
        return True, "goal 卡 topic 归属变更 %s → %s" % (
            prev.get("goal_card_topic_ids"), cur.get("goal_card_topic_ids"))
    return False, "无可判数据漂移信号"


def main():
    day = time.strftime("%Y%m%d")
    outdir = os.path.join(LOGS, "nightly_" + day)
    os.makedirs(outdir, exist_ok=True)
    results, flipped, rank_shifted, errs = [], False, False, []
    for name, anchor, goal, exp_v, exp_top in SCENARIOS:
        try:
            v, top, secs = run_scenario(name, anchor, goal, outdir)
            # snapshot json 挪进本场景目录(守卫留完整链路快照)
            snap_src = os.path.join(_HERE, "logs", "future_sim_snapshot.json")
            if os.path.exists(snap_src):
                os.replace(snap_src, os.path.join(outdir, name + ".snapshot.json"))
            flip = (v != exp_v)
            # 位次规约: 链路出局=判定翻转; 座次颠倒=top-k 短id序列与基线不一致
            # (分支数变化不算——explore1 这类窄场景本来就只有 1 条)
            rank_ok = top[:len(exp_top)] == exp_top[:len(top)] if top and exp_top else True
            if flip:
                flipped = True
            if not rank_ok:
                rank_shifted = True
            results.append({"name": name, "verdict": v, "expected": exp_v, "flip": flip,
                            "top": top, "expected_top": exp_top, "rank_ok": rank_ok, "secs": secs})
            print("[F20守卫] %s %s(基线%s%s%s) top=%s" %
                  (name, v, exp_v, " ⚠️翻转" if flip else "", " ⚠️位次移动" if not rank_ok else "",
                   ",".join(top)))
        except Exception as e:
            errs.append({"name": name, "error": str(e)[:300]})
            print("[F20守卫] %s 执行异常(吞错不阻塞): %s" % (name, e))
    # A-2 数据漂移快照(采集在判定后、写盘前; 只增字段, 不改 flip/rank 判定)
    prev_summary = None
    if os.path.exists(GUARD_JSON):
        try:
            prev_summary = json.loads(io.open(GUARD_JSON, encoding="utf-8").read())
        except Exception:
            prev_summary = None
    drift_cur = {}
    drift_of = {}
    for name, anchor, goal, exp_v, exp_top in SCENARIOS:
        cur = _data_drift_snapshot(goal)
        drift_cur[name] = cur
        prev = ((prev_summary or {}).get("data_drift") or {}).get(name) \
            if isinstance(prev_summary, dict) else None
        suspect, why = _drift_suspect(cur, prev)
        drift_of[name] = {"suspect": suspect, "reason": why}

    # 数据漂移优先提示: 若转换场景全部疑似数据漂移, 在晨报行标注, 避免误判为权重故障
    drift_any = any(d["suspect"] for d in drift_of.values())
    summary = {"date": time.strftime("%Y-%m-%d %H:%M"), "profile": "baseline",
               "flipped": flipped, "rank_shifted": rank_shifted,
               "scenarios": results, "errors": errs,
               "data_drift": drift_cur, "data_drift_verdict": drift_of,
               "snapshot_dir": os.path.relpath(outdir, _HERE)}
    with io.open(GUARD_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    # 翻转高亮(晨报抓取行)——但退出码恒 0
    if flipped or rank_shifted:
        # 归因提示: 区分"数据漂移"与"疑似权重异常"
        if drift_any:
            hints = "; ".join("%s:%s" % (n, d["reason"])
                              for n, d in drift_of.items() if d["suspect"])
            print("⚠️ F20基线异常(疑似【数据漂移】而非权重翻转): 翻转=%s 位次移动=%s | %s — 见 %s"
                  % (flipped, rank_shifted, hints, GUARD_JSON))
        else:
            print("⚠️ F20基线异常(无数据漂移信号, 需排查权重/代码): 翻转=%s 位次移动=%s — 见 %s"
                  % (flipped, rank_shifted, GUARD_JSON))
    else:
        print("[F20守卫] 基线稳定: 五场景判定与位次全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())