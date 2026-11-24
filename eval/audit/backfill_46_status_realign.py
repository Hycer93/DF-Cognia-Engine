#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#46 存量回填: status 混叠态修复 + 9b48551b asset 修正。

背景: demote() 历史只改 tier 不改 status, 造成 status='active' + tier='precedent'
+ superseded_by 非空 的 37 张混叠卡。检索主链路 load_memories() 只认 status,
导致已降级旧卡仍被 ask() 召回。本脚本:
  1. 37 张混叠卡: status -> superseded (tier/superseded_by/asset 不动, 除个案)
  2. 个案 9b48551b: asset_class runtime -> corpus (runtime 绕过 superseded_by 过滤会泄漏)
  3. 同步 memories.jsonl, 保证 DB-jsonl 对齐

约束: 零物理删除; 仅 UPDATE 字段; dry-run 默认, --apply 才真写。
用法:
  python backfill_46_status_realign.py            # dry-run (只打印)
  python backfill_46_status_realign.py --apply    # 真实写库 + jsonl
  python backfill_46_status_realign.py --db /path/to/copy.db --apply   # 指定库(测试用)
"""
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM_DIR = os.path.join(BASE, "data", "memory")
DB_PATH = os.path.join(MEM_DIR, "corpus_store.db")
JSONL_PATH = os.path.join(MEM_DIR, "memories.jsonl")

# 个案: asset_class 需修正的卡 (runtime -> corpus, 对齐正本)
ASSET_FIX = {"9b48551b2b054963a2810e633bc70936": "corpus"}

MIXED_COND = ("status='active' AND tier='precedent' "
              "AND superseded_by IS NOT NULL AND superseded_by!=''")


def scan(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"SELECT inst_id, asset_class, tier, status, superseded_by, "
        f"substr(summary,1,30) AS summ FROM core_memory_card WHERE {MIXED_COND} "
        f"ORDER BY asset_class, inst_id").fetchall()
    con.close()
    return [dict(r) for r in rows]


def verify_chains(db_path, rows):
    """校验每条 superseded_by 指向存在且 active 的正本。"""
    con = sqlite3.connect(db_path)
    bad = []
    for r in rows:
        tgt = con.execute(
            "SELECT status FROM core_memory_card WHERE inst_id=?",
            (r["superseded_by"],)).fetchone()
        if not tgt:
            bad.append((r["inst_id"], "目标缺失"))
        elif tgt[0] != "active":
            bad.append((r["inst_id"], f"目标非active:{tgt[0]}"))
    con.close()
    return bad


def apply_db(db_path, rows):
    """改 DB: 37 张 status->superseded; 9b48551b 额外 asset->corpus。"""
    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("BEGIN IMMEDIATE")
    changed_status = 0
    changed_asset = 0
    try:
        for r in rows:
            con.execute(
                "UPDATE core_memory_card SET status='superseded' WHERE inst_id=?",
                (r["inst_id"],))
            changed_status += 1
            if r["inst_id"] in ASSET_FIX:
                con.execute(
                    "UPDATE core_memory_card SET asset_class=? WHERE inst_id=?",
                    (ASSET_FIX[r["inst_id"]], r["inst_id"]))
                changed_asset += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return changed_status, changed_asset


def apply_jsonl(rows, jsonl_path=JSONL_PATH):
    """同步 jsonl: 37 张 status->superseded; 9b48551b asset->corpus。"""
    targets = {r["inst_id"]: r for r in rows}
    out = []
    n_status = 0
    n_asset = 0
    with io.open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            iid = o.get("inst_id")
            if iid in targets:
                if o.get("status") != "superseded":
                    o["status"] = "superseded"
                    n_status += 1
                if iid in ASSET_FIX and o.get("asset_class") != ASSET_FIX[iid]:
                    o["asset_class"] = ASSET_FIX[iid]
                    n_asset += 1
            out.append(o)
    # 备份
    bak = jsonl_path + ".bak_46_" + __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(jsonl_path, bak)
    with io.open(jsonl_path, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return n_status, n_asset, bak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真实写库+jsonl(默认dry-run)")
    ap.add_argument("--db", default=DB_PATH, help="DB路径(默认真实库)")
    ap.add_argument("--jsonl", default=JSONL_PATH, help="jsonl路径(默认真实)")
    args = ap.parse_args()

    print(f"[#46] 目标库: {args.db}")
    print(f"[#46] 模式: {'APPLY(真实写)' if args.apply else 'DRY-RUN(只读)'}")
    print()

    rows = scan(args.db)
    print(f"命中混叠卡: {len(rows)} 张 (status=active & tier=precedent & superseded_by 非空)")
    if len(rows) != 37:
        print(f"⚠️ 预期 37 张, 实际 {len(rows)} 张 — 请核对!")
    print()

    bad = verify_chains(args.db, rows)
    print(f"链路校验: {'ALL PASS ✅' if not bad else 'FAILED ❌ ' + str(bad)}")
    print()

    asset_count = sum(1 for r in rows if r["inst_id"] in ASSET_FIX)
    print(f"待回填 status: {len(rows)} 张 -> superseded")
    print(f"待修正 asset : {asset_count} 张 -> corpus (个案 {list(ASSET_FIX.keys())[0][:12]})")
    print()

    print("=== 37 张清单 ===")
    for i, r in enumerate(rows, 1):
        tag = " ⚠️asset修正" if r["inst_id"] in ASSET_FIX else ""
        print(f"{i:2d}. {r['inst_id'][:12]} asset={r['asset_class']:<8} "
              f"-> {r['superseded_by'][:12]}{tag}")

    if not args.apply:
        print()
        print("[DRY-RUN] 未写任何数据。加 --apply 执行真实变更。")
        return 0

    print()
    print("=== 执行真实变更 ===")
    cs, ca = apply_db(args.db, rows)
    print(f"DB: status 更新 {cs} 行, asset 更新 {ca} 行")
    ns, na, bak = apply_jsonl(rows, args.jsonl)
    print(f"jsonl: status 更新 {ns} 行, asset 更新 {na} 行, 备份={os.path.basename(bak)}")

    # 校验: 混叠态清零
    left = scan(args.db)
    print()
    print(f"=== 执行后校验 ===")
    print(f"剩余混叠卡(status=active & tier=precedent): {len(left)} 张 "
          f"{'✅ 已清零' if len(left) == 0 else '❌ 仍有残留'}")
    # 校验 9b48551b
    con = sqlite3.connect(args.db)
    r = con.execute("SELECT asset_class, tier, status FROM core_memory_card "
                    "WHERE inst_id LIKE '9b48551b%'").fetchone()
    print(f"9b48551b: asset={r[0]} tier={r[1]} status={r[2]}")
    con.close()
    return 0 if len(left) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())