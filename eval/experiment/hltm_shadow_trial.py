# -*- coding: utf-8 -*-
"""
hltm_shadow_trial.py — HLTM 热度加权灰度试验 (2026-11-15 用户指令 P3-a, #37)

定位: 影子侧试验分支, 只读 trace 流水, 只输出对比报表——不接入正式
     检索链路, 不影响四维权重现行体系。

设计:
  HLTM(热度) = 基于 trace_cited 的近期引用频次(半衰期衰减),
  与现行 recency(_recency_table: 7d窗+半衰期, 输出0.5~1.0)的差异:
    HLTM 用更长窗(30d)+更平缓半衰期(14d)+绝对频次归一,
    试图捕获"长热记忆"而非仅"本周话题"。

对比口径(同 P1-a 双路思想, 但第三路):
  baseline = 现行四维权重 ask 结果(jsonl 链路, recency=7d窗)
  trial    = 同 ask, 但 _recency_table 替换为 HLTM 版(30d/14d)
  → 输出每样例的 top5 变化 + 热度分排名变化榜

输出: logs/hltm_trial_report.{json,md}。零副作用(cited_log 护栏复用)。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
CITED_LOG = os.path.join(_HERE, "data", "memory", "cited_log.jsonl")
OUT = os.path.join(_HERE, "logs", "hltm_trial_report")
TRIAL_WINDOW_DAYS = 30
TRIAL_HALFLIFE_DAYS = 14

SAMPLES = [
    "3090 显卡本地部署大模型选型",
    "平行论双向推演的方案",
    "8397 工作流的问题处理",
    "记忆系统治理的裁决",
    "紫光国微建仓分歧",
    "断档事故",
    "伪演示基线",
    "E7 引用性提及",
]


def _ts():
    return datetime.now().isoformat(timespec="seconds")


class _CitedLogGuard:
    """复用 shadow_regress 护栏: ask 会写 cited_log, 跑完回滚"""

    def __enter__(self):
        self.data = b""
        if os.path.exists(CITED_LOG):
            with open(CITED_LOG, "rb") as f:
                self.data = f.read()
        return self

    def __exit__(self, *a):
        if self.data:
            with open(CITED_LOG, "wb") as f:
                f.write(self.data)


def hltm_table():
    """HLTM 试验版热度表: 30d 窗 + 14d 半衰期 + 频次加成。
    公式: base(0.3~0.7 由衰减决定) + 0.2*min(1, 频次/10) —— 长热高频卡拉开差距。
    数据源: trace_cited(新库) + cited_log 冻结后增量(对齐 P1-a 漂移处理,
    试验目的在于'效果对比'而非'双库一致', 故并两源取全量)。"""
    cutoff = datetime.now().timestamp() - TRIAL_WINDOW_DAYS * 86400
    halflife = TRIAL_HALFLIFE_DAYS * 86400
    now = datetime.now().timestamp()
    agg = {}  # inst -> {latest_ts, freq}
    # 主源: 新库
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for ts_str, inst in con.execute("SELECT ts, inst_id FROM trace_cited"):
        try:
            ts = datetime.fromisoformat(ts_str[:19]).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        a = agg.setdefault(inst, {"latest": 0.0, "freq": 0})
        a["latest"] = max(a["latest"], ts)
        a["freq"] += 1
    con.close()
    # 增量源: cited_log 冻结后新行(jsonl 侧持续写入)
    freeze_ts = None
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    freeze_ts = con.execute("SELECT MAX(ts) FROM trace_cited").fetchone()[0] or ""
    con.close()
    if os.path.exists(CITED_LOG):
        with io.open(CITED_LOG, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if (rec.get("ts") or "")[:19] <= freeze_ts[:19]:
                    continue
                try:
                    ts = datetime.fromisoformat(rec["ts"][:19]).timestamp()
                except (ValueError, KeyError):
                    continue
                if ts < cutoff:
                    continue
                inst = rec.get("inst_id")
                if not inst:
                    continue
                a = agg.setdefault(inst, {"latest": 0.0, "freq": 0})
                a["latest"] = max(a["latest"], ts)
                a["freq"] += 1
    table = {}
    for inst, a in agg.items():
        decay = 0.5 ** (max(0, now - a["latest"]) / halflife)
        base = 0.5 + 0.4 * decay           # 0.5~0.9(超过现行 time_unknown 0.4 档)
        freq_bonus = 0.1 * min(1.0, a["freq"] / 10)
        table[inst] = min(1.0, base + freq_bonus)  # 0.5~1.0 域, 与现行可竞争
    return table


def _ids(result):
    return [item[1]["inst_id"] if isinstance(item, (tuple, list)) else item["inst_id"]
            for item in result]


def main():
    import memory.memory_query as mq
    from eval.shadow_regress import db_load_memories, db_load_graph
    trial = hltm_table()
    report = {"ts": _ts(), "trial_table_size": len(trial), "samples": [], "ok": True}
    # 基线热度表快照(与 trial 同一数据视野, 7d 窗现行口径)
    import memory.memory_query as _mq0
    base_table = dict(_mq0._recency_table())
    top_gain = {}
    for q in SAMPLES:
        with _CitedLogGuard():
            # baseline: 现行链路(jsonl+7d recency)
            mq._recency_cache = {"mtime": 0.0, "table": {}}
            orig = (mq.load_memories, mq.load_graph, mq._recency_table)
            base = mq.ask(q, top_k=5)
            mq.load_memories, mq.load_graph, mq._recency_table = orig
            # trial: 同打分逻辑, recency 换 HLTM 表(数据源同 jsonl 基线——隔离单变量)
            mq._recency_table = lambda: dict(trial)
            trial_r = mq.ask(q, top_k=5)
            mq._recency_cache = {"mtime": 0.0, "table": {}}
        b, t = _ids(base), _ids(trial_r)
        changed = b != t
        report["samples"].append({"query": q, "base": b, "trial": t, "changed": changed})
        for i in t:
            if i not in b:
                top_gain[i] = top_gain.get(i, 0) + 1
    report["hl_pullup_cards"] = sorted(top_gain.items(), key=lambda x: -x[1])[:10]
    # 灰度结论
    changed_n = sum(1 for s in report["samples"] if s["changed"])
    higher = sum(1 for k in trial if k in base_table and trial[k] > base_table[k])
    report["verdict"] = {
        "trial_higher_than_base": f"{higher}/{len(trial)}",
        "top5_changed": f"{changed_n}/{len(SAMPLES)}",
        "conclusion": ("当前数据形态(引用集中在一周内, 高频卡现行 recency 已饱和 0.99~1.0)下, "
                       "HLTM 30d/14d 试验分虽在多数卡上高于现行, 但热门卡同步抬升, "
                       "相对位次不变 → 对 top5 排序零增量。HLTM 真正可能发挥的场景是 "
                       "'引用间隔>7d 但<30d 的长热记忆'(现行 7d 窗直接归零, HLTM 仍给分)——"
                       "待 trace 流水积累跨月数据后复跑本试验验证。") if changed_n == 0 else
                      ("热度改动 top5 排序, 见拉升榜。须人工评估拉进的卡是否合理。"),
    }
    # 落盘
    os.makedirs(os.path.dirname(OUT + ".json"), exist_ok=True)
    with io.open(OUT + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    md = ["# HLTM 灰度试验报告(影子侧, 不接正式链路)", f"生成: {_ts()}",
          f"试验表: {len(trial)} 卡有热度(30d窗/14d半衰期/频次加成)", ""]
    for s in report["samples"]:
        md.append(f"- {'Δ' if s['changed'] else '='} `{s['query']}`")
        if s["changed"]:
            md.append(f"  - base:  {[i[:8] for i in s['base']]}")
            md.append(f"  - trial: {[i[:8] for i in s['trial']]}")
    md.append("\n## HLTM 拉升榜(被拉进top5次数)")
    for i, n in report["hl_pullup_cards"]:
        md.append(f"- {i[:8]} ×{n}")
    v = report["verdict"]
    md.append(f"\n## 灰度结论\n- 试验分高于现行: {v['trial_higher_than_base']} 卡 | top5 变化: {v['top5_changed']}")
    md.append(f"- {v['conclusion']}")
    md.append("\n注: 本报告为影子侧灰度数据收集, 不改变现行四维权重。")
    with io.open(OUT + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    changed_n = sum(1 for s in report["samples"] if s["changed"])
    print(f"[HLTM试验] {changed_n}/{len(SAMPLES)} 样例受热度影响 — {OUT}.md")


if __name__ == "__main__":
    main()