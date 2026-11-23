# eval/audit/e30_p1_r4_regression.py — #30 C组回归: P1保留detail + R4统一读取(commit-2)
# 覆盖用例 10-12: derived保留detail/标记 / read_card_detail分流 / 非derived不标记
# 隔离测试: 复制主库→临时库, 重定向memory_organize.MEM_DIR, 不碰主库。
# 跑法: python eval/audit/e30_p1_r4_regression.py
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

MAIN_DB = os.path.join(HERE, "data", "memory", "corpus_store.db")

print("== #30 C组: P1保留detail + R4统一读取回归 ==")

tmpdir = tempfile.mkdtemp(prefix="t30_p1_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

# ---- 用例10: derived 保留 detail/source_ref + dfcp_tag.derived_only ----
# behavior=B00 → judge_repro → derived
st, msg = mo.atomic_store(
    "派生卡测试: 从既有事实派生的复述内容",
    "这是派生卡应保留的完整detail原文",
    "derived-src-1",
    {"domain": "005-001", "behavior": "B00", "relation": "H", "confidence": "L1"})
check("用例10a: derived 卡入库成功", st == "stored", f"st={st} msg={msg}")

con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
r = con.execute(
    "SELECT repro, detail, source_ref, dfcp_tag FROM core_memory_card "
    "WHERE source_ref='derived-src-1'").fetchone()
check("用例10b: derived 卡 repro 判定为 derived", r and r["repro"] == "derived", str(dict(r) if r else None))
check("用例10c: derived 卡 detail 物理保留(非空)", r and r["detail"] == "这是派生卡应保留的完整detail原文",
      f"detail={r['detail'] if r else None!r}")
check("用例10d: derived 卡 source_ref 保留", r and r["source_ref"] == "derived-src-1",
      f"src={r['source_ref'] if r else None!r}")
_tags = json.loads(r["dfcp_tag"]) if r else {}
check("用例10e: dfcp_tag.derived_only == true", _tags.get("derived_only") is True, str(_tags))
con.close()

# ---- 用例11: read_card_detail 分流 ----
derived_card = {"detail": "派生detail原文", "dfcp_tag": {"derived_only": True}}
check("用例11a: derived卡 search 用途 → None",
      mo.read_card_detail(derived_card, "search") is None, "")
check("用例11b: derived卡 reasoning 用途 → None",
      mo.read_card_detail(derived_card, "reasoning") is None, "")
check("用例11c: derived卡 display 用途 → None",
      mo.read_card_detail(derived_card, "display") is None, "")
check("用例11d: derived卡 audit 用途 → 完整detail",
      mo.read_card_detail(derived_card, "audit") == "派生detail原文", "")
check("用例11e: derived卡 dedupe 用途 → 完整detail",
      mo.read_card_detail(derived_card, "dedupe") == "派生detail原文", "")
check("用例11f: derived卡 governance 用途 → 完整detail",
      mo.read_card_detail(derived_card, "governance") == "派生detail原文", "")
# 兜底: dfcp_tag 为 JSON 字符串也应识别
derived_card_str = {"detail": "派生detail2", "dfcp_tag": '{"derived_only": true}'}
check("用例11g: dfcp_tag未反序列化(JSON串)也能过滤",
      mo.read_card_detail(derived_card_str, "search") is None, "")

# ---- 用例12: 非 derived 卡不标记, detail 正常可读 ----
st2, msg2 = mo.atomic_store(
    "普通situational卡测试摘要",
    "普通卡detail原文应正常可见",
    "normal-src-1",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
check("用例12a: 普通卡入库成功", st2 == "stored", f"st={st2}")
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
r2 = con.execute(
    "SELECT repro, dfcp_tag, detail FROM core_memory_card WHERE source_ref='normal-src-1'").fetchone()
_tags2 = json.loads(r2["dfcp_tag"]) if r2 else {}
check("用例12b: 普通卡无 derived_only 键", r2 and "derived_only" not in _tags2, str(_tags2))
check("用例12c: 普通卡 detail 保留", r2 and r2["detail"] == "普通卡detail原文应正常可见", "")
con.close()
normal_card = {"detail": "普通detail", "dfcp_tag": {"domain": "005-001"}}
check("用例12d: 普通卡 search 用途 → detail 原值",
      mo.read_card_detail(normal_card, "search") == "普通detail", "")

# ---- 非回归: derived 卡 detail 不再为空的连带确认(原行为是 None) ----
con = sqlite3.connect(test_db, timeout=5)
n_null = con.execute(
    "SELECT COUNT(*) FROM core_memory_card WHERE repro='derived' AND status='active' "
    "AND detail IS NULL").fetchone()[0]
print(f"  [观察项] 现存 derived active 卡中 detail 为 NULL 的历史存量: {n_null} 张"
      f"(P1 只影响新写入, 不回溯改存量)")
con.close()

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("C组 P1+R4 回归全过")