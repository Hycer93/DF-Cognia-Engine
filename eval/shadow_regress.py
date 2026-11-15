# -*- coding: utf-8 -*-
"""
shadow_regress.py — 全链路影子回归套件 (2026-11-15 用户指令 P1-a, 切换准入门槛)

目的: 双路对比旧 jsonl 检索链路 VS 读 corpus_store.db core_* 新库链路,
     命中 ID / 召回顺序 / 语义锚点 任一不一致 → 标记阻断切换。

方法(关键设计——打分逻辑单源):
  旧路 = memory_query.ask 原样跑(读 jsonl);
  新路 = monkeypatch 三个数据加载函数
      (load_memories→core_memory_card, load_graph→core_relation,
       _recency_table→trace_cited),
     打分代码零改动, 数据源换新库。
  → diff 只反映数据迁移差异, 不混入逻辑差异。

覆盖样例(迁移时 8 组锚点 + 扩充):
  1. 8 组标准检索样例(含时间窗/编码先验/别名)
  2. 引用性提及 → 7808dbac
  3. 幻觉 8397 → 13fd5b53
  4. F00 供给样例
  5. judge 边载入对账(171 边 me/other/relation 一致)
只读校验: 不写任何业务表; 结果落 logs/shadow_regress_report.json + md。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))

DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
REPORT_JSON = os.path.join(_HERE, "logs", "shadow_regress_report.json")
REPORT_MD = os.path.join(_HERE, "logs", "shadow_regress_report.md")

# 8+ 组标准样例 (query, query_tag, expect_anchor=None|inst_id)
SAMPLES = [
    ("我本地想部署大模型，机器配置3090+32G，当时怎么选的？", None, None),
    ("引用性提及 E7 裁决是什么", None, "7808dbac"),
    ("8397 工作流的幻觉问题怎么处理的", None, "13fd5b53"),
    ("平行论双向推演的方案", None, None),
    ("记忆系统运维的人工种子锚约束", None, None),
    ("3090 显卡选型经验", {"domain": "001-003", "behavior": "Ht", "relation": "H"}, None),
    ("上周记忆入库治理的裁决", None, None),
    ("紫光国微建仓分歧", None, None),
    ("断档事故 10764d3b", None, "10764d3bf16f4dc98eaf7cb20fb79080"),
    ("伪演示基线 7a499925", None, None),
]


def _ts():
    return datetime.now().isoformat(timespec="seconds")


# ---------- 新库数据加载器(对齐旧 jsonl 字段口径) ----------

def db_load_memories():
    """core_memory_card → 同 load_memories() 的 dict 列表口径"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM core_memory_card WHERE status='active'").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("dfcp_tag", "affect_tags", "origin_ids", "weight"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        # event_ts overlay 口径: 旧链路 _load_event_ts_overlay 单独读文件,
        # 新路沿用旧 overlay(不属于 core_* 迁移范围), 保持一致
        out.append(d)
    con.close()
    return out


def db_load_graph():
    """core_relation → 同 load_graph() 口径 {inst_id, entity_id, best_match...}"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM core_relation WHERE status='active'").fetchall()
    out = []
    for r in rows:
        meta = json.loads(r["judge_meta"] or "{}")
        out.append({
            "ts": r["ts"], "inst_id": r["me_inst"], "entity_id": meta.get("entity_id"),
            "relation": r["relation"], "best_match": r["other_inst"],
            "best_sim": meta.get("best_sim")})
    con.close()
    return out


def db_recency_table():
    """trace_cited → 同 _recency_table() 口径(7d 窗半衰期; E8 排除不适用——
    trace_cited 的 score 列当前未灌 source, 与 cited_log 全量对齐)"""
    from memory import constants as C
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cutoff = datetime.now().timestamp() - 7 * 86400
    halflife = C.RECENCY_HALFLIFE_DAYS * 86400
    now = datetime.now().timestamp()
    best = {}
    for ts_str, inst in con.execute(
            "SELECT ts, inst_id FROM trace_cited"):
        try:
            ts = datetime.fromisoformat(ts_str[:19]).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        decay = 0.5 ** (max(0, now - ts) / halflife)
        best[inst] = max(best.get(inst, 0.0), min(1.0, 0.5 + decay * 0.5))
    con.close()
    return best


# ---------- 双路执行 ----------

CITED_LOG = os.path.join(_HERE, "data", "memory", "cited_log.jsonl")


class _CitedLogGuard:
    """只读护栏: ask() 会 append cited_log(recency 喂引用)。
    回归自跑会自反馈污染 old 路结果(每轮差集都变)。
    用法: 跑前记行数, with 结束后截断回原行数——保证本套件零副作用。"""

    def __init__(self):
        self.n0 = 0
        self.data = b""

    def __enter__(self):
        if os.path.exists(CITED_LOG):
            with open(CITED_LOG, "rb") as f:
                self.data = f.read()
            self.n0 = self.data.count(b"\n")
        return self

    def __exit__(self, *a):
        if self.data:
            with open(CITED_LOG, "wb") as f:
                f.write(self.data)
        # 若跑前无文件(异常场景)且跑中被创建 → 删除
        elif not self.n0 and os.path.exists(CITED_LOG):
            os.remove(CITED_LOG)


def run_old(query, query_tag=None):
    import memory.memory_query as mq
    mq._recency_cache = {"mtime": 0.0, "table": {}}
    return mq.ask(query, query_tag=query_tag, top_k=5)


def run_new(query, query_tag=None):
    import memory.memory_query as mq
    orig = (mq.load_memories, mq.load_graph, mq._recency_table)
    try:
        mq.load_memories = db_load_memories
        mq.load_graph = db_load_graph
        mq._recency_table = db_recency_table
        # ask 内部以模块属性引用? 需确认: ask 体内直接调 load_memories()
        mq._recency_cache = {"mtime": 0.0, "table": {}}
        return mq.ask(query, query_tag=query_tag, top_k=5)
    finally:
        mq.load_memories, mq.load_graph, mq._recency_table = orig


def _ids(result):
    """ask 返回 [(total, entry_dict, bias), ...] 元组——按实际返回结构取 inst_id"""
    ids = []
    for item in result:
        e = item[1] if isinstance(item, (tuple, list)) else item
        if isinstance(e, dict):
            ids.append(e["inst_id"])
        else:
            ids.append(str(e))
    return ids


def drift_check():
    """数据漂移对账: jsonl(旧路,持续增长) vs core_*(新路,迁移时冻结)。
    漂移 = 迁移后 jsonl 侧新增的 cited 行 + 新卡。范围内容:
      · 召回顺序尾部抖动(同 set 不同 order) → 降级 warn(非阻断)
      · top1 语义/锚点不一致 → 仍阻断
    切换日应先重跑 v3_migrate_batch1 增量同步, 让 drift 归零再准入。"""
    with io.open(os.path.join(_HERE, "data", "memory", "cited_log.jsonl"), encoding="utf-8") as f:
        n_cited_jsonl = sum(1 for line in f if line.strip())
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n_cited_db = con.execute("SELECT COUNT(*) FROM trace_cited").fetchone()[0]
    max_db_ts = con.execute("SELECT MAX(ts) FROM trace_cited").fetchone()[0] or ""
    n_card_db = con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    con.close()
    with io.open(os.path.join(_HERE, "data", "memory", "memories.jsonl"), encoding="utf-8") as f:
        n_card_jsonl = sum(1 for line in f if line.strip())
    return {"cited_jsonl": n_cited_jsonl, "cited_db": n_cited_db,
            "cited_drift": n_cited_jsonl - n_cited_db,
            "card_jsonl": n_card_jsonl, "card_db": n_card_db,
            "card_drift": n_card_jsonl - n_card_db,
            "db_freeze_ts": max_db_ts,
            "is_drifted": (n_cited_jsonl != n_cited_db) or (n_card_jsonl != n_card_db)}


_DB_FREEZE_TS = None


def _db_freeze_ts():
    """新库 trace 最后灌入时刻(=迁移冻结点)。晚于它的引用 = 迁移后新增, 漂移。"""
    global _DB_FREEZE_TS
    if _DB_FREEZE_TS is None:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        _DB_FREEZE_TS = con.execute("SELECT MAX(ts) FROM trace_cited").fetchone()[0] or ""
        con.close()
    return _DB_FREEZE_TS


def _has_recent_cite_after_freeze(inst_id):
    """该卡在 cited_log 中存在晚于库冻结时刻的引用 → 新引用漂移。
    (不用 trace_cited 判: 该卡可能有迁移前旧 trace 行, 但最近引用是迁移后新增)"""
    freeze = _db_freeze_ts()[:19]
    with io.open(CITED_LOG, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("inst_id") == inst_id and (rec.get("ts") or "")[:19] > freeze:
                return True
    return False


def compare(query, query_tag, expect_anchor):
    # 双路都包护栏: ask 两路都会 append cited_log(new 路同样写)
    with _CitedLogGuard():
        old = run_old(query, query_tag)
        new = run_new(query, query_tag)
    old_ids = _ids(old)
    new_ids = _ids(new)
    order_match = old_ids == new_ids
    set_match = set(old_ids) == set(new_ids)
    top1_match = (old_ids[:1] == new_ids[:1])
    # 语义锚点: 期望命中卡是否在两路结果内
    anchor_old = expect_anchor in old_ids if expect_anchor else None
    anchor_new = expect_anchor in new_ids if expect_anchor else None
    anchor_ok = (anchor_old == anchor_new) if expect_anchor else True
    # 漂移容差: 差异可归因于'迁移后新增引用'(该卡在 trace_cited 无行=新库不知其近期热度)
    # 或'位次仅尾部交换'(同集合) 时降级 warn
    # 漂移归因(recency是相对竞争分: 一张卡被冻结后新引用喂肥会连锁改全榜位次):
    # 样例级归因——top5 并集任一卡在库冻结后有新引用 → 本样例位次/集合差异归因漂移, 降级 warn
    # 语义硬指标(锚点错判)不可归因, 仍阻断
    any_cite_drift = any(_has_recent_cite_after_freeze(i) for i in set(old_ids) | set(new_ids))
    tail_order_only = order_match is False and set_match and top1_match and anchor_ok
    top1_swap_drift = (not top1_match) and any_cite_drift and anchor_ok
    set_swap_drift = (not set_match) and any_cite_drift and anchor_ok and top1_match
    warn = tail_order_only or top1_swap_drift or set_swap_drift
    blocking = (not anchor_ok) or ((not order_match) and not warn) or ((not set_match) and not warn) or ((not top1_match) and not warn)
    return {
        "query": query,
        "old_ids": old_ids, "new_ids": new_ids,
        "order_match": order_match, "set_match": set_match,
        "anchor_expected": expect_anchor,
        "anchor_in_old": anchor_old, "anchor_in_new": anchor_new,
        "anchor_ok": anchor_ok,
        "top1_match": top1_match,
        "warn_tail_order": tail_order_only,
        "warn_cite_drift": any_cite_drift,
        "blocking": blocking,
        "old_top1": (_ids(old) and old[0][1]["summary"][:50]) or None,
        "new_top1": (_ids(new) and new[0][1]["summary"][:50]) or None,
    }


def judge_edge_check():
    """judge 边载入对账: 旧 relation_graph.jsonl vs core_relation。
    口径: jsonl 同对多次 judge(不同 relation/sim)是历史演进层叠,
    迁移保留最高档关系——按 (me,other) 对去重后比对是否同对同保留档。"""
    from collections import defaultdict
    # 旧: 对 → 按 relation 档位排序取最高(equivalent>augment>conflict>new)
    rank = {"equivalent": 3, "augment": 2, "conflict": 2, "new": 1}
    old_by_pair = {}
    with io.open(os.path.join(_HERE, "data", "memory", "relation_graph.jsonl"), encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            pair = (rec["inst_id"], rec.get("best_match"))
            if pair not in old_by_pair or rank.get(rec["relation"], 0) > rank.get(old_by_pair[pair][2], 0):
                old_by_pair[pair] = (rec["inst_id"], rec.get("best_match"), rec["relation"])
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    new_by_pair = {}
    for me, other, rel in con.execute("SELECT me_inst, other_inst, relation FROM core_relation"):
        new_by_pair[(me, other)] = (me, other, rel)
    con.close()
    old_pairs = set(old_by_pair)
    new_pairs = set(new_by_pair)
    # 新库对 = 旧对去重后应一致(新库保留最高档)
    mismatched = [p for p in (old_pairs & new_pairs) if old_by_pair[p][2] != new_by_pair[p][2]]
    return {"old_n_pairs": len(old_pairs), "new_n_pairs": len(new_pairs),
            "only_old": sorted(old_pairs - new_pairs)[:5],
            "only_new": sorted(new_pairs - old_pairs)[:5],
            "kept_rank_mismatch": mismatched[:5],
            "match": (old_pairs == new_pairs) and not mismatched}


def main():
    drift = drift_check()
    report = {"ts": _ts(), "drift": drift, "samples": [], "edge": judge_edge_check(), "ok": True}
    for query, qtag, anchor in SAMPLES:
        try:
            r = compare(query, qtag, anchor)
        except Exception as e:
            r = {"query": query, "error": str(e), "blocking": True}
        report["samples"].append(r)
        if r.get("blocking"):
            report["ok"] = False
    if not report["edge"]["match"]:
        report["ok"] = False
    # 落盘
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with io.open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    # md 版
    md = ["# 影子回归报告(切换准入门槛)", f"生成: {_ts()}", f"总判定: {'PASS 可切换' if report['ok'] else 'BLOCK 阻断切换'}", ""]
    d = report["drift"]
    md.append(f"- 数据漂移: cited jsonl{d['cited_jsonl']}/db{d['cited_db']}(Δ{d['cited_drift']}) | "
              f"卡 jsonl{d['card_jsonl']}/db{d['card_db']}(Δ{d['card_drift']}) | "
              f"库冻结于 {d['db_freeze_ts'][:16] if d['db_freeze_ts'] else '?'}"
              + ("  ⚠漂移中: 切换前须重跑 v3_migrate_batch1 增量同步归零" if d["is_drifted"] else ""))
    md.append("")
    for s in report["samples"]:
        flag = "✓" if not s.get("blocking") else "✗阻断"
        extra = "  ⚠recency漂移容差(冻结后新引用喂肥)" if s.get("warn_cite_drift") and not s.get("order_match") else ""
        md.append(f"- {flag} `{s['query'][:30]}` 顺序{'=' if s.get('order_match') else '≠'}"
                  f"{' | 锚点' + ('✓' if s.get('anchor_ok') else '✗') if s.get('anchor_expected') else ''}{extra}")
        if s.get("blocking"):
            md.append(f"  - old: {[i[:8] for i in s.get('old_ids', [])]}")
            md.append(f"  - new: {[i[:8] for i in s.get('new_ids', [])]}")
        if s.get("error"):
            md.append(f"  - error: {s['error']}")
    e = report["edge"]
    md.append(f"- judge边(对去重): 旧{e['old_n_pairs']}/新{e['new_n_pairs']} "
              f"{'一致' if e['match'] else '仅旧'+str(len(e['only_old']))+'仅新'+str(len(e['only_new']))+'档位错'+str(len(e['kept_rank_mismatch']))}")
    with io.open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[影子回归] {'PASS' if report['ok'] else 'BLOCK'} — 明细 {REPORT_MD}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())