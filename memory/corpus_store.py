# memory/corpus_store.py — 原始语料统一存储（SQLite 本体层）
# 2026-11-12 用户四表裁决落地：记忆统一存数据库，分表管理
#   raw_corpus     原始语料通用表（小说正文/前身语料/未来任何长文本）
#   corpus_tags    树级标签字典（卷-章位置键 + arc/chars 多入口，用户"记忆字典"多键思想）
#   persona_anchor 人格锚表（AI 一生性格基线，append-only，锚定人格）
#   iterations     迭代账本（改了什么/为什么/何时，append-only）
#
# 设计原则（用户裁决 2026-11-12）：
# - 存储与检索分层：SQLite 是本体（精确定址/版本/事务），向量索引是可重建衍生物
#   （chroma 未来灌入用），本体不可只存在衍生物里
# - append-only：大改走新 version + superseded_by，不删旧版
# - hash 版本锚：与 corpus_registry 同一思想，源头可验证
# - 树级标签：id 形如 novel_szt.v3.ch05，卷-章两级物理寻址；节=章内标题定位，不建表

import sqlite3
import hashlib
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "memory", "corpus_store.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_corpus (
    id          TEXT NOT NULL,             -- 树地址: novel_szt.v3.ch05 / origin_corpus3 整体
    kind        TEXT NOT NULL,             -- novel_chapter / origin_corpus / persona_source ...
    tree_vol    TEXT,                      -- 卷键: v1 种源 / v3 秩序降临 / null=无卷
    tree_ch     TEXT,                      -- 章键: ch05（含节时用 ch05.s2 但仍属章级文件）
    title       TEXT,
    body        TEXT NOT NULL,             -- 全文
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'current',   -- current / superseded / draft
    superseded_by TEXT,                    -- 指向新版 id（同 id 高 version）
    content_hash TEXT NOT NULL,            -- sha256[:16] 版本锚
    source_ref  TEXT,                      -- 来源说明（对话交底/文件迁移等）
    ts          TEXT NOT NULL,
    PRIMARY KEY (id, version)              -- 2026-11-23 复合主键: 同 id 多版本共存, upsert_corpus 升版可变
);

CREATE TABLE IF NOT EXISTS corpus_tags (
    corpus_id   TEXT NOT NULL,             -- 指向 raw_corpus.id（当前版）
    tag_key     TEXT NOT NULL,             -- arc / chars / theme / status_extra ...
    tag_value   TEXT NOT NULL,
    PRIMARY KEY (corpus_id, tag_key, tag_value)
);

-- 2026-11-21 P2-1: persona_anchor 已废弃(deprecated), 冻结保留不 DROP 数据。
-- 写入唯一真相迁至 core_persona_anchor(add_anchor 只写 core_); 旧定义移除防僵尸。
-- 见 memory/schema_migrate.py 迁移 0002。

CREATE TABLE IF NOT EXISTS iterations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    corpus_id   TEXT NOT NULL,
    action      TEXT NOT NULL,             -- create / revise / supersede / tag / anchor
    what        TEXT NOT NULL,             -- 改了什么
    why         TEXT,                      -- 为什么改（改稿理由，比字节 diff 更有价值）
    ts          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_tree ON raw_corpus(tree_vol, tree_ch);
CREATE INDEX IF NOT EXISTS idx_raw_kind ON raw_corpus(kind);
CREATE INDEX IF NOT EXISTS idx_tags_key ON corpus_tags(tag_key, tag_value);
CREATE INDEX IF NOT EXISTS idx_iter_corpus ON iterations(corpus_id);

-- 2026-11-16 方案B(用户裁决): 对话段全局单调序列表。
-- 纪律: 业务代码禁止 SELECT MAX+1, 只准经 next_dialog_seq() 事务内原子拿号。
-- 新旧 id 共存: 历史段保留旧格式; 新段 = dialog.<date>.<ch>nnnn(尾号全局池)。
CREATE TABLE IF NOT EXISTS dialog_seq (
    seq_name TEXT PRIMARY KEY DEFAULT 'dialog',
    last_seq INTEGER NOT NULL DEFAULT 0
);

