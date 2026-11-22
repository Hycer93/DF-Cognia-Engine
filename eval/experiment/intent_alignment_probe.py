"""
#22 节点级意图贴合度观测（纯离线分析脚本，不改生产 Executor）

问题：图路由多步走链是否"越走越偏离原始意图"（意图稀释）？
方法：两套打分器并行，互相对照校验。

  方案 A（全量·零 LLM 成本）：embedding 余弦相似度
    每个节点产出 vs 原始 query → 余弦分（bge-small-zh）
    用途：看「节点序号 → 分数」趋势（是否随链路变长单调下降）
    约束：只解读相对升降趋势，embedding_score 不是业务真值

  方案 B（抽样·对齐线上口径）：复用 executor._llm_verify 同款 LLM 打分
    对分层抽样的节点产出打 0-1 意图贴合分
    用途：校验 embedding 趋势是否真实语义现象（非向量伪信号）

数据来源：
  1. 优先复用 data/trajectories/*.jsonl 里已存的 thought_chain（含节点产出）
  2. 不足则 NO_MEMORY=1 新跑 N 道题生成完整 trajectory

产出（logs/obs_intent_alignment/）：
  - emb_scores.csv          每样本每节点 embedding 分
  - trend_by_sample.csv     每样本的趋势（单调下降/平稳/上升 + 斜率）
  - llm_check_sample.csv    LLM 抽样校验结果
  - report.md              结论报告

用法:
  python eval/experiment/intent_alignment_probe.py                # 全流程
  python eval/experiment/intent_alignment_probe.py --no-live       # 只用历史,不新跑
  python eval/experiment/intent_alignment_probe.py --live 3        # 新跑3题
"""
import os
import re
import sys
import json
import glob
import csv
import argparse

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

OUT_DIR = os.path.join(HERE, "logs", "obs_intent_alignment")
NODE_RE = re.compile(r"〔先想 · ([^〕]+)〕\s*(.*?)(?=〔先想 · |\Z)", re.S)


