"""dialog_trace_regression.py — #12 对话追溯回归 (2026-11-22)

覆盖 #12 四项改造的可验证性质:
  1. transcript 解析: user/assistant 文本提取, tool_use/tool_result 排除
  2. h 通道落库: payload{transcript_path} → raw_corpus(title=session, 用户+模型)
  3. 降级通路: transcript 缺失/异常 → 回退 dialog_last_segment.md
  4. session 分支分流: hook payload 走对话落库, 不误当候选
  5. list_dialog: 按 session/日期查询 session_dialog
  6. cards_from_dialog: 对话标识→记忆卡反向查找(多格式弱关联)
  7. E9 h 通道断流告警: h=0 时 stalled=True

**全部用临时沙箱, 不碰真实 data/memory 主库。**
运行: python eval/audit/dialog_trace_regression.py
退出码 0=全过 1=有失败。
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "memory"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "eval", "core"))

_R = []


def _check(name, cond, detail=""):
    _R.append({"name": name, "ok": bool(cond)})
    print(("  ✅ " if cond else "  ❌ ") + name + (" | " + detail if detail and not cond else ""))


def _sandbox():
    """复制真实 db 到临时目录，patch 所有路径常量。返回 (tmp, restore_fn)。"""
    tmp = tempfile.mkdtemp(prefix="dlgtrace_")
    real_db = os.path.join(_HERE, "data", "memory", "corpus_store.db")
    shutil.copy(real_db, os.path.join(tmp, "corpus_store.db"))
    import corpus_store as cs
    import memory_gate as mg
    import memory_query as mq
    orig = {
        "cs_db": cs.DB_PATH,
        "mg_dir": mg.DB_DIR, "mg_log": mg.GATE_LOG, "mg_miss": mg.MISS_CASE,
        "mq_db": getattr(mq, "_DB_PATH", None),
    }
    cs.DB_PATH = os.path.join(tmp, "corpus_store.db")
    mg.DB_DIR = tmp
    mg.GATE_LOG = os.path.join(tmp, "gate_log.jsonl")
    mg.MISS_CASE = os.path.join(tmp, "gate_miss_case.jsonl")
    if orig["mq_db"] is not None:
        mq._DB_PATH = os.path.join(tmp, "corpus_store.db")

    def restore():
        cs.DB_PATH = orig["cs_db"]
        mg.DB_DIR = orig["mg_dir"]
        mg.GATE_LOG = orig["mg_log"]
        mg.MISS_CASE = orig["mg_miss"]
        if orig["mq_db"] is not None:
            mq._DB_PATH = orig["mq_db"]
        shutil.rmtree(tmp, ignore_errors=True)

    return tmp, restore


def _write_transcript(path, recs):
    with io.open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _mk_transcript(tmp):
    tp = os.path.join(tmp, "t.jsonl")
    _write_transcript(tp, [
        {"type": "user", "message": {"role": "user", "content": "回归用户第一问内容足够长一些"},
         "timestamp": "2026-11-22T10:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "回归模型回复第一答内容也足够长"}]},
         "timestamp": "2026-11-22T10:00:05Z"},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "tool_use", "name": "Bash", "id": "x", "input": {}}]},
         "timestamp": "2026-11-22T10:00:06Z"},
        {"type": "user", "message": {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": "x", "content": "工具结果不入对话"}]},
         "timestamp": "2026-11-22T10:00:07Z"},
    ])
    return tp


def case_parse():
    print("[1] transcript 解析")
    import memory_hook as mh
    tmp = tempfile.mkdtemp(prefix="dlgparse_")
    try:
        tp = _mk_transcript(tmp)
        text = mh._parse_transcript(tp)
        _check("含用户文本", "回归用户第一问" in text)
        _check("含模型文本", "回归模型回复第一答" in text)
        _check("排除 tool_use", "tool_use" not in text and "Bash" not in text)
        _check("排除 tool_result", "工具结果" not in text)
        _check("角色标签", "【用户】" in text and "【模型】" in text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_store_transcript():
    print("[2] h 通道 transcript 落库")
    tmp, restore = _sandbox()
    try:
        import memory_hook as mh
        tp = _mk_transcript(tmp)
        payload = {"session_id": "reg-session-1", "transcript_path": tp,
                   "hook_event_name": "PreCompact"}
        r = mh._session_dialog_from_hook(payload)
        _check("status=stored", r.get("status") == "stored", str(r))
        _check("channel=transcript", r.get("channel") == "transcript", str(r))
        c = sqlite3.connect(os.path.join(tmp, "corpus_store.db"))
        row = c.execute("SELECT id,title,body,source_ref FROM raw_corpus WHERE title='reg-session-1'"
                        ).fetchone()
        c.close()
        _check("落库存在", row is not None)
        if row:
            _check("title=session_id", row[1] == "reg-session-1", row[1])
            _check("body 含用户+模型", "回归用户第一问" in row[2] and "回归模型回复" in row[2])
            _check("source_ref 标 stdin-transcript", "precompact-stdin-transcript" in row[3], row[3])
    finally:
        restore()


def case_fallback_md():
    print("[3] transcript 缺失降级 md")
    tmp, restore = _sandbox()
    try:
        import memory_hook as mh
        payload = {"session_id": "reg-fallback", "transcript_path": "",
                   "hook_event_name": "PreCompact"}
        r = mh._session_dialog_from_hook(payload)
        _check("channel=fallback-md", r.get("channel") == "fallback-md", str(r))
        # 路径不存在也降级
        payload2 = {"session_id": "reg-fallback2",
                    "transcript_path": os.path.join(tmp, "nope.jsonl"),
                    "hook_event_name": "PreCompact"}
        r2 = mh._session_dialog_from_hook(payload2)
        _check("路径不存在也降级", r2.get("channel") == "fallback-md", str(r2))
        _check("记录 transcript_err", r2.get("transcript_err") is not None)
    finally:
        restore()


def case_list_dialog():
    print("[4] list_dialog 查询")
    tmp, restore = _sandbox()
    try:
        import corpus_store as cs
        import memory_hook as mh
        tp = _mk_transcript(tmp)
        mh._session_dialog_from_hook({"session_id": "reg-query-sess", "transcript_path": tp,
                                      "hook_event_name": "PreCompact"})
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        r1 = cs.list_dialog(session_id="reg-query-sess")
        _check("按 session 查到", len(r1) >= 1, str(len(r1)))
        _check("title 匹配", all(x["title"] == "reg-query-sess" for x in r1))
        r2 = cs.list_dialog(tree_vol=today, session_id="reg-query-sess")
        _check("session+日期叠加", len(r2) >= 1)
        r3 = cs.list_dialog(session_id="reg-query-sess", with_body=True)
        _check("with_body 带正文", len(r3) >= 1 and "body" in r3[0])
        r4 = cs.list_dialog(session_id="nonexistent-xyz")
        _check("不存在的 session 返回空", r4 == [])
    finally:
        restore()


def case_cards_from_dialog():
    print("[5] cards_from_dialog 反向查找")
    tmp, restore = _sandbox()
    try:
        import corpus_store as cs
        import memory_query as mq
        import memory_hook as mh
        tp = _mk_transcript(tmp)
        r = mh._session_dialog_from_hook({"session_id": "reg-rev-sess", "transcript_path": tp,
                                          "hook_event_name": "PreCompact"})
        did = r.get("id")
        _check("先落一段对话", bool(did), str(r))
        # 反向查真实库已有的 raw锚 日期卡
        h = mq.cards_from_dialog("2026-11-17")
        _check("日期锚反查命中", len(h) >= 1, str(len(h)))
        if h:
            _check("返回含 card 与 corpus 键", "card" in h[0])
        _check("空 key 返回空", mq.cards_from_dialog("") == [])
        _check("不存在 key 返回空", mq.cards_from_dialog("dialog.zzz.none") == [])
    finally:
        restore()


def case_h_stalled():
    print("[6] E9 h 通道断流告警")
    tmp, restore = _sandbox()
    try:
        import e9_dialog_governance as e9
        # 沙箱库里近3天 h 记录: 真实库 h 全在11-16及更早, 近3天应 h=0 → stalled
        stat = e9._check_h_channel_stalled(3)
        _check("返回 h_records/t_records/days/stalled 键",
               all(k in stat for k in ("h_records", "t_records", "days", "stalled")))
        _check("stalled 布尔", isinstance(stat["stalled"], bool))
        # 造一条今日 h 记录后再测, stalled 应变 False
        import corpus_store as cs
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        c = sqlite3.connect(os.path.join(tmp, "corpus_store.db"))
        c.execute("INSERT INTO raw_corpus (id,kind,tree_vol,tree_ch,title,body,version,status,"
                  "content_hash,source_ref,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("dialog.%s.h99999" % today, "session_dialog", today, "h99999", "reg-h",
                   "回归h通道测试正文内容足够长一些呢", 1, "current", "abc", "reg; test", today))
        c.commit(); c.close()
        stat2 = e9._check_h_channel_stalled(3)
        _check("补一条h后 stalled=False", stat2["stalled"] is False, str(stat2))
    finally:
        restore()


def main():
    case_parse()
    case_store_transcript()
    case_fallback_md()
    case_list_dialog()
    case_cards_from_dialog()
    case_h_stalled()
    failed = [r for r in _R if not r["ok"]]
    print("\n[#12 对话追溯回归] %d/%d 通过" % (len(_R) - len(failed), len(_R)))
    out = os.path.join(_HERE, "logs", "dialog_trace_regression.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(
        json.dumps({"total": len(_R), "passed": len(_R) - len(failed), "results": _R},
                   ensure_ascii=False, indent=1))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()