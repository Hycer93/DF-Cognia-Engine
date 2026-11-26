"""只读审计：DB core_memory_card ↔ memories.jsonl 逐字段差异。

用途（2026-11-26 存储收敛前置）：
  在停实时双写 / 改夜间全量导出之前，先摸清两侧字段级漂移面，
  尤其找「jsonl 有值但 DB 为空/不一致」的字段（这些是待回灌 DB 的候选，
  因为 DB 是真相源，jsonl 只应派生于 DB）。

红线：**纯只读**。不写 DB、不写 jsonl、不建任何文件。只 print。
用法：python scripts/audit_db_jsonl_diff.py
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM = os.path.join(HERE, "data", "memory")
DB = os.path.join(MEM, "corpus_store.db")
JL = os.path.join(MEM, "memories.jsonl")

# 参与比对的字段（DB 列名 == jsonl 键名，共 23 个语义字段）
FIELDS = [
    "ts", "event_ts", "full_hash", "asset_class", "repro", "confidence",
    "dfcp_tag", "affect_tags", "summary", "summary_converged", "detail",
    "source_ref", "origin_ids", "entity_id", "derived_from", "tier",
    "weight", "protected", "superseded_by", "status", "memory_class",
]


def _norm(v):
    """把 DB 值 / jsonl 值归一化成可比较字符串。

    DB: 标量/JSON 字符串；jsonl: 原生类型(dict/list/bool/None/'0'/...)。
    统一策略：能 json 解析就解析再 dumps(sort_keys)，否则按原值。
    """
    if v is None:
        return "null"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    s = str(v).strip()
    if s == "":
        return "null"
    # DB 的 '0' 与 jsonl 的 0 / False 语义视作等价由调用方按字段定制；这里先原样
    try:
        obj = json.loads(s)
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        pass
    return s


def _is_null(v):
    return _norm(v) == "null"


def _eq(field, dv, jv):
    """按字段语义判断 DB 值 dv 与 jsonl 值 jv 是否等价。

    处理已知的序列化口径差异（DB 存字符串/JSON 文本，jsonl 存原生类型）：
      - bool 类字段：DB '0'/'1' ↔ jsonl False/True
      - 容器类字段：DB None ↔ jsonl [] / {}（空=无，语义等价）
      - 其余：归一化字符串比较
    """
    # bool 类
    if field in ("summary_converged", "protected"):
        def _b(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return False
            return str(v).strip() in ("1", "true", "True", "TRUE")
        return _b(dv) == _b(jv)
    # 容器类：空容器与 None 等价
    if field in ("dfcp_tag", "origin_ids", "derived_from", "weight", "affect_tags"):
        def _c(v):
            if v is None:
                return None
            if isinstance(v, (dict, list)):
                return v
            s = str(v).strip()
            if s in ("", "null"):
                return None
            try:
                return json.loads(s)
            except Exception:
                return s
        cd, cj = _c(dv), _c(jv)
        # None 与空容器视为同一"空"语义
        def _empty(x):
            return x is None or x == [] or x == {}
        if _empty(cd) and _empty(cj):
            return True
        return _norm(cd) == _norm(cj)
    # 标量
    return _norm(dv) == _norm(jv)


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_rows = {r["inst_id"]: dict(r) for r in con.execute(
        "SELECT * FROM core_memory_card").fetchall()}
    con.close()

    jl_rows = {}
    for line in open(JL, encoding="utf-8"):
        if not line.strip():
            continue
        e = json.loads(line)
        jl_rows[e["inst_id"]] = e

    print(f"DB core_memory_card 条数 = {len(db_rows)}")
    print(f"jsonl 条数              = {len(jl_rows)}")
    only_db = set(db_rows) - set(jl_rows)
    only_jl = set(jl_rows) - set(db_rows)
    both = set(db_rows) & set(jl_rows)
    print(f"仅 DB 有(缺 jsonl)      = {len(only_db)}")
    print(f"仅 jsonl 有(DB 缺)      = {len(only_jl)}")
    print(f"两侧都有                = {len(both)}")
    print()

    # 字段级差异统计
    field_diff_count = {f: 0 for f in FIELDS}
    # 关键分类：jsonl 有值 & DB 空  → 待回灌 DB
    jsonl_has_db_null = {f: [] for f in FIELDS}
    # DB 有值 & jsonl 空 → 导出后会自然带过去，不算问题
    db_has_jl_null = {f: 0 for f in FIELDS}
    # 两边都有值但不等 → 需定性谁对
    value_conflict = {f: [] for f in FIELDS}

    for iid in both:
        d = db_rows[iid]
        j = jl_rows[iid]
        for f in FIELDS:
            dv = d.get(f)
            jv = j.get(f)
            if _eq(f, dv, jv):
                continue
            field_diff_count[f] += 1
            if _is_null(dv) and not _is_null(jv):
                jsonl_has_db_null[f].append(iid)
            elif not _is_null(dv) and _is_null(jv):
                db_has_jl_null[f] += 1
            else:
                value_conflict[f].append(iid)

    print("=" * 64)
    print("字段级差异汇总（仅统计两侧都存在的卡）")
    print("=" * 64)
    print(f"{'字段':<20}{'差异数':<8}{'jsonl有DB空':<14}{'DB有jsonl空':<14}{'两边冲突':<10}")
    for f in FIELDS:
        print(f"{f:<20}{field_diff_count[f]:<8}"
              f"{len(jsonl_has_db_null[f]):<14}{db_has_jl_null[f]:<14}"
              f"{len(value_conflict[f]):<10}")
    print()

    print("=" * 64)
    print("【重点】jsonl 有值但 DB 为空 → 待回灌 DB 真相源")
    print("=" * 64)
    any_backfill = False
    for f in FIELDS:
        ids = jsonl_has_db_null[f]
        if ids:
            any_backfill = True
            print(f"\n字段 {f}: {len(ids)} 条")
            for iid in ids[:10]:
                print(f"  {iid[:16]}  jsonl值={_norm(jl_rows[iid][f])[:70]}")
            if len(ids) > 10:
                print(f"  ... 还有 {len(ids)-10} 条")
    if not any_backfill:
        print("无（两侧字段一致，无需回灌）")
    print()

    print("=" * 64)
    print("【需定性】两边都非空但值不同（需人工判断谁对）")
    print("=" * 64)
    any_conflict = False
    for f in FIELDS:
        ids = value_conflict[f]
        if ids:
            any_conflict = True
            print(f"\n字段 {f}: {len(ids)} 条")
            for iid in ids[:5]:
                print(f"  {iid[:16]}")
                print(f"    DB   ={_norm(db_rows[iid][f])[:70]}")
                print(f"    jsonl={_norm(jl_rows[iid][f])[:70]}")
            if len(ids) > 5:
                print(f"  ... 还有 {len(ids)-5} 条")
    if not any_conflict:
        print("无")

    print()
    print("=" * 64)
    print("【已知专项】666f95c5 血缘核对")
    print("=" * 64)
    target = [i for i in set(db_rows) | set(jl_rows) if i.startswith("666f95c5")]
    for iid in target:
        print(f"inst_id={iid}")
        print(f"  DB    derived_from = {db_rows.get(iid, {}).get('derived_from')!r}")
        print(f"  jsonl derived_from = {jl_rows.get(iid, {}).get('derived_from')!r}")


if __name__ == "__main__":
    sys.exit(main() or 0)