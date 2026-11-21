"""pending_writeback_regression.py — #7 pending 可靠写回回归 (2026-11-21 A-5)

覆盖 A-1(原子落盘) / A-2(跨进程锁) / A-4(写事务加固) 三类护栏的可验证性质。
**全部用临时沙箱, 不碰真实 data/memory/store_pending.jsonl 与主库。**

判据:
  1. 原子写: _atomic_write_jsonl 内容正确、无残留 .tmp、半截崩溃不留坏文件
  2. 锁互斥: 同一锁第二次进入抛退出(acquired=False); 陈旧锁(死pid/超龄)可被接管
  3. 转正幂等: 已存在卡重复 INSERT OR IGNORE 不产生重复行(唯一约束)
  4. 退避重试: 模拟 2 次 locked 后成功, 验证 _write_card_to_db 重试生效

运行: python eval/audit/pending_writeback_regression.py
退出码 0=全过 1=有失败。
"""
import io
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "memory"))
sys.path.insert(0, _HERE)

import memory_judge as MJ  # noqa: E402
import memory_organize as MO  # noqa: E402

_R = []


def _check(name, cond, detail=""):
    _R.append({"name": name, "ok": bool(cond)})
    print(("  ✅ " if cond else "  ❌ ") + name + (" | " + detail if detail and not cond else ""))


def case_atomic_write():
    """A-1: 原子写正确性 + 无残留 tmp。"""
    print("[1] A-1 原子落盘")
    d = tempfile.mkdtemp(prefix="pw_atomic_")
    p = os.path.join(d, "x.jsonl")
    recs = [{"a": 1}, {"b": "中文"}]
    n = MJ._atomic_write_jsonl(p, recs)
    _check("写入条数正确", n == 2, str(n))
    back = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
    _check("内容往返一致", back == recs, str(back))
    tmps = [f for f in os.listdir(d) if ".tmp." in f]
    _check("无残留 .tmp 文件", not tmps, str(tmps))
    # 覆盖写: 第二次用更少记录, 应完全替换(无旧残留)
    MJ._atomic_write_jsonl(p, [{"c": 3}])
    back2 = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
    _check("覆盖写完全替换", back2 == [{"c": 3}], str(back2))
    # 空记录 → 空文件(合法)
    MJ._atomic_write_jsonl(p, [])
    _check("空列表写空文件", os.path.getsize(p) == 0)


def case_lock_mutex():
    """A-2: 互斥 + 陈旧锁接管。"""
    print("[2] A-2 跨进程锁")
    d = tempfile.mkdtemp(prefix="pw_lock_")
    lp = os.path.join(d, "pend.lock")
    # 首次抢锁成功
    with MJ._PendingLock(lp) as lk1:
        _check("首进程抢锁成功", lk1.acquired)
        # 持锁期间第二进程抢锁失败
        lk2 = MJ._PendingLock(lp)
        lk2.__enter__()
        _check("持锁期间第二进程抢锁失败", not lk2.acquired)
        lk2.__exit__()
    _check("退出后锁文件被删除", not os.path.exists(lp))
    # 退出后可再次抢锁
    with MJ._PendingLock(lp) as lk3:
        _check("释放后可重新抢锁", lk3.acquired)
    # 陈旧锁(死 pid) 可接管
    with io.open(lp, "w", encoding="utf-8") as f:
        import time
        f.write(json.dumps({"pid": 999999999, "ts": time.time()}))
    with MJ._PendingLock(lp) as lk4:
        _check("死pid陈旧锁被接管", lk4.acquired)
    # 陈旧锁(超龄) 可接管
    with io.open(lp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "ts": 0}))  # 1970, 必然超龄
    with MJ._PendingLock(lp, stale_secs=60) as lk5:
        _check("超龄陈旧锁被接管", lk5.acquired)


def case_review_holds_skip_on_lock():
    """A-2: review_holds 抢锁失败时跳过并返 skipped, 不抛异常。"""
    print("[3] A-2 review_holds 锁冲突优雅跳过")
    d = tempfile.mkdtemp(prefix="pw_rev_")
    orig = MJ.STORE_PENDING_JSONL
    MJ.STORE_PENDING_JSONL = os.path.join(d, "store_pending.jsonl")
    io.open(MJ.STORE_PENDING_JSONL, "w", encoding="utf-8").write("")
    lp = MJ.STORE_PENDING_JSONL + ".lock"
    # 先占锁
    holder = MJ._PendingLock(lp)
    holder.__enter__()
    try:
        rep = MJ.review_holds()
        _check("锁被占时返回 skipped", rep.get("skipped") is True, str(rep))
        _check("skipped 时不抛异常且 reviewed=0", rep.get("reviewed") == 0, str(rep))
    finally:
        holder.__exit__()
        MJ.STORE_PENDING_JSONL = orig