-- 2026-11-21 碎片归档表(用户裁决 B 方案): 与 raw_corpus 同构 + archive_reason/archive_ts。
-- 历史 session_dialog 中 ≤20 字节单句碎片(t 通道批量导入未过滤)迁出主表, 不硬删可追溯。
-- 见 docs/08_patrol_review/maintenance_session_dialog_cleanup_20261121.md, 巡检 eval/core/patrol_dialog_fragments.py。
CREATE TABLE IF NOT EXISTS session_dialog_archive_fragment (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    tree_vol    TEXT,
    tree_ch     TEXT,
    title       TEXT,
    body        TEXT,
    version     TEXT,
    status      TEXT,
    superseded_by TEXT,
    content_hash TEXT,
    source_ref  TEXT,
    ts          TEXT,
    archive_reason TEXT,
    archive_ts  TEXT
);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    # A-6（2026-11-22 #7 收尾）：显式设置 WAL 模式 + 自动检查点，不依赖历史遗留状态。
    # - 兼容存量：journal_mode=WAL 幂等——已是 WAL 则无操作; 原 delete/rollback 模式库
    #   首次连接即切 WAL（对所有连接立即生效，持久于库文件）。
    # - wal_autocheckpoint=1000（默认同值，此处显式声明防未来漂移）：约 1000 页(~4MB)
    #   触发 checkpoint，控制 -wal 文件膨胀同时平衡写放大。
    # 注：本函数为读写连接（mode=ro 的只读连接不设 journal_mode，由调用方自行处理）。
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=1000")
    except sqlite3.Error:
        pass  # 只读/受限场景降级：沿用当前模式，不阻断（守卫式非致命）
    return c


def init_db():
    c = _conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def next_dialog_seq() -> int:
    """对话段全局序列原子取号(2026-11-16 方案B)。
    事务内 UPDATE 自增返回, 并发安全; 所有入库通道(hook seg / transcript tseg)
    共用同一池——规约: 业务代码禁止绕过本原语自行 SELECT MAX+1。"""
    c = _conn()
    try:
        # 起跳水位: 首次初始化时抬到 10000, 高于现存所有旧格式单日尾号(历史峰值~252),
        # 保证新旧 id 空间天然不重叠; 之后每次事务内 +1。
        c.execute("INSERT OR IGNORE INTO dialog_seq (seq_name, last_seq) VALUES ('dialog', 10000)")
        c.execute("UPDATE dialog_seq SET last_seq = last_seq + 1 WHERE seq_name='dialog'")
        n = c.execute("SELECT last_seq FROM dialog_seq WHERE seq_name='dialog'").fetchone()[0]
        c.commit()
        return n
    finally:
        c.close()


def upsert_corpus(id_: str, kind: str, body: str, *, tree_vol=None, tree_ch=None,
                  title=None, source_ref=None) -> str:
    """新建或迭代语料。同 id 再写 = 新 version，旧版自动 superseded。返回当前版 content_hash。"""
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    row = c.execute(
        "SELECT version FROM raw_corpus WHERE id=? AND status='current'",
        (id_,)).fetchone()
    if row:
        old_ver = row["version"]
        new_ver = old_ver + 1
        c.execute(
            "UPDATE raw_corpus SET status='superseded', superseded_by=? "
            "WHERE id=? AND version=?",
            (f"{id_}#{new_ver}", id_, old_ver))
        action = "revise"
    else:
        new_ver = 1
        action = "create"
    h = _hash(body)
    c.execute(
        "INSERT INTO raw_corpus (id, kind, tree_vol, tree_ch, title, body, version,"
        " status, content_hash, source_ref, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (id_, kind, tree_vol, tree_ch, title, body, new_ver, "current", h,
         source_ref, now))
    c.execute(
        "INSERT INTO iterations (corpus_id, action, what, why, ts) VALUES (?,?,?,?,?)",
        (id_, action,
         f"version {new_ver} (hash {h})" + (f" ← 替换 version {old_ver}" if action == "revise" else ""),
         source_ref, now))
    c.commit()
    c.close()
    return h


def get_corpus(id_: str, version: int = None) -> dict:
    """按树地址取语料。默认当前版；version 指定取历史版。"""
    c = _conn()
    if version is None:
        row = c.execute(
            "SELECT * FROM raw_corpus WHERE id=? AND status='current'", (id_,)).fetchone()
    else:
        row = c.execute(
            "SELECT * FROM raw_corpus WHERE id=? AND version=?", (id_, version)).fetchone()
    c.close()
    return dict(row) if row else None


def list_corpus(*, kind=None, tree_vol=None, status="current") -> list:
    """树级/类型级列举——加载协议的取件原语。"""
    c = _conn()
    q = "SELECT id, kind, tree_vol, tree_ch, title, version, status, content_hash, ts" \
        " FROM raw_corpus WHERE status=?"
    args = [status]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    if tree_vol:
        q += " AND tree_vol=?"
        args.append(tree_vol)
    q += " ORDER BY tree_vol, tree_ch"
    rows = c.execute(q, args).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_dialog(*, session_id=None, tree_vol=None, with_body=False,
                status="current") -> list:
    """#12 对话追溯：按 session(存 title) 或日期(tree_vol) 取 session_dialog 段。
    纯只读，不写任何表。session_id 对应 h/t 通道写入时放在 title 的会话标识；
    tree_vol 为日期(YYYY-MM-DD)。两者可叠加过滤；都不传=取全部对话段(按时间倒序)。
    with_body=True 时附带 body 正文(默认只返回元字段, 避免大文本误传)。
    返回 list[dict]，按 ts 倒序(最新在前)。"""
    c = _conn()
    cols = "id, kind, tree_vol, tree_ch, title, version, status, content_hash, source_ref, ts"
    if with_body:
        cols += ", body"
    q = "SELECT %s FROM raw_corpus WHERE kind='session_dialog' AND status=?" % cols
    args = [status]
    if session_id:
        q += " AND title=?"
        args.append(session_id)
    if tree_vol:
        q += " AND tree_vol=?"
        args.append(tree_vol)
    q += " ORDER BY ts DESC"
    rows = c.execute(q, args).fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_tags(corpus_id: str, tags: dict):
    """挂树级标签：{"arc": ["营救启动"], "chars": ["陈默","溯零"]}"""
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for k, vs in tags.items():
        for v in (vs if isinstance(vs, list) else [vs]):
            c.execute(
                "INSERT OR IGNORE INTO corpus_tags (corpus_id, tag_key, tag_value)"
                " VALUES (?,?,?)", (corpus_id, k, str(v)))
            n += 1
    c.execute(
        "INSERT INTO iterations (corpus_id, action, what, why, ts) VALUES (?,?,?,?,?)",
        (corpus_id, "tag", f"+{n} tags: {json.dumps(tags, ensure_ascii=False)[:120]}",
         "树级标签（记忆字典多键思想）", now))
    c.commit()
    c.close()


def query_by_tag(tag_key: str, tag_value: str) -> list:
    """按字典键取：arc=营救启动 / chars=陈默 / theme=干预vs放任"""
    c = _conn()
    rows = c.execute(
        "SELECT r.* FROM raw_corpus r JOIN corpus_tags t ON r.id=t.corpus_id"
        " WHERE t.tag_key=? AND t.tag_value=? AND r.status='current'"
        " ORDER BY r.tree_vol, r.tree_ch", (tag_key, tag_value)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_anchor(phase: str, summary: str, detail: str, source_ref: str):
    """人格锚：append-only，只增不改。每条必须可溯源。
    (2026-11-15 #31 切换: 写入改 core_persona_anchor 唯一真相;
     旧 persona_anchor 表冻结保留, 不再写入)"""
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "INSERT INTO core_persona_anchor (phase, summary, detail, source_ref, ts)"
        " VALUES (?,?,?,?,?)", (phase, summary, detail, source_ref, now))
    c.execute(
        "INSERT INTO iterations (corpus_id, action, what, why, ts) VALUES (?,?,?,?,?)",
        ("persona_anchor", "anchor", f"[{phase}] {summary[:60]}",
         "人格锚表 append-only 收编(core)", now))
    c.commit()
    c.close()


def get_anchors() -> list:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM core_persona_anchor WHERE phase NOT LIKE '[已迁范式]%' ORDER BY ts").fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_iterations(corpus_id: str = None) -> list:
    c = _conn()
    if corpus_id:
        rows = c.execute(
            "SELECT * FROM iterations WHERE corpus_id=? ORDER BY id", (corpus_id,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM iterations ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("corpus_store.db ready:", DB_PATH)