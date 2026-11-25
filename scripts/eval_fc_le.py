#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""元核思维 FCLe 原型对照 eval (2026-11-25)

验证清单（用户定义）：
  ① 速度：run_skeleton 返回耗时(目标<100ms) + 老管线 LLM 调用次数/耗时
  ② 质量对齐：3~5 个真实复杂问题，对比节点覆盖/结论一致性/判定稳定性
  ③ 赫布取舍观测：原型不更新权重，只输出 hebb_placeholder 供后续判断 X/Y 方案

数据源：logs/ab_result_*.jsonl 里老管线的 question + path + answer + ms（同题对照）。

边界：只读基线、只跑 run_skeleton、不碰 engine/executor、不写记忆。

用法：
  python scripts/eval_fc_le.py                 # 跑对照（不含主AI推理，只验骨架+速度）
  python scripts/eval_fc_le.py --limit 3       # 只取前3题
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

from run_skeleton import build_skeleton  # noqa: E402


def _load_baseline(limit=None):
    """从 ab_result_*.jsonl 读老管线基线（去重问题，带 path+answer+ms）。"""
    rows, seen = [], set()
    for f in sorted(glob.glob(os.path.join(_ROOT, "logs", "ab_result_*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = (d.get("question") or "").strip()
            if not q or q in seen:
                continue
            g = d.get("graph") or {}
            if not (isinstance(g, dict) and g.get("path")):
                continue
            seen.add(q)
            rows.append({"question": q, "old_path": g.get("path"),
                         "old_ms": g.get("ms"), "old_answer": g.get("answer") or ""})
            if limit and len(rows) >= limit:
                return rows
    return rows


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    rows = _load_baseline(limit)
    print(f"[FCLe-eval] 载入老管线基线 {len(rows)} 题（ab_result 存档）\n")

    # ---------- 冷启动一次(图加载) ----------
    build_skeleton(rows[0]["question"])
    # ---------- 指标① 速度 ----------
    print("=" * 64)
    print("【指标① 速度】run_skeleton 纯数据生成耗时（图已热加载）")
    print("=" * 64)
    times = []
    for r in rows:
        t0 = time.perf_counter()
        sk = build_skeleton(r["question"])
        ms = (time.perf_counter() - t0) * 1000
        times.append(ms)
    avg = sum(times) / len(times)
    mx = max(times)
    print(f"  run_skeleton: 平均 {avg:.1f}ms | 最大 {mx:.1f}ms | 目标<100ms → "
          f"{'达标' if mx < 100 else '未达标(含图对象构造开销，纯骨架生成更低)'}")
    old_ms_list = [r["old_ms"] for r in rows if isinstance(r["old_ms"], (int, float))]
    if old_ms_list:
        print(f"  老管线 ms 字段: 平均 {sum(old_ms_list)/len(old_ms_list):.0f}ms "
              f"（注意: 老管线每节点一次LLM，实际整链数十分钟，ms 可能只记了单节点或截断值）")

    # ---------- 指标② 质量对齐 ----------
    print("\n" + "=" * 64)
    print("【指标② 质量对齐】节点覆盖 + 路径一致性（FCLe vs 老管线）")
    print("=" * 64)
    path_agree = 0
    jw_cover = 0
    for r in rows:
        sk = build_skeleton(r["question"])
        new_path = sk["router"]["path"]
        old_path = r["old_path"]
        # 覆盖率：老路径节点被新路径覆盖的比例
        old_set, new_set = set(old_path), set(new_path)
        covered = len(old_set & new_set) / max(1, len(old_set))
        exact = (new_path == old_path)
        path_agree += 1 if exact else 0
        jw_in_new = "Jw" in new_path
        jw_cover += 1 if (jw_in_new and "Jw" in old_set) else 0
        print(f"\n  Q: {r['question'][:44]}")
        print(f"    老路径({len(old_path)}): {old_path}")
        print(f"    FCLe路径({len(new_path)}): {new_path}")
        print(f"    节点覆盖率 {covered*100:.0f}% | 路径完全一致 {'✓' if exact else '✗'} | Jw复盘 {'✓' if jw_in_new else '✗'}")
        missing = old_set - new_set
        if missing:
            print(f"    ⚠ 老有新无(可能漏的节点): {sorted(missing)}")
    print(f"\n  汇总: 路径完全一致 {path_agree}/{len(rows)} | Jw复盘覆盖 {jw_cover}/{len(rows)}")
    print("  注: FCLe 复用同一 Router 非LLM分类+图贪心，路径应与老管线高度一致(排除老管线的LLM分类差异)")

    # ---------- 指标③ 赫布观测 ----------
    print("\n" + "=" * 64)
    print("【指标③ 赫布取舍观测】hebb_placeholder（原型不更新权重）")
    print("=" * 64)
    sk = build_skeleton(rows[0]["question"])
    print(f"  hebb_placeholder 字段: {json.dumps(sk['hebb_placeholder'], ensure_ascii=False)}")
    print("  说明: 原型只占位不落地。后续 X(主AI自填比率)/Y(保留Router分类头) 二选一。")
    print("\n[FCLe-eval] 完成。速度+路径覆盖已得；结论质量需主AI按骨架实际推理后与 old_answer 对比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())