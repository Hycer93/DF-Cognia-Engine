# memory_alias.py — 二级备用标签（别名）沉淀机制 v2
# 流程（2026-11-09 用户裁决，取代预填别名表提案）：
#   ① 别名查询失败 → 如实不知道/反问（不瞎补关键词）
#   ② 用户换词重问 → 确凿命中
#   ③ 捕获等价叫法 → 沉淀二级备用标签（append-only 叠加层）
#   ④ 下次旧别名直接命中
# v2 五风险裁决：L1 粗滤 / L2 弱交集 / L3 生存验证；六条件 ALL 判定。
# Week2 换基座待办：C3 的 0.15 与 C5 交集判据重标，C5 可升级语义相似度，
# 三层防御架构保留不变。

import json
import io
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import constants as C
from memory_organize import MEM_DIR, append_jsonl, load_memories, is_protected
from memory_relations import _embed, _cosine, load_graph, grams

ALIAS_PENDING_JSONL = os.path.join(MEM_DIR, "alias_pending.jsonl")
ALIAS_TAGS_JSONL = os.path.join(MEM_DIR, "alias_tags.jsonl")

PENDING_TTL_HOURS = 24      # C1 挂起池时效
PAIR_TOPIC_COS = 0.15        # C3 话题延续门槛 [先验-可校准，换基座必重标]
ALIAS_INACTIVE_DAYS = 30    # L3 零命中失效窗（读侧，不删行）
WEAK_OVERLAP_GRAMS = 1      # C5 弱交集：>=1 个 2-gram 重叠即"沾边"
_ALIAS_MIN_CHARS = 2


def record_miss(query, match_eff, verdict):
    """失败捕获（流程1）：uncertain/marginal_ask/empty 挂起原始查询。
    纯寒暄（match_eff 约 0）不挂。"""
    if verdict not in ("uncertain", "marginal_ask", "empty"):
        return
    if match_eff <= 0.02 and verdict == "empty":
        return
    append_jsonl(ALIAS_PENDING_JSONL, {
        "ts": datetime.now().isoformat(),
        "query": query[:200],
        "match_eff": round(match_eff, 4),
        "verdict": verdict,
        "status": "pending",
    })


def _load_pending():
    if not os.path.exists(ALIAS_PENDING_JSONL):
        return []
    return [json.loads(l) for l in open(ALIAS_PENDING_JSONL, encoding="utf-8") if l.strip()]


# 可解释性设施③a（2026-11-25）：pending 队列状态区分读取。
# 背景：alias_pending.jsonl 是 append-only，含终态记录(rejected_weak_overlap 等)，
# 直接 len() 会把"已终结"误算成"积压"（实测 231 条中 209 条是终态，真待处理仅 22）。
# 真积压 = 仍需人处理的状态（pending 待审 / paired 待确认 等活跃态）。
_PENDING_ACTIVE_STATES = ("pending", "paired", "marginal_ask", "uncertain")


def count_active_pending() -> dict:
    """返回 alias_pending 的【真积压】统计，区分活跃态 vs 终态历史。

    供 E4 巡检/报表用：只看 active（真待处理），不把 rejected_* 等终态计入积压。
    """
    rows = _load_pending()
    from collections import Counter
    by_status = Counter(r.get("status", "pending") for r in rows)
    active = sum(n for st, n in by_status.items()
                 if st in _PENDING_ACTIVE_STATES or not st.startswith("rejected"))
    return {
        "total_rows": len(rows),          # 物理行数（含终态历史）
        "active": active,                  # 真待处理
        "terminal": len(rows) - active,    # 终态历史（已拒/已终结，不算积压）
        "by_status": dict(by_status),
    }


def _load_alias_records():
    if not os.path.exists(ALIAS_TAGS_JSONL):
        return []
    return [json.loads(l) for l in open(ALIAS_TAGS_JSONL, encoding="utf-8") if l.strip()]


