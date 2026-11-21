# -*- coding: utf-8 -*-
"""memory_consistency_fix.py — #14 记忆一致性修复（存量对账）

修复三类存量不一致（详见 docs/review_memory_consistency_20261122.md）：
  ① 29 条降级卡: jsonl 有 superseded_by, db 为空 → UPDATE db
  ② 4 条仅 jsonl: db 缺失（真实丢失）→ INSERT 进 db
  ③ 75 条仅 db: jsonl 未同步 → 这是 db 权威/新增数据, 不删不补
     (只作报告: 确认 jsonl 是否退化为纯导出流; 本脚本默认不处理 jsonl 侧)

安全设计:
  - 默认 --dry-run: 只打印将执行的变更, 不写任何数据
  - --apply: 真正执行, 执行前自动备份 db 到 data/memory/backup/
  - 全程事务 + 逐条留痕; 只碰 core_memory_card 的 tier/superseded_by 列 + 补插 4 条
  - 不删除任何数据, 不改主推理字段

运行:
  python eval/audit/memory_consistency_fix.py            # dry-run 预览
  python eval/audit/memory_consistency_fix.py --apply    # 真执行
退出码: 0=成功(或dry-run) 1=异常
"""
import io
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM = os.path.join(_HERE, "data", "memory")
DB = os.path.join(MEM, "corpus_store.db")
JL = os.path.join(MEM, "memories.jsonl")


def _load_jl():
    out = {}
    if not os.path.exists(JL):
        return out
    with io.open(JL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                out[e["inst_id"]] = e
    return out


def _card_args(m):
    """把 jsonl 条目映射为 core_memory_card 22 列 INSERT 参数。
    对齐 memory_organize._write_card_to_db 与 v3_migrate_batch1 的口径。"""
    tag = m.get("dfcp_tag") or {}
    if isinstance(tag, str):
        try:
            tag = json.loads(tag)
        except Exception:
            tag = {}
    w = m.get("weight")
    w_val = json.dumps(w, ensure_ascii=False) if isinstance(w, dict) else (w if w is not None else 1.0)
    return (
        m["inst_id"], m.get("ts"), m.get("event_ts"), m.get("full_hash"),
        m.get("asset_class", "runtime"), m.get("repro", "situational"),
        m.get("confidence", "L1"), json.dumps(tag, ensure_ascii=False),
        json.dumps(m.get("affect_tags") or {}, ensure_ascii=False) if m.get("affect_tags") else None,
        m.get("summary", ""), m.get("summary_converged"), m.get("detail"),
        m.get("source_ref"),
        json.dumps(m.get("origin_ids") or [], ensure_ascii=False),
        m.get("entity_id"),
        (json.dumps(m.get("derived_from"), ensure_ascii=False) if isinstance(m.get("derived_from"), (list, dict))
         else m.get("derived_from")),
        m.get("tier") or "active",
        w_val,
        1 if m.get("protected") else 0,
        m.get("superseded_by"),          # ★ 关键: 不再吞成 None
        m.get("status") or "active",
        tag.get("memory_class") or m.get("memory_class"),
    )


def plan(jl, con):
    """计算三类差异, 返回变更计划(不执行)。"""
    db_ids = {r[0] for r in con.execute("SELECT inst_id FROM core_memory_card")}
    db_sup = {r[0]: r[1] for r in con.execute("SELECT inst_id, superseded_by FROM core_memory_card")}

    # ① 降级卡 sup 补写
    sup_fix = []
    for iid, e in jl.items():
        if e.get("superseded_by") and iid in db_sup and not db_sup[iid]:
            sup_fix.append((iid, e["superseded_by"], e.get("tier") or "precedent"))

    # ② 仅 jsonl → db 缺失, 需补插
    insert_fix = [iid for iid in jl if iid not in db_ids]

    # ③ 仅 db (报告, 不处理)
    only_db = [iid for iid in db_ids if iid not in jl]
    return {"sup_fix": sup_fix, "insert_fix": insert_fix, "only_db": only_db}


def main(argv):
    apply = "--apply" in argv
    jl = _load_jl()
    con = sqlite3.connect(DB, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        p = plan(jl, con)
        print("=" * 56)
        print("[#14 记忆一致性修复] 模式: %s" % ("APPLY(真执行)" if apply else "DRY-RUN(仅预览)"))
        print("  jsonl 条数: %d | db 卡数: %d" % (
            len(jl), con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]))
        print("  ① 降级卡 sup 补写: %d 条" % len(p["sup_fix"]))
        for iid, sup, tier in p["sup_fix"]:
            print("      %s -> tier=%s sup=%s" % (iid[:12], tier, str(sup)[:20]))
        print("  ② 仅 jsonl 补插 db: %d 条" % len(p["insert_fix"]))
        for iid in p["insert_fix"]:
            print("      %s | %s" % (iid[:12], str(jl[iid].get("summary"))[:36]))
        print("  ③ 仅 db (仅报告, 不删不补): %d 条" % len(p["only_db"]))
        print("=" * 56)

        if not apply:
            print("[DRY-RUN] 未写任何数据。确认后加 --apply 执行。")
            return 0

        # 备份 db
        bdir = os.path.join(MEM, "backup")
        os.makedirs(bdir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(bdir, "corpus_store.db.bak_consistency_%s" % ts)
        # sqlite backup API(一致快照, 含 WAL)
        s = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=10)
        d = sqlite3.connect(bak, timeout=10)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        print("[备份] %s" % bak)

        done_sup = done_ins = 0
        # ① 补 sup
        for iid, sup, tier in p["sup_fix"]:
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE core_memory_card SET tier=?, superseded_by=? WHERE inst_id=?",
                            (tier, sup, iid))
                con.execute("COMMIT")
                done_sup += 1
            except Exception as e:  # noqa: BLE001
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                print("  [FAIL sup] %s %r" % (iid[:12], e))
        # ② 补插
        for iid in p["insert_fix"]:
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT OR IGNORE INTO core_memory_card VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _card_args(jl[iid]))
                con.execute("COMMIT")
                done_ins += 1
            except Exception as e:  # noqa: BLE001
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                print("  [FAIL insert] %s %r" % (iid[:12], e))

        print("[完成] sup补写 %d/%d, 补插 %d/%d"
              % (done_sup, len(p["sup_fix"]), done_ins, len(p["insert_fix"])))
        # 复验
        p2 = plan(jl, con)
        print("[复验] 剩余 sup 待补: %d | 剩余插入待补: %d"
              % (len(p2["sup_fix"]), len(p2["insert_fix"])))
        print("[复验] db 卡数: %d | integrity: %s" % (
            con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0],
            con.execute("PRAGMA integrity_check").fetchone()[0]))
        return 0 if not p2["sup_fix"] and not p2["insert_fix"] else 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))