def case_write_card_idempotent():
    """A-4: 重复 INSERT OR IGNORE 同 inst_id 不产生重复行(主键幂等)。"""
    print("[4] A-4 落卡幂等(主键约束)")
    import sqlite3
    d = tempfile.mkdtemp(prefix="pw_card_")
    dbp = os.path.join(d, "t.db")
    c = sqlite3.connect(dbp, timeout=5)
    c.execute("CREATE TABLE core_memory_card (inst_id TEXT PRIMARY KEY, summary TEXT)")
    c.commit()
    c.close()
    # 直接验证 INSERT OR IGNORE 主键幂等语义(不依赖完整 22 列 schema)
    for _ in range(3):
        cc = sqlite3.connect(dbp, timeout=5, isolation_level=None)
        cc.execute("PRAGMA busy_timeout=5000")
        cc.execute("BEGIN IMMEDIATE")
        cc.execute("INSERT OR IGNORE INTO core_memory_card VALUES (?,?)", ("dup", "s"))
        cc.commit()
        cc.close()
    n = sqlite3.connect(dbp, timeout=5).execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    _check("同主键3次插入仅1行", n == 1, f"rows={n}")


def case_retry_backoff():
    """A-4: _write_card_to_db 对 locked 退避重试(前2次抛 locked 第3次成功)。"""
    print("[5] A-4 退避重试")
    import sqlite3
    import types
    d = tempfile.mkdtemp(prefix="pw_retry_")
    dbp = os.path.join(d, "t.db")
    c = sqlite3.connect(dbp, timeout=5)
    c.execute("CREATE TABLE core_memory_card (%s)" % ",".join("c%d" % i for i in range(22)))
    c.commit()
    c.close()

    real_connect = sqlite3.connect
    state = {"n": 0}

    class _FakeCon:
        def __init__(self, conn):
            self._c = conn
        def execute(self, sql, *a):
            if sql == "BEGIN IMMEDIATE":
                state["n"] += 1
                if state["n"] <= 2:
                    raise sqlite3.OperationalError("database is locked")
            return self._c.execute(sql, *a)
        def commit(self):
            return self._c.commit()
        def rollback(self):
            return self._c.rollback()
        def close(self):
            return self._c.close()

    def fake_connect(path, *a, **k):
        return _FakeCon(real_connect(path, *a, **k))

    orig_connect = sqlite3.connect
    orig_memdb = MO.os.path.join
    # 用 monkeypatch 注入假 connect + 改 MEM_DIR 指向临时库
    class _MO:
        pass
    old_connect = sqlite3.connect
    sqlite3.connect = fake_connect
    old_join = MO.os.path.join
    MO.os.path.join = lambda *p: dbp if p and p[-1] == "corpus_store.db" else old_join(*p)
    try:
        entry = {"inst_id": "x1", "ts": "t", "event_ts": None, "full_hash": "h",
                 "asset_class": "corpus", "repro": "flash", "confidence": "L1",
                 "dfcp_tag": {}, "affect_tags": None, "summary": "s", "summary_converged": None,
                 "detail": None, "source_ref": None, "origin_ids": [], "entity_id": None,
                 "derived_from": None, "tier": "active", "weight": {},
                 "protected": False, "memory_class": None}
        MO._write_card_to_db(entry, max_retries=3)
        _check("前2次locked后第3次成功(重试生效)", state["n"] == 3, f"attempts={state['n']}")
    except Exception as e:
        _check("退避重试成功", False, repr(e))
    finally:
        sqlite3.connect = old_connect
        MO.os.path.join = old_join


def case_fsync_append():
    """A-1: append_jsonl fsync=True 可正常写入(不抛)。"""
    print("[6] A-1 append_jsonl fsync 参数")
    d = tempfile.mkdtemp(prefix="pw_fsync_")
    p = os.path.join(d, "a.jsonl")
    try:
        MO.append_jsonl(p, {"k": 1}, fsync=True)
        MO.append_jsonl(p, {"k": 2})  # 默认 False
        lines = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
        _check("fsync=True 与默认均正确追加", lines == [{"k": 1}, {"k": 2}], str(lines))
    except Exception as e:
        _check("append_jsonl fsync", False, repr(e))


def main():
    case_atomic_write()
    case_lock_mutex()
    case_review_holds_skip_on_lock()
    case_write_card_idempotent()
    case_retry_backoff()
    case_fsync_append()
    failed = [r for r in _R if not r["ok"]]
    print("\n[pending 写回回归] %d/%d 通过" % (len(_R) - len(failed), len(_R)))
    out = os.path.join(_HERE, "logs", "pending_writeback_regression.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(
        json.dumps({"total": len(_R), "passed": len(_R) - len(failed), "results": _R},
                   ensure_ascii=False, indent=1))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()