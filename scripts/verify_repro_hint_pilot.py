#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ht/Jw repro 自报 hint 试点验证 (dry-run, 2026-11-25)

目的：验证"高频思维节点执行时自报 repro"这个方向是否可行，切换正式入库前先量化三指标：
  ① 不一致率：同一条产出，【节点自报 hint】vs【judge_repro 规则标签】的不一致比例
  ② 可读性  ：人工抽检 hint 的 repro_reason 是否讲到点子上（本脚本只导出样本供人工看）
  ③ 稳定性  ：同一输入多次跑 skill，hint 是否稳定（同输入反复乱跳=不可用）

设计约束（用户要求）：
  - dry-run：只收集、只对比、不切换正式入库
  - 不改 judge_repro；只观测两者差异
  - 不要求所有 skill 自动入库；只验证 Ht/Jw 的"执行→hint 产出"这一截

用法：
  python scripts/verify_repro_hint_pilot.py                 # 跑内置样例问题
  python scripts/verify_repro_hint_pilot.py --repeat 3      # 稳定性：每问重复3次
  python scripts/verify_repro_hint_pilot.py --no-llm        # 无LLM时用桩(只验管道通)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "memory"))
sys.path.insert(0, os.path.join(_ROOT, "cgraph"))

import memory_organize  # noqa: E402  (memory/ 非包, 直接把 memory/ 加进 path)
judge_repro = memory_organize.judge_repro

# 内置样例：覆盖 Ht(决策整合)和 Jw(复盘固化) 容易触发的任务类型
SAMPLE_QUESTIONS = [
    "复盘一下：本地想部署大模型，机器配置3090+32G，当时怎么选的？给出决策依据。",
    "综合权衡：分布式训练用 DDP 还是 FSDP，数据并行 vs 张量并行，给最终选型结论。",
    "总结这次踩坑：vhal_test 未预置导致部署失败，根因和规避措施是什么？",
    "定稿决策：记忆系统三级可复现（derived/situational/flash）存哪一层，说明理由。",
    "复盘经验：为什么非农超预期金价反跌，归因流动性拐点，这个推理可复现吗？",
]


def _make_stub_llm():
    """无 LLM 时的桩：返回带 repro_hint 的 JSON，仅验证管道能通、字段能解析。
    注意：桩的 hint 是固定值，不用于真实不一致率/稳定性评估。"""
    class StubLLM:
        def __init__(self):
            self.last_usage = None
        def chat(self, messages):
            return json.dumps({
                "analysis": "（桩输出）占位分析",
                "deepen": None,
                "repro_hint": "situational",
                "repro_reason": "桩：默认条件可复现",
            }, ensure_ascii=False)
    return StubLLM()


def _make_real_llm():
    try:
        # 用项目标准 LLMClient(读 config llm.active; codechat 后端自动 agent_free=True,
        # 纯文本直返才守严格 JSON)，与 cgraph/llm.py 主链路一致。
        from cgraph.llm import LLMClient
        return LLMClient()
    except Exception as e:
        print(f"[warn] 真实 LLM 不可用({e})，回退桩。真实不一致率需 --llm 环境。", file=sys.stderr)
        return _make_stub_llm()


def _run_once(engine, question):
    """跑一次整链，返回 (repro_hints, node_outputs)。"""
    try:
        r = engine.run(question, utility=0.0)
    except Exception as e:
        return {}, {}, f"engine.run 异常: {e}"
    # engine.run 返回 dict，含 repro_hints
    hints = r.get("repro_hints") or {}
    return hints, r, None


def _run_forced_path(engine, question, path):
    """强制走指定骨架路径(绕路由)，专验 Ht 等非默认路径节点的自报能力。
    Ht 的 trigger 是「重组完成+G24拓扑完成」，默认路由 rarely 走到；本函数直接
    构造 RouteDecision 让它执行，验证"节点执行时能否自报 repro"。"""
    from cgraph.router import RouteDecision
    try:
        dec = RouteDecision(complexity="complex", path=list(path),
                            domain_nodes=list(set(path) | {"Ht"}), reason="pilot-forced")
        wr = engine.executor.run(question, dec)
        return wr.repro_hints or {}, {"actual_path": wr.path}, None
    except Exception as e:
        return {}, {}, f"forced_path 异常: {e}"


