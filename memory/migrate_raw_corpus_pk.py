"""migrate_raw_corpus_pk.py — raw_corpus 单主键 → (id, version) 复合主键迁移（一次性、幂等）

背景（2026-11-23 用户决策方案2）：
  原 raw_corpus.id 是单列 PRIMARY KEY，导致 upsert_corpus 的"同 id 升版"
  （INSERT version=2 同 id）必然触发 UNIQUE 冲突 —— 版本升级机制代码完备但从未能跑通。
  本脚本重建业务主表 raw_corpus 为 PRIMARY KEY(id, version)，打通版本链。

范围红线：
  - 只动 raw_corpus；core_raw_corpus（历史冻结镜像）不动，允许结构暂不对齐。
  - 其余字段/索引定义严格原样复刻。
  - 迁移前必须已手工备份 corpus_store.db / memories.jsonl（脚本仅做辅助备份 + 提示）。

流程（校验失败即终止，绝不切换表）：
  幂等检测 → 建 raw_corpus_new → 全量搬迁 → content_hash 逐行校验 → 表切换
  → 重建索引 → 写 audit_schema_migrate

用法：
  python memory/migrate_raw_corpus_pk.py            # 执行迁移（已迁过则跳过）
  python memory/migrate_raw_corpus_pk.py --dry-run  # 只体检不落库
"""
import os
import sqlite3
import sys
import shutil
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(_HERE), "data", "memory", "corpus_store.db")

NEW_DDL = """
CREATE TABLE raw_corpus_new (
    id          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    tree_vol    TEXT,
    tree_ch     TEXT,
    title       TEXT,
    body        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'current',
    superseded_by TEXT,
    content_hash TEXT NOT NULL,
    source_ref  TEXT,
    ts          TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
"""

COLS = ("id, kind, tree_vol, tree_ch, title, body, version, status, "
        "superseded_by, content_hash, source_ref, ts")


def _is_composite_pk(c) -> bool:
    """已迁移标志：raw_corpus 的建表 SQL 含 'PRIMARY KEY (id, version)'。"""
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_corpus'").fetchone()
    if not row or not row[0]:
        return False
    norm = " ".join(row[0].lower().split())
    return "primary key (id, version)" in norm or "primary key(id, version)" in norm


