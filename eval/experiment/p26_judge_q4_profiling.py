"""p26_judge_q4_profiling.py — P2-6 Q4 全库扫描性能实测（调研用，不改线上）

目的: 量化 memory_judge._bloodline 的 O(C×N×G) 开销:
  - C = 候选条数, N = 全库条目数, G = 每条目 grams() 计算成本
  - 当前实现: 每条候选各调一次 load_memories(), 且每次都重新 grams() 全库条目
  - 预期: 候选数线性放大全库 grams 计算量

产出: logs/p26_judge_q4_profiling.json —— 记录不同规模下的耗时与放大倍数,
      为"预聚合 gram 快照"方案提供量化依据。

用法: python eval/experiment/p26_judge_q4_profiling.py
只读: 不写库、不改权重。
"""
import io
import json
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))
OUT = os.path.join(_HERE, "logs", "p26_judge_q4_profiling.json")


def main():
    import memory_judge as MJ
    from memory_relations import grams

    mems = MJ.load_memories()
    n_total = len(mems)
    high = [m for m in mems if (m.get("confidence") or "") in ("L0", "L1")]
    n_high = len(high)
    print(f"库内条目 N={n_total}, 其中 L0/L1 高置信 {n_high} 条")

    # ---- 单次全库 grams 成本基线（一次遍历所有高置信条目）----
    def scan_once(cand_gram):
        best = 0
        for m in high:
            ov = len(cand_gram & grams(m.get("summary") or ""))
            if ov > best:
                best = ov
        return best

    sample_summary = (high[0].get("summary") if high else "测试候选摘要文本") or "测试候选摘要"
    sample_gram = grams(sample_summary)

    # ---- 1. 单次 scan_once 耗时（N 条 grams 重算）----
    t0 = time.perf_counter()
    rounds = 20
    for _ in range(rounds):
        scan_once(sample_gram)
    t_once = (time.perf_counter() - t0) / rounds
    print(f"[1] 单次全库 grams 重算(N={n_high}): {t_once*1000:.2f} ms/次")

    # ---- 2. 不缓存 load_memories 的候选放大效应 ----
    cand_counts = [1, 5, 10, 20]
    scaling = []
    for C in cand_counts:
        t0 = time.perf_counter()
        for _ in range(C):
            # 模拟 _bloodline: 每条候选各 load_memories() + 全库 grams
            mm = MJ.load_memories()
            gs = sample_gram
            for m in mm:
                if (m.get("confidence") or "") in ("L0", "L1"):
                    _ = len(gs & grams(m.get("summary") or ""))
        dt = time.perf_counter() - t0
        scaling.append({"candidates": C, "ms": round(dt * 1000, 2)})
        print(f"[2] 候选 C={C:>2} 全量 _bloodline 耗时: {dt*1000:.2f} ms")
    base = scaling[0]["ms"] or 1e-6
    for s in scaling:
        s["amplification_vs_C1"] = round(s["ms"] / base, 2)
        print(f"     C={s['candidates']:>2} 放大 {s['amplification_vs_C1']}×")

    # ---- 3. 预聚合 gram 快照的理论收益（模拟：一次构建, C 次复用）----
    t0 = time.perf_counter()
    snapshot = [(m.get("inst_id"), grams(m.get("summary") or ""))
                for m in high]
    t_build = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(20):
        for _inst, g in snapshot:
            _ = len(sample_gram & g)
    t_reuse = (time.perf_counter() - t0) / 20
    print(f"[3] 快照构建一次: {t_build*1000:.2f} ms; 复用一次扫描: {t_reuse*1000:.2f} ms")
    saved_pct = (1 - t_reuse / t_once) * 100 if t_once else 0
    print(f"     预聚合后单次扫描较原实现省 {saved_pct:.1f}% (仅省 grams 重算; "
          f"load_memories 重复调用另需单独消除)")

    report = {
        "n_total": n_total, "n_high": n_high,
        "single_scan_ms": round(t_once * 1000, 3),
        "candidate_scaling": scaling,
        "snapshot_build_ms": round(t_build * 1000, 3),
        "snapshot_reuse_scan_ms": round(t_reuse * 1000, 3),
        "grams_recompute_saved_pct": round(saved_pct, 1),
        "complexity": "O(C × N × G): 每条候选重复 load_memories() + 全库 grams() 重算",
        "verdict": "调研结论见 docs/design_judge_q4_perf_20261121.md",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n[写入] {OUT}")


if __name__ == "__main__":
    main()