def _rule_label_for_hint(hints: dict, node_outputs: dict):
    """对节点自报 hint 的每条产出，用 judge_repro 规则反推标签做对比。
    节点产出正文不在 tags 里，这里只能近似：把 node 的 behavior(节点码) 当 behavior 标签，
    其余标签(affect/domain)未知 → 走规则默认路径。这本身就是当前 judge_repro 的现实，
    正是要暴露二者差异。"""
    out = {}
    for code, h in hints.items():
        tags = {"behavior": code, "affect_tags": None, "domain": ""}
        out[code] = judge_repro("", tags)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="每个问题重复次数(测稳定性)")
    ap.add_argument("--no-llm", action="store_true", help="强制用桩(只验管道)")
    args = ap.parse_args()

    llm = _make_stub_llm() if args.no_llm else _make_real_llm()
    from cgraph.engine import Engine
    try:
        engine = Engine()
        # Engine 自建 LLM；试点强制注入桩/真实实例到 router+executor（不改 engine 源码）
        engine.llm = llm
        engine.executor.llm = llm
        if hasattr(engine, "router"):
            engine.router.llm = llm
    except Exception as e:
        print(f"[fatal] Engine 构建失败: {e}", file=sys.stderr)
        return 2

    # 统计容器
    all_rows = []            # 每次运行每节点的对比记录
    hint_by_q = defaultdict(list)  # question -> 每次运行的 hint 快照(稳定性用)
    node_hit = Counter()

    for q in SAMPLE_QUESTIONS:
        for rep in range(args.repeat):
            # (a) 正常路由跑：主要触发 Jw(支线) + 若路由到 Ht 则含 Ht
            hints, r, err = _run_once(engine, q)
            if not err:
                rule_labels = _rule_label_for_hint(hints, r)
                snap = {}
                for code, h in hints.items():
                    node_hit[code] += 1
                    all_rows.append({"q": q[:30], "node": code,
                                     "hint": h.get("repro_hint"),
                                     "rule": rule_labels.get(code),
                                     "agree": h.get("repro_hint") == rule_labels.get(code),
                                     "reason": h.get("repro_reason", "")})
                    snap[code] = h.get("repro_hint")
                hint_by_q[q[:30]].append(snap)
            else:
                print(f"[err] 正常跑 {q[:24]} rep{rep}: {err}")

            # (b) 强制 Ht 路径跑：专验 Ht 节点自报(默认路由 rarely 到 Ht)
            fhints, fr, ferr = _run_forced_path(engine, q, ["Aa", "Cf", "Di", "Ht", "Eo", "Jw"])
            if not ferr and fhints:
                rule_labels = _rule_label_for_hint(fhints, fr)
                for code, h in fhints.items():
                    node_hit[code] += 1
                    all_rows.append({"q": f"[强制]{q[:24]}", "node": code,
                                     "hint": h.get("repro_hint"),
                                     "rule": rule_labels.get(code),
                                     "agree": h.get("repro_hint") == rule_labels.get(code),
                                     "reason": h.get("repro_reason", "")})

    # ---------- 指标① 不一致率 ----------
    total = len(all_rows)
    disagree = sum(1 for x in all_rows if not x["agree"])
    print("\n" + "=" * 60)
    print("【指标① 不一致率】节点自报 hint vs judge_repro 规则标签")
    print("=" * 60)
    if total == 0:
        print("  无样本(LLM未产出hint)。用 --no-llm 可验证管道。")
    else:
        print(f"  样本数: {total}（Ht/Jw 自报总数）")
        print(f"  不一致: {disagree}  一致: {total-disagree}  不一致率: {disagree*100//total}%")
        print(f"  按节点: {dict(node_hit)}")
        print("  逐条:")
        for x in all_rows:
            flag = "✗不一致" if not x["agree"] else "✓"
            print(f"    [{x['node']}] hint={x['hint']:<11} rule={x['rule']:<11} {flag}  "
                  f"理由={x['reason'][:36]}")

    # ---------- 指标③ 稳定性 ----------
    print("\n" + "=" * 60)
    print("【指标③ 稳定性】同一问题多次跑，hint 是否跳变")
    print("=" * 60)
    if args.repeat < 2:
        print("  (需 --repeat >= 2 才评估)")
    else:
        unstable = 0
        for q, snaps in hint_by_q.items():
            codes = set().union(*[s.keys() for s in snaps]) if snaps else set()
            for code in codes:
                vals = [s.get(code) for s in snaps if code in s]
                if len(set(vals)) > 1:
                    unstable += 1
                    print(f"  ✗ 不稳定 [{q}] {code}: {vals}")
        print(f"  不稳定组数: {unstable} / {len(hint_by_q)} 问题")

    # ---------- 指标② 人工抽检导出 ----------
    print("\n" + "=" * 60)
    print("【指标② 人工抽检】导出 hint+理由，供人工核对是否符合裁决标准")
    print("=" * 60)
    out_path = os.path.join(_ROOT, "logs", "repro_hint_pilot_samples.jsonl")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for x in all_rows:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        print(f"  样本已导出: {out_path}（{len(all_rows)} 条）")
    except Exception as e:
        print(f"  导出失败: {e}")

    print("\n完成。判定：不一致率=0 且稳定 → 自报无增量价值；"
          "不一致率高但人工抽检 hint 更准 → 值得切换正式入库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())