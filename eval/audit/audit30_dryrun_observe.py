# eval/audit/audit30_dryrun_observe.py — #30 段六 dry-run 灰度观测(影子重放)
# 不污染现网库: 复制到 tmpdir, 用池中每条真实 summary 作为"模拟新写入"
# 走 P0 dry-run 比对(排除自身), 采集命中分布/分数/慢调用/E4三指标/单条耗时
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))

MAIN_DIR = os.path.join(HERE, "data", "memory")
tmpdir = tempfile.mkdtemp(prefix="t30_obs_")
for f in ("corpus_store.db",):
    src = os.path.join(MAIN_DIR, f)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(tmpdir, f))

os.environ["DF_SUMMARY_EQUIV_DEDUPE"] = "dry-run"
import memory_organize as mo
mo.MEM_DIR = tmpdir
mo.MEMORIES_JSONL = os.path.join(tmpdir, "memories.jsonl")

print("== #30 段六 dry-run 灰度观测(影子重放) ==")
pool = mo.load_active_pool()
print(f"真实池规模: {len(pool)}")

# 冷启动矩阵
t0 = time.perf_counter()
mat, ids = mo._get_pool_matrix(pool)
t_cold = (time.perf_counter() - t0) * 1000
print(f"冷启动(池+矩阵构建): {t_cold:.1f}ms  matrix={mat.shape}")

# 影子重放: 每条 summary vs 池(排除自身), 记录 best_sim
import numpy as np
per_item_ms = []
sims_all = []
hits = []          # best_sim >= 0.95 的 (inst, best_sim, best_inst)
slow_cnt = 0
for e in pool:
    q = mo_read = None
    import embed_semantic as E
    tv = time.perf_counter()
    qv = E.embed_cached(e.get("summary") or "")
    sims = mat @ qv
    # 排除自身
    self_idx = ids.index(e["inst_id"]) if e["inst_id"] in ids else -1
    if self_idx >= 0:
        sims[self_idx] = -1.0
    bi = int(sims.argmax())
    bs = float(sims[bi])
    el = (time.perf_counter() - tv) * 1000
    per_item_ms.append(el)
    sims_all.append(bs)
    if bs >= 0.95:
        hits.append((e["inst_id"], bs, ids[bi]))
    if el > 300:
        slow_cnt += 1

n = len(sims_all)
print(f"\n--- 影子重放统计 (n={n}) ---")
print(f"单条比对耗时: 均值={statistics.mean(per_item_ms):.2f}ms "
      f"中位={statistics.median(per_item_ms):.2f}ms "
      f"P95={sorted(per_item_ms)[int(n*0.95)]:.2f}ms "
      f"max={max(per_item_ms):.2f}ms")
print(f"慢调用(>300ms)占比: {slow_cnt}/{n} = {slow_cnt/n*100:.1f}%")
print(f"best_sim 分布: min={min(sims_all):.3f} P50={statistics.median(sims_all):.3f} "
      f"P95={sorted(sims_all)[int(n*0.95)]:.3f} max={max(sims_all):.3f}")
print(f">=0.95 命中数(would_skip): {len(hits)} / {n} = {len(hits)/n*100:.1f}%")
# 分数段分布
bands = {"[0.99,1.0]": 0, "[0.95,0.99)": 0, "[0.90,0.95)": 0, "[0.80,0.90)": 0, "<0.80": 0}
for s in sims_all:
    if s >= 0.99: bands["[0.99,1.0]"] += 1
    elif s >= 0.95: bands["[0.95,0.99)"] += 1
    elif s >= 0.90: bands["[0.90,0.95)"] += 1
    elif s >= 0.80: bands["[0.80,0.90)"] += 1
    else: bands["<0.80"] += 1
print("分数段分布:", json.dumps(bands, ensure_ascii=False))

print(f"\n--- 高相似命中样本(would_skip=true, 需人工核是否真同义) 前10 ---")
for inst, s, bid in sorted(hits, key=lambda x: -x[1])[:10]:
    summ = next((e["summary"][:50] for e in pool if e["inst_id"] == inst), "")
    best_summ = next((e["summary"][:50] for e in pool if e["inst_id"] == bid), "")
    print(f"  {s:.4f} | [{summ}] ≈ [{best_summ}]")

# E4 三指标
print(f"\n--- E4 巡检三指标(现网真实库) ---")
try:
    import e4_maintenance_patrol as e4
    conn = sqlite3.connect(os.path.join(tmpdir, "corpus_store.db"))
    conn.row_factory = sqlite3.Row
    mems = [dict(r) for r in conn.execute("SELECT * FROM core_memory_card")]
    conn.close()
    health = e4._memory_health(mems)
    print(f"  skip_equiv_count: {health['dedup'].get('skip_equiv_count')}")
    print(f"  cross_pool_warn: {health['conflict'].get('cross_pool_warn')}")
    print(f"  active_same_summary_multi: {health['dedup'].get('active_same_summary_multi')}")
except Exception as ex:
    print(f"  E4 指标采集异常: {repr(ex)[:200]}")

shutil.rmtree(tmpdir, ignore_errors=True)
print("\n观测完成(现网库零污染)")