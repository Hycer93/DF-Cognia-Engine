# eval/audit/e44_detail_guard_regression.py — #44 detail 质量护栏回归
# 覆盖: ①_detail_quality_issue 检测准确性 ②atomic_store 只警告不拒收 ③绿档 detail 去模板化
# 隔离测试: 复制主库→临时库, 重定向 MEM_DIR, 不碰主库。
# 跑法: python eval/audit/e44_detail_guard_regression.py
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
NOW = datetime.now().isoformat(timespec="seconds")

print("== #44 detail 质量护栏回归 ==")

tmpdir = tempfile.mkdtemp(prefix="t44_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")
mo.SUMMARY_EQUIV_MODE = "off"   # 关闭 P0 查重, 避免干扰

# ---- 用例1: _detail_quality_issue 检测准确 ----
det = mo._detail_quality_issue
check("用例1a: 空 detail → empty", det("") == "empty" and det(None) == "empty" and det("  ") == "empty")
check("用例1b: 绿档模板 → green_template",
      det("绿档自动立卡(memory_gate 0f2f4458双层写入规约); 素材步: x; 档案动词: y") == "green_template")
check("用例1c: 纯ASCII短占位 → ascii_placeholder",
      det("d1") == "ascii_placeholder" and det("content") == "ascii_placeholder")
check("用例1d: 纯数字 → numeric_only", det("123") == "numeric_only")
check("用例1e: 合法中文短详情 → 不判问题",
      det("双暮暮对峙完整现场，含挂单去留分析。") == "")
check("用例1f: 合法中文中等详情 → 不判问题",
      det("详情层：经济学·宏观指标·多标的情报扫描") == "")
check("用例1g: 长ASCII(>=10)不误判", det("this_is_a_long_detail_text") == "")

# ---- 用例2: atomic_store 遇脏 detail 只警告不拒收 ----
con = sqlite3.connect(test_db, timeout=5); con.row_factory = sqlite3.Row
con.execute("DELETE FROM core_memory_card"); con.commit(); con.close()

gate_log = os.path.join(tmpdir, "gate_log.jsonl")
if os.path.exists(gate_log):
    os.remove(gate_log)

# 故意用占位 detail "d1" 入库
TAGS = {"domain": "005-001", "behavior": "Ht", "relation": "C", "confidence": "L1"}
st, msg = mo.atomic_store("用例2测试摘要: 这是一条用于验证detail护栏的完整业务摘要内容", "d1",
                          "e44-test", dict(TAGS))
check("用例2a: 脏 detail 仍 stored(不拒收)", st == "stored", f"status={st} msg={msg}")

# 检查 gate_log 有 warn_detail_quality
warns = []
if os.path.exists(gate_log):
    warns = [json.loads(l) for l in open(gate_log, encoding="utf-8")
             if l.strip() and json.loads(l).get("op") == "warn_detail_quality"]
check("用例2b: gate_log 记录 warn_detail_quality", len(warns) >= 1, f"warns={warns}")
check("用例2c: warning 含 issue/issue=ascii_placeholder",
      warns and warns[0].get("issue") == "ascii_placeholder", str(warns[:1]))

# ---- 用例3: 合法 detail 不入 warning ----
if os.path.exists(gate_log):
    os.remove(gate_log)
st2, _ = mo.atomic_store("用例3测试摘要: 另一条验证合法详情不报警的完整业务内容描述", 
                          "这是一段完全合法的详细说明内容，长度充足且有实义，不应触发任何警告信号。",
                          "e44-test2", dict(TAGS))
warns2 = []
if os.path.exists(gate_log):
    warns2 = [json.loads(l) for l in open(gate_log, encoding="utf-8")
              if l.strip() and json.loads(l).get("op") == "warn_detail_quality"]
check("用例3a: 合法 detail 仍 stored", st2 == "stored", f"status={st2}")
check("用例3b: 合法 detail 不产生 warn", len(warns2) == 0, f"warns={warns2}")

# ---- 用例4: 绿档 detail 生成去模板化 ----
try:
    sys.path.insert(0, os.path.join(HERE, "cgraph"))
    import green_card_writer as gcw
    x = {"proposed_action": "写入核心记忆卡", "evidence_step": "node_Ch", "gist": "绿档立卡要点内容"}
    d = gcw._green_detail(x)
    check("用例4a: 绿档 detail 不含旧模板前缀", "绿档自动立卡(memory_gate 0f2f4458" not in d, repr(d))
    check("用例4b: 绿档 detail 含实义内容(proposed_action)", "写入核心记忆卡" in d, repr(d))
    check("用例4c: 绿档 detail 长度>0", len(d) > 0, repr(d))
    # 空字段兜底
    d2 = gcw._green_detail({"proposed_action": "", "evidence_step": "", "gist": ""})
    check("用例4d: 全空字段有兜底文案不崩溃", len(d2) > 0, repr(d2))
except Exception as e:
    check("用例4: 绿档 detail 生成可加载", False, f"exc={repr(e)[:150]}")

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("#44 detail 质量护栏回归全过")