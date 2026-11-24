# eval/audit/e30_p0_regression.py — #30 B组回归: P0语义查重(commit-3)
# 覆盖用例 5-9: 8空壳召回 / PAIR6反例 / off零行为 / dry-run只记不拦 / on真拦
# 隔离测试: 复制主库→临时库, 重定向MEM_DIR, 按需 reload 切换三态开关。
# 跑法: python eval/audit/e30_p0_regression.py
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

print("== #30 B组: P0 语义查重回归 ==")

tmpdir = tempfile.mkdtemp(prefix="t30_p0_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

# 用 isolation_level 默认写, 手动 commit
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row

def mk_card(iid, summary, *, status="active", tier="active", detail="d",
            repro="situational", full_hash=None):
    con.execute(
        "INSERT OR REPLACE INTO core_memory_card "
        "(inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, "
        "summary, detail, source_ref, tier, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, NOW, full_hash or ("h" + iid[-8:]), "knowledge", repro, "L1",
         "{}", summary, detail, "test", tier, status))

def reload_mode(mode):
    os.environ["DF_SUMMARY_EQUIV_DEDUPE"] = mode
    import memory_organize as mo
    importlib.reload(mo)
    mo.MEM_DIR = tmpdir
    mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")
    mo._pool_vec_cache["key"] = None      # 清池向量缓存(reload后模块级变量重置, 双保险)
    import embed_semantic as E
    E._cache.clear()
    return mo

def gate_log_ops():
    p = os.path.join(tmpdir, "gate_log.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l)["op"] for l in open(p, encoding="utf-8") if l.strip()]

# ===== 准备正样本: 取 #29 的 8 组(空壳summary vs 正本summary) =====
# 直接从主库取 8 组: 用 demote 脚本 PLAN
import importlib.util
spec = importlib.util.spec_from_file_location("dss", os.path.join(HERE, "eval", "audit", "demote_superseded_shells.py"))
dss = importlib.util.module_from_spec(spec)
# 避免执行其 main: 直接读 PLAN 常量(模块级)
spec.loader.exec_module(dss)
PLAN = dss.PLAN

shell_cases = []  # (shell_summary, zhengben_summary)
for row in PLAN:
    sp, ap = row[1], row[2]
    sc = con.execute("SELECT summary FROM core_memory_card WHERE inst_id LIKE ?", (sp + "%",)).fetchone()
    ac = con.execute("SELECT summary FROM core_memory_card WHERE inst_id LIKE ?", (ap + "%",)).fetchone()
    if sc and ac and sc["summary"] and ac["summary"]:
        shell_cases.append((sp, ap, sc["summary"], ac["summary"]))
print(f"  (正样本源: #29 PLAN {len(PLAN)}组, 可用 {len(shell_cases)}组)")

# ===== 用例5: P0 正样本召回 8/8 (on态) =====
# 策略: 用干净小库只装 8 个正本(独立库避免主库280条干扰), 逐条空壳summary作输入
con.close()   # 先关旧的 test_db 连接, 避免锁
small_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, small_db)   # 重新复制干净库
con = sqlite3.connect(small_db, timeout=5); con.row_factory = sqlite3.Row
con.execute("DELETE FROM core_memory_card")
# 装 8 个正本(真实 full_hash/summary/detail)
_zb_rows = []
for sp, ap, ss, as_ in shell_cases:
    zb = con.execute("SELECT summary FROM core_memory_card WHERE inst_id LIKE ?", (ap + "%",)).fetchone()
    # 正本可能已被 DELETE 清掉, 从主库取
    _m = sqlite3.connect(MAIN_DB); _m.row_factory = sqlite3.Row
    zbr = _m.execute("SELECT full_hash,asset_class,repro,dfcp_tag,summary,detail,source_ref FROM core_memory_card WHERE inst_id LIKE ?", (ap + "%",)).fetchone()
    _m.close()
    _zb_rows.append((ap, dict(zbr) if zbr else None))
for ap, zbr in _zb_rows:
    if not zbr:
        continue
    con.execute(
        "INSERT OR REPLACE INTO core_memory_card "
        "(inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, "
        "summary, detail, source_ref, tier, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ap + "0" * (32 - len(ap)), NOW, zbr["full_hash"], zbr["asset_class"] or "corpus",
         zbr["repro"] or "flash", "L1", zbr["dfcp_tag"] or "{}", zbr["summary"],
         zbr["detail"] or "正本detail", zbr.get("source_ref") or "zb", "active", "active"))
con.commit()
n_zb = con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
print(f"  (干净小库正本数: {n_zb})")
con.close()   # 关闭写连接, 让 mo 读到最新

