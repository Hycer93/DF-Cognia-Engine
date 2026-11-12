"""
夜间实验 3：SFT 七池蒸馏质检（E3）v2

v1 教训：七池 schema 各不相同（不是统一 inst_id/entity_id/summary），
统一字段检测全误报。v2 按每池真实 schema 定制体检项。

通用体检（所有池）：
  - 坏 JSON 行
  - 内容字段空/过短（每池的主内容字段见 SCHEMA）
  - src 字段占位（"来源origin-r"前缀不完整 / content2 / d1-d4）
  - 同池近似重复（主内容 3gram Jaccard>0.7）

池特有：
  - S7: thinking_value 非空
  - S1_pairs: stance 应为已知档位
  - groupB/C: skeleton 码必须在 27 图内

产出：logs/e3_sft_audit.json + 池级修复清单（不改数据，报告裁决权在用户）
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFT_DIR = os.path.join(HERE, "data", "trajectories")
GRAPH = os.path.join(HERE, "data", "mind_graph.json")
E3_OUT = os.path.join(HERE, "logs", "e3_sft_audit.json")

VALID_CODES = set()

# 每池主内容字段（重复检测+空检测用）
SCHEMA = {
    "sft_s7_corpus3_assets.jsonl": {"content": "content", "src": "src"},
    "sft_s1_pairs.jsonl": {"content": "response_head", "src": "src"},
    "sft_s1_doubter.jsonl": {"content": "full_doubt", "src": "src"},
    "sft_s2_doubter.jsonl": {"content": "findings", "src": "src"},
    "sft_s5_deepreflect.jsonl": {"content": "chosen_head", "src": "src"},
    "sft_s6_thinking_assets.jsonl": {"content": "structure_desc", "src": "src"},
    "sft_pool_bc_aa.jsonl": {"content": "head", "src": "src"},
    "sft_groupB_performative.jsonl": {"content": "pair", "src": "group"},
    "sft_groupC_explicit.jsonl": {"content": "explicit_query", "src": "src"},
}

PLACEHOLDERS = ["content2", "详情层：域", "来源origin-r", "TODO", "XXX占位"]


def trigrams(s: str) -> set:
    s = s[:200]
    return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def audit_pool(path: str) -> dict:
    name = os.path.basename(path)
    if name not in SCHEMA:
        return {"pool": name, "total": 0, "unknown_schema": True}
    cfield, sfield = SCHEMA[name]["content"], SCHEMA[name]["src"]

    recs, bad_json = [], 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            bad_json += 1

    empty, placeholder, bad_src, bad_skeleton, dup = [], [], [], [], []
    cache = []
    for i, r in enumerate(recs):
        rid = r.get("id") or f"L{i+1}"
        c = str(r.get(cfield) or "")
        # 空内容
        if len(c.strip()) < 5:
            empty.append({"id": rid, "len": len(c)})
        # 占位符
        for ph in PLACEHOLDERS:
            if ph in json.dumps(r, ensure_ascii=False):
                placeholder.append({"id": rid, "ph": ph})
        # src 规范
        src = str(r.get(sfield) or "")
        if not src:
            bad_src.append({"id": rid, "why": "src空"})
        # 池特有
        if name == "sft_s7_corpus3_assets.jsonl":
            if not r.get("thinking_value"):
                empty.append({"id": rid, "why": "thinking_value空"})
        if name == "sft_s1_pairs.jsonl":
            if r.get("stance") not in ("坚持", "修正", "让步", "part", "改", "顶", "撤"):
                bad_skeleton.append({"id": rid, "stance": r.get("stance")})
        if name in ("sft_groupB_performative.jsonl", "sft_groupC_explicit.jsonl"):
            sk = r.get("skeleton")
            if VALID_CODES and sk and str(sk) not in VALID_CODES and len(str(sk)) <= 3:
                bad_skeleton.append({"id": rid, "code": sk})
        # 近重复
        tri = trigrams(c)
        for j, (cj, tj) in enumerate(cache):
            if jaccard(tri, tj) > 0.7:
                dup.append({"id": rid, "dup_of": cj, "j": round(jaccard(tri, tj), 2)})
                break
        cache.append((rid, tri))

    dirty_ids = {x["id"] for x in empty + placeholder + bad_src + bad_skeleton + dup}
    return {"pool": name, "total": len(recs), "bad_json": bad_json,
            "empty": empty, "placeholder": placeholder, "bad_src": bad_src,
            "bad_skeleton": bad_skeleton, "dup": dup,
            "dirty": len(dirty_ids), "clean": len(recs) - len(dirty_ids)}


def main():
    g = json.load(open(GRAPH, encoding="utf-8"))
    VALID_CODES.update(g["nodes"].keys())
    pools = sorted(glob.glob(os.path.join(SFT_DIR, "sft_*.jsonl")))
    report = {"pools": [], "ts": __import__("time").strftime("%Y-%m-%d %H:%M")}
    total, total_dirty = 0, 0
    for p in pools:
        r = audit_pool(p)
        report["pools"].append(r)
        if r.get("unknown_schema"):
            print(f"[E3] {r['pool']}: 未知 schema 跳过", flush=True)
            continue
        total += r["total"]
        total_dirty += r["dirty"]
        print(f"[E3] {r['pool']}: {r['total']}条 clean={r['clean']} "
              f"空={len(r['empty'])} 占位={len(r['placeholder'])} "
              f"src={len(r['bad_src'])} 码={len(r['bad_skeleton'])} "
              f"重复={len(r['dup'])} 坏JSON={r['bad_json']}", flush=True)

    report["summary"] = {
        "total_records": total, "total_dirty": total_dirty,
        "dirty_rate": round(total_dirty / max(1, total), 3),
        "action_hint": "dirty 条目进 watchlist 修复队列；不自动改数据（裁决权在用户）",
    }
    with open(E3_OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[E3] 总计 {total} 条, dirty {total_dirty} ({total_dirty/max(1,total):.1%})", flush=True)
    print(f"报告: {E3_OUT}", flush=True)


if __name__ == "__main__":
    main()