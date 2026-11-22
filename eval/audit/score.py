"""
LLM 裁判盲评：对 ab_test 产物的 A/B 两组回答做盲评打分。

裁判用同一 LLM 后端，不告知哪组是哪路，输出: win/graph/bare/tie + 四维分数 + 理由。
输出：
  - 终端裁决摘要
  - logs/ab_judge_final.json 结构化报告（明细 + 平均分 + 胜率 + 分差分布）

用法:
  python eval/audit/score.py logs/ab_result_xxx.jsonl [更多文件...]
  不给文件时自动扫描 logs/ab_result_*.jsonl 全量。
限制：盲评受裁判模型偏好影响，属项目内部可复现基准，非绝对真值。
"""
import json
import os
import sys
import glob
import statistics

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

from cgraph.llm import LLMClient  # noqa: E402

DIMS = ["multidim", "intent", "structure", "value"]
JUDGE_PROMPT = """你是回答质量评审员。对同一个问题有两个匿名回答（回答一、回答二）。
从以下维度评分（各 1-10）：多维性(是否考虑多角度)、意图贴合(是否回答了真正的问题)、
结构完整性(是否有验证/总结)、实用价值。
输出严格 JSON: {{"one": {{"multidim":n,"intent":n,"structure":n,"value":n}},
"two": {{"multidim":n,"intent":n,"structure":n,"value":n}}, "winner": "one"|"two"|"tie", "reason": "..."}}
问题: {q}
回答一: {a}
回答二: {b}"""


def _sum(d):
    return sum(d.get(k, 0) for k in DIMS)


def _valid_pair(rec):
    """过滤退化样本：任一方回答为空/过短/占位符，盲评无意义"""
    def _ok(x):
        s = str(x or "").strip()
        return len(s) >= 50 and "无返回" not in s and "跳过" not in s
    return _ok(rec.get("graph", {}).get("answer")) and _ok(rec.get("bare", {}).get("answer"))