def _alias_last_activity(rec, recs):
    """最近活跃时间 = 沉淀 ts 与其后同 inst_id 全部 op=hit/reactivate 的最大 ts。"""
    last = _safe_date(rec.get("ts"))
    for r in recs:
        if r.get("op") in ("hit", "reactivate") and r["inst_id"] == rec["inst_id"]:
            t = _safe_date(r.get("ts"))
            if t and (last is None or t > last):
                last = t
    return last


def effective_alias_map():
    """生效别名视图（L3 读侧）：{inst_id: [alias,...]}。
    排除：显式 expire_inactive；30 天零命中（自动失效，物理行保留，人可复活）。"""
    now = datetime.now()
    recs = _load_alias_records()
    out = {}
    for rec in recs:
        if rec.get("op"):
            continue
        if rec.get("status") == "expire_inactive":
            continue
        if rec.get("hit_count", 0) == 0:
            last = _alias_last_activity(rec, recs)
            if last and now - last > timedelta(days=ALIAS_INACTIVE_DAYS):
                continue
        if rec.get("status") in (None, "active", "reactivated"):
            out.setdefault(rec["inst_id"], []).extend(rec.get("aliases", []))
    return out


def _safe_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return None


def reactivate_alias(inst_id, aliases=None):
    """人工复活接口（L3）：失效别名手动重新激活——append 状态记录，复活即续命起点。"""
    append_jsonl(ALIAS_TAGS_JSONL, {
        "ts": datetime.now().isoformat(), "inst_id": inst_id,
        "op": "reactivate", "aliases": aliases or [], "origin": "manual",
        "status": "reactivated", "hit_count": 1,
    })


def _extract_alias_terms(failed_query):
    """候选别名词提取（L1 粗滤——头道筛拦大头，不是安全边界；
    真正边界在 L2 弱交集 + L3 生存验证）。"""
    import re
    q = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?|最近|昨天|今天|上次|上次那个|刚才|刚刚|上周|上个月|这个月|本周", " ", failed_query)
    q = re.sub(r"能用吗|可以用吗|行吗|怎么样|是什么|是什么意思|哪个|哪些|能不|有没有|多少|是什么|什么|吗|呢|啊|么|呗|别的|其他|怎么|如何", " ", q)
    parts = re.split(r"[，。？！、\s的是了呢吧和跟把在有帮助我你他它想找查看看一下个哪种说说怎]", q)
    drop_pat = re.compile(r"^(为什么|哪|什么|能|可|会|要|有|是)")
    stop = getattr(C, "GATE_ANCHOR_STOPGRAMS", set())
    terms = []
    for p in parts:
        p = p.strip()
        if len(p) >= _ALIAS_MIN_CHARS and not drop_pat.match(p):
            if any(w in p for w in stop):
                continue
            terms.append(p)
    return terms


def _entity_text_pool(inst_id):
    """实体文本池（C5 比对面）= 摘要 U 旧别名 U 实体链邻居摘要。
    无 entity_id（用户风险1：没上户口的记忆）→ 退化为 摘要 U 旧别名，
    交集照样可算——没上户口也允许起外号，判据不依赖 entity_id 存在。"""
    mems = {m["inst_id"]: m for m in load_memories()}
    me = mems.get(inst_id)
    if me is None:
        return ""
    pool = (me.get("summary") or "")
    for rec in _load_alias_records():
        if rec["inst_id"] == inst_id and rec.get("aliases"):
            pool += " " + " ".join(rec["aliases"])
    ent = me.get("entity_id")
    if ent:
        for g in load_graph():
            if g.get("entity_id") == ent and g["inst_id"] != inst_id:
                nb = mems.get(g["inst_id"])
                if nb:
                    pool += " " + (nb.get("summary") or "")
    return pool


def weak_entity_overlap(terms, inst_id):
    """C5 弱交集（用户风险2：沾边就算，不要求文字相等）。
    任一候选词与实体文本池有 >=1 个 2-gram 重叠即过。
    「SPI调试模块」vs「SPI硬件注入调试组件」共享多枚 2-gram，通过；
    套话「方案定」vs SPI 条目零重叠，拦——不靠名单靠结构。"""
    pool_grams = grams(_entity_text_pool(inst_id))
    if not pool_grams:
        return False, {"reason": "empty_pool"}
    for t in terms:
        tg = grams(t)
        if len(tg & pool_grams) >= WEAK_OVERLAP_GRAMS:
            return True, {"term": t, "overlap": sorted(tg & pool_grams)[:4]}
    return False, {"reason": "no_overlap"}