mo = reload_mode("on")
hit_cnt = 0
sims = []
for sp, ap, ss, as_ in shell_cases:
    st, msg = mo.atomic_store(ss, "空壳detail", "shell-" + sp,
        {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
    if st == "replica" and "语义等价" in msg:
        hit_cnt += 1
        try:
            sims.append(float(msg.split("sim=")[1]))
        except (IndexError, ValueError):
            pass
check(f"用例5: P0 正样本召回 {hit_cnt}/{len(shell_cases)} 全拦",
      hit_cnt == len(shell_cases) and len(shell_cases) == 8, f"hit={hit_cnt}/{len(shell_cases)}")
check("用例5b: 召回样本 best_sim 均 ≥0.95",
      sims and min(sims) >= 0.95, f"sims={sims}")

# 用例6起复用该连接
con = sqlite3.connect(small_db, timeout=5); con.row_factory = sqlite3.Row

# ===== 用例6: PAIR6 反例不被拦 (on态, 不同事实0.905) =====
# PAIR6: ef590c15(归因流动性拐点) vs d7d25873(归因央行购金) — 不同事实
p6_a = "复盘结论：非农超预期金价不涨反跌，归因流动性拐点，市场已price in。"
p6_b = "复盘结论：非农超预期金价不涨反跌，归因央行购金支撑对冲了流动性冲击。"
mk_card("ee000000000000000000000000000001", p6_a, repro="situational", detail="d1")
con.commit()
mo = reload_mode("on")
st6, msg6 = mo.atomic_store(p6_b, "d2", "pair6-src",
    {"domain": "001-004", "behavior": "Jw", "relation": "P", "confidence": "L2"})
check("用例6: PAIR6 不同事实(0.905)不被拦(stored)", st6 == "stored", f"st={st6} msg={msg6}")

# ===== 用例7: off 态零行为 =====
mo = reload_mode("off")
# 清 gate_log
gp = os.path.join(tmpdir, "gate_log.jsonl")
if os.path.exists(gp):
    os.remove(gp)
# 用与池中正本高度相似的内容(0.99+), off 态应无 P0 日志且正常入库
_pool_sum = con.execute("SELECT summary FROM core_memory_card LIMIT 1").fetchone()["summary"]
st7, msg7 = mo.atomic_store(_pool_sum + "微改", "d", "off-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
ops7 = gate_log_ops()
check("用例7a: off 态无 P0 日志(skip/dry 均无)",
      "skip_equivalent_derived" not in ops7 and "p0_equiv_dry" not in ops7, f"ops={ops7}")
check("用例7b: off 态行为退回改造前(高分卡正常入库)", st7 == "stored", f"st={st7}")

# ===== 用例8: dry-run 只记不拦 =====
mo = reload_mode("dry-run")
if os.path.exists(gp):
    os.remove(gp)
_pool_sum = con.execute("SELECT summary FROM core_memory_card LIMIT 1").fetchone()["summary"]
st8, msg8 = mo.atomic_store(_pool_sum + "微调v2", "d", "dry-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
ops8 = gate_log_ops()
dry_recs = [json.loads(l) for l in open(gp, encoding="utf-8")
            if l.strip() and json.loads(l)["op"] == "p0_equiv_dry"] if os.path.exists(gp) else []
check("用例8a: dry-run 命中仍入库(stored)", st8 == "stored", f"st={st8}")
check("用例8b: dry-run 写 p0_equiv_dry 且 would_skip=true",
      bool(dry_recs) and dry_recs[0].get("would_skip") is True,
      f"dry={dry_recs[:1]}")
check("用例8c: dry-run 不写 skip_equivalent_derived",
      "skip_equivalent_derived" not in ops8, f"ops={ops8}")

# ===== 用例9: on 态真拦, DB 无新行 =====
mo = reload_mode("on")
if os.path.exists(gp):
    os.remove(gp)
cnt_before = con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
_pool_sum = con.execute("SELECT summary FROM core_memory_card LIMIT 1").fetchone()["summary"]
st9, msg9 = mo.atomic_store(_pool_sum + "微调v3", "d", "on-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
cnt_after = con.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
ops9 = gate_log_ops()
check("用例9a: on 态命中真拦(replica)", st9 == "replica" and "语义等价" in msg9, f"st={st9} msg={msg9}")
check("用例9b: on 态 DB 无新行", cnt_after == cnt_before, f"before={cnt_before} after={cnt_after}")
check("用例9c: on 态写 skip_equivalent_derived", "skip_equivalent_derived" in ops9, f"ops={ops9}")

# ===== 用例10: origin 字段打标（#10 2026-11-24 口径修复）=====
# 10a: 默认 origin=business
mo = reload_mode("dry-run")
if os.path.exists(gp):
    os.remove(gp)
_pool_sum = con.execute("SELECT summary FROM core_memory_card LIMIT 1").fetchone()["summary"]
mo.atomic_store(_pool_sum + "微调v4biz", "d", "biz-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
_dry_biz = [json.loads(l) for l in open(gp, encoding="utf-8")
            if l.strip() and json.loads(l)["op"] == "p0_equiv_dry"] if os.path.exists(gp) else []
check("用例10a: 默认 origin=business",
      bool(_dry_biz) and _dry_biz[0].get("origin") == "business",
      f"rec={_dry_biz[:1]}")

# 10b: 显式 origin=script-reingest 透传打标
if os.path.exists(gp):
    os.remove(gp)
mo.atomic_store(_pool_sum + "微调v4scr", "d", "scr-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"},
    origin="script-reingest")
_dry_scr = [json.loads(l) for l in open(gp, encoding="utf-8")
            if l.strip() and json.loads(l)["op"] == "p0_equiv_dry"] if os.path.exists(gp) else []
check("用例10b: 显式 origin=script-reingest 透传",
      bool(_dry_scr) and _dry_scr[0].get("origin") == "script-reingest",
      f"rec={_dry_scr[:1]}")

# ===== 用例11: clean/raw 统计过滤（#11 2026-11-24）=====
# 构造混合日志：2条 business would_skip=true + 1条 script-reingest would_skip=true + 1条 business would_skip=false
_mixed = [
    {"op": "p0_equiv_dry", "would_skip": True,  "origin": "business"},
    {"op": "p0_equiv_dry", "would_skip": True,  "origin": "business"},
    {"op": "p0_equiv_dry", "would_skip": True,  "origin": "script-reingest"},
    {"op": "p0_equiv_dry", "would_skip": False, "origin": "business"},
    {"op": "skip_equivalent_derived", "would_skip": True, "origin": "business"},  # 旧on态无would_skip字段,这条有
]
_raw = len(_mixed)
_clean = sum(1 for r in _mixed
             if r.get("would_skip") is True and r.get("origin", "business") == "business")
check("用例11a: raw 统计=全部P0记录", _raw == 5, f"raw={_raw}")
check("用例11b: clean 仅计 would_skip=true AND business", _clean == 3, f"clean={_clean}")
# 旧日志无 origin 字段 → 保守视为 business
_legacy = [{"op": "p0_equiv_dry", "would_skip": True}]  # 无origin
_clean_legacy = sum(1 for r in _legacy
                    if r.get("would_skip") is True and r.get("origin", "business") == "business")
check("用例11c: 旧日志无origin视为business(保守计入clean)", _clean_legacy == 1, f"clean={_clean_legacy}")

# ===== 阈值静态断言 =====
import memory_organize as _mo
check("静态断言: SUMMARY_EQUIV_THRESHOLD == 0.95", _mo.SUMMARY_EQUIV_THRESHOLD == 0.95,
      str(_mo.SUMMARY_EQUIV_THRESHOLD))

# ===== 用例12: 护栏1 equiv_intercepts 全量留痕（#30 2026-11-24 dry-run/on 均写）=====
_eq_path = os.path.join(tmpdir, "equiv_intercepts.jsonl")
# 12a: dry-run 模式命中也写
mo = reload_mode("dry-run")
if os.path.exists(_eq_path):
    os.remove(_eq_path)
_pool_sum = con.execute("SELECT summary FROM core_memory_card LIMIT 1").fetchone()["summary"]
_full_detail = "完整未截断的detail内容-用于护栏验证-不应被截断"
_full_src = "guard-src-full-ref"
mo.atomic_store(_pool_sum + "微调guard-dry", _full_detail, _full_src,
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
_eq_dry = [json.loads(l) for l in open(_eq_path, encoding="utf-8") if l.strip()] if os.path.exists(_eq_path) else []
check("用例12a: dry-run 命中写 equiv_intercepts(1条)",
      len(_eq_dry) == 1, f"n={len(_eq_dry)}")
check("用例12b: dry-run 拦截日志含完整未截断 detail/source_ref/mode",
      bool(_eq_dry) and _eq_dry[0].get("detail") == _full_detail
      and _eq_dry[0].get("source_ref") == _full_src
      and _eq_dry[0].get("mode") == "dry-run"
      and _eq_dry[0].get("best_sim", 0) >= 0.95,
      f"rec={_eq_dry[:1]}")

# 12c: on 模式命中也写
mo = reload_mode("on")
if os.path.exists(_eq_path):
    os.remove(_eq_path)
mo.atomic_store(_pool_sum + "微调guard-on", _full_detail + "-on", "guard-src-on",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
_eq_on = [json.loads(l) for l in open(_eq_path, encoding="utf-8") if l.strip()] if os.path.exists(_eq_path) else []
check("用例12c: on 命中写 equiv_intercepts 且 mode=on",
      len(_eq_on) == 1 and _eq_on[0].get("mode") == "on"
      and _eq_on[0].get("detail") == _full_detail + "-on",
      f"rec={_eq_on[:1]}")

# 12d: 未命中(低相似)不写日志
if os.path.exists(_eq_path):
    os.remove(_eq_path)
mo.atomic_store("完全无关的全新内容：今天天气晴朗适合散步购物看电影", "无关detail", "no-match-src",
    {"domain": "005-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
_eq_nomatch = [json.loads(l) for l in open(_eq_path, encoding="utf-8") if l.strip()] if os.path.exists(_eq_path) else []
check("用例12d: 未命中(低相似)不写 equiv_intercepts", len(_eq_nomatch) == 0, f"n={len(_eq_nomatch)}")

con.close()
shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("B组 P0 语义查重回归全过")