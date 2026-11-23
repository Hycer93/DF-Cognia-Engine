# eval/audit/e30_p2_p3_regression.py — #30 D组回归: P2跨池告警(commit-4) + P3 E4指标(commit-5)
# 覆盖用例 13-14。隔离测试: 复制主库→临时库, 重定向MEM_DIR, 不碰主库。
# 跑法: python eval/audit/e30_p2_p3_regression.py
import importlib
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

print("== #30 D组: P2跨池告警 + P3 E4指标回归 ==")

tmpdir = tempfile.mkdtemp(prefix="t30_d_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

os.environ["DF_SUMMARY_EQUIV_DEDUPE"] = "on"
import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
con.execute("DELETE FROM core_memory_card")   # 干净库

def mk_card(iid, summary, *, asset_class="runtime", status="active", tier="active", detail="d"):
    con.execute(
        "INSERT OR REPLACE INTO core_memory_card "
        "(inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, "
        "summary, detail, source_ref, tier, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, NOW, "h" + iid[-8:], asset_class, "situational", "L1", "{}",
         summary, detail, "test", tier, status))

# ---- 用例13: P2 corpus↔runtime 跨池告警 ----
# 布局: 一条 runtime 卡在池, 一条 corpus 卡 summary 与之等价
EQ_SUMMARY = "跨池等价测试: 8397车机vhal_test部署失败需重跑脚本"
mk_card("aa000000000000000000000000000001", EQ_SUMMARY,
        asset_class="runtime", detail="runtime详情")
con.commit()

import memory_relations as mr
# 构造 corpus 卡 dict 直接调 _cross_pool_warn(绕过入库, 精准测旁路)
corpus_card = {"inst_id": "cc000000000000000000000000001", "summary": EQ_SUMMARY,
               "asset_class": "corpus"}
gate_p = os.path.join(tmpdir, "gate_log.jsonl")
if os.path.exists(gate_p):
    os.remove(gate_p)
mr._cross_pool_warn(corpus_card)
ops = []
if os.path.exists(gate_p):
    ops = [json.loads(l) for l in open(gate_p, encoding="utf-8") if l.strip()]
warns = [o for o in ops if o["op"] == "warn_corpus_runtime_equivalent"]
check("用例13a: corpus↔runtime 等价触发 warn_corpus_runtime_equivalent", bool(warns), f"ops={ops}")
check("用例13b: 告警含 corpus_inst/runtime_inst/best_sim",
      warns and all(k in warns[0] for k in ("corpus_inst", "runtime_inst", "best_sim")),
      str(warns[:1]))
check("用例13c: 告警 best_sim ≥0.95", warns and warns[0]["best_sim"] >= 0.95,
      f"sim={warns[0]['best_sim'] if warns else None}")
# 反证: 不相似的 corpus 卡不告警
corpus_card2 = {"inst_id": "cc000000000000000000000000002",
                "summary": "完全无关的另一主题: 财经新闻摘要与天气预报", "asset_class": "corpus"}
if os.path.exists(gate_p):
    os.remove(gate_p)
mr._cross_pool_warn(corpus_card2)
warns2 = [json.loads(l) for l in open(gate_p, encoding="utf-8")
          if l.strip() and json.loads(l)["op"] == "warn_corpus_runtime_equivalent"] if os.path.exists(gate_p) else []
check("用例13d: 不相似 corpus 卡不误告警", not warns2, str(warns2[:1]))
# 反证: corpus 卡 classify_and_link 仍硬跳过(语义不变)
# classify_and_link 走 load_memories()(jsonl), 此处 monkeypatch 注入 corpus 卡
_orig_lm = mr.load_memories
mr.load_memories = lambda: [{"inst_id": "cc000000000000000000000000003",
                             "asset_class": "corpus", "summary": EQ_SUMMARY,
                             "dfcp_tag": {"domain": "005-001"}}]
try:
    r = mr.classify_and_link("cc000000000000000000000000003")
finally:
    mr.load_memories = _orig_lm
check("用例13e: corpus 卡仍返回 skipped_protected(语义不变)",
      r.get("status") == "skipped_protected", str(r))

# ---- 用例14: P3 E4 三指标 ----
# 触发一条 P0 拦截(生成 skip 日志) + 一条跨池告警, 再跑 E4 _memory_health
con = sqlite3.connect(test_db, timeout=5); con.row_factory = sqlite3.Row
con.execute("DELETE FROM core_memory_card")
E4_ZB = "E4指标测试正本摘要: 8397车机vhal_test部署失败需重跑部署脚本修复vendor分区"
mk_card("ee000000000000000000000000000001", E4_ZB, detail="正本")
con.commit(); con.close()
# P0 拦截一条等价卡(长文本 sim≈0.979>0.95) → skip_equivalent_derived
mo.atomic_store("E4指标测试正本摘要: 8397车机vhal_test部署失败需要重新运行部署脚本以修复vendor分区",
    "d", "e4-src", {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
# 跨池告警(已有 gate_log 逻辑)
corpus_card3 = {"inst_id": "ee000000000000000000000000000002",
                "summary": E4_ZB, "asset_class": "corpus"}
mr._cross_pool_warn(corpus_card3)

# 加载 E4 模块(其 db_dir 会跟随 mo.MEM_DIR 重定向)
sys.path.insert(0, os.path.join(HERE, "eval", "core"))
try:
    import e4_maintenance_patrol as e4
    # mems 参数: 从临时库读 active 卡
    _c = sqlite3.connect(test_db); _c.row_factory = sqlite3.Row
    mems = [dict(x) for x in _c.execute("SELECT * FROM core_memory_card WHERE status='active'")]
    _c.close()
    health = e4._memory_health(mems)
    dedup = health.get("dedup", {}) if isinstance(health, dict) else {}
    conflict = health.get("conflict", {}) if isinstance(health, dict) else {}
    check("用例14a: E4 health.dedup 含 skip_equiv_count",
          "skip_equiv_count" in dedup, f"dedup keys={list(dedup.keys())}")
    check("用例14b: E4 health.conflict 含 cross_pool_warn",
          "cross_pool_warn" in conflict, f"conflict keys={list(conflict.keys())}")
    check("用例14c: E4 health.dedup 含 active_same_summary_multi",
          "active_same_summary_multi" in dedup, f"dedup keys={list(dedup.keys())}")
    check("用例14d: skip_equiv_count ≥1(捕获P0拦截)",
          (dedup.get("skip_equiv_count") or 0) >= 1, f"val={dedup.get('skip_equiv_count')}")
    check("用例14e: cross_pool_warn ≥1(捕获跨池告警)",
          (conflict.get("cross_pool_warn") or 0) >= 1, f"val={conflict.get('cross_pool_warn')}")
except Exception as e:
    check("用例14: E4 模块可加载并含三指标", False, f"exc={repr(e)[:150]}")

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("D组 P2+P3 回归全过")