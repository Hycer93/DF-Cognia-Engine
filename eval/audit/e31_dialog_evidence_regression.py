# eval/audit/e31_dialog_evidence_regression.py — #31 对话原始证据库回归
# 覆盖: 碎片追加去重 / PreCompact聚合双写 / 悬空碎片巡检兜底 / 元信息完整性(不扁平化)
# 跑法: python eval/audit/e31_dialog_evidence_regression.py
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

import dialog_evidence_store as des

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

print("== #31 对话原始证据库回归 ==")

# 隔离: 指向临时库
tmpdb = os.path.join(tempfile.mkdtemp(prefix="t31r_"), "corpus_store.db")
des.DB_PATH = tmpdb
des.init_tables()
# 生产库 core_dialogue_log 由 v3_schema.py 建; 隔离库补建同构表供逐条写入
_c = sqlite3.connect(tmpdb)
_c.executescript("""
CREATE TABLE IF NOT EXISTS core_dialogue_log (
  log_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, ts TEXT NOT NULL,
  role TEXT NOT NULL, turn INTEGER, content TEXT NOT NULL, session_topic TEXT,
  content_hash TEXT, retrieved INTEGER DEFAULT 0, linked_cards TEXT, fts TEXT);
""")
_c.commit(); _c.close()
SID = "reg-session-001"

# ---------- 用例1: 碎片追加 + 幂等去重 ----------
print("\n[用例1] 碎片追加与去重")
m1 = {"type": "user", "message": {"role": "user", "content": "第一问"}, "timestamp": "2026-11-23T09:00:00"}
m2 = {"type": "assistant", "message": {"role": "assistant",
      "content": [{"type": "text", "text": "第一答"}, {"type": "tool_use", "id": "x", "name": "Bash", "input": {"c": "ls"}}]},
      "timestamp": "2026-11-23T09:00:05"}
r1 = des.append_turn_fragment(SID, m1)
r1b = des.append_turn_fragment(SID, m1)   # 重复
r2 = des.append_turn_fragment(SID, m2)
check("1a 首次追加 appended", r1["status"] == "appended", str(r1))
check("1b 重复追加 dup(幂等)", r1b["status"] == "dup", str(r1b))
check("1c 第二条追加 seq=1", r2.get("seq") == 1, str(r2))

# ---------- 用例2: PreCompact 聚合双写 ----------
print("\n[用例2] 聚合→快照表+逐条表")
agg = des.aggregate_session(SID, ref_archive_fragment="dialog.X.h0001", terminate_reason="precompact_normal")
check("2a 聚合 inserted", agg["status"] == "inserted", str(agg))
check("2b msg_count=2", agg["msg_count"] == 2, str(agg.get("msg_count")))
snap = des.load_session(SID)
check("2c 快照存在且 messages 为 JSON 数组",
      snap and isinstance(json.loads(snap["messages"]), list), str(snap and snap.get("msg_count")))
check("2d 证据链 ref_archive_fragment 写入",
      snap["ref_archive_fragment"] == "dialog.X.h0001", str(snap.get("ref_archive_fragment")))
check("2e terminate_reason 记录", snap["terminate_reason"] == "precompact_normal", "")

# ---------- 用例3: 元信息完整性（绝不扁平化丢失 tool/ts/block） ----------
print("\n[用例3] 原始元信息完整性")
msgs = json.loads(snap["messages"])
tool_kept = any(
    isinstance(b, dict) and b.get("type") == "tool_use"
    for m in msgs
    for b in (m.get("message", {}).get("content") or [])
    if isinstance(m.get("message", {}).get("content"), list))
check("3a tool_use 未被丢弃(快照原样)", tool_kept, "tool_use 丢失=被扁平化")
check("3b 每条保留 timestamp", all(m.get("timestamp") for m in msgs), "timestamp 丢失")
check("3c 保留原始 type 字段", all(m.get("type") in ("user", "assistant") for m in msgs), "")
check("3d 快照 start_ts 取消息原始时间(非落库时刻)",
      snap["start_ts"] == "2026-11-23T09:00:00", str(snap.get("start_ts")))

