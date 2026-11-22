"""DB→jsonl 回补（#20 漂移修复，方案 A · 只补零删除）。

背景：11-15 读源切 SQLite 后，jsonl 降级为审计导出流但缺"增量回补"步骤，
11-15~11-19 漏写 75 条 active 卡。本脚本把 DB 中「inst_id 不在 jsonl」的 active 卡
按 jsonl 标准字段格式 append 进 memories.jsonl。

红线（用户 2026-11-22 裁决）：
- **只补不删**：不删 jsonl 任何原有行，不改 DB 任何内容。
- 7 组「同事件多版本」并存：DB 与 jsonl 均原样保留，版本降级留待后续 superseded_by 任务。
- 数学自洽：245 + 75 = 320 = DB 全量，db_jsonl_drift 归零。

安全：
- 默认 dry-run（只打印将补什么，不写盘）
- --apply 才实际写；先备份 memories.jsonl
- 幂等：已存在 inst_id 跳过；重跑不翻倍

用法:
  python eval/audit/backfill_jsonl_from_db.py            # dry-run 预览
  python eval/audit/backfill_jsonl_from_db.py --apply    # 实际执行
"""
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM = os.path.join(HERE, "data", "memory")
DB = os.path.join(MEM, "corpus_store.db")
JL = os.path.join(MEM, "memories.jsonl")


def _b(v):
    """DB 的 '0'/'1' 字符串 → bool"""
    return bool(int(v)) if isinstance(v, str) and v in ("0", "1") else bool(v)


def _j(v, default):
    """DB 的 JSON 字符串 → 容器"""
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


def db_row_to_entry(cols, row):
    """DB 一行 → jsonl entry（类型还原对齐切换后写入的 21 字段格式）"""
    d = dict(zip(cols, row))
    return {
        "inst_id": d["inst_id"],
        "ts": d["ts"],
        "event_ts": d["event_ts"],
        "full_hash": d["full_hash"],
        "asset_class": d["asset_class"],
        "repro": d["repro"],
        "confidence": d["confidence"],
        "dfcp_tag": _j(d["dfcp_tag"], {}),
        "affect_tags": _j(d["affect_tags"], None) if d["affect_tags"] else None,
        "summary": d["summary"],
        "summary_converged": _b(d["summary_converged"]),
        "detail": d["detail"],
        "source_ref": d["source_ref"],
        "origin_ids": _j(d["origin_ids"], []),
        "entity_id": d["entity_id"],
        "derived_from": _j(d["derived_from"], None),
        "tier": d["tier"],
        "unclassified_cycles": 0,          # 切换后新卡恒 0（旧字段，DB 不存）
        "weight": _j(d["weight"], {"match": 0.0, "cited": 0}),
        "protected": _b(d["protected"]),
        "memory_class": d["memory_class"],
    }


def main():
    apply = "--apply" in sys.argv
    # nightly 模式：作为夜间巡检步骤，自动补齐并 exit 0（无新增时 0 补亦成功）
    nightly = "--nightly" in sys.argv
    if nightly:
        apply = True
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    cols = [r[1] for r in c.execute("PRAGMA table_info(core_memory_card)").fetchall()]

    jl_rows = []
    for line in open(JL, encoding="utf-8"):
        if line.strip():
            jl_rows.append(json.loads(line))
    jl_ids = {r["inst_id"] for r in jl_rows}

    # 只取 DB active 且 jsonl 不含的 → 待补（不涉及任何删除）
    missing = []
    for row in c.execute("SELECT * FROM core_memory_card WHERE status='active'").fetchall():
        d = dict(zip(cols, row))
        if d["inst_id"] in jl_ids:
            continue
        missing.append(db_row_to_entry(cols, row))

    db_total = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    db_active = c.execute("SELECT COUNT(*) FROM core_memory_card WHERE status='active'").fetchone()[0]

    print("=" * 60)
    print("DB→jsonl 回补（只补零删除）%s" % ("[APPLY]" if apply else "[DRY-RUN 预览]"))
    print("=" * 60)
    print("当前 jsonl: %d 条 | DB 全量: %d | DB active: %d" % (len(jl_rows), db_total, db_active))
    print("待补（DB active 且 jsonl 无）: %d 条" % len(missing))
    print("\n将补入的 %d 条（前8条预览）:" % len(missing))
    for e in missing[:8]:
        print("  + %s %s | %s" % (e["inst_id"][:8], (e["ts"] or "")[:10], (e["summary"] or "")[:40]))
    print("\n预估最终 jsonl: %d + %d = %d 条 | 目标 DB 全量 %d"
          % (len(jl_rows), len(missing), len(jl_rows) + len(missing), db_total))
    print("注意：不删任何行、不改 DB。7 组同事件多版本原样并存（后续 superseded_by 任务处理）。")

    if not missing:
        print("[OK] 无待补条目，DB↔jsonl 已一致（drift=0）")
        return

    if not apply:
        print("\n[DRY-RUN] 未写盘。确认无误后加 --apply 执行。")
        return

    bak = JL + ".bak_%s" % time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(JL, bak)
    print("\n已备份 → %s" % bak)

    with open(JL, "a", encoding="utf-8") as f:
        for e in missing:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("已 append %d 条" % len(missing))

    # 校验
    c2 = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    db_all = {r[0] for r in c2.execute("SELECT inst_id FROM core_memory_card").fetchall()}
    new_ids = [json.loads(l)["inst_id"] for l in open(JL, encoding="utf-8") if l.strip()]
    print("\n校验: jsonl=%d(unique %d) | DB全量=%d | drift(DB-jsonl)=%d | jsonl独有=%d"
          % (len(new_ids), len(set(new_ids)), len(db_all),
             len(db_all - set(new_ids)), len(set(new_ids) - db_all)))


if __name__ == "__main__":
    main()