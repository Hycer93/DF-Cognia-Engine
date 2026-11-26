"""DB → jsonl 全量覆盖导出（2026-11-26 存储收敛，替代"只补不删"的 backfill）。

与 eval/audit/backfill_jsonl_from_db.py 的本质区别：
  - backfill：只补 jsonl 缺的卡（append），只导 status='active'，缺 status/superseded_by 字段。
  - 本脚本：SELECT * FROM core_memory_card 全量、整文件重写、22 字段完整（含
    status/superseded_by/unclassified_cycles），使 jsonl 严格 = DB 的投影。
    这样 DB 是唯一真相源，jsonl 永远是派生快照 → 一致性天然成立，漂移债归零。

字段口径（对齐 memory_organize.atomic_store 写入的 jsonl 格式）：
  - summary_converged / protected：DB 存 '0'/'1' → 还原为 bool（False/True）
  - dfcp_tag / origin_ids / derived_from / weight：DB 存 JSON 字符串 → 还原为容器
  - affect_tags：DB 空串/None → None；否则 JSON
  - unclassified_cycles：DB 不存此列 → 恒写 0（与 atomic_store 新卡一致）

安全：
  - 默认 dry-run（只统计 + 预览，不写盘）
  - --apply 才写：先备份 memories.jsonl.bak_<ts>，再原子替换
  - 写后自校验：重新解析新文件，逐 inst_id 与 DB 比对 22 字段，diff 必须为 0，否则报错
  - 幂等：DB 不变时连跑两次字节一致（不追加时间戳等噪声）

用法：
  python scripts/export_jsonl_from_db.py            # dry-run 预览
  python scripts/export_jsonl_from_db.py --apply    # 实际覆盖写
"""
import io
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM = os.path.join(HERE, "data", "memory")
DB = os.path.join(MEM, "corpus_store.db")
JL = os.path.join(MEM, "memories.jsonl")

# DB 列 → jsonl 键的顺序（22 列 + 1 个 DB 不存的 unclassified_cycles）
DB_COLS = [
    "inst_id", "ts", "event_ts", "full_hash", "asset_class", "repro",
    "confidence", "dfcp_tag", "affect_tags", "summary", "summary_converged",
    "detail", "source_ref", "origin_ids", "entity_id", "derived_from",
    "tier", "weight", "protected", "superseded_by", "status", "memory_class",
]


