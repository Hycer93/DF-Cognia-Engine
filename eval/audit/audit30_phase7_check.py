# eval/audit/audit30_phase7_check.py — #30 段七 18:05 nightly 后观测汇总(只读)
# 用途: 18:05 nightly 执行完毕后一键汇总, 为切 on 决策提供数据
# 跑法: python eval/audit/audit30_phase7_check.py
import json
import os
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMDIR = os.path.join(HERE, "data", "memory")
LOGS = os.path.join(HERE, "logs")

print("=" * 60)
print("#30 段七观测汇总 (只读)")
print("=" * 60)
print(f"汇总时间: {datetime.now().isoformat(timespec='seconds')}")

# ---- ① gate_log 的 P0 观测 ----
print("\n① gate_log P0 观测")
gp = os.path.join(MEMDIR, "gate_log.jsonl")
if not os.path.exists(gp):
    print("  gate_log.jsonl 不存在")
else:
    recs = [json.loads(l) for l in open(gp, encoding="utf-8") if l.strip()]
    ops = Counter(r["op"] for r in recs)
    print(f"  总记录: {len(recs)} | op分布: {dict(ops)}")
    dry = [r for r in recs if r["op"] == "p0_equiv_dry"]
    skip = [r for r in recs if r["op"] == "skip_equivalent_derived"]
    slow = [r for r in recs if r["op"] == "p0_slow"]
    unav = [r for r in recs if r["op"] == "p0_unavailable"]
    print(f"  p0_equiv_dry(观测): {len(dry)} | skip(实拦): {len(skip)}")
    print(f"  p0_slow(慢调用): {len(slow)} | p0_unavailable(降级): {len(unav)}")
    if dry:
        ws = [r for r in dry if r.get("would_skip")]
        print(f"  dry中would_skip=true: {len(ws)}/{len(dry)}")
        print("  --- would_skip=true 样本(人工核是否真同义) ---")
        for r in ws[:15]:
            print(f"    {r['best_sim']:.4f} | {r['new_summary'][:50]}")
        if slow:
            els = [r["elapsed_ms"] for r in slow]
            print(f"  慢调用耗时: max={max(els):.0f}ms 均值={sum(els)/len(els):.0f}ms")
    # 日期分布(看是否含18:05当日新增)
    print(f"  日期分布: {dict(Counter(r['ts'][:10] for r in recs))}")

# ---- ② E4 三指标 ----
print("\n② E4 巡检三指标")
e4p = os.path.join(LOGS, "e4_maintenance.json")
if not os.path.exists(e4p):
    print("  e4_maintenance.json 不存在")
else:
    d = json.load(open(e4p, encoding="utf-8"))
    h = d.get("health", {})
    print(f"  ts: {d.get('ts')} | total_memories: {d.get('total_memories')} | ok: {d.get('ok')}")
    print(f"  skip_equiv_count:          {h.get('dedup',{}).get('skip_equiv_count')}")
    print(f"  cross_pool_warn:           {h.get('conflict',{}).get('cross_pool_warn')}")
    print(f"  active_same_summary_multi: {h.get('dedup',{}).get('active_same_summary_multi')}")
    print(f"  alerts数: {len(d.get('alerts',[]))}")
    for a in d.get("alerts", [])[:6]:
        print(f"    - {a[:70]}")

# ---- ③ 决策辅助 ----
print("\n③ 切 on 决策辅助")
print("  门槛: ① dry命中抽检全真同义(无高相似不同事实) ② active_multi不异常增长 ③ 无慢调用激增")
print("  切on: setx DF_SUMMARY_EQUIV_DEDUPE on")
print("  回退: setx DF_SUMMARY_EQUIV_DEDUPE dry-run  (秒级, 无需改码)")
print("\n" + "=" * 60)