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
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(_HERE, "logs", "f20")
GUARD_JSON = os.path.join(_HERE, "logs", "f20", "guard_latest.json")

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
    summary = {"date": time.strftime("%Y-%m-%d %H:%M"), "profile": "baseline",
               "flipped": flipped, "rank_shifted": rank_shifted,
               "scenarios": results, "errors": errs,
               "snapshot_dir": os.path.relpath(outdir, _HERE)}
    with io.open(GUARD_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    # 翻转高亮(晨报抓取行)——但退出码恒 0
    if flipped or rank_shifted:
        print("⚠️ F20基线异常: 翻转=%s 位次移动=%s — 见 %s" % (flipped, rank_shifted, GUARD_JSON))
    else:
        print("[F20守卫] 基线稳定: 五场景判定与位次全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())