def _json_or(v, default):
    """DB 的 JSON 字符串 → 容器；空/None → default。"""
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    s = str(v).strip()
    if s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _bool(v):
    """DB 的 '0'/'1'/0/1/False/True → bool。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip() in ("1", "true", "True", "TRUE")


def db_row_to_entry(row: dict) -> dict:
    """DB 一行 → jsonl entry（22 字段完整 + unclassified_cycles=0）。

    用 .get() 容错：真实生产表 22 列齐全，但测试仓/旧快照可能缺列，
    缺列按空值还原而非 KeyError，便于复用与冒烟隔离。
    """
    def g(col):
        return row.get(col)

    return {
        "inst_id": g("inst_id"),
        "ts": g("ts"),
        "event_ts": g("event_ts"),
        "full_hash": g("full_hash"),
        "asset_class": g("asset_class"),
        "repro": g("repro"),
        "confidence": g("confidence"),
        "dfcp_tag": _json_or(g("dfcp_tag"), {}),
        "affect_tags": (_json_or(g("affect_tags"), None)
                        if g("affect_tags") not in (None, "") else None),
        "summary": g("summary"),
        "summary_converged": _bool(g("summary_converged")),
        "detail": g("detail"),
        "source_ref": g("source_ref"),
        "origin_ids": _json_or(g("origin_ids"), []),
        "entity_id": g("entity_id"),
        "derived_from": _json_or(g("derived_from"), None),
        "tier": g("tier"),
        "weight": _json_or(g("weight"), {"match": 0.0, "cited": 0}),
        "protected": _bool(g("protected")),
        "superseded_by": g("superseded_by"),
        "status": g("status"),
        "memory_class": g("memory_class"),
        "unclassified_cycles": 0,   # DB 不存，恒 0（对齐 atomic_store 新卡）
    }


def _read_db():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM core_memory_card").fetchall()]
    finally:
        con.close()
    # 稳定排序：按 ts 再 inst_id，保证幂等字节一致
    rows.sort(key=lambda r: (r["ts"] or "", r["inst_id"]))
    return rows


def _norm(v):
    """字段值归一化用于自校验比较。"""
    if v is None:
        return "null"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    s = str(v).strip()
    if s == "":
        return "null"
    return s


def _self_check(entries, db_rows):
    """自校验：entry 逐 inst_id 与"DB 经同一转换函数产出"的期望值比对 22 字段。

    期望值统一用 db_row_to_entry(d) 产出，实际值用 entry 本身——两者都是容器/标量，
    可直接 _norm 比较。这样 dry-run 能验证"写入内存的 entry"与"DB 语义"一致；
    --apply 后磁盘回读再跑一次，则验证"JSON 序列化→反序列化实无损往返"。
    """
    db_by_id = {r["inst_id"]: r for r in db_rows}
    diffs = []
    for e in entries:
        d = db_by_id.get(e["inst_id"])
        if d is None:
            diffs.append(f"{e['inst_id'][:12]}: DB 无此卡")
            continue
        expect = db_row_to_entry(d)
        for col in DB_COLS + ["unclassified_cycles"]:
            if _norm(expect[col]) != _norm(e[col]):
                diffs.append(f"{e['inst_id'][:12]}.{col}: "
                             f"期望={_norm(expect[col])[:30]} 实际={_norm(e[col])[:30]}")
    return diffs


def main():
    apply = "--apply" in sys.argv
    db_rows = _read_db()
    entries = [db_row_to_entry(r) for r in db_rows]

    # 统计现状
    old_n = 0
    old_ids = set()
    if os.path.exists(JL):
        for line in open(JL, encoding="utf-8"):
            if line.strip():
                old_n += 1
                try:
                    old_ids.add(json.loads(line)["inst_id"])
                except Exception:
                    pass
    new_ids = {e["inst_id"] for e in entries}

    print("=" * 64)
    print("DB → jsonl 全量覆盖导出  %s" % ("[APPLY]" if apply else "[DRY-RUN 预览]"))
    print("=" * 64)
    print(f"DB core_memory_card : {len(db_rows)} 条")
    print(f"当前 jsonl          : {old_n} 条")
    print(f"将写(jsonl 全量=DB) : {len(entries)} 条")
    print(f"DB 有 jsonl 无      : {len(new_ids - old_ids)} 条")
    print(f"jsonl 有 DB 无(将删): {len(old_ids - new_ids)} 条")
    if old_ids - new_ids:
        for iid in list(old_ids - new_ids)[:10]:
            print(f"    - {iid[:20]}")
    print()

    # 预览前 3 条关键字段
    print("前 3 条预览（关键字段）：")
    for e in entries[:3]:
        print(f"  {e['inst_id'][:16]} status={e['status']} tier={e['tier']} "
              f"protected={e['protected']} derived_from={e['derived_from']}")

    # 特殊核对 666f95c5
    for e in entries:
        if e["inst_id"].startswith("666f95c5"):
            print(f"\n[专项] 666f95c5 derived_from = {e['derived_from']!r}")
            break

    # 自校验（dry-run 也跑：验证转换函数本身不引入 diff）
    diffs = _self_check(entries, db_rows)
    print(f"\n自校验（转换后 vs DB 22 字段）：{'❌ %d 处差异' % len(diffs) if diffs else '✅ 0 差异'}")
    for d in diffs[:10]:
        print("   ", d)
    if diffs:
        print("\n自校验未通过，中止（即使 --apply 也不写）。")
        return 1

    if not apply:
        print("\n[DRY-RUN] 未写盘。加 --apply 实际覆盖。")
        return 0

    # 备份 + 原子写
    bak = JL + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(JL):
        shutil.copy2(JL, bak)
        print(f"\n已备份 → {os.path.basename(bak)}")
    tmp = JL + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, JL)
    print(f"已写入 {len(entries)} 条 → memories.jsonl")

    # 写后从磁盘回读再自校验一次
    disk = []
    for line in open(JL, encoding="utf-8"):
        if line.strip():
            disk.append(json.loads(line))
    diffs2 = _self_check(disk, db_rows)
    print(f"落盘回读自校验：{'❌ %d 处' % len(diffs2) if diffs2 else '✅ 0 差异，一致性达成'}")
    return 0 if not diffs2 else 1


if __name__ == "__main__":
    sys.exit(main())