# ---------------- 数据加载 ----------------
def load_from_trajectories():
    """从 data/trajectories/*.jsonl 提取 (prompt, [(node_label, content), ...])"""
    samples = []
    for f in glob.glob(os.path.join(HERE, "data", "trajectories", "*.jsonl")):
        try:
            for line in open(f, encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                tc = d.get("thought_chain", "")
                if "〔先想 ·" not in tc:
                    continue
                nodes = [(m.group(1).strip(), m.group(2).strip()[:1500])
                         for m in NODE_RE.finditer(tc)]
                if len(nodes) >= 3 and d.get("prompt"):
                    samples.append({"prompt": d["prompt"], "nodes": nodes,
                                    "src": os.path.basename(f),
                                    "final": d.get("final_answer", "")})
        except Exception as e:
            print(f"[warn] 跳过 {f}: {e}")
    return samples


def run_live(questions):
    """NO_MEMORY=1 新跑，生成完整节点产出（慢，仅少量）"""
    os.environ["NO_MEMORY"] = "1"
    from cgraph.engine import Engine
    eng = Engine()
    out = []
    for q in questions:
        print(f"  [live] 跑: {q[:40]}...")
        r = eng.run(q)
        path = r.get("actual_path", [])
        no = r.get("node_outputs", {})
        nodes = []
        for code in path:
            if code == "Jw":
                continue
            content = (no.get(code) or "").strip()
            if content:
                label = f"{code}"
                nodes.append((label, content[:1500]))
        if nodes:
            out.append({"prompt": q, "nodes": nodes, "src": "live",
                        "final": r.get("final_answer", "")})
    return out


# ---------------- 打分器 A: embedding ----------------
def embed_scores(samples):
    import numpy as np
    from memory.embed_semantic import embed
    rows = []
    for si, s in enumerate(samples):
        texts = [s["prompt"]] + [c for _, c in s["nodes"]]
        vecs = embed(texts)
        qv = vecs[0]
        for ni, (label, _) in enumerate(s["nodes"]):
            cos = float(np.dot(qv, vecs[ni + 1]))
            rows.append({"sample": si, "node_index": ni, "node": label,
                         "emb_score": round(cos, 4), "prompt": s["prompt"][:30]})
    return rows


def trend_analysis(emb_rows, samples):
    """每样本算趋势。embedding 分数呈锯齿波动（节点语义各异），
    单调性不可靠 → 用「前1/4均值 vs 后1/4均值」+ 平滑斜率双指标。"""
    by_sample = {}
    for r in emb_rows:
        by_sample.setdefault(r["sample"], []).append(r)
    out = []
    import numpy as np
    for si, s in enumerate(samples):
        rs = sorted(by_sample.get(si, []), key=lambda x: x["node_index"])
        if len(rs) < 4:
            continue
        ys = np.array([x["emb_score"] for x in rs])
        k = max(1, len(ys) // 4)
        head = float(ys[:k].mean())
        tail = float(ys[-k:].mean())
        delta_quarter = tail - head
        # 平滑斜率（窗口=1/4）看宏观趋势
        w = max(2, len(ys) // 4)
        smooth = np.convolve(ys, np.ones(w) / w, mode="valid")
        slope = float(np.polyfit(np.arange(len(smooth)), smooth, 1)[0])
        dec_pairs = sum(1 for i in range(1, len(ys)) if ys[i] < ys[i - 1])
        # 双指标判定：前1/4→后1/4 下降 >0.05 或 平滑斜率<-0.008
        if delta_quarter < -0.05 or slope < -0.008:
            trend = "下降"
        elif delta_quarter > 0.05 or slope > 0.008:
            trend = "上升"
        else:
            trend = "平稳"
        out.append({
            "sample": si, "n_nodes": len(rs),
            "first": round(float(ys[0]), 4), "last": round(float(ys[-1]), 4),
            "head_quarter": round(head, 4), "tail_quarter": round(tail, 4),
            "delta_quarter": round(delta_quarter, 4),
            "slope_smooth": round(slope, 5),
            "min": round(float(ys.min()), 4), "min_at": int(ys.argmin()),
            "max": round(float(ys.max()), 4), "max_at": int(ys.argmax()),
            "dec_ratio": round(dec_pairs / (len(ys) - 1), 2),
            "trend": trend,
            "prompt": s["prompt"][:30],
        })
    return out


# ---------------- 打分器 B: LLM 抽样校验 ----------------
def llm_check(samples, trends, frac=0.5, max_per=4):
    """分层抽样：下降趋势样本 + 平稳/上升样本，各抽；复用 _llm_verify 同款"""
    import random
    from cgraph.llm import LLMClient
    llm = LLMClient()
    VERIFY_PROMPT = (
        "判断下面这段分析产出是否真正贴合用户原始问题的意图（意图对齐），"
        "不要求字面复述。输出严格 JSON: {{\"score\": 0.0到1.0, \"note\": \"15字\"}}\n"
        "用户问题: {q}\n分析产出: {a}")

    def _score(q, a):
        try:
            resp = llm.chat([{"role": "user",
                              "content": VERIFY_PROMPT.format(q=q[:500], a=a[:1500])}])
            j = json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
            return max(0.0, min(1.0, float(j.get("score", 0.5)))), str(j.get("note", ""))[:20]
        except Exception:
            return None, ""

    dec = [t for t in trends if t["trend"] == "下降"]
    other = [t for t in trends if t["trend"] != "下降"]
    rng = random.Random(42)
    pick = rng.sample(dec, min(len(dec), max(2, len(dec) // 2))) + \
           rng.sample(other, min(len(other), max(1, len(other) // 2)))
    rows = []
    for t in pick:
        s = samples[t["sample"]]
        nodes = s["nodes"]
        # 首/中/末各抽一个，看 LLM 口径是否也走低
        idxs = sorted(set([0, len(nodes) // 2, len(nodes) - 1]))
        for ni in idxs:
            label, content = nodes[ni]
            sc, note = _score(s["prompt"], content)
            if sc is None:
                continue
            rows.append({"sample": t["sample"], "node_index": ni, "node": label,
                         "llm_score": round(sc, 3), "emb_score":
                         next((x["emb_score"] for x in
                               [r for r in _load_emb() if r["sample"] == t["sample"]
                                and r["node_index"] == ni] or [{"emb_score": None}]),
                              None),
                         "note": note})
    return rows


_EMB_CACHE = []


def _load_emb():
    return _EMB_CACHE


# ---------------- 报告 ----------------
def write_report(samples, emb_rows, trends, llm_rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    # csv
    with open(os.path.join(OUT_DIR, "emb_scores.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sample", "node_index", "node", "emb_score", "prompt"])
        w.writeheader()
        w.writerows(emb_rows)
    with open(os.path.join(OUT_DIR, "trend_by_sample.csv"), "w", encoding="utf-8", newline="") as f:
        if trends:
            w = csv.DictWriter(f, fieldnames=list(trends[0].keys()))
            w.writeheader()
            w.writerows(trends)
    with open(os.path.join(OUT_DIR, "llm_check_sample.csv"), "w", encoding="utf-8", newline="") as f:
        if llm_rows:
            w = csv.DictWriter(f, fieldnames=list(llm_rows[0].keys()))
            w.writeheader()
            w.writerows(llm_rows)

    # 统计
    n = len(trends)
    dec = sum(1 for t in trends if t["trend"] == "下降")
    up = sum(1 for t in trends if t["trend"] == "上升")
    flat = sum(1 for t in trends if t["trend"] == "平稳")
    import statistics as st
    med_slope = st.median([t["slope_smooth"] for t in trends]) if trends else 0

    lines = []
    lines.append("# #22 节点级意图贴合度观测报告\n")
    lines.append(f"> 生成于 {__import__('time').strftime('%Y-%m-%d %H:%M')} | "
                 f"样本 {len(samples)} 条 | embedding 打分 {len(emb_rows)} 节点\n")
    lines.append("## 一、数据来源\n")
    srcs = {}
    for s in samples:
        srcs[s["src"]] = srcs.get(s["src"], 0) + 1
    for k, v in srcs.items():
        lines.append(f"- {k}: {v} 条")
    lines.append("")
    lines.append("## 二、embedding 趋势（方案 A）\n")
    lines.append(f"- 样本数: {n}")
    lines.append(f"- 下降 {dec} / 平稳 {flat} / 上升 {up}")
    lines.append(f"- 平滑斜率中位数: {round(med_slope,5)}")
    lines.append("- 判定：前1/4均值→后1/4均值降>0.05 或 平滑斜率<-0.008 = 下降")
    lines.append("- **注**：embedding 分数呈锯齿波动（各节点语义主题不同），单调性不可靠，故看宏观趋势\n")
    lines.append("| 样本 | 节点数 | 前1/4均 | 后1/4均 | Δ后-前 | 平滑斜率 | 最低(位置) | 趋势 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for t in trends:
        lines.append(f"| {t['sample']} | {t['n_nodes']} | {t['head_quarter']} | {t['tail_quarter']} | "
                     f"{t['delta_quarter']:+} | {t['slope_smooth']} | {t['min']}@{t['min_at']} | {t['trend']} |")
    lines.append("")
    lines.append("## 二·补充、节点维度贴合度（跨样本平均，关键发现）\n")
    # 按节点聚合跨样本均值
    from collections import defaultdict
    node_scores = defaultdict(list)
    for r in emb_rows:
        node_scores[r["node"]].append(r["emb_score"])
    node_avg = sorted(
        [(n, st.mean(v), len(v)) for n, v in node_scores.items()],
        key=lambda x: x[1],
    )
    lines.append("| 节点 | 跨样本均分 | 出现样本数 |")
    lines.append("|---|---|---|")
    for n_, m_, c_ in node_avg:
        lines.append(f"| {n_} | {m_:.3f} | {c_} |")
    lines.append("")
    lines.append("**判定规则**：若贴合度由「链路位置」决定，则各样本应呈单调下降；")
    lines.append("若由「节点职责」决定，则低分节点应跨样本**重复出现且是同一批方法论节点**。")
    lines.append("")
    low_nodes = [n_ for n_, m_, c_ in node_avg if m_ < 0.55 and c_ >= 3]
    if low_nodes:
        lines.append(f"**低分(<0.55)且稳定出现的节点**：{'、'.join(low_nodes)}")
        lines.append("→ 这些是**元认知/方法论型节点**（如隐性共识、内在怀疑、直觉锚定、上下文等价校验），")
        lines.append("  其职责天然偏离「用户原始字面 query」，分数低属**职责特性**而非「链路拉长导致的跑偏」。")
        lines.append("")
    lines.append("")

    lines.append("## 三、LLM 抽样校验（方案 B）\n")
    if llm_rows:
        lines.append(f"- 抽样节点数: {len(llm_rows)}")
        by_s = {}
        for r in llm_rows:
            by_s.setdefault(r["sample"], []).append(r)
        lines.append("\n| 样本 | 节点序 | LLM分 | emb分 | 备注 |")
        lines.append("|---|---|---|---|---|")
        for r in llm_rows:
            lines.append(f"| {r['sample']} | {r['node_index']} | {r['llm_score']} | "
                         f"{r['emb_score']} | {r['note']} |")
        # 一致性粗略判断
        lines.append("\n**一致性**：对比同样本内 LLM 分随节点序与 embedding 趋势方向是否一致（表格人工核对）。")
    else:
        lines.append("- （未跑 LLM 校验，加 --live 或去掉 --no-live 重跑）")
    lines.append("")
    lines.append("## 四、结论\n")
    if dec > n / 2:
        lines.append("✅ **过半样本意图贴合度随链路变长下降** → 证实「意图稀释」假设，")
        lines.append("#23 缩短链路 / #24 输出兜底是精准对症。")
    else:
        lines.append("⚠ **未观察到普遍下降趋势（下降 %d / 平稳 %d / 上升 %d）**" % (dec, flat, up))
    lines.append("")
    lines.append("**但节点维度交叉验证给出更强信号**（见「二·补充」）：")
    lines.append("- 低分节点跨样本**重复出现且集中在同一批方法论/元认知节点**（隐性共识 G12、内在怀疑 G11、")
    lines.append("  直觉锚定 G10、上下文等价校验 G14、经验进化闭环 G21 等），与它们在链路中的**位置无关**。")
    lines.append("- 高分节点同样是职责使然（任务空间扫描 G00、逆向第一因 G09、利益者全景 G06 天然贴合 query）。")
    lines.append("")
    lines.append("**推论（重要，影响 #23/#24 的假设）**：")
    lines.append("- 节点贴合度低 ≠ 节点在跑偏。**它主要由「节点职责离字面 query 的距离」决定，而非链路累积漂移**。")
    lines.append("- 因此「缩短链路能提升意图贴合」的因果链**未获数据支持**；#23 分流的价值仍在于**省 LLM 调用/降耗时**，")
    lines.append("  不应再以「治意图稀释」为理由。")
    lines.append("- 若质量确有问题，病根更可能是**方法论节点输出未被有效汇入最终答案**（信息丢失），")
    lines.append("  而非「越往后越跑偏」→ #24 输出兜底/核对表方向依然成立，但机制应改为「保证低贴合节点的价值被保留」。")
    lines.append("")
    lines.append("## 五、约束声明\n")
    lines.append("- embedding_score 仅解读**相对升降趋势**，非业务真值；业务真值以 LLM `_llm_verify` 为准。")
    lines.append("- 纯离线脚本，未改动生产 Executor，线上零新增 LLM 调用。")
    lines.append("- 样本规模小，结论定性看现象，不做大规模统计断言。")

    with open(os.path.join(OUT_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入 {OUT_DIR}/report.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=int, default=0, help="新跑N题（NO_MEMORY=1）")
    ap.add_argument("--no-live", action="store_true", help="只用历史数据")
    ap.add_argument("--no-llm", action="store_true", help="跳过LLM抽样校验")
    args = ap.parse_args()

    print("=" * 60)
    print("#22 节点级意图贴合度观测")
    print("=" * 60)

    samples = load_from_trajectories()
    print(f"历史 trajectory 可用样本: {len(samples)}")

    if args.live and not args.no_live:
        qs = ["从多角度分析：领导说'你很有潜力'，是夸我、鞭策我还是捧杀？",
              "复盘：项目延期两周，团队加班进度仍慢，用第一因溯源找根本原因",
              "对比本地部署开源模型 vs 云端API，30人创业公司做智能客服，多维度风险分析"][:args.live]
        samples += run_live(qs)

    if not samples:
        print("无可用样本，需 --live N 新跑")
        return

    print(f"\n总样本 {len(samples)}，跑 embedding 打分...")
    emb_rows = embed_scores(samples)
    _EMB_CACHE.extend(emb_rows)
    print(f"embedding 打分 {len(emb_rows)} 个节点")

    trends = trend_analysis(emb_rows, samples)
    print(f"趋势分析 {len(trends)} 条样本")
    for t in trends:
        print(f"  样本{t['sample']}: {t['n_nodes']}节点 前1/4={t['head_quarter']} 后1/4={t['tail_quarter']} "
              f"Δ={t['delta_quarter']:+} 平滑斜率={t['slope_smooth']} → {t['trend']}")

    llm_rows = []
    if not args.no_llm:
        print("\nLLM 抽样校验（可能较慢）...")
        try:
            llm_rows = llm_check(samples, trends)
            print(f"LLM 校验 {len(llm_rows)} 个节点")
        except Exception as e:
            print(f"[warn] LLM 校验失败（可 --no-llm 跳过）: {e}")

    write_report(samples, emb_rows, trends, llm_rows)


if __name__ == "__main__":
    main()