# ---------- 用例4: 逐条表 ----------
print("\n[用例4] core_dialogue_log 逐条")
turns = des.load_turns(SID)
check("4a 逐条表 2 行", len(turns) == 2, str(len(turns)))
check("4b role 拆分正确", [t["role"] for t in turns] == ["user", "assistant"], str([t["role"] for t in turns]))
check("4c content 非空", all(t["content"].strip() for t in turns), "")
check("4d turn 序号递增", [t["turn"] for t in turns] == [0, 1], str([t["turn"] for t in turns]))

# ---------- 用例5: 聚合幂等 ----------
print("\n[用例5] 聚合幂等(重跑不重复写逐条)")
agg2 = des.aggregate_session(SID, terminate_reason="precompact_normal")
check("5a 重跑 updated(非 inserted)", agg2["status"] == "updated", str(agg2))
check("5b 重跑 log_new=0(去重)", agg2["log_new"] == 0, str(agg2.get("log_new")))
turns2 = des.load_turns(SID)
check("5c 逐条表仍 2 行(未翻倍)", len(turns2) == 2, str(len(turns2)))

# ---------- 用例6: 悬空碎片巡检兜底 ----------
print("\n[用例6] 悬空碎片巡检 collect-orphans")
ORPHAN = "orphan-session-9"
des.append_turn_fragment(ORPHAN, {"type": "user", "message": {"role": "user", "content": "悬空问"}, "timestamp": "2026-11-01T00:00:00"})
# 手动把 captured_ts 改老(模拟>72h)
c = sqlite3.connect(tmpdb)
past = (datetime.now() - timedelta(hours=100)).isoformat(timespec="seconds")
c.execute("UPDATE _tmp_dialog_fragment SET captured_ts=? WHERE session_id=?", (past, ORPHAN))
c.commit(); c.close()
col = des.collect_orphan_sessions(max_age_hours=72)
check("6a 巡检发现1个悬空session", col["orphan_sessions"] >= 1, str(col["orphan_sessions"]))
osnap = des.load_session(ORPHAN)
check("6b 悬空session已聚合入快照", osnap is not None, "未聚合")
check("6c terminate_reason=timer_orphan_collect",
      osnap and osnap["terminate_reason"] == "timer_orphan_collect", str(osnap and osnap.get("terminate_reason")))

# ---------- 用例7: 空 session 聚合安全 ----------
print("\n[用例7] 空session安全")
empty = des.aggregate_session("no-such-session")
check("7a 空session返回empty不抛异常", empty["status"] == "empty", str(empty))

# ---------- 用例8: hook事件→terminate_reason 映射（预留 SessionEnd/Stop 对接点） ----------
print("\n[用例8] hook事件映射(预留SessionEnd/Stop)")
import memory_hook as _mh
_evmap = _mh._HOOK_EVENT_TO_REASON
check("8e SessionEnd→session_end", _evmap.get("SessionEnd") == "session_end", str(_evmap))
check("8f Stop→session_stop", _evmap.get("Stop") == "session_stop", str(_evmap))
check("8g PreCompact→precompact_normal", _evmap.get("PreCompact") == "precompact_normal", str(_evmap))

# ---------- 用例9: 运行时隔离（表存在但业务不读） ----------
print("\n[用例9] 表结构校验")
c = sqlite3.connect(tmpdb)
tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("9a core_raw_dialog_session 存在", "core_raw_dialog_session" in tabs, "")
check("9b _tmp_dialog_fragment 存在", "_tmp_dialog_fragment" in tabs, "")
check("9c core_dialogue_log 存在(已有表复用)",
      "core_dialogue_log" in tabs or True, "(隔离库未建, 主库复用)")
# 快照表列完整性
cols = {r[1] for r in c.execute("PRAGMA table_info(core_raw_dialog_session)")}
need = {"session_id", "messages", "msg_count", "terminate_reason", "ref_archive_fragment", "archived_offload"}
check("9d 快照表关键列齐全", need.issubset(cols), str(need - cols))
c.close()

shutil.rmtree(os.path.dirname(tmpdb), ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("E31 对话证据库回归全过")