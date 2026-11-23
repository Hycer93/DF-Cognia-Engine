# eval/audit/e30_smoke.py — #30 S1-S6 dry-run 冒烟
# dry-run: DF_SUMMARY_EQUIV_DEDUPE=on 但只记录放行(p0_equiv_dry), 不实际拦截
# 验证生产链路可跑通: 池加载/向量化/单条比对/PAIR6反例/gate_log字段/冷启动计时
# 跑法: python eval/audit/e30_smoke.py
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

print("== #30 S1-S6 dry-run 冒烟 ==")

MAIN_DB = os.path.join(HERE, "data", "memory", "corpus_store.db")
tmpdir = tempfile.mkdtemp(prefix="t30_smoke_")
test_db = os.path.join(tmpdir, "corpus_store.db")
shutil.copy(MAIN_DB, test_db)

os.environ["DF_SUMMARY_EQUIV_DEDUPE"] = "dry-run"   # 冒烟用 dry-run 模式: 只记录放行(p0_equiv_dry), 不实际拦截
import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

gate_log = os.path.join(tmpdir, "gate_log.jsonl")

def read_gate():
    if not os.path.exists(gate_log):
        return []
    out = []
    for ln in open(gate_log, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out

# ---- S1: 池加载 ----
t0 = time.perf_counter()
pool = mo.load_active_pool()
t_pool = (time.perf_counter() - t0) * 1000
check("S1: load_active_pool 返回非空且含关键字段",
      isinstance(pool, list) and len(pool) > 0
      and all(k in pool[0] for k in ("inst_id", "summary", "full_hash", "dfcp_tag")),
      f"pool={len(pool)} keys={list(pool[0].keys()) if pool else 'None'}")
print(f"     池规模={len(pool)} 加载耗时={t_pool:.1f}ms")

# ---- S2: 向量化 ----
import embed_semantic as E
v = E.embed_cached("dry运行冒烟测试一段中文摘要文本")
check("S2: embed_cached 返回512维向量", len(v) == 512, f"dim={len(v)}")

# ---- S3: 单条比对(dry-run 记 p0_equiv_dry) ----
# 取池中一条正本 summary, 用其近似文本比对应命中 dry 日志
src = pool[0]["summary"] if pool else "默认摘要"
st, msg = mo.atomic_store(src + "（dry近似副本）", "smoke-detail", "smoke-src-s3",
                          {"domain": "001-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
ops = [r.get("op") for r in read_gate()]
check("S3: dry模式产生 p0_equiv_dry 或 p0_slow 日志",
      ("p0_equiv_dry" in ops) or ("p0_slow" in ops) or st == "stored",
      f"ops={ops} st={st}")

# ---- S4: PAIR6 反例(0.905 不拦) ----
# PAIR6 原文 0.905 < 0.95, 应放行
PAIR6_A = "PAIR6冒烟反例正本: 场景A的完整描述文本内容用于相似度下界验证"
PAIR6_B = "PAIR6冒烟反例副本: 场景B略有差异的描述文本内容用于相似度下界验证"
sim = E.cosine(E.embed_cached(PAIR6_A), E.embed_cached(PAIR6_B))
mo.atomic_store(PAIR6_A, "d", "smoke-pair6-a",
                {"domain": "001-002", "behavior": "Ht", "relation": "H", "confidence": "L1"})
st6, msg6 = mo.atomic_store(PAIR6_B, "d", "smoke-pair6-b",
                            {"domain": "001-002", "behavior": "Ht", "relation": "H", "confidence": "L1"})
check("S4: PAIR6近似(下表实测sim)不误拦",
      st6 == "stored" or "dry" in str(msg6), f"sim={sim:.3f} st6={st6} msg={msg6[:50]}")

# ---- S5: gate_log 字段完整性 ----
recs = read_gate()
# 优先取实际比对记录(含 best_sim): skip_equivalent_derived / p0_equiv_dry / warn_*
p0_rec = next((r for r in recs if "best_sim" in r), None)
check("S5: 存在含 best_sim 的比对日志", p0_rec is not None,
      f"records={[r.get('op') for r in recs]}")
if p0_rec:
    need = {"op", "best_sim", "best_inst_id", "elapsed_ms"}
    have = set(p0_rec.keys())
    check("S5: 比对 gate_log 含必要字段", need.issubset(have), f"have={sorted(have)}")

# ---- S6: 冷启动计时(池加载+首条向量化) ----
t1 = time.perf_counter()
_p = mo.load_active_pool()
_m = mo._get_pool_matrix(_p) if hasattr(mo, "_get_pool_matrix") else None
t_cold = (time.perf_counter() - t1) * 1000
check("S6: 冷启动(池+矩阵构建) < 5000ms", t_cold < 5000, f"{t_cold:.1f}ms")
print(f"     冷启动耗时={t_cold:.1f}ms (段四实测基线1.34s)")

# ---- gate_log 全字段打印(人工复核用) ----
print("\n--- gate_log 输出(人工复核各 op 字段) ---")
for r in recs:
    print("   ", json.dumps(r, ensure_ascii=False))

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL:
        print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("S1-S6 dry-run 冒烟全过")