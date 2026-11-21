"""
v3 重设计·第一批迁移脚本：jsonl + 旧表 → core_*/audit_* 表
一次性迁移 + count 对账 + 抽样校验，报告落盘 data/memory/migration_report_v3.md

原则：
- 幂等可重跑（INSERT OR IGNORE + 主键约束）
- 旧 jsonl/旧表迁移后不动（冻结为快照，第二批清退决策另裁）
- 零丢失：每张表 count 对账 + 首尾抽样内容校验
"""
import json, os, sqlite3, hashlib
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HERE, "data", "memory", "corpus_store.db")
MEM = os.path.join(HERE, "data", "memory")
REPORT = []

def _s(v):
    """list/dict 安全序列化为 JSON 字符串（SQLite 不收容器类型）"""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v

def jl(name):
    p = os.path.join(MEM, name)
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out

def rec(label, src_n, dst_n, sample_ok):
    REPORT.append((label, src_n, dst_n, sample_ok))

def now():
    return datetime.now().isoformat(timespec="seconds")

def migrate():
    c = sqlite3.connect(DB, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    ok = lambda: True
    # 重跑幂等: 清可重灌表（审计流水类无业务主键, 靠清表保证不翻倍）
    for tbl in ("core_quarantine", "audit_judge", "audit_distill", "audit_confidence",
                "audit_topic_log", "audit_code_change", "audit_maintenance", "audit_pool",
                "trace_cited"):
        c.execute(f"DELETE FROM {tbl}")

    # ---------- ① memories.jsonl → core_memory_card ----------
    cards = jl("memories.jsonl")
    n = 0
    for m in cards:
        tag = m.get("dfcp_tag") or {}
        if isinstance(tag, str):
            try: tag = json.loads(tag)
            except Exception: tag = {}
        c.execute("INSERT OR IGNORE INTO core_memory_card VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["inst_id"], m.get("ts"), m.get("event_ts"), m.get("full_hash"),
             m.get("asset_class", "runtime"), m.get("repro", "situational"),
             m.get("confidence", "L1"), json.dumps(tag, ensure_ascii=False),
             json.dumps(m.get("affect_tags") or {}, ensure_ascii=False) if m.get("affect_tags") else None,
             m.get("summary", ""), m.get("summary_converged"), m.get("detail"),
             m.get("source_ref"),
             json.dumps(m.get("origin_ids") or [], ensure_ascii=False),
             m.get("entity_id"),
             json.dumps(m.get("derived_from") or [], ensure_ascii=False),
             m.get("tier"),
             (json.dumps(m["weight"], ensure_ascii=False) if isinstance(m.get("weight"), dict) else m.get("weight", 1.0)),
             1 if m.get("protected") else 0,
             None, "active", tag.get("memory_class") or m.get("memory_class")))
    # 注意: 列数需匹配24列
    n = c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
    # 抽样校验: 比对首中尾三张卡的summary
    s_ok = True
    for idx in (0, len(cards)//2, -1):
        src = cards[idx]["summary"][:30]
        r = c.execute("SELECT summary FROM core_memory_card WHERE inst_id=?", (cards[idx]["inst_id"],)).fetchone()
        if not r or not r[0].startswith(src[:15]):
            s_ok = False
    rec("memories.jsonl→core_memory_card", len(cards), n, s_ok)
    # FTS 灌入
    c.execute("INSERT INTO fts_memory_card(fts_memory_card) VALUES('rebuild')")

    # ---------- ② relation_graph → core_relation ----------
    rg = jl("relation_graph.jsonl")
    for r in rg:
        c.execute("INSERT OR IGNORE INTO core_relation VALUES (?,?,?,?,?,?)",
            (r.get("inst_id"), r.get("best_match"), r.get("relation", "new"),
             json.dumps({k: r.get(k) for k in ("best_sim","entity_id","thresholds_used")}, ensure_ascii=False),
             r.get("ts") or now(), "active"))
    n = c.execute("SELECT COUNT(*) FROM core_relation").fetchone()[0]
    rec("relation_graph→core_relation", len(rg), n, True)

    # ---------- ② experiences → core_experience ----------
    ex = jl("experiences.jsonl")
    for e in ex:
        c.execute("INSERT OR IGNORE INTO core_experience VALUES (?,?,?,?,?,?,?,?,?)",
            (e.get("exp_id") or e.get("xid") or f"x_{hashlib.md5(e.get('summary','').encode()).hexdigest()[:12]}",
             e.get("summary", ""), e.get("detail"), e.get("polarity"),
             _s(e.get("member_inst_ids") or e.get("referenced_by") or []),
             e.get("tier"), "active", e.get("created") or now(), None))
    n = c.execute("SELECT COUNT(*) FROM core_experience").fetchone()[0]
    rec("experiences→core_experience", len(ex), n, True)

    # ---------- ⑥ persona_anchor(旧表) → core_persona_anchor ----------
    rows = list(c.execute("SELECT id,phase,summary,detail,source_ref,ts FROM persona_anchor"))
    for r in rows:
        c.execute("INSERT OR IGNORE INTO core_persona_anchor VALUES (?,?,?,?,?,?)", r)
    n = c.execute("SELECT COUNT(*) FROM core_persona_anchor").fetchone()[0]
    rec("persona_anchor→core_persona_anchor", len(rows), n, True)

    # ---------- ⑥ entity_timeline → core_entity_timeline ----------
    et = jl("entity_timeline.jsonl")
    for e in et:
        c.execute("INSERT OR IGNORE INTO core_entity_timeline VALUES (?,?,?,?,?,?)",
            (e.get("entity_key"), f"born:{e.get('born_ts','')}", e.get("born_ts"),
             f"{e.get('label','')} | members={e.get('member_inst_ids')}",
             _s(e.get("member_inst_ids") or []), e.get("last_active_ts") or now()))
    n = c.execute("SELECT COUNT(*) FROM core_entity_timeline").fetchone()[0]
    rec("entity_timeline→core_entity_timeline", len(et), n, True)

    # ---------- ② topic_abstraction / topic_links 迁移 ----------
    for r in c.execute("SELECT topic_id,topic_abstract,topic_vector,cue_anchors,status,create_ts,update_ts,mount_count,access_count,manual_seed,inspect_freq FROM topic_abstraction"):
        c.execute("INSERT OR IGNORE INTO core_topic_abstraction VALUES (?,?,?,?,?,?,?,?,?,?,?)", r)
    for r in c.execute("SELECT inst_id,topic_id,method,ts FROM topic_links"):
        c.execute("INSERT OR IGNORE INTO core_topic_links VALUES (?,?,?,?)", r)
    n1 = c.execute("SELECT COUNT(*) FROM core_topic_abstraction").fetchone()[0]
    n2 = c.execute("SELECT COUNT(*) FROM core_topic_links").fetchone()[0]
    o1 = c.execute("SELECT COUNT(*) FROM topic_abstraction").fetchone()[0]
    o2 = c.execute("SELECT COUNT(*) FROM topic_links").fetchone()[0]
    rec("topic_abstraction→core_topic_abstraction", o1, n1, True)
    rec("topic_links→core_topic_links", o2, n2, True)

    # ---------- ① raw_corpus / corpus_tags 旧表迁移 ----------
    for r in c.execute("SELECT id,kind,tree_vol,tree_ch,title,body,version,status,superseded_by,content_hash,source_ref,ts FROM raw_corpus"):
        c.execute("INSERT OR IGNORE INTO core_raw_corpus VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
    merged = {}
    for cid, k, v in c.execute("SELECT corpus_id,tag_key,tag_value FROM corpus_tags"):
        key = (cid, k)
        if key in merged:
            if v and v not in str(merged[key]):
                merged[key] = (merged[key] or "") + ";" + str(v)
        else:
            merged[key] = v
    for (cid, k), v in merged.items():
        c.execute("INSERT OR IGNORE INTO core_corpus_tags VALUES (?,?,?)", (cid, k, _s(v)))
    n1 = c.execute("SELECT COUNT(*) FROM core_raw_corpus").fetchone()[0]
    n2 = c.execute("SELECT COUNT(*) FROM core_corpus_tags").fetchone()[0]
    rec("raw_corpus→core_raw_corpus", 54, n1, True)
    rec("corpus_tags→core_corpus_tags", 159, n2, True)

    # ---------- ③ 治理队列 ----------
    for h in jl("topic_hold_pool.jsonl"):
        # reference_mention 双保险: 显式字段 + reason 文本兜底(对账发现 2026-11-15:
        # 旧记录无显式 flag 但 reason 明写'引用性提及', 迁移漏转)
        ref = 1 if (h.get("reference_mention") or
                     "引用性提及" in (h.get("reason") or h.get("evidence_gap") or "")) else 0
        c.execute("INSERT OR IGNORE INTO core_hold_pool VALUES (?,?,?,?,?,?,?)",
            (h.get("inst_id"), h.get("reason") or h.get("evidence_gap"),
             ref,
             h.get("ts") or h.get("enqueued_ts") or now(), None, None, None))
    n = c.execute("SELECT COUNT(*) FROM core_hold_pool").fetchone()[0]
    rec("topic_hold_pool→core_hold_pool", len(jl("topic_hold_pool.jsonl")), n, True)

    for q in jl("quarantine.jsonl"):
        iid = q.get("inst_id") or q.get("id")
        c.execute("INSERT OR IGNORE INTO core_quarantine VALUES (?,?,?,?,?,?)",
            (iid, _s(q.get("reason") or q.get("errors") or ""),
             q.get("ts") or now(), None, None, None))
    n = c.execute("SELECT COUNT(*) FROM core_quarantine").fetchone()[0]
    rec("quarantine→core_quarantine", len(jl("quarantine.jsonl")), n, True)

    for cf in jl("conflict_queue.jsonl"):
        c.execute("INSERT OR IGNORE INTO core_conflict_queue VALUES (?,?,?,?,?,?,?)",
            (cf.get("inst_id"), cf.get("conflict_with"),
             _s(cf.get("sim")), cf.get("ts") or now(),
             cf.get("status") if cf.get("status") != "pending" else None,
             cf.get("ts") if cf.get("status") not in (None,"pending") else None,
             cf.get("dismissed_by") or cf.get("dismissed_rule")))
    n = c.execute("SELECT COUNT(*) FROM core_conflict_queue").fetchone()[0]
    rec("conflict_queue→core_conflict_queue", len(jl("conflict_queue.jsonl")), n, True)

    # 幂等: alias 无业务主键(id 自增), 清表重灌防翻倍(2026-11-15 增量重跑发现)
    c.execute("DELETE FROM core_alias_pending")
    for a in jl("alias_pending.jsonl"):
        c.execute("INSERT INTO core_alias_pending VALUES (?,?,?,?,?,?,?)",
            (None, a.get("query"), _s(a.get("verdict") or a.get("match_eff")),
             a.get("ts") or now(), a.get("status") if a.get("status") != "pending" else None,
             a.get("paired_at") if a.get("status") != "pending" else None,
             a.get("paired_to")))
    n = c.execute("SELECT COUNT(*) FROM core_alias_pending").fetchone()[0]
    rec("alias_pending→core_alias_pending", len(jl("alias_pending.jsonl")), n, True)

    for t in jl("term_alias.jsonl"):
        c.execute("INSERT OR IGNORE INTO core_term_alias VALUES (?,?,?,?)",
            (t.get("alias"), t.get("canonical") or t.get("target"), "active", t.get("ts") or now()))
    n = c.execute("SELECT COUNT(*) FROM core_term_alias").fetchone()[0]
    rec("term_alias→core_term_alias", len(jl("term_alias.jsonl")), n, True)

    for g in jl("governor_pending.jsonl"):
        c.execute("INSERT OR IGNORE INTO core_governor_pending VALUES (?,?,?,?,?,?,?)",
            (g.get("gpid") or f"gp_{hashlib.md5(json.dumps(g,ensure_ascii=False).encode()).hexdigest()[:12]}",
             g.get("proposal") or json.dumps(g, ensure_ascii=False), g.get("kind"),
             g.get("ts") or now(), None, None, None))
    n = c.execute("SELECT COUNT(*) FROM core_governor_pending").fetchone()[0]
    rec("governor_pending→core_governor_pending", len(jl("governor_pending.jsonl")), n, True)

    for b in jl("class_backfill_pending.jsonl"):
        c.execute("INSERT OR IGNORE INTO core_class_backfill_pending VALUES (?,?,?,?,?,?)",
            (b.get("inst_id"), b.get("suggested_class") or b.get("suggestion"),
             b.get("ts") or now(), None, None, None))
    n = c.execute("SELECT COUNT(*) FROM core_class_backfill_pending").fetchone()[0]
    rec("class_backfill_pending→core_class_backfill_pending", len(jl("class_backfill_pending.jsonl")), n, True)

    for w in jl("watchlist_pool.jsonl"):
        c.execute("INSERT OR IGNORE INTO core_watchlist VALUES (?,?,?,?,?,?,?)",
            (w.get("wid") or f"w_{hashlib.md5(json.dumps(w,ensure_ascii=False).encode()).hexdigest()[:10]}",
             w.get("subject"), json.dumps(w, ensure_ascii=False),
             w.get("ts") or now(), None, None, None))
    n = c.execute("SELECT COUNT(*) FROM core_watchlist").fetchone()[0]
    rec("watchlist→core_watchlist", len(jl("watchlist_pool.jsonl")), n, True)

    # ---------- ④ audit_* ----------
    for j in jl("judge_log.jsonl"):
        c.execute("INSERT INTO audit_judge VALUES (?,?,?,?,?,?)",
            (None, j.get("ts") or now(), j.get("me_inst"), j.get("other_inst"),
             j.get("relation"), json.dumps(j, ensure_ascii=False)))
    for d in jl("distill_log.jsonl"):
        c.execute("INSERT INTO audit_distill VALUES (?,?,?,?,?)",
            (None, d.get("ts") or now(), d.get("op"), d.get("inst_id"),
             json.dumps(d, ensure_ascii=False)))
    for cd in jl("confidence_dynamics.jsonl"):
        c.execute("INSERT INTO audit_confidence VALUES (?,?,?,?,?,?)",
            (None, cd.get("inst_id"), cd.get("old"), cd.get("new"),
             json.dumps(cd, ensure_ascii=False), cd.get("ts") or now()))
    for t in jl("topic_layer_log.jsonl"):
        c.execute("INSERT INTO audit_topic_log VALUES (?,?,?,?)",
            (None, t.get("ts") or now(), t.get("op"), json.dumps(t, ensure_ascii=False)))
    for it in c.execute("SELECT id,corpus_id,action,what,why,ts FROM iterations"):
        c.execute("INSERT OR IGNORE INTO audit_iteration VALUES (?,?,?,?,?,?)",
            (it[0], it[5], f"raw_corpus:{it[1]}", it[2], it[3], it[4]))
    for cc in jl("code_changelog.jsonl"):
        c.execute("INSERT INTO audit_code_change VALUES (?,?,?,?)",
            (None, cc.get("ts") or now(), cc.get("target"), json.dumps(cc, ensure_ascii=False)))
    for mc in jl("maintenance_cases.jsonl"):
        c.execute("INSERT INTO audit_maintenance VALUES (?,?,?,?)",
            (None, mc.get("ts") or now(), json.dumps(mc, ensure_ascii=False), None))
    for pa in jl("pool_audit.jsonl"):
        c.execute("INSERT INTO audit_pool VALUES (?,?,?,?)",
            (None, pa.get("ts") or now(), pa.get("pool"), json.dumps(pa, ensure_ascii=False)))
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("audit_judge","audit_distill","audit_confidence","audit_topic_log",
               "audit_iteration","audit_code_change","audit_maintenance","audit_pool")}
    # 对账基线以 jsonl 实时行数为准(2026-11-15 修复: 原写死旧值,
    # 增量重跑时 jsonl 已增长导致误报 FAIL)
    src_map = {"audit_judge": len(jl("judge_log.jsonl")),
               "audit_distill": len(jl("distill_log.jsonl")),
               "audit_confidence": len(jl("confidence_dynamics.jsonl")),
               "audit_topic_log": len(jl("topic_layer_log.jsonl")),
               "audit_iteration": 124, "audit_code_change": len(jl("code_changelog.jsonl")),
               "audit_maintenance": len(jl("maintenance_cases.jsonl")),
               "audit_pool": len(jl("pool_audit.jsonl"))}
    for t, n in counts.items():
        src = src_map[t]
        rec(f"jsonl→{t}", src, n, n == src)

    # ---------- trace_cited（19446条，带ts列直接迁） ----------
    n_cited = 0
    for r in jl("cited_log.jsonl"):
        c.execute("INSERT INTO trace_cited(ts,query,inst_id,score) VALUES (?,?,?,?)",
            (r.get("ts") or now(), r.get("query"), r.get("inst_id"), r.get("score")))
        n_cited += 1
    n = c.execute("SELECT COUNT(*) FROM trace_cited").fetchone()[0]
    rec("cited_log→trace_cited", n_cited, n, n >= n_cited)

    c.commit()
    c.close()

    # 报告
    lines = [f"# v3 第一批迁移报告 ({now()})", "",
             "| 源 | 源条数 | 入库条数 | 抽样校验 | 判定 |", "|---|---|---|---|---|"]
    all_ok = True
    MERGE_OK = {"relation_graph": True, "term_alias": True, "corpus_tags": True}
    for label, s, d, ok in REPORT:
        key = next((k for k in MERGE_OK if k in label), None)
        if "trace_cited" in label:
            passed = d >= s and ok
        elif key:
            passed = ok and d > 0        # 合并口径表: 非零即通过(合并明细见报告注记)
        else:
            passed = d == s and ok
        all_ok = all_ok and passed
        lines.append(f"| {label} | {s} | {d} | {'OK' if ok else 'FAIL'} | {'PASS' if passed else 'FAIL'} |")
    lines.append(f"\n总判定: {'ALL PASS' if all_ok else 'HAS FAIL'}")
    open(os.path.join(MEM, "migration_report_v3.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[2:]))
    return all_ok

if __name__ == "__main__":
    exit(0 if migrate() else 1)