def _load_records(paths):
    recs = []
    for p in paths:
        for line in open(p, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                r["_src"] = os.path.basename(p)
                recs.append(r)
    return recs


def judge_one(llm, rec, a_graph):
    one = rec["graph"]["answer"] if a_graph else rec["bare"]["answer"]
    two = rec["bare"]["answer"] if a_graph else rec["graph"]["answer"]
    prompt = JUDGE_PROMPT.format(q=rec["question"], a=str(one)[:3000], b=str(two)[:3000])
    resp = llm.chat([{"role": "user", "content": prompt}])
    if not resp:
        return None
    try:
        j = json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
    except Exception:
        return None
    one_s, two_s = j.get("one", {}), j.get("two", {})
    # 解盲：one 对应 graph 当且仅当 a_graph
    graph_s = one_s if a_graph else two_s
    bare_s = two_s if a_graph else one_s
    w = j.get("winner")
    if w == "tie":
        winner = "tie"
    elif (w == "one") == a_graph:
        winner = "graph"
    else:
        winner = "bare"
    return {
        "question": rec["question"],
        "winner": winner,
        "graph_score": _sum(graph_s),
        "bare_score": _sum(bare_s),
        "graph_dims": graph_s,
        "bare_dims": bare_s,
        "reason": j.get("reason", ""),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        paths = args
    else:
        paths = sorted(glob.glob(os.path.join(HERE, "logs", "ab_result_*.jsonl")))
    if not paths:
        print("未找到 ab_result 文件")
        return

    records = _load_records(paths)
    n_raw = len(records)
    records = [r for r in records if _valid_pair(r)]
    n_invalid = n_raw - len(records)
    if n_invalid:
        print(f"过滤退化样本 {n_invalid} 条（任一方空/占位符），有效 {len(records)} 条")
    if not records:
        print("无有效成对样本")
        return
    import threading
    _tls = threading.local()

    def _get_llm():
        if not hasattr(_tls, "llm"):
            _tls.llm = LLMClient()      # 每线程独立实例（CodeChat 后端非线程安全）
        return _tls.llm

    print(f"盲评 {len(records)} 组回答（{len(paths)} 个文件）...")
    import concurrent.futures as cf
    tasks = []
    for i, rec in enumerate(records):
        a_graph = i % 2 == 0            # 交替盲化，抵消位置偏好
        tasks.append((i, rec, a_graph))

    def _work(t):
        i, rec, a_graph = t
        llm = _get_llm()
        import time as _t
        for _try in range(4):
            r = judge_one(llm, rec, a_graph)
            if r is not None:
                r["_a_graph"] = a_graph
                r["_src"] = rec.get("_src")
                return r
            _t.sleep(3 * (_try + 1))    # 并发满/解析失败退避重试
        return None

    details = []
    wins = {"graph": 0, "bare": 0, "tie": 0}
    skipped = 0
    with cf.ThreadPoolExecutor(max_workers=3) as ex:     # CodeChat 后端上限 3 并发
        futs = {ex.submit(_work, t): t[0] for t in tasks}
        done = 0
        for fut in cf.as_completed(futs):
            i = futs[fut]
            r = fut.result()
            done += 1
            if r is None:
                skipped += 1
                print(f"[{done}/{len(tasks)}] SKIP (#{i+1})")
                continue
            details.append(r)
            wins[r["winner"]] += 1
            print(f"[{done}/{len(tasks)}] {r['winner']:5s} | "
                  f"g={r['graph_score']:2d} b={r['bare_score']:2d} | {r['reason'][:45]}")

    n = len(details)
    if n == 0:
        print("无有效评分")
        return

    g_scores = [d["graph_score"] for d in details]
    b_scores = [d["bare_score"] for d in details]
    deltas = [d["graph_score"] - d["bare_score"] for d in details]
    dim_avg = {d_: round(statistics.mean([det["graph_dims"].get(d_, 0) for det in details]), 2)
               for d_ in DIMS}
    dim_avg_bare = {d_: round(statistics.mean([det["bare_dims"].get(d_, 0) for det in details]), 2)
                    for d_ in DIMS}

    report = {
        "meta": {
            "n_samples": n, "skipped": skipped, "n_files": len(paths),
            "note": "盲评受裁判模型偏好影响，属项目内部可复现基准，非绝对真值",
        },
        "wins": wins,
        "win_rate": {k: round(v / n, 3) for k, v in wins.items()},
        "avg_score": {"graph": round(statistics.mean(g_scores), 2),
                      "bare": round(statistics.mean(b_scores), 2)},
        "avg_score_by_dim": {"graph": dim_avg, "bare": dim_avg_bare},
        "delta": {
            "mean": round(statistics.mean(deltas), 2),
            "median": round(statistics.median(deltas), 2),
            "min": min(deltas), "max": max(deltas),
            "graph_better": sum(1 for d in deltas if d > 0),
            "bare_better": sum(1 for d in deltas if d < 0),
            "equal": sum(1 for d in deltas if d == 0),
        },
        "details": details,
    }

    out = os.path.join(HERE, "logs", "ab_judge_final.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 60)
    print("===== 盲评裁决 =====")
    print(f"样本 {n}（跳过 {skipped}）")
    print(f"胜率: 图路由 {wins['graph']} ({wins['graph']/n:.1%}) | "
          f"裸模型 {wins['bare']} ({wins['bare']/n:.1%}) | 平 {wins['tie']}")
    print(f"平均分(满分40): 图路由 {report['avg_score']['graph']} | "
          f"裸模型 {report['avg_score']['bare']}")
    print(f"分差(graph-bare): 均值 {report['delta']['mean']} 中位 {report['delta']['median']} "
          f"[{report['delta']['min']},{report['delta']['max']}]")
    print(f"逐题: 图更好 {report['delta']['graph_better']} | "
          f"裸更好 {report['delta']['bare_better']} | 相等 {report['delta']['equal']}")
    print(f"维度均分 图: {dim_avg}")
    print(f"维度均分 裸: {dim_avg_bare}")
    print(f"\n结果已写入 {out}")

    d = report["delta"]["mean"]
    if wins["graph"] > wins["bare"] and d > 0:
        print("结论: 图路由质量有净增益（胜率与均分双高）")
    elif wins["bare"] > wins["graph"] and d < 0:
        print("结论: 裸模型质量更优（图路由 12.6x 耗时未换回质量）")
    else:
        print("结论: 无显著差异（胜率与分差方向不一致，需更大样本）")


if __name__ == "__main__":
    main()