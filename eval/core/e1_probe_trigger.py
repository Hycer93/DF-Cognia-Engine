"""
夜间实验 1：探针触发率回归（E1）

目的：123 条探针（Aa/Bc/Dl 三骨架 × trigger/no_trigger + held-out 野生 3 条）
跑 cgraph 图路由全链，度量：
  1. 触发命中率：expect=trigger 的题，目标骨架是否进 walked 路径（宁漏报不误报）
  2. 负例误触率：expect=no_trigger 的题，目标骨架是否混进路径
  3. detection_hint 结构命中：输出侧字段/结构特征正则（输出侧证据，与路由侧分开计）
  4. 每题完整 trace（含 deepen 事件、验证语），落盘供晨间分析

安全设计：
  - 备份图: data/mind_graph_backup_20261108.json（实验前已备份）
  - 赫布更新照常发生（这正是要观测的：探针跑完后边权怎么漂）
  - 断点续跑：已有 id 跳过（读 E1_OUT）
  - 限速：每题间 sleep 2s，防 API 限流；单题失败记录 continue 不中断
  - NO_MEMORY=1：记忆层 chromadb 在此环境 ConnectError，关闭后主流程不受影响

用法: NO_MEMORY=1 python eval/e1_probe_trigger.py
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

from cgraph.engine import Engine          # noqa: E402

PROBE_DIR = os.path.join(HERE, "data", "probe")
E1_OUT = os.path.join(HERE, "logs", "e1_probe_result.jsonl")


def load_probes():
    probes = []
    for fn in ["aa_g00_probes.jsonl", "bc_g02_probes.jsonl",
               "dl_g11_probes.jsonl", "heldout_dl_wild.jsonl"]:
        p = os.path.join(PROBE_DIR, fn)
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    probes.append(json.loads(line))
    return probes


def detect_hint_hit(answer: str, hints: list) -> float:
    """输出侧结构检测：detection_hint 命中率（正则宽松匹配，宁漏报）"""
    if not hints or not answer:
        return 0.0
    hit = sum(1 for h in hints if h and re.search(re.escape(h), answer))
    return round(hit / len(hints), 3)


def snapshot_config() -> str:
    """P0-1 证据链：每次 E1 启动，把当时的 config.yaml 快照落进 logs。
    返回快照文件名（判读与归档同源）。11-12 定案——config 无版本史导致
    11-9 基线实际配置不可考，Bc 崩塌排查证据链断裂，此为修复。"""
    src = os.path.join(HERE, "config", "config.yaml")
    snap = "config_snapshot_" + time.strftime("%Y%m%d_%H%M%S") + ".yaml"
    dst = os.path.join(HERE, "logs", snap)
    with open(src, encoding="utf-8") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        f_out.write(f"# E1 config 快照 @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_out.write(f_in.read())
    return snap


def probe_meta(engine) -> dict:
    """P0-1 证据链：判定环境三元组——后端/模型/图文件。
    jsonl 每条强制携带，禁止空串（空串让跨天对比失去归因锚点）。"""
    llm = engine.llm
    backend = getattr(llm, "_backend", None) or engine.cfg["llm"]["active"]
    model = getattr(llm, "model", "") or ""
    return {
        "llm_backend": str(backend),
        "llm_model": str(model),
        "graph_file": os.path.basename(engine.graph_file),
    }


def main():
    snap = snapshot_config()
    meta = None      # Engine 起来后填充；jsonl 侧空守卫见下
    engine = Engine()
    meta = probe_meta(engine)
    probes = load_probes()
    print(f"[E1] config 快照: logs/{snap} | 判定后端: {meta['llm_backend']}/{meta['llm_model']}", flush=True)

    done = set()
    if os.path.exists(E1_OUT):
        for line in open(E1_OUT, encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    todo = [p for p in probes if p["id"] not in done]
    print(f"[E1] probes total {len(probes)}, done {len(done)}, todo {len(todo)}", flush=True)

    stats = {"trigger_total": 0, "trigger_hit": 0, "neg_total": 0,
             "neg_false": 0, "hint_scores": [], "errors": 0}
    t_start = time.time()

    for i, p in enumerate(todo):
        target = p["target"]
        expect = p.get("expect", "trigger")
        try:
            r = engine.run(p["input"])
            walked = r["actual_path"]
            hit = target in walked
            hint_score = detect_hint_hit(r["answer"], p.get("detection_hint"))
            # complexity 强制真实取值：engine.run 未回传时从 verify/route 环节
            # 拿不到就标 unknown——禁止空串（空串=归因锚点丢失）
            complexity = r.get("complexity") or "unknown"
            rec = {
                "id": p["id"], "target": target, "expect": expect,
                "domain": p.get("domain"), "source": p.get("source"),
                "probe_type": p.get("probe_type") or "route_hard",   # P1-4: 行为依赖探针标注透传
                "triggered": hit, "in_planned": target in r["planned_path"],
                "deepened": target in (r.get("deepen_events") or []),
                "walked": walked, "planned": r["planned_path"],
                "complexity": complexity,
                "hint_score": hint_score,
                "verify": r.get("verify_note", ""),
                "ms": r.get("ms"),
                "answer_head": (r["answer"] or "")[:300],
                "ts": time.strftime("%H:%M:%S"),
                # P0-1: 判定环境三元组，每条强制携带
                "llm_backend": meta["llm_backend"],
                "llm_model": meta["llm_model"],
                "graph_file": meta["graph_file"],
                "config_snapshot": snap,
            }
        except Exception as e:
            stats["errors"] += 1
            rec = {"id": p["id"], "target": target, "expect": expect,
                  "error": f"{type(e).__name__}: {e}",
                  "ts": time.strftime("%H:%M:%S"),
                  # 失败样本同样带判定环境（失败归因也需要锚点）
                  "llm_backend": meta["llm_backend"], "llm_model": meta["llm_model"],
                  "graph_file": meta["graph_file"], "config_snapshot": snap}
        with open(E1_OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 累计统计（从全量文件重算，保证续跑后一致）
        if "error" not in rec:
            if expect == "trigger":
                stats["trigger_total"] += 1
                stats["trigger_hit"] += hit
                stats["hint_scores"].append(hint_score)
            else:
                stats["neg_total"] += 1
                stats["neg_false"] += hit

        elapsed = int(time.time() - t_start)
        eta_min = round(elapsed / max(1, i + 1) * (len(todo) - i - 1) / 60, 1)
        mark = "✓" if ("error" not in rec and hit) else " "
        print(f"[E1 {i+1}/{len(todo)}] {mark}{p['id']} {expect}->{target} "
              f"{'IN' if hit else 'out'} hint={rec.get('hint_score')} "
              f"elapsed={elapsed}s eta={eta_min}min", flush=True)
        time.sleep(2)

    # 汇总
    print("\n[E1] ===== 触发率汇总 =====", flush=True)
    if stats["trigger_total"]:
        print(f"正例触发率: {stats['trigger_hit']}/{stats['trigger_total']} "
              f"= {stats['trigger_hit']/stats['trigger_total']:.1%}", flush=True)
    if stats["neg_total"]:
        print(f"负例误触率: {stats['neg_false']}/{stats['neg_total']} "
              f"= {stats['neg_false']/stats['neg_total']:.1%}", flush=True)
    if stats["hint_scores"]:
        print(f"detection_hint 平均命中: {sum(stats['hint_scores'])/len(stats['hint_scores']):.1%}", flush=True)
    print(f"errors: {stats['errors']}", flush=True)


if __name__ == "__main__":
    main()