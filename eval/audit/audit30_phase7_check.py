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
    _dd = h.get('dedup', {})
    print(f"  raw_skip_equiv_count:      {_dd.get('raw_skip_equiv_count', _dd.get('skip_equiv_count'))}  (原始全量,含脚本回放)")
    print(f"  clean_skip_equiv_count:    {_dd.get('clean_skip_equiv_count')}  (✅真实业务有效,切on评估依据)")
    print(f"  cross_pool_warn:           {h.get('conflict',{}).get('cross_pool_warn')}")
    print(f"  active_same_summary_multi: {_dd.get('active_same_summary_multi')}")
    print(f"  alerts数: {len(d.get('alerts',[]))}")
    for a in d.get("alerts", [])[:6]:
        print(f"    - {a[:70]}")

# ---- ③ 决策辅助 ----
print("\n③ 切 on 决策辅助")
print("  门槛: ① dry命中抽检全真同义(无高相似不同事实) ② active_multi不异常增长 ③ 无慢调用激增")
print("  切on: setx DF_SUMMARY_EQUIV_DEDUPE on")
print("  回退: setx DF_SUMMARY_EQUIV_DEDUPE dry-run  (秒级, 无需改码)")

# ---- ④ 切 on 判定（2026-11-24 改口径：多证据综合，clean≥8 降为运营参考）----
# 旧口径问题：clean_skip_equiv 仅由真实会话 PreCompact 触发累积，日常命中≈0，
# 把"运营观测指标"当成"技术门槛"，永远卡在 clean<8 无法切 on（已 2026-11-24 实切）。
# 新口径（技术门槛）：基于可实时读取的 gate_log + E4 数据做综合判定——
#   ① 假阳性=0：dry 样本中 would_skip=true 全部真同义（人工抽检），无「高相似但事实不同」
#   ② 无慢调用激增：p0_slow 占比低、无 p0_unavailable 降级
#   ③ active_same_summary_multi 不异常增长
#   + 影子重放证据（n≥200，命中全真同义零假阳；可用 audit30_dryrun_observe.py 现场跑）
# clean_skip_equiv ≥ 8 保留为「运营健康参考」，不再作切 on 硬门槛。
print("\n④ 切 on 判定（多证据综合；clean≥8 已降为运营参考）")

_EQUIV_REF = 8  # 运营参考线，非硬门槛
_clean = None; _raw = None; _multi = None; _cpw = None
try:
    _e4 = json.load(open(os.path.join(LOGS, "e4_maintenance.json"), encoding="utf-8"))
    _dd = _e4.get("health", {}).get("dedup", {})
    _clean = _dd.get("clean_skip_equiv_count")
    _raw = _dd.get("raw_skip_equiv_count", _dd.get("skip_equiv_count"))
    _multi = _dd.get("active_same_summary_multi")
    _cpw = _e4.get("health", {}).get("conflict", {}).get("cross_pool_warn")
except Exception:
    pass

# 从 gate_log 计算技术门槛证据
_c_dry = _c_skip = _c_slow = _c_unav = _c_ws = 0
if os.path.exists(gp):
    _r = [json.loads(l) for l in open(gp, encoding="utf-8") if l.strip()]
    _c_dry = sum(1 for x in _r if x["op"] == "p0_equiv_dry")
    _c_skip = sum(1 for x in _r if x["op"] == "skip_equivalent_derived")
    _c_slow = sum(1 for x in _r if x["op"] == "p0_slow")
    _c_unav = sum(1 for x in _r if x["op"] == "p0_unavailable")
    _c_ws = sum(1 for x in _r if x["op"] == "p0_equiv_dry" and x.get("would_skip"))

print(f"  [技术门槛] 判定依据（可实时读取）:")
print(f"    ① 假阳性: dry样本 {_c_dry} 条, would_skip=true {_c_ws} 条 → 需人工抽检全真同义(见①节)")
print(f"    ② 慢调用: p0_slow {_c_slow} 条, p0_unavailable {_c_unav} 条 → 门槛: 无降级/慢占比低")
print(f"    ③ active_same_summary_multi: {_multi} → 门槛: 不异常增长")
print(f"  [运营参考] clean_skip_equiv_count={_clean} (raw={_raw}) — 非硬门槛")
print()
_MODE_NOW = os.environ.get("DF_SUMMARY_EQUIV_DEDUPE", "(未设/进程默认off)")
print(f"  当前三态开关: DF_SUMMARY_EQUIV_DEDUPE={_MODE_NOW}")
print("  ✅ 切 on 技术门槛判定规则（2026-11-24 改口径）:")
print("     - 假阳性=0（dry would_skip 样本人工核全真同义，无高相似不同事实）")
print("     - 无 p0_unavailable 降级 + p0_slow 占比低")
print("     - active_same_summary_multi 不异常增长")
print("     - 影子重放证据充分（n≥200 命中零假阳；audit30_dryrun_observe.py 现场可跑）")
print("     - clean_skip_equiv≥8 仅运营参考，不卡切 on")
print("  切on: setx DF_SUMMARY_EQUIV_DEDUPE on")
print("  回退: setx DF_SUMMARY_EQUIV_DEDUPE dry-run  (秒级, 无需改码)")

print("\n" + "=" * 60)
