"""fix_quarantine_20261123.py — 隔离区修复脚本（一次性，基于 2026-11-23 用户 D1-D6 裁决）

背景：
  core_quarantine 表 43 条 + quarantine.jsonl 35 条，均因入库前编码校验失败被拒。
  表内 43 条 inst_id 全 NULL（建卡前拒收，无主库记录可原地改）；原始素材只在 jsonl。

处置策略（用户 2026-11-23 逐项确认 D1-D6）：
  - 仅对 jsonl 有素材的条目修正 tags 后重跑 atomic_store 入库。
  - 表独有 12 条（10 entity_history + 2 缺字段）无素材：不重入库，仅打 verdict=discarded 留痕。
  - 测试垃圾/冒烟数据：丢弃，verdict=discarded 留痕，不物理删除。
  - 所有丢弃/处置均更新 core_quarantine.verdict（满足审计）。

修正规则（D1-D6）：
  D1 旧骨架码 Gxx → 2字母（G22→Ht, G21→Jw, G26→Gz, G11→Dl, G06→Cg, G04→Be）
  D2 relation=derived/situational → P
  D3 #22 relation=I→C ; #29,30 relation=N→F ; #32 relation=R→C（按摘要语义）
  D4 repro=verified → situational
  D5 entity_history → 废弃（仅表独有10条，丢弃）
  D6 小说6条保留 repro=derived；测试2条+冒烟2条 丢弃

用法：
  python memory/fix_quarantine_20261123.py --dry-run   # 只演练不写
  python memory/fix_quarantine_20261123.py             # 真执行
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import schema as S
import memory_organize as MO

MEM_DIR = os.path.join(os.path.dirname(_HERE), "data", "memory")
QJSONL = os.path.join(MEM_DIR, "quarantine.jsonl")
DB = os.path.join(MEM_DIR, "corpus_store.db")

# ---- D1: 旧骨架码映射（G编号是历史写法，非废弃）----
G2CODE = {}
for k, v in S.BEHAVIOR_TREE.items():
    if v.startswith("G"):
        G2CODE[v.split()[0]] = k

# ---- D3: 按语义硬编码 relation 重映射（按 summary 前缀匹配定位）----
REL_OVERRIDE = {
    "四缺口裁决定案": "C",          # #22 I→C 并行
    "记忆入库断档事故": "F",          # #29,30 N→F 反馈
    "collab协同推演定案": "C",        # #32 R→C 并行
}

# ---- D6: 丢弃清单（按 summary 关键字识别测试垃圾/冒烟）----
DISCARD_SUMMARY_HINTS = ["测试非法域码", "PAIR6冒烟反例方案"]
DISCARD_EXACT = {"t"}  # #04 summary='t'


def _classify(d):
    """判定单条 jsonl 记录：return ('keep_tests'|'discard', reason)"""
    s = str(d.get("summary", "")).strip()
    if s in DISCARD_EXACT or any(h in s for h in DISCARD_SUMMARY_HINTS):
        return "discard", "测试垃圾/冒烟数据"
    # domain=009-xxx 是测试域码（#33-35）
    dom = (d.get("tags") or {}).get("domain", "")
    if str(dom).startswith("009"):
        return "discard", f"测试域码 {dom}"
    return "keep", ""


def _fix_tags(d):
    """按 D1-D4 规则修正单条 tags，返回 (new_tags, changes:list)"""
    tags = dict(d.get("tags") or {})
    changes = []
    summary = str(d.get("summary", ""))

    # D1 骨架码
    beh = tags.get("behavior")
    if beh in G2CODE and beh not in S.BEHAVIOR_TREE:
        tags["behavior"] = G2CODE[beh]
        changes.append(f"behavior {beh}→{tags['behavior']}")

    # D2 relation=derived/situational（误填 repro 值）→ P
    rel = tags.get("relation")
    if rel in ("derived", "situational"):
        tags["relation"] = "P"
        changes.append(f"relation {rel}→P")
        rel = "P"

    # D3 relation=I/N/R 按语义覆盖
    if rel in ("I", "N", "R"):
        for hint, newrel in REL_OVERRIDE.items():
            if hint in summary:
                tags["relation"] = newrel
                changes.append(f"relation {rel}→{newrel}(语义:{hint})")
                break
        else:
            # 兜底：未命中语义规则的，保守改 P（应人工复核）
            tags["relation"] = "P"
            changes.append(f"relation {rel}→P(兜底,需复核)")

    # D4 repro=verified → situational
    if tags.get("repro") == "verified":
        tags["repro"] = "situational"
        changes.append("repro verified→situational")

    # D6 小说语料 repro 路径串 → derived
    rep = tags.get("repro")
    if isinstance(rep, str) and rep.startswith("sqlite:"):
        tags["repro"] = "derived"
        changes.append(f"repro {rep[:30]}…→derived(小说)")

    # domain 003 无子域（#21）→ 补子域 003-004(用户问题修复)；cognia_engine(#31)→002-003
    dom = tags.get("domain", "")
    if dom == "003":
        tags["domain"] = "003-004"
        changes.append("domain 003→003-004")
    elif dom == "cognia_engine":
        tags["domain"] = "002-003"
        changes.append("domain cognia_engine→002-003")
    # #31 剩余非法 behavior/relation（atomic_skill_pilot2 / skill_scope_scan_consensus）
    if tags.get("behavior") == "atomic_skill_pilot2":
        tags["behavior"] = "Aa"   # 任务空间平铺扫描≈发散A(Aa)
        changes.append("behavior atomic_skill_pilot2→Aa")
    if tags.get("relation") == "skill_scope_scan_consensus":
        tags["relation"] = "P"
        changes.append("relation skill_scope_scan_consensus→P")
    # confidence 缺失或非枚举 → 统一 L1（先存旧值再改，避免记录显示错位）
    _old_conf = tags.get("confidence")
    if _old_conf not in S.CONFIDENCE_LEVELS:
        tags["confidence"] = "L1"
        changes.append(f"confidence {_old_conf}→L1")

    return tags, changes


def main(dry_run=False):
    if not os.path.exists(QJSONL):
        print(f"[FATAL] 缺 {QJSONL}")
        return 2
    lines = [json.loads(l) for l in open(QJSONL, encoding="utf-8") if l.strip()]
    print(f"[READ] quarantine.jsonl {len(lines)} 条")

    report = {"reingested": 0, "discarded_jsonl": 0, "failed_validate": 0,
              "changes": [], "discard": [], "errors": []}
    q_ts_kept = []   # 成功入库的原始 ts，用于同步表标记

    for i, d in enumerate(lines, 1):
        summary = str(d.get("summary", ""))
        verdict, why = _classify(d)
        if verdict == "discard":
            report["discarded_jsonl"] += 1
            report["discard"].append({"i": i, "summary": summary[:40], "why": why})
            continue

        new_tags, changes = _fix_tags(d)
        ok, errs = S.validate_memory_tags(new_tags)
        if not ok:
            report["failed_validate"] += 1
            report["errors"].append({"i": i, "summary": summary[:40], "errs": errs})
            continue

        detail = str(d.get("detail_preview", ""))
        truncated = detail.endswith("...") or len(detail) >= 200
        src = f"quarantine-restore-20261123#{i}"
        if dry_run:
            report["reingested"] += 1
            report["changes"].append({"i": i, "summary": summary[:40],
                                      "changes": changes or ["(tags已合法,仅重入库)"],
                                      "detail_truncated": truncated})
            continue

        try:
            status, msg = MO.atomic_store(summary, detail, src, new_tags,
                                          asset_class="runtime", event_ts=None)
            if status == "stored":
                report["reingested"] += 1
                q_ts_kept.append(d.get("ts"))
                report["changes"].append({"i": i, "summary": summary[:40],
                                          "changes": changes or ["(tags已合法)"],
                                          "detail_truncated": truncated, "msg": msg})
            else:
                # replica/quarantined：不算成功入库
                report["failed_validate"] += 1
                report["errors"].append({"i": i, "summary": summary[:40],
                                         "status": status, "msg": msg})
        except Exception as e:
            report["errors"].append({"i": i, "summary": summary[:40], "exc": repr(e)})

    # ---- 表侧：丢弃处置留痕（jsonl丢弃 + 表独有12条）----
    if not dry_run:
        c = sqlite3.connect(DB, timeout=10)
        now = datetime.now().isoformat(timespec="seconds")
        # 表全部 43 条：成功的标 verdict=reingested；其余（丢弃）标 discarded
        kept_set = set(t for t in q_ts_kept if t)
        n_mark = 0
        for r in c.execute("SELECT enqueued_ts FROM core_quarantine WHERE verdict IS NULL").fetchall():
            ts = r[0]
            if ts in kept_set:
                v, rb = "reingested", "fix_quarantine_20261123"
            else:
                v, rb = "discarded", "fix_quarantine_20261123"
            c.execute("UPDATE core_quarantine SET verdict=?, verdict_ts=?, resolved_by=? "
                      "WHERE enqueued_ts=? AND verdict IS NULL", (v, now, rb, ts))
            n_mark += 1
        c.commit()
        c.close()
        report["table_marked"] = n_mark

    # ---- 报告 ----
    print("\n===== 执行报告 =====")
    print(f"  成功入库: {report['reingested']}")
    print(f"  jsonl丢弃: {report['discarded_jsonl']}")
    print(f"  校验失败: {report['failed_validate']}")
    if not dry_run:
        print(f"  表标记条数: {report.get('table_marked')}")
    print("\n  --- 条件映射变更明细 ---")
    for ch in report["changes"]:
        tr = " ⚠detail截断" if ch.get("detail_truncated") else ""
        print(f"   [#{ch['i']}] {ch['summary']}: {', '.join(ch['changes'])}{tr}")
    print("\n  --- 丢弃明细 ---")
    for d in report["discard"]:
        print(f"   [#{d['i']}] {d['summary']}: {d['why']}")
    if report["errors"]:
        print("\n  --- 失败明细 ---")
        for e in report["errors"]:
            print(f"   {e}")

    if not dry_run:
        rp = os.path.join(os.path.dirname(_HERE), "docs",
                          f"quarantine_fix_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n  报告已存: {rp}")

    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))