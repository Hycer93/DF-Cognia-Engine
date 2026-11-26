#!/usr/bin/env python3
"""analyze_cgraph_judge_shadow.py — 汇总 cgraph_judge_shadow.jsonl 观测指标（只读）

纯只读：不修改日志、不触写记忆库、不自动切换开关。仅统计 + 告警提示，
切 true 仍由人工决策。

适配日志格式（cgraph/green_card_writer.py::_shadow_judge 产出）：
  每行一条 rec: {ts, op, context, candidates, judged, store, hold, discard,
                 pass_rate, hold_rate, discard_rate, elapsed_ms, judge_source,
                 judge_failure, verdicts:[{summary, verdict, reasons}]}

用法:
  python scripts/analyze_cgraph_judge_shadow.py
  python scripts/analyze_cgraph_judge_shadow.py --log-path xxx.jsonl --min-sample 50
"""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def _stat(arr):
    if not arr:
        return {"mean": 0.0, "p50": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": statistics.mean(arr),
        "p50": statistics.median(arr),
        "min": min(arr),
        "max": max(arr),
    }


def _fmt_stat(s):
    return (f"均值 {s['mean']:.4f} | 中位 {s['p50']:.4f} | "
            f"min {s['min']:.4f} | max {s['max']:.4f}")


def main():
    parser = argparse.ArgumentParser(description="汇总 cgraph_judge_shadow.jsonl 观测指标")
    parser.add_argument("--log-path", default="logs/cgraph_judge_shadow.jsonl")
    parser.add_argument("--min-sample", type=int, default=20, help="样本量达标线")
    args = parser.parse_args()

    log_file = Path(args.log_path)
    if not log_file.exists():
        print(f"[ERROR] 日志文件不存在：{log_file}")
        return

    records = []
    verdict_cnt = defaultdict(int)      # 单卡裁决分布 store/hold/discard/...
    latency_list = []
    failure_cnt = 0
    source_cnt = defaultdict(int)       # judge_source 分布(llm/stub)
    bad_lines = 0

    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception as e:
                bad_lines += 1
                print(f"[WARN] 行解析跳过: {e}")
                continue
            records.append(rec)
            latency_list.append(float(rec.get("elapsed_ms", 0.0)))
            if rec.get("judge_failure"):
                failure_cnt += 1
            source_cnt[rec.get("judge_source", "unknown")] += 1
            # 真实格式: verdicts 是 list[dict]，每项含 verdict 键
            for v in rec.get("verdicts") or []:
                if isinstance(v, dict):
                    verdict_cnt[v.get("verdict", "unknown")] += 1
                else:  # 兼容极端情况：字符串
                    verdict_cnt[str(v)] += 1

    n = len(records)
    if n == 0:
        print("暂无 shadow 观测样本")
        return

    pass_rates = [float(r.get("pass_rate", 0.0)) for r in records]
    hold_rates = [float(r.get("hold_rate", 0.0)) for r in records]
    discard_rates = [float(r.get("discard_rate", 0.0)) for r in records]
    fail_rate = failure_cnt / n
    stub_share = source_cnt.get("stub", 0) / n

    print("=" * 70)
    print(f"Shadow Judge 观测汇总 | 总样本轮次：{n}"
          + (f"（解析失败 {bad_lines} 行）" if bad_lines else ""))
    print("=" * 70)
    print(f"judge_failure 轮次：{failure_cnt} | 失败率：{fail_rate:.2%}")
    print(f"judge_source 分布：{dict(source_cnt)}"
          + ("  ⚠️ 含 stub 数据，统计为桩判定，注意失真" if stub_share > 0 else ""))
    print()
    print("【通过率 pass_rate】", _fmt_stat(_stat(pass_rates)))
    print("【hold占比 hold_rate】", _fmt_stat(_stat(hold_rates)))
    print("【discard占比 discard_rate】", _fmt_stat(_stat(discard_rates)))
    print("【耗时 elapsed_ms】", _fmt_stat(_stat(latency_list)))
    print()
    print("【单卡裁决分布 verdict】")
    if verdict_cnt:
        for k, v in sorted(verdict_cnt.items()):
            print(f"  {k}: {v}")
    else:
        print("  （无逐卡裁决记录）")
    print()

    # ---- 准入检查提示 ----
    ok_sample = n >= args.min_sample
    print(f"准入检查：样本量 {n} ≥ {args.min_sample} ? {'✅' if ok_sample else '❌'}")
    warns = []
    if fail_rate > 0.15:
        warns.append(f"⚠️ judge 失败率 {fail_rate:.1%} >15%，不稳定，暂不适合切 true")
    if hold_rates and max(hold_rates) > 0.45:
        warns.append(f"⚠️ hold 占比出现高值 {max(hold_rates):.1%} >45%，注意排查误 hold")
    if discard_rates and max(discard_rates) > 0.30:
        warns.append(f"⚠️ discard 占比出现高值 {max(discard_rates):.1%} >30%，留意误丢风险")
    if stub_share > 0:
        warns.append(f"⚠️ {stub_share:.0%} 样本为 stub（JUDGE_USE_LLM=0），非真实 LLM 判定，"
                     f"切 true 评估前应跑真实 LLM 样本")
    if warns:
        for w in warns:
            print(w)
    else:
        print("✅ 未触发告警阈值（仅供参考，切 true 仍需人工综合判断）")
    print("=" * 70)


if __name__ == "__main__":
    main()