def try_pair_on_hit(query, hit_entry, top_match):
    """成功配对：六条件 ALL 判定（用户风险4——全部同时满足才生成，少一条拒绝；
    改代码严禁放松成满足其中几条）。C2/C6 目标级入口先判；C1/C3/C4/C5 逐挂起查询判。"""
    if top_match < C.GATE_HARD_PASS:
        return {"paired": 0, "rejected": "C2_not_hard_pass"}
    if is_protected(hit_entry):
        return {"paired": 0, "rejected": "C6_protected_target"}

    now = datetime.now()
    cutoff = now - timedelta(hours=PENDING_TTL_HOURS)
    qvec = _embed(query)
    qgrams = grams(query)

    paired, rejected = 0, []
    pending = _load_pending()
    survivors = []
    for p in pending:
        if p.get("status") != "pending":
            survivors.append(p)
            continue
        pts = _safe_date(p.get("ts"))
        if pts is None:
            survivors.append(p)
            continue
        if pts < cutoff:
            continue  # C1 超时：读侧过期（不删行）
        pvec = _embed(p["query"])
        cos = _cosine(pvec, qvec)
        pgr = grams(p["query"])
        subset = bool(pgr) and pgr <= qgrams
        if not (cos >= PAIR_TOPIC_COS or subset):
            survivors.append(p)
            continue
        terms = _extract_alias_terms(p["query"])
        if not terms:
            survivors.append(p)
            continue
        ok, detail = weak_entity_overlap(terms, hit_entry["inst_id"])
        if not ok:
            rejected.append({"query": p["query"][:60], "fail": detail})
            p["status"] = "rejected_weak_overlap"
            p["rejected_at"] = now.isoformat()
            survivors.append(p)
            continue
        append_jsonl(ALIAS_TAGS_JSONL, {
            "ts": now.isoformat(),
            "inst_id": hit_entry["inst_id"],
            "aliases": terms,
            "derived_from_query": p["query"][:200],
            "paired_with_query": query[:200],
            "origin": "user-feedback",
            "status": "active",
            "hit_count": 0,
            "conditions_passed": {
                "C1_pending_ttl": True, "C2_hard_pass": True,
                "C3_topic_cos": round(cos, 4), "C4_terms_nonempty": True,
                "C5_weak_overlap": detail, "C6_not_protected": True,
            },
        })
        paired += 1
        p["status"] = "paired"
        p["paired_at"] = now.isoformat()
        p["paired_to"] = hit_entry["inst_id"]
        survivors.append(p)
    with io.open(ALIAS_PENDING_JSONL, "w", encoding="utf-8", newline="\n") as f:
        for p in survivors:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return {"paired": paired, "rejected_weak_overlap": rejected}


def alias_lookup(query):
    """检索辅助：{inst_id: [命中的别名词]}——只看生效视图（L3 读侧）。"""
    qgrams = grams(query)
    hits = {}
    for iid, aliases in effective_alias_map().items():
        matched = [a for a in aliases if a in query or grams(a) & qgrams]
        if matched:
            hits[iid] = matched
    return hits


def mark_alias_hit(query):
    """L3 写侧：查询击中别名 → 追加 op=hit 记录（append-only）。命中即续命。"""
    now = datetime.now()
    qgrams = grams(query)
    seen = set()
    for rec in _load_alias_records():
        if rec.get("op") or rec["inst_id"] in seen:
            continue
        for a in rec.get("aliases", []):
            if a in query or grams(a) & qgrams:
                append_jsonl(ALIAS_TAGS_JSONL, {
                    "ts": now.isoformat(), "inst_id": rec["inst_id"],
                    "op": "hit", "aliases": [a], "origin": "survival-refresh",
                })
                seen.add(rec["inst_id"])
                break
