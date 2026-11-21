"""dialog_frag_unit_regression.py — 碎片判定口径回归 (2026-11-21 用户裁决修正)

背景: 2026-11-21 碎片治理发现四处判定点口径错位——归档 SQL / patrol 用了
     length(body)(SQLite 字符数), 而 t/h 通道过滤用 len(text.encode())(UTF-8 字节)。
     导致 552 条 21~60 字节的中文短指令(如「帮我编译apk然后推包」11字符/27字节)
     被误判碎片误归档。现已回滚主表并对齐为【UTF-8 字节 ≤20】唯一口径。

本用例锁死口径, 防止再次错位。只读校验, 不写库。

断言组:
  1. 常量一致: patrol.FRAG_BYTES == 20, 且 patrol 内 _BLEN 用 cast(body as blob)
  2. 归档表纯净: 归档表所有行 UTF-8 字节 <= 20 (回滚后仅应留真字节碎片)
  3. 主表字节碎片可复现: patrol.patrol() 的 fragment_le20 == 直接字节 SQL 计数
  4. 字符/字节会分歧: 构造 11 字符 27 字节样例, 证明字符口径会漏判(用于回归告警)
  5. t/h 通道口径: e9 与 memory_gate 的过滤都是 len(encode()) 字节
输出 logs/dialog_frag_unit_regression.json。退出码 0=全过 1=有断言失败。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "eval", "core"))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
OUT = os.path.join(_HERE, "logs", "dialog_frag_unit_regression.json")

# 多字节中文样例: 11 字符但 27 字节 —— 字符口径会判"非碎片", 字节口径判"碎片附近"
MULTIBYTE_SAMPLE = "帮我编译apk然后推包"


def _blen_sql():
    """直接字节 SQL 统计主表碎片(作为 patrol 的对照真值)。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM raw_corpus WHERE kind='session_dialog' "
                    "AND length(cast(body as blob))<=20").fetchone()[0]
    con.close()
    return n


def main():
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. 常量 & _BLEN 口径
    try:
        import patrol_dialog_fragments as p  # noqa: E402
        check("const_FRAG_BYTES", p.FRAG_BYTES == 20, f"FRAG_BYTES={p.FRAG_BYTES}")
        blen = getattr(p, "_BLEN", "")
        check("patrol_uses_byte_length", "cast(body as blob)" in blen.replace(" ", "").lower()
              or "cast(bodyasblob)" in blen.replace(" ", "").lower(), f"_BLEN={blen!r}")
    except Exception as e:  # noqa: BLE001
        check("import_patrol", False, repr(e))

    # 2. 归档表纯净: 全部字节 <= 20
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dirty = con.execute(
        "SELECT COUNT(*) FROM session_dialog_archive_fragment WHERE length(cast(body as blob))>20"
    ).fetchone()[0]
    archived_total = con.execute("SELECT COUNT(*) FROM session_dialog_archive_fragment").fetchone()[0]
    con.close()
    check("archive_all_bytes_le20", dirty == 0,
          f"归档总数={archived_total}, 超20字节残留={dirty}")

    # 3. patrol 统计与直接字节 SQL 一致
    try:
        import patrol_dialog_fragments as p  # noqa: E402,F811
        r = p.patrol()
        truth = _blen_sql()
        check("patrol_matches_byte_truth", r["fragment_le20"] == truth,
              f"patrol={r['fragment_le20']} byteSQL={truth}")
    except Exception as e:  # noqa: BLE001
        check("patrol_run", False, repr(e))

    # 4. 字符/字节分歧保护: 样例 chars<=20 但 bytes>20, 证明字符口径危险
    chars = len(MULTIBYTE_SAMPLE)
    bytes_ = len(MULTIBYTE_SAMPLE.encode("utf-8"))
    check("multibyte_sample_diverges", chars <= 20 and bytes_ > 20,
          f"{MULTIBYTE_SAMPLE!r} chars={chars} bytes={bytes_} (字符口径会漏判)")

    # 5. t/h 通道口径: 源码必须用 encode() 而非裸 len()
    e9 = io.open(os.path.join(_HERE, "eval", "core", "e9_dialog_governance.py"),
                 encoding="utf-8").read()
    gate = io.open(os.path.join(_HERE, "memory", "memory_gate.py"),
                   encoding="utf-8").read()
    check("t_channel_uses_bytes", '.strip().encode("utf-8")' in e9 or "encode('utf-8')" in e9,
          "e9 应含 .encode(\"utf-8\") 字节判定")
    check("h_channel_uses_bytes", 'encode("utf-8")' in gate or "encode('utf-8')" in gate,
          "memory_gate 应含 encode(\"utf-8\") 字节判定")

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}")
    print(f"[dialog碎片口径回归] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()