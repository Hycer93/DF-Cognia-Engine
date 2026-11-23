# eval/audit/e30_pool_regression.py — #30 A组回归: R1 查重池口径(commit-1)
# 覆盖用例 1-4: load_active_pool 口径 / deleted 不误拦 / precedent 不误拦 / full_hash 新口径
# 隔离测试: 复制主库→临时库, 重定向 memory_organize.MEM_DIR, 注入构造行, 不碰主库。
# 跑法: python eval/audit/e30_pool_regression.py
import io
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

print("== #30 A组: R1 查重池口径回归 ==")

# ---- 隔离: 临时库 ----
tmpdir = tempfile.mkdtemp(prefix="t30_pool_")
# 必须命名为 corpus_store.db: load_active_pool/_write_card_to_db 走 MEM_DIR/corpus_store.db
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

# ---- 构造用例卡(注入临时库) ----
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row

def mk_card(iid, summary, *, status="active", tier="active", detail="d",
            repro="situational", asset_class="knowledge", source_ref="test",
            full_hash=None, dfcp_tag=None):
    con.execute(
        "INSERT OR REPLACE INTO core_memory_card "
        "(inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, "
        "summary, detail, source_ref, tier, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, NOW, full_hash or ("h" + iid[-8:]), asset_class, repro, "L1",
         json.dumps(dfcp_tag or {}, ensure_ascii=False), summary, detail,
         source_ref, tier, status))

T = {  # 测试卡 id(32位hex)
    "del":   "dd000000000000000000000000000001",  # deleted 卡
    "prec":  "dd000000000000000000000000000002",  # precedent 卡
    "act":   "dd000000000000000000000000000003",  # 正常 active 卡
}
SAME_SUMMARY = "共享摘要: 8397车机模拟空调开启信号验证流程说明"
mk_card(T["del"],  SAME_SUMMARY, status="deleted", tier="active", full_hash="hDEL00000001")
mk_card(T["prec"], SAME_SUMMARY, status="active",  tier="precedent", full_hash="hPREC0000001")
mk_card(T["act"],  "另一条无关的正常卡摘要", status="active", tier="active", full_hash="hACT00000001")
con.commit()

# ---- 用例1: load_active_pool 口径正确 ----
pool = mo.load_active_pool()
pool_ids = {e["inst_id"] for e in pool}
db_active_cnt = con.execute(
    "SELECT COUNT(*) FROM core_memory_card WHERE status='active' AND tier='active'").fetchone()[0]
check("用例1a: load_active_pool 条数 == DB(status=active∧tier=active)",
      len(pool) == db_active_cnt, f"pool={len(pool)} db={db_active_cnt}")
check("用例1b: 池含正常 active 卡", T["act"] in pool_ids, "缺失act")
check("用例1c: 池不含 deleted 卡", T["del"] not in pool_ids, "deleted 混入池")
check("用例1d: 池不含 precedent 卡", T["prec"] not in pool_ids, "precedent 混入池")
# 每卡必带比对字段
check("用例1e: 池卡字段完整(inst_id/summary/full_hash/dfcp_tag)",
      all(all(k in e for k in ("inst_id", "summary", "full_hash", "dfcp_tag")) for e in pool),
      "字段缺失")
con.close()

# ---- 用例2: deleted 卡不误拦(同 summary 准新卡应 stored) ----
st, msg = mo.atomic_store(
    SAME_SUMMARY + "(准新)", "新详情", "new-src-1",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
check("用例2: deleted 同summary卡不误拦(准新卡 stored)", st == "stored", f"st={st} msg={msg}")

# ---- 用例3: precedent 卡不误拦 ----
# 注意: 用例2 已入库一条同 summary 的 active 卡, 会进入池 → 用例3 换个 summary 构 precedent
con = sqlite3.connect(test_db, timeout=5)
mk_card("dd000000000000000000000000000004", "precedent专用摘要: 某技术结论旧版本",
        status="active", tier="precedent", full_hash="hPREC0000002")
con.commit(); con.close()
st3, msg3 = mo.atomic_store(
    "precedent专用摘要: 某技术结论旧版本", "新详情3", "new-src-3",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
check("用例3: precedent 同summary卡不误拦(准新卡 stored)", st3 == "stored", f"st={st3} msg={msg3}")

# ---- 用例4: full_hash 查重口径(纯读断言, 不受 DB UNIQUE 约束干扰) ----
# R1 核心: full_hash 查重读 active 池(message_organize.load_active_pool),
# 而非 jsonl 全量。deleted/precedent 卡的 full_hash 不应出现在查重池中。
con = sqlite3.connect(test_db, timeout=5)
uniq_summary = "full_hash新口径验证专用摘要"
uniq_detail = "专属详情XYZ"
uniq_src = "uniq-src"
uniq_fh = mo._full_hash(uniq_summary, uniq_detail, uniq_src)
mk_card("dd000000000000000000000000000005", uniq_summary, status="deleted",
        tier="active", detail=uniq_detail, source_ref=uniq_src, full_hash=uniq_fh)
con.commit(); con.close()

pool = mo.load_active_pool()
pool_hashes = {e["full_hash"] for e in pool}
check("用例4a: deleted 卡的 full_hash 不在查重池",
      uniq_fh not in pool_hashes, "deleted 的 fh 混入查重池")
check("用例4b: active 池查重不命中 deleted 的 fh(改造前读jsonl会命中)",
      not any(e.get("full_hash") == uniq_fh for e in pool),
      "查重仍会命中 deleted 副本")
# 反证: active 池内真实存在的 full_hash 必须命中(查重有效)
active_fh = con_fh = mo._full_hash(uniq_summary + "ACT", "d", "s")
con = sqlite3.connect(test_db, timeout=5)
mk_card("dd000000000000000000000000000006", uniq_summary + "ACT", status="active",
        tier="active", detail="d", source_ref="s", full_hash=con_fh)
con.commit(); con.close()
pool = mo.load_active_pool()
check("用例4c: active 池内真实 full_hash 正常命中(查重有效)",
      any(e.get("full_hash") == con_fh for e in pool),
      "active 卡 fh 未命中")
# 观察项登记(不 assert 失败, 仅打印): DB full_hash UNIQUE + INSERT OR IGNORE 的既有边界
con = sqlite3.connect(test_db, timeout=5)
dup_cnt = con.execute(
    "SELECT COUNT(*) FROM core_memory_card WHERE full_hash=?", (uniq_fh,)).fetchone()[0]
print(f"  [观察项] deleted 卡 full_hash 在 DB 中仍占 UNIQUE 约束(既有行为,"
      f" 与 R1 无关): 同 fh 行数={dup_cnt}(=1 即 deleted 仍占用)")
con.close()

# 清理
shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("A组 R1 查重池口径回归全过")