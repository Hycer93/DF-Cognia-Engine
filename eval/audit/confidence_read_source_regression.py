"""confidence_read_source_regression.py — 置信度读源一致性回归 (2026-11-21 P0-2)

背景: confidence_dynamics._load_memories() 原固定读 memories.jsonl, 而主库已切
     SQLite(corpus_store.db core_memory_card), 两者长期漂移(jsonl 245 条 vs
     sqlite active 314 条, 差 69 条), 导致置信度升降级对着过期快照做动作。
     已改为复用 memory_query.load_memories() 同源读取。

本用例锁死读源一致, 防回退。只读。

断言组:
  1. confidence_dynamics._load_memories() 与 memory_query.load_memories() 集合一致
  2. 默认读源为 sqlite(主库唯一真相), 集合应等于 sqlite active 全集
  3. 应急回切 DF_MEMORY_SOURCE=jsonl 时两者仍一致(jsonl 链路)
退出码 0=全过 1=有断言失败。输出 logs/confidence_read_source_regression.json。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEM = os.path.join(_HERE, "memory")
sys.path.insert(0, _HERE)
sys.path.insert(0, _MEM)
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
OUT = os.path.join(_HERE, "logs", "confidence_read_source_regression.json")


def main():
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    import confidence_dynamics as cd
    import memory_query as mq

    # 1. 同源一致（默认 sqlite）
    a = {m["inst_id"] for m in cd._load_memories()}
    b = {m["inst_id"] for m in mq.load_memories()}
    check("cd_equals_mq", a == b,
          f"cd={len(a)} mq={len(b)} cd_only={len(a-b)} mq_only={len(b-a)}")

    # 2. 默认读源应等于 sqlite active 全集
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sq_active = {r[0] for r in con.execute(
        "SELECT inst_id FROM core_memory_card WHERE status='active'")}
    con.close()
    check("cd_equals_sqlite_active", a == sq_active,
          f"cd={len(a)} sqlite_active={len(sq_active)} 差={len(a ^ sq_active)}")

    # 3. 应急回切 jsonl 链路仍一致（子进程避免污染本进程环境变量）
    import subprocess
    import tempfile
    snippet = (
        "import sys\n"
        f"sys.path.insert(0, {_HERE!r})\n"
        f"sys.path.insert(0, {_MEM!r})\n"
        "import confidence_dynamics as cd, memory_query as mq\n"
        "a = {m['inst_id'] for m in cd._load_memories()}\n"
        "b = {m['inst_id'] for m in mq.load_memories()}\n"
        "print(len(a), len(b), len(a ^ b))\n"
    )
    env = dict(os.environ)
    env["DF_MEMORY_SOURCE"] = "jsonl"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(snippet)
        out = subprocess.run([sys.executable, tmp], capture_output=True,
                             text=True, encoding="utf-8", env=env, timeout=60)
        parts = (out.stdout or "").strip().split()
        ok = len(parts) == 3 and parts[2] == "0"
        check("jsonl_fallback_consistent", ok,
              f"stdout={out.stdout.strip()!r} stderr={out.stderr.strip()[:80]!r}")
    except Exception as e:  # noqa: BLE001
        check("jsonl_fallback_consistent", False, repr(e))
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}")
    print(f"[置信度读源回归] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()