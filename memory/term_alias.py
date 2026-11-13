# term_alias.py — 术语级受控别名桥（检索链路改善·阶段一，2026-11-13 用户定案）
# 与 memory_alias（卡片级别名: alias→inst_id）正交：本模块是 query 术语翻译通道
# （用户词 → 体系标准术语），登记时只收严格同义，宽泛上位词/分类词禁入。
# 执行顺序（用户方案）：一级精确锚无有效命中 → 别名桥翻译重跑一级 → sem → 碎片兜底。
# 阶段一只做翻译通道本体；优先级接入与碎片屏蔽在阶段二（ngram 分流）落。

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_organize import MEM_DIR, append_jsonl

TERM_ALIAS_JSONL = os.path.join(MEM_DIR, "term_alias.jsonl")

# 宽泛分类词黑名单（登记时拒绝——泛词泛滥/错误挂靠的历史教训，用户规约）
BANNED_PATTERNS = ("方法", "系统", "方案", "架构", "机制", "技术", "理论", "模型",
                   "相关", "处理", "分析", "设计", "东西", "那个")

# 别名重试限次（用户裁决配套细则：一次，防语义漂移）
MAX_EXPANSIONS_PER_QUERY = 1


def _load_registry():
    """生效术语映射视图: {用户词: 标准术语}。只看 status=active。"""
    out = {}
    if not os.path.exists(TERM_ALIAS_JSONL):
        return out
    with io.open(TERM_ALIAS_JSONL, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("op") == "hit":
                continue
            if rec.get("status") == "active":
                out[rec["term"]] = rec["canonical"]
    return out


def register_term_alias(term: str, canonical: str, origin: str = "judge",
                        note: str = "") -> dict:
    """登记术语别名（judge/人工审批入口）。

    规约：只收严格同义（等价叫法）；宽泛分类词/上位词拒绝。
    canonical 必须是体系内已确立的标准术语（要求在库内 summary 中存在，
    防止翻译到一个库里没有的词白跑一轮）。
    """
    term, canonical = term.strip(), canonical.strip()
    if not term or not canonical or term == canonical:
        return {"ok": False, "reason": "empty_or_self"}
    for bad in BANNED_PATTERNS:
        # term 侧：宽泛词本身或以宽泛词为主体（如"方法论""推演方法"）都拒
        if term == bad or (len(term) <= len(bad) + 1 and bad in term):
            return {"ok": False, "reason": f"banned_generic_word:{bad}"}
        # canonical 侧禁止宽泛词（标准术语必须是实体级长词）
        if canonical == bad:
            return {"ok": False, "reason": f"banned_generic_word:{bad}"}
    if len(canonical) < 4:
        return {"ok": False, "reason": "canonical_too_short",
                "hint": "标准术语<4字必是碎片词, 严禁"}
    # canonical 必须真实存在于库内摘要（锚得住才值得翻译）
    from memory_organize import load_memories
    pool = " ".join(m.get("summary") or "" for m in load_memories())
    if canonical not in pool:
        return {"ok": False, "reason": "canonical_not_in_corpus",
                "hint": "标准术语在库内任何摘要中都不存在，翻译后无锚可命中"}
    append_jsonl(TERM_ALIAS_JSONL, {
        "term": term, "canonical": canonical, "origin": origin,
        "note": note, "status": "active", "hit_count": 0,
    })
    return {"ok": True, "term": term, "canonical": canonical}


def expand_query(query: str) -> dict:
    """查询展开（读侧）：命中的用户词替换为标准术语，拼接式替换非整句替换。

    返回 {"expanded": 新query或None, "applied": [(用户词,标准术语),..]}
    限次 MAX_EXPANSIONS_PER_QUERY；无命中返回原样（None 表示无需重跑）。
    """
    reg = _load_registry()
    applied = []
    q = query
    for term, canonical in reg.items():
        if len(applied) >= MAX_EXPANSIONS_PER_QUERY:
            break
        if term in q and canonical not in q:
            q = q.replace(term, canonical)
            applied.append((term, canonical))
    if not applied:
        return {"expanded": None, "applied": []}
    _mark_hits([t for t, _ in applied])
    return {"expanded": q, "applied": applied}


def _mark_hits(terms):
    """读侧命中续命记录（append-only，与卡片级 alias 同惯例）。"""
    now = __import__("datetime").datetime.now().isoformat()
    with io.open(TERM_ALIAS_JSONL, "a", encoding="utf-8", newline="\n") as f:
        for t in terms:
            f.write(json.dumps({"op": "hit", "term": t, "ts": now},
                               ensure_ascii=False) + "\n")


def term_alias_stats():
    reg = _load_registry()
    return {"active": len(reg), "pairs": dict(list(reg.items())[:10])}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    reg_p = sub.add_parser("register")
    reg_p.add_argument("term")
    reg_p.add_argument("canonical")
    reg_p.add_argument("--origin", default="judge")
    reg_p.add_argument("--note", default="")
    exp_p = sub.add_parser("expand")
    exp_p.add_argument("query")
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "register":
        print(json.dumps(register_term_alias(a.term, a.canonical, a.origin, a.note),
                         ensure_ascii=False))
    elif a.cmd == "expand":
        print(json.dumps(expand_query(a.query), ensure_ascii=False))
    elif a.cmd == "stats":
        print(json.dumps(term_alias_stats(), ensure_ascii=False))