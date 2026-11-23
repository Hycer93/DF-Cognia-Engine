"""dialog_evidence_store.py — #31 对话原始证据库（两张业务表 + 一张兜底碎片表）

职责边界（用户 2026-11-23 拍板 C 方案：逐条 + 会话快照双写）：
  core_dialogue_log       逐条消息粒度, 支持 SQL/FTS 检索筛查（检索/统计用）
  core_raw_dialog_session 整会话快照, transcript 原始 JSON 数组, 权威证据真相源
                          （故障复现/离线重跑/评测导出用; 运行时记忆流水线禁止读取）
  _tmp_dialog_fragment    内部临时碎片表, 兜底防线（进程崩溃不丢）+ 巡检聚合载体

写入时机（决策2 = 方案B）：
  每轮 turn 完成 → append_turn_fragment()  落碎片（崩溃兜底）
  PreCompact 触发 → aggregate_session()    聚合碎片→写快照+逐条表→清理→证据链
  后台巡检        → collect_orphan_sessions() 收拢 >72h 悬空碎片

红线：本模块【绝不】调用 _parse_transcript 做扁平化; 快照 messages 原样保存 transcript 对象。

外部接口（供 memory_hook / 巡检脚本调用）：
  append_turn_fragment(session_id, msg_obj)       追加单条原始消息碎片
  ingest_transcript(session_id, transcript_path, **meta)  直接从 transcript JSONL 落地全部碎片(幂等)
  aggregate_session(session_id, ref_archive_fragment=None) 聚合到两业务表
  collect_orphan_sessions(max_age_hours=72)       巡检悬空碎片并聚合
  load_session(session_id)                        只读: 取快照(排错用)
  load_turns(session_id)                          只读: 取逐条消息
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(_HERE), "data", "memory", "corpus_store.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return c


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _msg_hash(msg_raw: str) -> str:
    return hashlib.sha256(msg_raw.encode("utf-8")).hexdigest()[:16]


# ---------------- 材料化：解析 transcript 为原始对象数组（不扁平化） ----------------

def _iter_transcript_objs(transcript_path: str):
    """逐行读 transcript JSONL, 产出原始 dict 对象（type/message/timestamp/tool 全保留）。
    与 _parse_transcript 的区别：不拼文本、不丢 tool、不丢 ts——原样对象。"""
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                yield rec


# ---------------- 碎片追加（兜底防线） ----------------

def append_turn_fragment(session_id: str, msg_obj: dict) -> dict:
    """追加单条原始消息碎片到 _tmp_dialog_fragment（崩溃兜底）。
    msg_obj 原样 json 序列化保存; 同一 session 内同 msg_hash 幂等去重返回 dup。"""
    if not isinstance(msg_obj, dict):
        return {"status": "skip-non-dict"}
    msg_raw = json.dumps(msg_obj, ensure_ascii=False, sort_keys=True)
    mh = _msg_hash(msg_raw)
    c = _conn()
    try:
        dup = c.execute(
            "SELECT frag_id FROM _tmp_dialog_fragment WHERE session_id=? AND msg_hash=? LIMIT 1",
            (session_id, mh)).fetchone()
        if dup:
            return {"status": "dup", "frag_id": dup["frag_id"]}
        seq = c.execute(
            "SELECT COALESCE(MAX(seq),-1)+1 FROM _tmp_dialog_fragment WHERE session_id=?",
            (session_id,)).fetchone()[0]
        cur = c.execute(
            "INSERT INTO _tmp_dialog_fragment(session_id,seq,msg_hash,msg_raw,captured_ts,consumed) "
            "VALUES(?,?,?,?,?,0)",
            (session_id, seq, mh, msg_raw, _now()))
        c.commit()
        return {"status": "appended", "frag_id": cur.lastrowid, "seq": seq}
    finally:
        c.close()


def ingest_transcript(session_id: str, transcript_path: str, **meta) -> dict:
    """从 transcript JSONL 一次性落地全部碎片（幂等, 已存在 msg_hash 跳过）。
    供 PreCompact 入口在聚合前调用, 保证当轮完整对话进碎片表。"""
    n_new = n_dup = 0
    first_ts = last_ts = None
    c = _conn()
    try:
        for rec in _iter_transcript_objs(transcript_path):
            msg_raw = json.dumps(rec, ensure_ascii=False, sort_keys=True)
            mh = _msg_hash(msg_raw)
            ts = rec.get("timestamp") or _now()
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            dup = c.execute(
                "SELECT 1 FROM _tmp_dialog_fragment WHERE session_id=? AND msg_hash=? LIMIT 1",
                (session_id, mh)).fetchone()
            if dup:
                n_dup += 1
                continue
            seq = c.execute(
                "SELECT COALESCE(MAX(seq),-1)+1 FROM _tmp_dialog_fragment WHERE session_id=?",
                (session_id,)).fetchone()[0]
            c.execute(
                "INSERT INTO _tmp_dialog_fragment(session_id,seq,msg_hash,msg_raw,captured_ts,consumed) "
                "VALUES(?,?,?,?,?,0)",
                (session_id, seq, mh, msg_raw, _now()))
            n_new += 1
        c.commit()
    finally:
        c.close()
    return {"status": "ingested", "new": n_new, "dup": n_dup,
            "first_ts": first_ts, "last_ts": last_ts}


# ---------------- 聚合：碎片 → 快照表 + 逐条表 ----------------

def _extract_for_turn(rec: dict):
    """把一条原始 transcript 对象转成 core_dialogue_log 的检索字段 (role/content/ts)。
    适度加工仅供检索; 原文完整保留在 msg_raw→messages。tool 元信息进 content 的重组或留快照。"""
    typ = rec.get("type")
    msg = rec.get("message") or {}
    role = typ if typ in ("user", "assistant") else (msg.get("role") or typ)
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    parts.append(b["text"])
                elif b.get("type") in ("tool_use", "tool_result"):
                    parts.append(f"[{b.get('type')}]")
        content = "\n".join(parts)
    elif not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""
    return role or "unknown", (content or ""), (rec.get("timestamp") or "")


def aggregate_session(session_id: str, ref_archive_fragment: str = None,
                      terminate_reason: str = "precompact_normal",
                      model_version: str = None, meta: dict = None) -> dict:
    """聚合某 session 全部碎片 → core_raw_dialog_session(快照) + core_dialogue_log(逐条),
    写证据链, 标记碎片 consumed。返回统计。

    - messages: 全碎片 msg_raw 原样拼成 JSON 数组（零裁剪）
    - 逐条表: 每条消息提取 role/content/ts 写一行(turn 递增)
    - 幂等: 同 session 重跑 → 快照 UPDATE, 逐条表按 content_hash 去重
    """
    c = _conn()
    try:
        frags = c.execute(
            "SELECT seq,msg_raw,captured_ts FROM _tmp_dialog_fragment "
            "WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
        if not frags:
            return {"status": "empty", "session_id": session_id}
        msgs = []
        first_ts = last_ts = None
        for r in frags:
            try:
                obj = json.loads(r["msg_raw"])
                msgs.append(obj)
            except (ValueError, TypeError):
                obj = {"_raw": r["msg_raw"]}
                msgs.append(obj)
            # 起止时间优先取消息原始 timestamp（真相）, 缺省退回碎片落库时刻
            _ts = (obj.get("timestamp") if isinstance(obj, dict) else None) or r["captured_ts"]
            if first_ts is None:
                first_ts = _ts
            last_ts = _ts
        messages_json = json.dumps(msgs, ensure_ascii=False)
        now = _now()

        # 1) 快照表 upsert（保留已有 start_ts，更新 end_ts/messages）
        exist = c.execute("SELECT created_at FROM core_raw_dialog_session WHERE session_id=?",
                          (session_id,)).fetchone()
        if exist:
            c.execute(
                "UPDATE core_raw_dialog_session SET end_ts=?, messages=?, msg_count=?, "
                "terminate_reason=?, ref_archive_fragment=COALESCE(?,ref_archive_fragment), "
                "model_version=COALESCE(?,model_version), meta=COALESCE(?,meta), updated_at=? "
                "WHERE session_id=?",
                (last_ts, messages_json, len(msgs), terminate_reason, ref_archive_fragment,
                 model_version, json.dumps(meta, ensure_ascii=False) if meta else None, now, session_id))
            op = "updated"
        else:
            c.execute(
                "INSERT INTO core_raw_dialog_session(session_id,start_ts,end_ts,model_version,meta,"
                "messages,msg_count,terminate_reason,ref_archive_fragment,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, first_ts, last_ts, model_version,
                 json.dumps(meta, ensure_ascii=False) if meta else None,
                 messages_json, len(msgs), terminate_reason, ref_archive_fragment, now, now))
            op = "inserted"

        # 2) 逐条表（按 turn 递增, content_hash 幂等去重）
        n_log_new = 0
        for turn, r in enumerate(frags):
            rec = msgs[turn] if turn < len(msgs) else {}
            role, content, ts = _extract_for_turn(rec)
            if not content.strip():
                continue
            chash = hashlib.sha256(f"{session_id}:{turn}:{content}".encode("utf-8")).hexdigest()[:16]
            ctx = c.execute("SELECT 1 FROM core_dialogue_log WHERE log_id=? LIMIT 1", (chash,)).fetchone()
            if ctx:
                continue
            c.execute(
                "INSERT INTO core_dialogue_log(log_id,session_id,ts,role,turn,content,"
                "session_topic,content_hash,retrieved,linked_cards,fts) "
                "VALUES(?,?,?,?,?,?,?,?,0,NULL,?)",
                (chash, session_id, ts or r["captured_ts"], role, turn, content,
                 None, chash, content))
            n_log_new += 1

        # 3) 标记碎片 consumed
        c.execute("UPDATE _tmp_dialog_fragment SET consumed=1 WHERE session_id=? AND consumed=0",
                  (session_id,))
        c.commit()
        return {"status": op, "session_id": session_id, "msg_count": len(msgs),
                "log_new": n_log_new, "first_ts": first_ts, "last_ts": last_ts}
    finally:
        c.close()


# ---------------- 巡检：收拢悬空碎片 ----------------

def collect_orphan_sessions(max_age_hours: int = 72) -> dict:
    """扫描 captured_ts 早于 max_age_hours 且仍有未消费碎片的 session,
    聚合进两业务表（terminate_reason=timer_orphan_collect）, 避免碎片永久悬空。"""
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    c = _conn()
    try:
        sess = [r["session_id"] for r in c.execute(
            "SELECT DISTINCT session_id FROM _tmp_dialog_fragment "
            "WHERE consumed=0 AND captured_ts < ?", (cutoff,)).fetchall()]
    finally:
        c.close()
    done = []
    for sid in sess:
        try:
            r = aggregate_session(sid, terminate_reason="timer_orphan_collect")
            done.append({"session_id": sid, **r})
        except Exception as e:  # noqa: BLE001
            done.append({"session_id": sid, "status": "error", "err": repr(e)})
    return {"orphan_sessions": len(sess), "results": done}


# ---------------- 只读接口（排错/离线） ----------------

def load_session(session_id: str) -> dict:
    c = _conn()
    try:
        r = c.execute("SELECT * FROM core_raw_dialog_session WHERE session_id=?",
                      (session_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def load_turns(session_id: str) -> list:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT turn,role,ts,content FROM core_dialogue_log "
            "WHERE session_id=? ORDER BY turn", (session_id,)).fetchall()]
    finally:
        c.close()


def init_tables():
    """幂等建三张表（独立于 v3_schema 全量 build，供测试/首次初始化）。"""
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS core_raw_dialog_session (
      session_id TEXT PRIMARY KEY, start_ts TEXT, end_ts TEXT, model_version TEXT,
      meta TEXT, messages TEXT, msg_count INTEGER DEFAULT 0,
      terminate_reason TEXT, ref_archive_fragment TEXT, archived_offload TEXT,
      created_at TEXT NOT NULL, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_rawdlg_end ON core_raw_dialog_session(end_ts);
    CREATE INDEX IF NOT EXISTS idx_rawdlg_reason ON core_raw_dialog_session(terminate_reason);
    CREATE TABLE IF NOT EXISTS _tmp_dialog_fragment (
      frag_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER,
      msg_hash TEXT, msg_raw TEXT NOT NULL, captured_ts TEXT NOT NULL,
      consumed INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_tmpfrag_sess ON _tmp_dialog_fragment(session_id, consumed);
    CREATE INDEX IF NOT EXISTS idx_tmpfrag_cap ON _tmp_dialog_fragment(captured_ts);
    """)
    c.commit()
    c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="dialog_evidence_store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_agg = sub.add_parser("aggregate")
    p_agg.add_argument("--session", required=True)
    p_agg.add_argument("--ref", default=None)
    p_collect = sub.add_parser("collect-orphans")
    p_collect.add_argument("--max-age-hours", type=int, default=72)
    p_load = sub.add_parser("load")
    p_load.add_argument("--session", required=True)
    p_load.add_argument("--turns", action="store_true")
    args = ap.parse_args()
    if args.cmd == "init":
        init_tables(); print("建表完成")
    elif args.cmd == "aggregate":
        print(json.dumps(aggregate_session(args.session, ref_archive_fragment=args.ref),
                         ensure_ascii=False, indent=1))
    elif args.cmd == "collect-orphans":
        print(json.dumps(collect_orphan_sessions(args.max_age_hours),
                         ensure_ascii=False, indent=1))
    elif args.cmd == "load":
        if args.turns:
            print(json.dumps(load_turns(args.session), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(load_session(args.session), ensure_ascii=False, indent=1))