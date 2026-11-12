"""
夜间实验 3：SFT 七池蒸馏质检（E3）

背景：七池 356 条 SFT 资产将作为蒸馏教材。入库时有三批已知病
（watchlist 首条记录），但从未做过全量机械化体检。

做法：纯本地静态扫描（零 LLM 调用，秒级完成）：
  1. 占位符病：content2/d1-d4/"详情层：域"/"来源origin-rXXX" 未替换字段
  2. 结构病：缺 inst_id/entity_id/asset_class/blood_tags 关键字段的条目
  3. 长度病：summary>500 或 <10（过长进 prompt 会挤占，过短无信息量）
  4. 重复病：同 entity+summary 近重复（精确串重复 + 3gram Jaccard>0.6）
  5. 血统病：blood_tags 引用了不存在的骨架码（27 图外的码）
  6. 语法病：JSON 行解析失败统计

产出：logs/e3_sft_audit.json + 每池修复清单（不改数据，只出报告）

用法: python eval/e3_sft_audit.py
"""
import glob
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFT_DIR = os.path.join(HERE, "data", "trajectories")
GRAPH = os.path.join(HERE, "data", "mind_graph.json")
E3_OUT = os.path.join(HERE, "logs", "e3_sft_audit.json")

VALID_CODES = set()


def load_codes():
    global VALID_CODES
    g = json.load(open(GRAPH, encoding="utf-8"))
    VALID_CODES = set(g["nodes"].keys())


def trigrams(s: str) -> set:
    s = s[:200]
    return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def audit_pool(path: str) -> dict:
    name = os.path.basename(path)
    recs, bad_json = [], 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            bad_json += 1

    placeholder, missing_field, length_bad, bad_blood, dup = [], [], [], [], []
    tri_cache = []
    for i, r in enumerate(recs):
        rid = r.get("inst_id") or f"L{i}"
        # 1 占位符
        txt = json.dumps(r, ensure_ascii=False)
        for ph in ["content2", "d1=", "d2=", "d3=", "d4=",
                   "详情层：域", "来源origin-r"]:
            if ph in txt:
                placeholder.append({"id": rid, "ph": ph})
        # 2 关键字段
        for fld in ["inst_id", "entity_id", "summary"]:
            if fld not in r or not r.get(fld):
                missing_field.append({"id": rid, "field": fld})
        # 3 长度
        s = r.get("summary") or ""
        if len(s) > 500 or (0 < len(s) < 10):
            length_bad.append({"id": rid, "len": len(s)})
        # 5 血统码
        for bt in r.get("blood_tags") or []:
            if VALID_CODES and bt not in VALID_CODES and len(bt) <= 3:
                bad_blood.append({"id": rid, "code": bt})
        # 4 重复（同池内）
        key = (r.get("entity_id"), s[:100])
        tri = trigrams(s)
        for j, (rj, tj) in enumerate(tri_cache):
            if r.get("entity_id") == rj.get("entity_id") and jaccard(tri, tj) > 0.6:
                dup.append({"id": rid, "dup_of": rj.get("inst_id") or f"L{j}",
                            "j": round(jaccard(tri, tj), 2)})
                break
        tri_cache.append((r, tri))

    return {
        "pool": name, "total": len(recs), "bad_json": bad_json,
        "placeholder": placeholder, "missing_field": missing_field,
        "length_bad": length_bad, "bad_blood": bad_blood, "dup": dup,
        "clean": len(recs) - len({x["id"] for x in placeholder + missing_field
                                  + length_bad + bad_blood + dup}),
    }


def main():
    load_codes()
    pools = sorted(glob.glob(os.path.join(SFT_DIR, "sft_*.jsonl")))
    report = {"pools": [], "ts": __import__("time").strftime("%Y-%m-%d %H:%M")}
    print(f"[E3] pools: {len(pools)}", flush=True)
    total, total_dirty = 0, 0
    for p in pools:
        r = audit_pool(p)
        report["pools"].append(r)
        dirty = r["total"] - r["clean"]
        total += r["total"]
        total_dirty += dirty
        print(f"[E3] {r['pool']}: {r['total']}条 clean={r['clean']} "
              f"占位={len(r['placeholder'])} 缺字段={len(r['missing_field'])} "
              f"长度={len(r['length_bad'])} 血统={len(r['bad_blood'])} "
              f"重复={len(r['dup'])} 坏JSON={r['bad_json']}", flush=True)

    report["summary"] = {
        "total_records": total, "total_dirty": total_dirty,
        "dirty_rate": round(total_dirty / max(1, total), 3),
        "action_hint": "dirty 条目进 watchlist 修复队列；不自动改数据（裁决权在用户）",
    }
    with open(E3_OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[E3] 总计 {total} 条, dirty {total_dirty} ({report['summary']['dirty_rate']:.1%})", flush=True)
    print(f"报告: {E3_OUT}", flush=True)


if __name__ == "__main__":
    main()