def _table_exists(c, name: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def main(dry_run=False):
    if not os.path.exists(DB):
        print(f"[FATAL] 数据库不存在: {DB}")
        return 2

    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row

    # ---------- ① 幂等检测 ----------
    if _is_composite_pk(c):
        print("[SKIP] raw_corpus 已是复合主键 (id, version)，无需迁移。")
        c.close()
        return 0

    total = c.execute("SELECT COUNT(*) FROM raw_corpus").fetchone()[0]
    ver_dist = dict(c.execute("SELECT version, COUNT(*) FROM raw_corpus GROUP BY version").fetchall())
    dup = c.execute(
        "SELECT COUNT(*) FROM (SELECT id FROM raw_corpus GROUP BY id HAVING COUNT(*)>1)").fetchone()[0]
    print(f"[CHECK] 存量 {total} 行, version 分布={ver_dist}, id重复组={dup}")
    if dup != 0:
        print("[FATAL] 存在重复 id，复合主键会冲突，终止（请人工排查）。")
        c.close()
        return 3
    if set(ver_dist.keys()) != {1}:
        print(f"[WARN] 存在非 v1 记录 {ver_dist}，"  # 理论不会有；若出现仍可迁移（复合键天然支持）
              "迁移按原样保留各 version。")

    # 迁移前快照：全量 (id, version, content_hash) 用于事后逐行校验
    before = {(r["id"], r["version"]): r["content_hash"]
              for r in c.execute("SELECT id, version, content_hash FROM raw_corpus")}
    print(f"[CHECK] 快照 {len(before)} 条 (id,version,hash)")

    if dry_run:
        print("[DRY-RUN] 体检通过，未落库。")
        c.close()
        return 0

    # ---------- 辅助备份（用户要求已手工备份，这里再兜一层） ----------
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB + f".bak_migrate_{stamp}"
    c.close()
    shutil.copy2(DB, bak)
    print(f"[BACKUP] {bak}")
    mem_jsonl = os.path.join(os.path.dirname(_HERE), "data", "memory", "memories.jsonl")
    if os.path.exists(mem_jsonl):
        mbak = mem_jsonl + f".bak_migrate_{stamp}"
        shutil.copy2(mem_jsonl, mbak)
        print(f"[BACKUP] {mbak}")

    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row

    # ---------- ② 建新表 ----------
    if _table_exists(c, "raw_corpus_new"):
        c.execute("DROP TABLE raw_corpus_new")
    c.executescript(NEW_DDL)
    print("[STEP] 建 raw_corpus_new (复合主键)")

    # ---------- ③ 全量搬迁 ----------
    c.execute(f"INSERT INTO raw_corpus_new ({COLS}) SELECT {COLS} FROM raw_corpus")
    n_new = c.execute("SELECT COUNT(*) FROM raw_corpus_new").fetchone()[0]
    print(f"[STEP] 搬迁 {n_new} 行")

    # ---------- ④ content_hash 逐行校验 ----------
    after = {(r["id"], r["version"]): r["content_hash"]
             for r in c.execute("SELECT id, version, content_hash FROM raw_corpus_new")}
    if n_new != total:
        print(f"[FATAL] 行数不符: 新表 {n_new} != 旧表 {total}，回滚（不切换），原表完好。")
        c.execute("DROP TABLE raw_corpus_new")
        c.commit()
        c.close()
        return 4
    if after != before:
        diff = set(before.items()) ^ set(after.items())
        print(f"[FATAL] content_hash 校验失败，差异 {len(diff)} 项，回滚（不切换），原表完好。")
        for k in list(diff)[:5]:
            print("   ", k)
        c.execute("DROP TABLE raw_corpus_new")
        c.commit()
        c.close()
        return 5
    print(f"[CHECK] content_hash 逐行校验通过 ({len(after)} 条完全一致)")

    # ---------- ⑤ 表切换 ----------
    c.execute("DROP TABLE raw_corpus")
    c.execute("ALTER TABLE raw_corpus_new RENAME TO raw_corpus")
    print("[STEP] 切换: DROP 旧表 + RENAME 新表")

    # ---------- ⑥ 重建索引（严格复刻原定义） ----------
    c.execute("CREATE INDEX IF NOT EXISTS idx_raw_tree ON raw_corpus(tree_vol, tree_ch)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_raw_kind ON raw_corpus(kind)")
    print("[STEP] 重建索引 idx_raw_tree / idx_raw_kind")

    # ---------- ⑦ 记账（audit_schema_migrate 真实字段: mig_version/inst_id/field/old_val/new_val/ts） ----------
    try:
        mv = c.execute("SELECT COALESCE(MAX(mig_version),0)+1 FROM audit_schema_migrate").fetchone()[0]
        c.execute(
            "INSERT INTO audit_schema_migrate"
            "(mig_version, inst_id, field, old_val, new_val, ts) VALUES (?,?,?,?,?,?)",
            (mv, "raw_corpus", "primary_key",
             "id",
             f"(id, version) @ {datetime.now().isoformat(timespec='seconds')}"
             f"; 迁移{n_new}行, content_hash逐行校验通过",
             datetime.now().isoformat(timespec="seconds")))
    except sqlite3.Error as e:
        print(f"[WARN] audit_schema_migrate 记账失败（不影响迁移结果）: {e}")
    c.commit()

    # 收尾校验
    final = c.execute("SELECT COUNT(*) FROM raw_corpus").fetchone()[0]
    pk_ok = _is_composite_pk(c)
    c.close()
    print(f"[DONE] 迁移完成: raw_corpus {final} 行, 复合主键={pk_ok}")
    return 0 if (final == total and pk_ok) else 6


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry))