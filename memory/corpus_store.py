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
    id          TEXT PRIMARY KEY,          -- 树地址: novel_szt.v3.ch05 / origin_corpus3 整体
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
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_tags (
    corpus_id   TEXT NOT NULL,             -- 指向 raw_corpus.id（当前版）
    tag_key     TEXT NOT NULL,             -- arc / chars / theme / status_extra ...
    tag_value   TEXT NOT NULL,
    PRIMARY KEY (corpus_id, tag_key, tag_value)
);

CREATE TABLE IF NOT EXISTS persona_anchor (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phase       TEXT NOT NULL,             -- 时期: 2025-xx / 项目里程碑 / 一生分期
    summary     TEXT NOT NULL,             -- 一句话：该阶段人格侧写了什么
    detail      TEXT,                      -- 展开描述
    source_ref  TEXT NOT NULL,             -- 溯源（语料id/对话日期）——人格锚必须可溯源
    ts          TEXT NOT NULL
);

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
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


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
    """人格锚：append-only，只增不改。每条必须可溯源。"""
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "INSERT INTO persona_anchor (phase, summary, detail, source_ref, ts)"
        " VALUES (?,?,?,?,?)", (phase, summary, detail, source_ref, now))
    c.execute(
        "INSERT INTO iterations (corpus_id, action, what, why, ts) VALUES (?,?,?,?,?)",
        ("persona_anchor", "anchor", f"[{phase}] {summary[:60]}",
         "人格锚表 append-only 收编", now))
    c.commit()
    c.close()


def get_anchors() -> list:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM persona_anchor ORDER BY ts").fetchall()
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