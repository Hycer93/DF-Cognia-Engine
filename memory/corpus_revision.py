"""corpus_revision.py — 原文（专利/文档）的"待采纳修订"暂存与血缘关联

职责边界（2026-11-23 用户拍板"轻量版本记忆"路线）：
  讨论过程中产生的修订想法 → 先作为 pending 修订挂到 raw_corpus 原文下（仅记血缘+提案）
  → 人明确"采纳/输出最新"时，才 upsert_corpus 升版（iterations 记 why，旧版 superseded 可追回）。

红线：
  - 本模块【绝不】在 pending 阶段改 raw_corpus 正文；挂载只是加一行契约结构。
  - 采纳升版复用 corpus_store.upsert_corpus，保持 append-only 版本链，不绕过既有版本机。

外部接口：
  propose_revision(target_id, change_note, *, source_type, source_id,
                   proposed_body=None, target_version=None) -> rpid   挂载待采纳修订
  list_pending(target_id=None) -> list[dict]                          列待采纳修订
  get_pending(rpid) -> dict | None                                    取单条
  reject_revision(rpid, *, verdict=None, resolved_by=None) -> bool    驳回（不落版）
  adopt_revision(rpid, *, body=None, resolved_by=None) -> str|None    采纳→升版，返回新 content_hash
  pending_summary(target_id) -> dict                                  输出用：当前版号 + 待采纳清单
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(os.path.dirname(_HERE), "data", "memory", "corpus_store.db")

# 复用既有版本机（同 id 再写=新 version + 旧版 superseded + iterations 记 revise）
import sys
sys.path.insert(0, _HERE)
import corpus_store


def _conn():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_table(c):
    """幂等确保表存在（首次运行不必先跑 v3_schema.build）。"""
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS core_revision_pending (
          rpid TEXT PRIMARY KEY,
          target_id TEXT NOT NULL,
          target_version INTEGER,
          source_type TEXT NOT NULL,
          source_id TEXT,
          change_note TEXT NOT NULL,
          proposed_body TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          enqueued_ts TEXT NOT NULL,
          verdict TEXT, verdict_ts TEXT, resolved_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_revision_pending_target
          ON core_revision_pending(target_id, status);
        """
    )


# ---------------- 写入 ----------------

def propose_revision(target_id, change_note, *, source_type="manual", source_id=None,
                     proposed_body=None, target_version=None) -> str:
    """把一条修订想法挂到原文 target_id 下（status=pending，不改正文）。

    target_version 缺省时自动取原文当前版号，便于采纳时校验"未漂移"。
    """
    c = _conn()
    try:
        _ensure_table(c)
        if target_version is None:
            row = c.execute(
                "SELECT version FROM raw_corpus WHERE id=? AND status='current'",
                (target_id,)).fetchone()
            if row is None:
                raise ValueError(f"原文不存在或非 current: {target_id}")
            target_version = row["version"]
        rpid = "rp-" + uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO core_revision_pending"
            "(rpid,target_id,target_version,source_type,source_id,change_note,"
            " proposed_body,status,enqueued_ts) VALUES (?,?,?,?,?,?,?, 'pending', ?)",
            (rpid, target_id, int(target_version), source_type, source_id,
             change_note, proposed_body, _now()))
        c.commit()
        return rpid
    finally:
        c.close()


def reject_revision(rpid, *, verdict=None, resolved_by=None) -> bool:
    """驳回某条修订（status=rejected，不动正文）。"""
    c = _conn()
    try:
        _ensure_table(c)
        cur = c.execute(
            "UPDATE core_revision_pending SET status='rejected', verdict=?, verdict_ts=?, "
            "resolved_by=? WHERE rpid=? AND status='pending'",
            (verdict, _now(), resolved_by, rpid))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def adopt_revision(rpid, *, body=None, resolved_by=None):
    """采纳某条修订 → 调 upsert_corpus 升版（旧版 superseded 可追回）。

    body：新正文；缺省用 pending 的 proposed_body。两者都无 → 返回 None（需人工提供正文）。
    返回新 content_hash 表示成功；返回 None 表示无法落版。
    采纳后该 rpid 置 adopted，并把 change_note 并入 iterations.why（由 upsert_corpus 的 source_ref 承担）。
    """
    c = _conn()
    try:
        _ensure_table(c)
        row = c.execute(
            "SELECT * FROM core_revision_pending WHERE rpid=? AND status='pending'",
            (rpid,)).fetchone()
        if row is None:
            return None
        new_body = body if body is not None else row["proposed_body"]
        if not new_body:
            return None
    finally:
        c.close()

    target_id = row["target_id"]
    # 校验基线未漂移：当前版号必须仍是提案时的 target_version
    cur = corpus_store.get_corpus(target_id)
    if cur is None:
        return None
    cur_ver = int(cur["version"])
    if row["target_version"] is not None and cur_ver != int(row["target_version"]):
        raise ValueError(
            f"基线已漂移: 提案基于 v{row['target_version']}, 当前 v{cur_ver}；请重新提案")

    # why 用 change_note 承载"为什么改"，source_ref 由 upsert_corpus 写入 iterations
    why = f"[rpid={rpid}] {row['change_note']}"
    h = corpus_store.upsert_corpus(
        target_id, cur["kind"], new_body,
        tree_vol=cur["tree_vol"], tree_ch=cur["tree_ch"],
        title=cur["title"], source_ref=why)

    c = _conn()
    try:
        _ensure_table(c)
        c.execute(
            "UPDATE core_revision_pending SET status='adopted', verdict=?, verdict_ts=?, "
            "resolved_by=? WHERE rpid=?",
            (f"adopted->v{cur_ver + 1}", _now(), resolved_by, rpid))
        c.commit()
    finally:
        c.close()
    return h


# ---------------- 只读 ----------------

def list_pending(target_id=None) -> list:
    c = _conn()
    try:
        _ensure_table(c)
        if target_id is None:
            rows = c.execute(
                "SELECT * FROM core_revision_pending WHERE status='pending' "
                "ORDER BY enqueued_ts").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM core_revision_pending WHERE status='pending' AND target_id=? "
                "ORDER BY enqueued_ts", (target_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_pending(rpid):
    c = _conn()
    try:
        _ensure_table(c)
        row = c.execute("SELECT * FROM core_revision_pending WHERE rpid=?", (rpid,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def pending_summary(target_id) -> dict:
    """输出用：原文当前状态 + 待采纳修订清单（不返回正文，正文由调用方按需取）。"""
    cur = corpus_store.get_corpus(target_id)
    base = {
        "target_id": target_id,
        "current_version": int(cur["version"]) if cur else None,
        "title": cur["title"] if cur else None,
    }
    base["pending"] = list_pending(target_id)
    base["pending_count"] = len(base["pending"])
    return base