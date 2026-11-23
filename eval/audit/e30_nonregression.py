# eval/audit/e30_nonregression.py — #30 E组非回归校验
# 覆盖用例 15-16: 正常non-derived逻辑不变 / 路径B行为逐行不变
# 跑法: python eval/audit/e30_nonregression.py
import json
import os
import shutil
import sqlite3
import subprocess
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

print("== #30 E组: 非回归校验 ==")

# ---- 用例16: 路径B green_card_writer 零代码变更(git diff) ----
r = subprocess.run(["git", "diff", "HEAD~5", "HEAD", "--", "cgraph/green_card_writer.py"],
                   cwd=HERE, capture_output=True, text=True)
check("用例16a: 路径B green_card_writer.py 近5commit零变更",
      r.stdout.strip() == "", f"diff非空: {r.stdout[:200]}")
# 也确认整个 cgraph/ 未因 #30 改动(仅我们改 memory/ eval/)
r2 = subprocess.run(["git", "diff", "HEAD~5", "HEAD", "--stat", "--", "cgraph/"],
                    cwd=HERE, capture_output=True, text=True)
check("用例16b: cgraph/ 目录零变更(路径B所在)", r2.stdout.strip() == "",
      f"cgraph变更: {r2.stdout[:200]}")

# ---- 用例15: 正常 non-derived 逻辑不变 ----
MAIN_DB = os.path.join(HERE, "data", "memory", "corpus_store.db")
tmpdir = tempfile.mkdtemp(prefix="t30_nr_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

os.environ["DF_SUMMARY_EQUIV_DEDUPE"] = "off"   # 非回归在 off 态(改造前语义)
import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

# 正常 situational 卡入库: 应与改造前一致(stored + 各字段正常)
st, msg = mo.atomic_store(
    "非回归测试: 正常的situational记忆卡摘要内容说明",
    "正常的detail内容", "nr-src-1",
    {"domain": "001-001", "behavior": "Ht", "relation": "H", "confidence": "L1",
     "memory_class": "skill_refine"})
check("用例15a: 正常 situational 卡 stored", st == "stored", f"st={st}")

con = sqlite3.connect(test_db, timeout=5); con.row_factory = sqlite3.Row
card = con.execute("SELECT * FROM core_memory_card WHERE source_ref='nr-src-1'").fetchone()
check("用例15b: 正常卡 detail 保留", card and card["detail"] == "正常的detail内容", "")
check("用例15c: 正常卡无 derived_only 标记",
      card and "derived_only" not in json.loads(card["dfcp_tag"]), "")
check("用例15d: 正常卡 repro=situational", card and card["repro"] == "situational", "")
check("用例15e: 正常卡 status=active/tier=active",
      card and card["status"] == "active" and card["tier"] == "active", "")
# full_hash 查重仍有效(off态下)
st2, msg2 = mo.atomic_store(
    "非回归测试: 正常的situational记忆卡摘要内容说明",
    "正常的detail内容", "nr-src-1",
    {"domain": "001-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
check("用例15f: off态 full_hash 精确查重仍有效(replica)", st2 == "replica", f"st={st2}")
# flash 卡(domain 005)也应正常
st3, msg3 = mo.atomic_store(
    "非回归测试: 005域flash卡摘要内容", "d3", "nr-src-3",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L0"})
check("用例15g: 005域卡 flash 判定不变", st3 == "stored" and "flash" in msg3, f"msg={msg3}")
con.close()

# ---- 用例16c: 路径B commit_green_cards 可正常导入调用(行为不变证明) ----
sys.path.insert(0, os.path.join(HERE, "cgraph"))
try:
    import green_card_writer
    check("用例16c: green_card_writer 可导入(commit_green_cards存在)",
          callable(getattr(green_card_writer, "commit_green_cards", None)), "")
except Exception as e:
    check("用例16c: green_card_writer 可导入", False, repr(e)[:150])

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("E组 非回归校验全过")