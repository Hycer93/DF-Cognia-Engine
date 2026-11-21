# memory_query.py — 读侧检索投影（三层架构之读出口）
# 实现：四维加权（编码匹配0.30[缺省折减] + 置信度0.25 + 调用统计0.25 + 关联激活度0.20）
#       三检字法并列入口（领域/行为/关联任选直达，树间不串联——v1.4-1）
#       查询期联表（领域×行为交叉=只读视图，不写编码——v1.4-3）
#       同 entity 互斥去重（top_k 内同本体只留最高分——防同源占坑）
#       摘要双词召回（专有锚点+上位概念词参与关键词匹配=叶子正式地位）

import json
import io
import os
import sys
from datetime import datetime, timedelta, date
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema
import constants as C
from memory_organize import load_memories as _load_memories_jsonl, MEM_DIR, append_jsonl
from memory_relations import _embed, _cosine, load_graph as _load_graph_jsonl, grams
import embed_semantic as _es
import memory_alias as ma
import topic_layer            # Topic锚层路由(规约2; 开关默认关, 零行为变化)
from corpus_registry import load_registry

# ---- 读源切换开关 (2026-11-15 用户批准切换, 闸门点 #31) ----
# 默认读 SQLite 主库(corpus_store.db, 唯一真相); 应急回切:
#   设 DF_MEMORY_SOURCE=jsonl 后进程重启即回 jsonl 链路(冻结快照仍在)。
# 三道准入门(eval/shadow_regress|governance_reconcile|paradigm_read_regress)
# 切换前已全 PASS; 双路 diff 报告见 logs/shadow_regress_report.md。
import sqlite3 as _sq

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "memory", "corpus_store.db")
_MEMORY_SOURCE = os.environ.get("DF_MEMORY_SOURCE", "sqlite")


def load_memories() -> list:
    """读源分派: sqlite(新主库) / jsonl(应急回切)。口径对齐 shadow_regress 双路验证。"""
    if _MEMORY_SOURCE == "jsonl":
        return _load_memories_jsonl()
    con = _sq.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
    con.row_factory = _sq.Row
    rows = con.execute("SELECT * FROM core_memory_card WHERE status='active'").fetchall()
    con.close()
    import json as _j
    out = []
    for r in rows:
        d = dict(r)
        for k in ("dfcp_tag", "affect_tags", "origin_ids", "weight"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = _j.loads(d[k])
                except ValueError:
                    pass
        out.append(d)
    return out


def load_graph() -> list:
    if _MEMORY_SOURCE == "jsonl":
        return _load_graph_jsonl()
    con = _sq.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
    rows = con.execute("SELECT * FROM core_relation WHERE status='active'").fetchall()
    con.close()
    import json as _j
    out = []
    for r in rows:
        # 列序: me_inst, other_inst, relation, judge_meta, ts, status
        meta = _j.loads(r[3] or "{}")
        out.append({"ts": r[4], "inst_id": r[0], "entity_id": meta.get("entity_id"),
                    "relation": r[2], "best_match": r[1], "best_sim": meta.get("best_sim")})
    return out


# ---- 统一索引层·治理过滤（2026-11-12 用户裁决：DFCP 管召回打分，治理管后置过滤）----
# 流水线：召回(DFCP) → 匹配打分(DFCP) → 治理规则过滤(registry/资产标签) → 返回
# 不合并编码：治理属性（可否蒸馏/虚构/作废）是批量管控开关，绝大多数单次推理不碰，
# 留外部元数据层（升格判据：每次推理都用到且直接参与相似度计算才进 DFCP——如 confidence）
_GOV_CACHE = {"mtime": None, "families": None}


def _governance_families() -> dict:
    """registry → 治理映射（文件 mtime 缓存，一次加载全检索复用）。
    返回 {族id: {sim_ok, distill_ok, readonly, fiction}}——fiction 由 content_type 判定。"""
    import os as _os
    p = _os.path.join(MEM_DIR, "corpus_registry.jsonl")
    try:
        mtime = _os.path.getmtime(p)
    except OSError as e:
        # P2-5: 原为 return {} 静默; registry 缺失会让治理映射整体失效, 须留痕
        print(f"[governance_registry_stat_fail] {p}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return {}
    if _GOV_CACHE["mtime"] == mtime:
        return _GOV_CACHE["families"]
    fams = {}
    for r in load_registry():
        ct = r.get("content_type") or ""
        fams[r["corpus_id"]] = {
            "sim_ok": bool(r.get("sim_ok")),
            "distill_ok": bool(r.get("distill_ok")),
            "fiction": "novel" in ct or "fiction" in ct,
        }
    _GOV_CACHE["mtime"] = mtime
    _GOV_CACHE["families"] = fams
    return fams


def _card_family(mem: dict, families: dict) -> str:
    """摘要卡 → 治理族：source_ref 可解析树地址则按语料 kind 归族；
    旧式资产地址 → 按血缘映射归族（P0-3 收口，2026-11-12）；
    真正无地址 → runtime 族（无治理限制，正常参与）。

    旧式地址映射依据（assets 文件 src 字段考证）：
    - sft_s7_corpus3_assets#S7-01~10 → corpus3_matrix_snapshot（origin3/origin3a 快照血缘；
      注意 corpus3_source_raw=ds_share3 是另一份语料，sim_ok=False，勿混）
    - sft_s7_corpus3_assets#S7-11/12 → corpus2_guiyuanzai_patents（origin-rXXX 专利族血缘）
    - origin0-rXXX → corpus1_distill_master（投资会话语料）"""
    ref = mem.get("source_ref") or ""
    if ref.startswith("sqlite:corpus_store") and "#" in ref:
        node = ref.split("#", 1)[1].split(".")[0].split(" ")[0]
        if node == "novel_szt":
            return "novel_szt_chapters"
        if node == "patent":
            return "patent_disclosures"
    # P0-3: 旧式资产地址归族（消除 corpus 卡绕治理的特例）
    if ref.startswith("sft_s7_corpus3_assets.jsonl#"):
        sid = ref.split("#", 1)[1]
        return ("corpus2_guiyuanzai_patents" if sid in ("S7-11", "S7-12")
                else "corpus3_matrix_snapshot")
    if ref.startswith("origin0-"):
        return "corpus1_distill_master"
    return "runtime"

# ---- 置信度 L0-L4 → 归一分（L0=1.0, L4=0）----
CONF_SCORE = {"L0": 1.0, "L1": 0.75, "L2": 0.5, "L3": 0.25, "L4": 0.0}


def _recency_cited_score(entry: dict) -> float:
    """时间×调用混合分（人类习惯①：最近话题主语优先）。
    调用统计不再是裸计数，而是按半衰期衰减：7天内调用满权重，每过一个半衰期减半。
    依据 cited_log.jsonl 的调用时间戳计算，无日志条目得 0。
    性能（2026-11-11）：全库逐条扫日志 O(N×M) 1.8M 次 json.loads；
    改 ask 级一次扫日志建 inst→score 表后查表 O(N+M)。"""
    inst = entry["inst_id"]
    table = _recency_table()
    return table.get(inst, 0.0)


_recency_cache = {"mtime": 0.0, "table": {}}


def _recency_table() -> dict:
    """引用流水 → {inst_id: score}。读源分派(2026-11-15 切换):
    sqlite: trace_cited(主库, 按表行数+MAX(tid)缓存键);
    jsonl:  cited_log.jsonl(应急回切, 按 mtime 缓存)。
    E8 自考自测的调用（source=e8）不计入——防影子对话把考过的题喂肥霸榜。"""
    cutoff = datetime.now().timestamp() - 7 * 86400
    halflife = C.RECENCY_HALFLIFE_DAYS * 86400
    now = datetime.now().timestamp()

    def _score(ts_epoch):
        decay = 0.5 ** (max(0, now - ts_epoch) / halflife)
        return min(1.0, 0.5 + decay * 0.5)

    if _MEMORY_SOURCE == "sqlite":
        con = _sq.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
        n, max_tid = con.execute("SELECT COUNT(*), COALESCE(MAX(tid),0) FROM trace_cited").fetchone()
        key = (n, max_tid)
        con.close()
        if _recency_cache.get("sqlite_key") == key:
            return _recency_cache["table"]
        best = {}
        con = _sq.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
        for ts_str, inst, src in con.execute("SELECT ts, inst_id, source FROM trace_cited"):
            if src == "e8":
                continue
            if not inst or not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
            except ValueError:
                continue
            if ts < cutoff:
                continue
            best[inst] = max(best.get(inst, 0.0), _score(ts))
        con.close()
        _recency_cache["sqlite_key"] = key
        _recency_cache["table"] = best
        return best

    # jsonl 应急路径(原实现)
    path = os.path.join(MEM_DIR, "cited_log.jsonl")
    if not os.path.exists(path):
        return {}
    mt = os.path.getmtime(path)
    if _recency_cache["mtime"] == mt:
        return _recency_cache["table"]
    best = {}
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("source") == "e8":
                continue          # E8 演习调用不喂肥 recency
            ts_str = rec.get("ts")
            inst = rec.get("inst_id")
            if not inst or not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
            except ValueError:
                continue
            if ts < cutoff:
                continue
            best[inst] = max(best.get(inst, 0.0), _score(ts))
    _recency_cache["mtime"] = mt
    _recency_cache["table"] = best
    return best


def _code_match_score(query_tag: dict, entry: dict) -> float:
    """编码匹配度（权重最高）：按命中层数得分 + L3缺省折减。
    query_tag 各位可选：{'domain':'005-001', 'behavior':'Ht', 'relation':'P'}"""
    score = 0.0
    used = 0
    if query_tag.get("domain"):
        used += 1
        qd, ed = query_tag["domain"], entry["dfcp_tag"]["domain"]
        if qd == ed:
            score += 1.0
        elif qd.split("-")[0] == ed.split("-")[0]:
            score += 0.6 if qd.split("-")[0] == qd.split("-")[0] else 0
        # 同域不同子域给 0.5：先验导航仍有价值
            score = score  # 保持上面分支
    if query_tag.get("behavior"):
        used += 1
        score += 1.0 if query_tag["behavior"] == entry["dfcp_tag"]["behavior"] else 0.0
    if query_tag.get("relation"):
        used += 1
        score += 1.0 if query_tag["relation"] == entry["dfcp_tag"]["relation"] else 0.0
    # 第五维 memory_class（件② 2026-11-10 用户终裁，初始权重 0.05 锁定，纳入夜间标定）：
    # null 条目该维不参与（不扣分不强行匹配——S015：普通记录照常语义召回，不进专项视图）；
    # 查询带 class 且条目非空命中 → 加分；查询带 class 条目为 null → 维度归零不进分母。
    if query_tag.get("memory_class"):
        if entry["dfcp_tag"].get("memory_class"):
            # 权重0.05锁定（件②用户裁决）：命中纯增益不进分母，桶内其余维1.0、class维0.05——
            # 独立小权重=五类筛选的导航提示，不与三大思维坐标等权稀释
            score += C.W_MEMORY_CLASS if query_tag["memory_class"] == entry["dfcp_tag"]["memory_class"] else 0.0
        # else: 条目 null → 该维跳过（用户裁决：空值不扣分不强行匹配）
    if used == 0:
        return 0.0
    raw = score / used
    # L3 缺省折减：查询带场景位但条目编码无场景位（L3未启用态）→ 折减（盲测sim1 DS建议）
    if query_tag.get("domain") and "-" in query_tag["domain"] and len(query_tag["domain"].split("-")) == 3:
        if len(entry["dfcp_tag"]["domain"].split("-")) == 2:
            raw *= C.L3_ABSENT_PENALTY
            # 差额概念上补到关键词匹配（此处仅折减，关键词项独立计分已覆盖）
    return raw


def _keyword_score(query: str, entry: dict, _fw_block: bool = False,
                   return_detail: bool = False):
    """摘要关键词匹配（叶子正式地位：专有锚点+上位词都长在摘要里）。
    双向召回：2-gram 重叠 + 1-gram 锚点词召回（短锚点如"预告/3090"2-gram会拆碎）。
    停用词过滤（2026-11-09 E2 尸检）：会话泛指词（我们/方案/那个…）不贡献匹配证据——
    corpus 长引语里这些词必然出现，'我们之前讨论的那个方案定了没'曾靠 ['我们','方案']
    拿到 kw=0.363 误放行。匹配证据必须是专有实体词。
    残渣碎片防御（2026-11-11 bge 切换后 E2 二轮尸检）：停用词切走后残留跨词 bigram
    （'的那'/'了没'/'定了'）是汉语高频连接碎片，任何长摘要都撞——碎片不贡献证据：
    只有 ≥2 个连续 gram 命中（≈真词或真短语）才计入 g2。
    框架词组分流（2026-11-13 阶段二）：GATE_FRAMEWORK_GRAMS 模板词单独隔离，
    主池空时按 FRAMEWORK_KW_WEIGHT 极低兜底；sem 有效时由 ask 循环联合屏蔽。"""
    s = entry["summary"] or ""
    stop = getattr(C, "GATE_ANCHOR_STOPGRAMS", set())
    fw_table = getattr(C, "GATE_FRAMEWORK_GRAMS", set())
    qgrams = grams(query) - stop
    sgrams = grams(s) - stop
    if not qgrams or not sgrams:
        return (0.0, False) if return_detail else 0.0
    # ngram 打标分流（2026-11-13 阶段二，用户方案）：实体 gram 进主池，
    # 框架 gram（标题模板词）单独隔离计 fw——两套系数不混算。
    inter_all = qgrams & sgrams
    inter = inter_all - fw_table          # 实体主池交集
    fw_hits = inter_all & fw_table        # 框架词组命中
    if not inter_all:
        return (0.0, False) if return_detail else 0.0
    # 碎片过滤：命中 gram 拼回查询原文里必须是「连续片段」（真词/短语），
    # 孤立 1 个 gram 且不构成连续 2-gram 链的按碎片丢弃
    q_seq = query.replace(" ", "")
    solid = 0
    for g in inter:
        i = q_seq.find(g)
        if i < 0:
            continue
        chain = any(q_seq[i + k:i + k + 2] in inter for k in (-2, 1) if 0 <= i + k and i + k + 2 <= len(q_seq))
        if chain:
            solid += 1
    if solid == 0:
        # 整个交集全是孤立碎片：降级用 1-gram 锚点判（短锚点场景），g2 记 0
        g2 = 0.0
    else:
        g2 = solid / len(qgrams)
    # 1-gram：查询单字/短词直击摘要（专有锚点常为2字词，其2-gram只有1个，容忍度低）
    qchars = set(query)
    schars = set(s)
    hit1 = sum(1 for c in "预告/痛/办法/情感/复盘/记忆/纠错/部署/选型" if c and c in qchars and c in schars)
    # 通用改法：查询与摘要的字符交集率（Jaccard-1gram）——同样剔除停用词涉及的单字池
    q_chars_eff = set("".join(qgrams))
    s_chars_eff = set("".join(sgrams))
    j1 = len(q_chars_eff & s_chars_eff) / max(1, len(q_chars_eff)) if q_chars_eff else 0.0
    # j1 同样被碎片抬高（'了'/'定'单字交集率虚高）——碎片场景 j1 减半
    j1 = j1 * (0.5 if solid == 0 else 1.0)
    main = g2 * 0.5 + j1 * 0.5
    # 框架屏蔽规则（用户方案核心开关）：实体/语义任意一路有有效信号 → 框架词
    # 分数归零，不允许句式模板拉高分数挤占 top 候选；仅主池全空（g2=0 且 j1 近零）
    # 时按 FRAMEWORK_KW_WEIGHT 极低兜底——保留全链路极弱资格而非一刀切删除。
    fw = 0.0
    fw_only = False
    if fw_hits and g2 == 0.0 and j1 < 0.10:
        fw = (len(fw_hits) / len(qgrams)) * C.FRAMEWORK_KW_WEIGHT
        fw_only = True
    # 用户规则（阶段二核心开关）：精确锚/别名/语义任意一路有效信号 → 框架词
    # kw 归零。sem 在 ask 主循环才算，本函数看不到 → fw_only 标记外传
    # （return_detail=True 返回 (分数, fw_only)），由 ask 循环拿 sem 联合判定。
    score = main + (0.0 if _fw_block else fw)
    if return_detail:
        return score, fw_only
    return score


def _relation_activation(entry: dict, graph: list) -> float:
    """关联激活度：同 entity_id 的条目数（本体链厚度）+ 被引用计数。"""
    ent = entry.get("entity_id")
    if not ent:
        return 0.0
    same_ent = sum(1 for g in graph if g.get("entity_id") == ent)
    cited = entry.get("weight", {}).get("cited", 0)
    return min(1.0, (same_ent - 1) * 0.2 + cited * 0.15)


def _has_session_anchor(query: str, query_tag: dict, out: list) -> str:
    """边缘带二次校验（2026-11-09 方案C）：判断"真实指代存在性"，不看 bias。
    放行条件（任一满足，返回锚类型；全不满足返回 ""）：
    1. "code"   —— query_tag 编码先验非空：用户明确指定了检字法入口（领域/行为/关联）
    2. "recent" —— 查询与近期调用条目（cited_log 近7天）summary 的 2-gram 重叠
                   ≥ GATE_ANCHOR_OVERLAP：指代词确实锚到了被调过的记忆
    3. "chain"  —— top 候选与查询存在专有锚点交集（char 2-gram ≥ 阈值），
                   且该候选 entity 有本体链厚度（entity_id 重复 ≥2）——实体真实存在
    依据：E2 负例全无实体锚（"今天天气""帮我写周报"），正例长尾全有锚
    （"预告/3090"等专有词锚点），锚点存在性才是"能不能看懂指代"的真判据。"""
    if query_tag and any(query_tag.get(k) for k in ("domain", "behavior", "relation")):
        return "code"
    # 近期调用锚（停用泛指词过滤：会话套话不算实体锚——E2 漏报尸检 2026-11-09）
    qgrams = grams(query) - C.GATE_ANCHOR_STOPGRAMS
    recent_summaries = _recent_cited_summaries()
    for s in recent_summaries:
        if len(qgrams & grams(s)) >= C.GATE_ANCHOR_OVERLAP:
            return "recent"
    # 候选实体链锚
    if out:
        _, top_m, _ = out[0]
        sgrams = grams(top_m.get("summary") or "")
        if len(qgrams & sgrams) >= C.GATE_ANCHOR_OVERLAP and top_m.get("entity_id"):
            return "chain"
    return ""


def _recent_cited_summaries(days: int = 7) -> list:
    """近 N 天被调用过的条目 summary（会话上下文锚点池）。
    读 cited_log.jsonl 反查 memories，无日志/无条目返回空表。"""
    cutoff = datetime.now().timestamp() - days * 86400
    path = os.path.join(MEM_DIR, "cited_log.jsonl")
    insts = set()
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            try:
                ts = datetime.fromisoformat(rec["ts"]).timestamp()
            except (ValueError, KeyError):
                continue
            if ts >= cutoff and rec.get("inst_id"):
                insts.add(rec["inst_id"])
    if not insts:
        return []
    return [m.get("summary") or "" for m in load_memories() if m.get("inst_id") in insts]


# ---- 时间感知（2026-11-09 时间概念工程：记忆需要时间维度）----

# 时间表达式 → 相对今天的日期窗（from_date, to_date）。解析失败返回 None。
# 支持形态：上周/上个月/最近N天/最近一周/N月初/N月末/今天/昨天/YYYY-MM-DD/YYYY年N月N日
def _parse_time_window(query: str, today: datetime = None):
    """查询侧时间表达式解析。返回 (from_date, to_date, expr) 或 None。
    口径：闭区间 [from, to]，全部相对'今天'。"""
    today = today or datetime.now()
    import re as _re

    def _d(y, m, d):
        try:
            return datetime(y, m, d).date()
        except ValueError:
            return None

    # 显式日期
    m = _re.search(r"(2026|\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?", query)
    if m:
        d0 = _d(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d0:
            return d0, d0, m.group(0)
    # N月初 / N月末 → 近似月首/月尾（corpus 时间粒度只到月）
    m = _re.search(r"(\d{1,2})月初", query)
    if m:
        mo = int(m.group(1))
        f = _d(today.year, mo, 1)
        t = _d(today.year, mo, 10)
        if f:
            return f, t, m.group(0)
    m = _re.search(r"(\d{1,2})月末", query)
    if m:
        mo = int(m.group(1))
        f = _d(today.year, mo, 20)
        t = _d(today.year, mo, 28)
        if f:
            return f, t, m.group(0)
    # 相对窗
    if "上周" in query:
        mon = today.date() - timedelta(days=today.weekday() + 7)
        sun = mon + timedelta(days=6)
        return mon, sun, "上周"
    if "昨天" in query:
        y = today.date() - timedelta(days=1)
        return y, y, "昨天"
    if "今天" in query or "刚才" in query or "刚刚" in query:
        return today.date(), today.date(), "今天"
    m = _re.search(r"最近(\d{1,3})\s*[天日]", query)
    if m:
        n = int(m.group(1))
        return today.date() - timedelta(days=n - 1), today.date(), m.group(0)
    if "最近一周" in query or "这周" in query or "本周" in query:
        return today.date() - timedelta(days=6), today.date(), "最近一周"
    if "上个月" in query or "上月" in query:
        mo = today.month - 1 or 12
        yr = today.year if today.month > 1 else today.year - 1
        return _d(yr, mo, 1), _d(yr, mo, 28), "上个月"
    if "这个月" in query or "本月" in query:
        return _d(today.year, today.month, 1), today.date(), "这个月"
    # "最近"（无量化）→ 宽窗 30 天
    if "最近" in query or "近来" in query:
        return today.date() - timedelta(days=29), today.date(), "最近"
    return None


def _load_event_ts_overlay() -> dict:
    """存量回填叠加层（append-only 纪律：不改主记录，inst_id → event_ts）。"""
    path = os.path.join(MEM_DIR, "event_ts_backfill.jsonl")
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            rec = json.loads(line)
            out[rec["inst_id"]] = rec.get("event_ts")
    return out


def _entry_event_date(entry: dict, overlay: dict):
    """条目事件日期（优先 event_ts 字段 → 回填叠加层 → None=时间未知）。
    返回 date 或 None。ts（入库时间）不作为事件时间——迁移批量 ts 是伪时间。"""
    et = entry.get("event_ts") or overlay.get(entry["inst_id"])
    if not et:
        return None
    try:
        return datetime.fromisoformat(str(et)[:10]).date()
    except ValueError:
        try:
            return datetime.strptime(str(et)[:10], "%Y%m%d").date()
        except ValueError:
            return None


# ---- 实体时间区间层（2026-11-09 未来/演进时间工程）----

def load_entity_timeline() -> list:
    """实体时间区间（项目/模块 = 跨记忆聚合体，born_ts~last_active_ts）。
    data/memory/entity_timeline.jsonl 每行：entity_key/label/born_ts/last_active_ts/
    member_inst_ids/status。回答'X 什么时候开始的/现在怎么样/演进过程'的聚合基础。"""
    path = os.path.join(MEM_DIR, "entity_timeline.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def entity_evolution(entity_key: str, as_of: date = None) -> dict:
    """实体演进视图（2026-11-09）：'认知引擎现在演变成什么样'的正确打开方式。
    返回 {entity, born_ts, last_active_ts, milestones:[(event_ts, summary)...]}。
    milestones 按 event_ts 升序 = 演进弧；as_of 截止日过滤（回答'截至X时'）。

    as_of 切片语义（2026-11-09 用户裁决=B 认知视角）：
    event_ts ≤ as_of **AND** ts(入库时间) ≤ as_of 双过滤——回答"当时的我知道什么"。
    事后补录（5月事件11月入库）不出现在5月切片，按入库时间归位到11月切片。
    固有边界：只看当时已定稿入库的记忆，未定稿草稿讨论天然看不到，
    不承诺完整复原当时全部思考过程（风险5明示）。"""
    tl = {e["entity_key"]: e for e in load_entity_timeline()}
    ent = tl.get(entity_key)
    if not ent:
        return {"error": f"实体不存在: {entity_key}", "known": list(tl.keys())}
    overlay = _load_event_ts_overlay()
    mems = {m["inst_id"]: m for m in load_memories()}
    milestones = []
    for iid in ent.get("member_inst_ids", []):
        m = mems.get(iid)
        if not m:
            continue
        d = _entry_event_date(m, overlay)
        # B 认知视角双过滤（2026-11-09 裁决）：事件时间 AND 入库时间都须 ≤ as_of。
        # 入库时间用主记录 ts 前10位（迁移批量 ts 是伪时间，但认知视角关心的是
        # "这条记忆当时是否已在库中"，ts 即库中存在性的事实源）。
        if as_of:
            try:
                stored = datetime.fromisoformat(m["ts"][:10]).date()
            except ValueError:
                stored = None
            if (d is None or d > as_of) or (stored is None or stored > as_of):
                continue
        milestones.append((str(d) if d else None, (m.get("summary") or "")[:60]))
    # 有时间的排前面按时间升序，无时间的殿后
    milestones.sort(key=lambda x: (x[0] is None, x[0] or ""))
    return {"entity": entity_key, "label": ent.get("label"),
            "born_ts": ent.get("born_ts"), "last_active_ts": ent.get("last_active_ts"),
            "status": ent.get("status"), "n_milestones": len(milestones),
            "milestones": milestones}


def _time_filter_score(entry: dict, win, overlay: dict):
    """时间窗命中打分（2026-11-09 时间概念工程）。
    返回 (hit, score, label)：
    - 窗内 → (True, 1.0, 'in_window')
    - 时间未知(event_ts=None) → (False, C.TIME_UNKNOWN_SCORE, 'time_unknown')——
      不硬排除（时间线索缺失是存量数据现实），降权参与；纯语义查询无窗时全 1.0
    - 窗外 → (False, 0.0, 'out_of_window')
    """
    if win is None:
        return True, 1.0, "no_window"
    f, t, _ = win
    d = _entry_event_date(entry, overlay)
    if d is None:
        return False, C.TIME_UNKNOWN_SCORE, "time_unknown"
    if f <= d <= t:
        return True, 1.0, "in_window"
    return False, 0.0, "out_of_window"


def ask(query: str, query_tag: dict = None, top_k: int = 5, include_corpus: bool = True,
        include_fiction: bool = False, negative_channel: bool = False, debug: bool = False,
        cite_source: str = "human") -> list:
    """四维加权检索。query=自然语言；query_tag=可选编码先验（任一检字法入口）。
    返回 top_k 条目摘要列表（同 entity 互斥去重）。
    cite_source：调用方标记（human=真实问询 / e8=影子对话演习）——
    E8 调用打标，recency 统计排除，防演习自肥霸榜（2026-11-11）。
    include_fiction（2026-11-12 统一索引层）：默认 False——虚构小说卡
    （novel_szt 族）命中后由治理过滤筛除，不进入人格/推理上下文；
    用户显式要聊小说时传 True 放行。治理规则不进打分（批量管控开关，
    非每条推理必用——升格判据见模块头注释）。
    时间感知（2026-11-09）：查询含时间表达式（上周/最近/5月末…）时自动开时间窗，
    窗内条目加成、窗外归零、时间未知降权；verdict 带 time_window 元信息。"""
    query_tag = query_tag or {}
    mems = load_memories()
    if not include_corpus:
        mems = [m for m in mems if m.get("asset_class") != "corpus"]
    graph = load_graph()
    # entity_id 以图谱末值为准（回填走 changelog，主记录字段滞后——
    # 不修正则去重失效：同本体姊妹条全部入榜挤占 top_k。memory_distill 同款修正）
    ent_latest = {}
    for g in graph:
        ent_latest[g["inst_id"]] = g.get("entity_id")
    for m in mems:
        if m["inst_id"] in ent_latest:
            m["entity_id"] = ent_latest[m["inst_id"]]

    # 时间窗解析（2026-11-09 时间概念工程）
    win = _parse_time_window(query)
    overlay = _load_event_ts_overlay()
    ask.last_time_window = ({"from": str(win[0]), "to": str(win[1]), "expr": win[2]}
                            if win else None)

    # bge 批量预热：全库 summary 一次嵌入进缓存（159 条单跑 ~8s → 批跑 <1s）
    _es.warmup([m.get("summary") or "" for m in mems])
    qvec = _embed(query)
    # 二级备用标签（2026-11-09 用户裁决：别名来自真实查询-命中反馈，不预填）：
    # 查询词直接击中别名的条目，别名词拼进 kw 匹配池（只提召回，不动门控公式）
    alias_hits = ma.alias_lookup(query)
    # Topic 锚层路由（规约2, 2026-11-13 design_topic_layer.md）：
    # 多路并行取并集——锚命中的 topic 内原子做 match 加成（仍走全库打分，
    # 非主题内独占查询——禁串行漏斗：锚失配时全库路径原样召回）。
    # 加成只在 match 侧（topic 是召回证据不是上下文先验——规约5：不掺 bias）。
    # 开关关闭/无锚时 topic_boost 为空 dict，零行为变化。
    topic_boost = {}
    if topic_layer.topic_enabled():
        try:
            c_t = topic_layer._conn()
            hits = []
            for row in c_t.execute(
                    "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE status='active'"):
                cues = json.loads(row["cue_anchors"])
                if any(cue and cue in query for cue in cues):
                    hits.append(row["topic_id"])
            c_t.close()
            if hits:
                c_t = topic_layer._conn()
                for tid in hits[:2]:            # top-2 主题（与 topic_route_retrieve 同口径）
                    for row in c_t.execute(
                            "SELECT inst_id FROM topic_links WHERE topic_id=?", (tid,)):
                        topic_boost[row["inst_id"]] = tid
                    c_t.execute("UPDATE topic_abstraction SET access_count=access_count+1 "
                                "WHERE topic_id=?", (tid,))
                c_t.commit()
                c_t.close()
        except Exception:
            topic_boost = {}                    # 锚层故障不阻断检索（降级=纯旧路径）
    scored = []
    # 全局信号判定（阶段二·用户规则）：本轮是否有任一路有效信号——
    # 实体 kw 主池 / 编码先验 / 别名命中。sem 是逐卡值不在此判（逐卡联合判定见下）。
    any_alias_signal = bool(alias_hits)
    for m in mems:
        code = _code_match_score(query_tag, m)
        kw, fw_only = _keyword_score(query, m, return_detail=True)
        # 别名命中加权：命中的别名词并入摘要匹配池（把别名当 summary 的一部分匹配）。
        # 双保险（2026-11-09 泛指事故）：别名池匹配时同样走停用词过滤——
        # 历史脏别名（含会话套话）在匹配侧再拦一道，防绕过反问门
        if m["inst_id"] in alias_hits:
            kw_alias = _keyword_score(query, {"summary": " ".join(alias_hits[m["inst_id"]])})
            kw = max(kw, kw_alias)
            fw_only = False            # 别名有效信号 → 框架屏蔽
        sem = _cosine(qvec, _embed(m["summary"] or ""))
        # 框架词联合屏蔽（用户规则：精确锚/别名/语义任一路有效 → 框架 kw 归零）：
        # fw_only=True 表示 kw 分全来自框架词；code/alias/该卡 sem 任一有效即屏蔽。
        # sem 有效线 = 0.45（bge 基座下无关 query 约 0.40-0.44, 语义近似卡 ≥0.50）。
        if fw_only and (code > 0 or any_alias_signal or sem >= 0.45):
            fw_only = True             # 保留标记供下游判决层使用
            kw = 0.0                   # 框架分剥除, 不参与本次加权
        # 编码匹配度：取大不取均——编码满分不该被弱关键词稀释（Q4回归教训：code=1.0被kw拖出top3）
        # 逻辑：编码先验命中=决赛圈资格（保底），关键词=加成；无编码先验时纯关键词
        code_eff = max(code, kw) if query_tag else kw
        conf = CONF_SCORE.get(m.get("confidence", "L2"), 0.5)
        cited = max(min(1.0, m.get("weight", {}).get("cited", 0) * 0.2), _recency_cited_score(m))
        rel_act = _relation_activation(m, graph)

        # 时间窗打分（2026-11-09）：窗外归零，窗内 1.0，时间未知降权参与
        t_hit, t_score, t_label = _time_filter_score(m, win, overlay)

        # 语义保底 0.15：无 query_tag 时纯关键词+语义仍可召回（模拟 sim3 模糊查询）
        # topic 路由加成（规约2/5）：锚命中主题内原子 match_eff +0.10——
        # 召回证据级加成（同 kw 命中性质），不碰 bias（topic 不是上下文先验），
        # 不碰门控阈值（GATE_HARD_PASS 不动——锚只提座次不降门槛）
        _tb = C.TOPIC_ROUTE_BOOST if m["inst_id"] in topic_boost else 0.0
        match_eff = code_eff * C.W_CODE_MATCH + sem * 0.30 + kw * 0.30 + _tb
        bias_eff = conf * C.W_CONFIDENCE + cited * C.W_CITED_STATS + rel_act * C.W_RELATION_ACTIVATE
        total = (match_eff + bias_eff) * t_score
        # 语义保底 0.15：无 query_tag 时纯关键词+语义仍可召回（模拟 sim3 模糊查询）
        scored.append((total, m, match_eff, t_label))

    scored.sort(key=lambda x: -x[0])

    # 两段式排序（2026-11-11 用户裁决：match 定资格，bias 定座次——
    # E8首跑实证: 目标卡match=0.266全场最高被L0错标条目bias=0.500压过排第3。
    # 旧 total=(match+bias) 排序让上下文先验把没资格的抬进回答位）
    # 段1 合格池: match_eff ≥ GATE_HARD_PASS 的候选（上限 POOL_CAP=20 防炸池开销）
    # 段2 池内排序: bias_eff 正常排序（池内偏好暂不动——先跑E8看数据再定）
    # bias 封顶 BIAS_CAP：踩线进池的条目 bias 堆爆也不能碾压池内匹配更优者
    pool = [x for x in scored if x[2] >= C.GATE_HARD_PASS][:C.QUALIFIED_POOL_CAP]
    # 池内排序键 = match + min(bias, BIAS_CAP)：bias 封顶后正常参与排序——
    # 踩线进池的高热度条目话语权有上限，match 更优的冷门条目不被碾压
    # （首版bug教训: 排序键写成纯bias序丢掉match, P39 match=0.765被P40 0.267反超）
    pool.sort(key=lambda x: -(x[2] + min(x[0] - x[2], C.BIAS_CAP)))
    # 高bias低match拦截记录（用户裁决：日志分清"匹配不行"vs"热度抢前排被拦"）
    blocked_high_bias = [
        {"inst_id": m["inst_id"][:8], "summary": m["summary"][:40],
         "match_eff": round(match_eff, 3),
         "bias_eff": round(total - match_eff, 3)}
        for total, m, match_eff, _t in scored
        if match_eff < C.GATE_HARD_PASS and (total - match_eff) > 0.30
    ][:10]
    ask.last_pool_audit = {
        "query": query[:60],
        "pool_size": len(pool),
        "pool_members": [{"inst_id": m["inst_id"][:8], "match_eff": round(me, 3)}
                         for _, m, me, _ in pool[:20]],
        "blocked_high_bias": blocked_high_bias,
    }
    append_jsonl(os.path.join(MEM_DIR, "pool_audit.jsonl"), ask.last_pool_audit)

    # 两段式产物作为正式排序结果（池空则退回旧排序——反问门照常拦）
    ranked = pool if pool else scored

    # 治理后置过滤（2026-11-12 统一索引层用户裁决）：
    # 流水线 = 召回(DFCP) → 打分(DFCP两段式) → 治理过滤 → 去重返回。
    # 规则：命中后筛掉 sim_ok=False 族、superseded 已作废卡、虚构小说族按 include_fiction 开关。
    # 被滤卡片落 pool_audit.governance_blocked（可追溯，不静默）。
    # 注意：只滤 asset_class=corpus 的卡（语料产物）；runtime 卡无族约束不走此关。
    fams = _governance_families()
    gov_blocked = []
    if fams:
        kept, seen_ids = [], set()
        for total, m, match_eff, t_label in ranked:
            if m.get("asset_class") == "corpus":
                fam = _card_family(m, fams)
                g = fams.get(fam)
                if g and not g["sim_ok"]:
                    gov_blocked.append({"inst_id": m["inst_id"][:8], "summary": m["summary"][:40],
                                        "family": fam, "rule": "sim_ok=False"})
                    continue
                if g and g["fiction"] and not include_fiction:
                    gov_blocked.append({"inst_id": m["inst_id"][:8], "summary": m["summary"][:40],
                                        "family": fam, "rule": "fiction(虚构禁入人格推理)"})
                    continue
                if m.get("superseded_by"):
                    gov_blocked.append({"inst_id": m["inst_id"][:8], "summary": m["summary"][:40],
                                        "family": fam, "rule": "superseded(已作废)"})
                    continue
            kept.append((total, m, match_eff, t_label))
        ranked = kept
    if gov_blocked:
        ask.last_pool_audit["governance_blocked"] = gov_blocked

    # 池审计落盘带 ts（晨报24h窗口统计用）
    ask.last_pool_audit["ts"] = datetime.now().isoformat()
    append_jsonl(os.path.join(MEM_DIR, "pool_audit.jsonl"), ask.last_pool_audit)
    ask.last_pool_audit.pop("ts", None)

    # 同 entity 互斥去重（top_k 内同本体只留最高分）
    seen_ent, out = [], []
    for total, m, match_eff, t_label in ranked:
        ent = m.get("entity_id")
        if ent and ent in seen_ent:
            continue
        if ent:
            seen_ent.append(ent)
        out.append((total, m, match_eff))
        if len(out) >= top_k:
            break

    # 调用统计回写（cited 计数——投影层只记，不改主记录）
    # time_window 一并落日志（design_event_ts.md 风险1约束②：解析区间可排查）
    # 双轨写入(2026-11-15 切换): sqlite 主写 trace_cited(唯一真相),
    # cited_log.jsonl 同步保留(应急回切读源+既有审计消费方)
    cite_rows = [(m["inst_id"], query[:100], cite_source) for _, m, _ in out]
    if _MEMORY_SOURCE == "sqlite":
        try:
            _wc = _sq.connect(_DB_PATH, timeout=5)
            _wc.executemany(
                "INSERT INTO trace_cited (ts, query, inst_id, source) VALUES (?,?,?,?)",
                [(datetime.now().isoformat(), q, iid, src) for iid, q, src in cite_rows])
            _wc.commit()
            _wc.close()
        except Exception:
            pass  # 写失败不阻断检索(流水容忍丢失)
    for _, m, _ in out:
        rec = {"ts": datetime.now().isoformat(), "inst_id": m["inst_id"],
               "query": query[:100], "source": cite_source}
        if win:
            rec["time_window"] = {"expr": win[2], "from": str(win[0]), "to": str(win[1]),
                                  "resolved_at": datetime.now().isoformat(timespec="seconds")}
        append_jsonl(os.path.join(MEM_DIR, "cited_log.jsonl"), rec)

    # 反问门（2026-11-09 方案C 裁决：双门分层 + 边缘带二次校验）——
    # 单阈值已被 E2 标定否决：最难负例(0.217)与正例长尾重叠，一刀切必有结构性损失。
    #   ≥0.22 直接放行；≤0.12 直接触发反问；中间带走实体锚点二次校验：
    #   query_tag 编码先验非空（用户明确指定了检字法入口），或查询与近期调用条目
    #   2-gram 重叠 ≥2（"上次说的那个bug"确实锚到了某条被调过的记忆）→ 放行。
    #   二次校验仍然只看"有没有真实指代"，不看 bias——先验不能凭空造匹配。
    if out:
        top_match = out[0][2]
        if top_match >= C.GATE_HARD_PASS:
            ask.last_verdict = {"verdict": "hit", "match_eff": round(top_match, 4),
                                "total": round(out[0][0], 4)}
            # 成功配对（流程②③）：近期失败查询与本次确凿命中配成等价叫法，
            # 原始查询实词沉淀为该记忆的二级备用标签（append-only 叠加层）
            ask.last_alias_pair = ma.try_pair_on_hit(query, out[0][1], top_match)
        elif top_match <= C.GATE_HARD_ASK:
            ask.last_verdict = {"verdict": "uncertain",
                                "match_eff": round(top_match, 4),
                                "total": round(out[0][0], 4),
                                "guess": out[0][1]["summary"][:60],
                                "hint": "指代模糊或主题不在库中，请确认所指（你问的是哪一条？）"}
        else:
            # 边缘带：实体锚点二次校验（2026-11-09 工程建议落地）
            anchored = _has_session_anchor(query, query_tag, out)
            if anchored:
                ask.last_verdict = {"verdict": "marginal_hit", "match_eff": round(top_match, 4),
                                    "total": round(out[0][0], 4),
                                    "anchor": anchored,
                                    "guess": out[0][1]["summary"][:60]}
            else:
                ask.last_verdict = {"verdict": "marginal_ask", "match_eff": round(top_match, 4),
                                    "total": round(out[0][0], 4),
                                    "guess": out[0][1]["summary"][:60],
                                    "hint": "匹配证据落在边缘带且无实体锚——请确认所指（你问的是哪一条？）"}
    else:
        ask.last_verdict = {"verdict": "empty"}

    # 失败捕获（流程①，用户裁决：失败先如实说不知道，不猜）：
    # uncertain/marginal_ask/empty 挂起原始查询，等用户换词重问后配对沉淀别名
    v = ask.last_verdict.get("verdict")
    if v in ("uncertain", "marginal_ask", "empty"):
        ma.record_miss(query, ask.last_verdict.get("match_eff", 0.0), v)
        ask.last_alias_pair = {"recorded_miss": True}

    if debug:
        overlay_dbg = overlay
        for total, m, match_eff in out:
            ed = _entry_event_date(m, overlay_dbg)
            print(f"  {total:.4f} (match {match_eff:.4f}) "
                  f"[{m['dfcp_tag']['domain']}|{m['dfcp_tag']['behavior']}|{m['dfcp_tag']['relation']}] "
                  f"[event:{ed or '未知'}] {m['summary'][:50]}")
    return out


def cross_view(domain_prefix: str = None, behavior: str = None):
    """查询期联表：领域×行为二维交叉计数（只读视图，不写编码——v1.4-3）。"""
    mems = load_memories()
    cross = Counter()
    for m in mems:
        d, b = m["dfcp_tag"]["domain"], m["dfcp_tag"]["behavior"]
        if domain_prefix and not d.startswith(domain_prefix):
            continue
        if behavior and b != behavior:
            continue
        cross[(d.split("-")[0], b)] += 1
    return dict(cross)


# ---------------- 取件原语：source_ref → 正文（读回路最后一公里，2026-11-12 P0 收尾） ----------------
# 缺口背景：摘要卡把正文地址存进 source_ref，但 ask() 只给摘要层——回表取正文
# 此前靠调用方手写 SQL，治理拦截（superseded）在取件环节不生效。
# fetch_full/fetch_by_ref 补上这一跳：地址解析 + superseded 拦截 + 内容一致性校验。

def _parse_source_ref(ref: str) -> str | None:
    """sqlite:corpus_store.db#<树地址>(注释…) → 树地址；非库地址返回 None。"""
    if not ref or not ref.startswith("sqlite:corpus_store") or "#" not in ref:
        return None
    node = ref.split("#", 1)[1]
    return node.split("(")[0].split(" ")[0].strip()


def fetch_by_ref(source_ref: str) -> dict | None:
    """按摘要卡的 source_ref 取 corpus_store 正文（当前版）。
    superseded 条目显式拦（返回 {'blocked': …} 而非静默 None）——
    治理从检索层延伸到取件层，绕过 ask 手持地址直取也逃不掉。
    None=地址不存在（与被拦区分，便于审计定位）。"""
    node = _parse_source_ref(source_ref)
    if not node:
        return None
    from corpus_store import get_corpus
    row = get_corpus(node)
    if row is not None:
        return row
    # get_corpus 只查 status='current'；命不中再查一次不限状态，区分"地址错"与"已作废"
    import sqlite3
    from corpus_store import _conn
    c = _conn()
    old = c.execute("SELECT id, status, superseded_by FROM raw_corpus WHERE id=? AND status!='current'",
                    (node,)).fetchone()
    c.close()
    if old:
        return {"blocked": "superseded", "id": old[0], "superseded_by": old[2]}
    return None


def cards_from_dialog(dialog_key: str, include_fiction: bool = False) -> list:
    """#12 对话追溯：反向查找——给定对话标识，列出由该对话产生的记忆卡。
    内存遍历 core_memory_card 的 source_ref(不新增 DB 表/索引)，弱关联匹配。
    与 fetch_by_ref(卡→对话) 互补, 构成双向追溯。

    dialog_key 兼容三种真实 source_ref 形态(大小写/全前缀皆可):
      1. 树地址 id: 'dialog.2026-11-22.h0015' (新 stdin transcript 路径)
         命中 source_ref 形如 'sqlite:corpus_store.db#dialog.xxx(...)' 或含该 id;
      2. session 标识: 'codechat-session/default' / 'sbx-session-001'
         命中 source_ref 含 'session=<id>' 或 title 关联(弱, 仅字符串包含);
      3. 会话日期锚: '2026-11-17' 或 'session 2026-11-17'
         命中历史 'memory_gate; raw锚: session <日期>; ...' 形态。

    返回 list[dict]: {card, corpus|blocked|None}; corpus 走 fetch_by_ref 取正文。
    弱关联: 字符串包含匹配, 不做强外键; dialog_key 支持完整值或唯一片段。"""
    if not dialog_key:
        return []
    key = dialog_key.strip()
    # 归一历史『session <date>』锚: 若传入纯日期或 session+日期, 生成匹配片段
    keys = {key}
    if key.startswith("session "):
        keys.add(key[len("session "):])
    out = []
    seen = set()
    for m in load_memories():
        ref = m.get("source_ref") or ""
        if not ref:
            continue
        hit = False
        # 形态1: 树地址解析
        node = _parse_source_ref(ref)
        if node and (node == key or node.startswith(key + "(") or key in ref):
            hit = True
        # 形态2/3: 任意 key 片段包含(source_ref 全文子串)
        if not hit:
            for k in keys:
                if k and k in ref:
                    hit = True
                    break
        if not hit:
            continue
        iid = m.get("inst_id")
        if iid in seen:
            continue
        seen.add(iid)
        item = {"card": m}
        corpus = fetch_by_ref(ref)
        if corpus is not None and "blocked" in corpus:
            item["blocked"] = corpus
        else:
            item["corpus"] = corpus
        out.append(item)
    return out


def fetch_full(inst_id: str, include_fiction: bool = False) -> dict | None:
    """按记忆卡 inst_id 取其 source_ref 指向的正文全量。
    走完整治理链：小说族须显式 include_fiction（与 ask 同口径）。
    返回 {card, corpus} 或 {card, blocked}；无库地址的卡返回 {card}。"""
    mems = load_memories()
    card = next((m for m in mems if m.get("inst_id", "").startswith(inst_id)), None)
    if card is None:
        return None
    ref = card.get("source_ref") or ""
    node = _parse_source_ref(ref)
    if node is None:
        return {"card": card}
    fams = _governance_families()
    fam = _card_family(card, fams)
    g = fams.get(fam)
    if g and g.get("fiction") and not include_fiction:
        return {"card": card, "blocked": "fiction(虚构禁入,需include_fiction=True)"}
    row = fetch_by_ref(ref)
    if row is None:
        return {"card": card}
    if "blocked" in row:
        return {"card": card, "blocked": f"{row['blocked']}: {row.get('superseded_by', '')}"}
    return {"card": card, "corpus": row}


# ---------------- 回归验收：盲测三轮原查询 ----------------
if __name__ == "__main__":
    print("=== Step3 回归验收（盲测原查询 vs 本地检索） ===\n")
    tests = [
        # (盲测轮次, 查询, 期望编码先验, 期望命中摘要关键词)
        ("sim2-Q1", "我本地想部署大模型，机器配置3090+32G，当时怎么选的？", {"behavior": "Ht"}, "3090"),
        ("sim2-Q4", "季报超预期抓不到机会那个痛点，后来想到什么办法？", {"domain": "001-002"}, "预告"),
        ("sim3-Q2", "情感的定位当时怎么定的？", {"domain": "005"}, "情感"),
        ("sim3-Q3", "被顶回来那次学到了什么？", {"domain": "005"}, "顶回"),
        ("sim3-Q5", "记忆这块的规则定过哪些？", {"domain": "005-003"}, "置信度"),
    ]
    passed = 0
    for name, q, tag, expect_kw in tests:
        results = ask(q, query_tag=tag, top_k=3)
        top_summaries = [m["summary"] for _, m, _ in results]
        hit = any(expect_kw in s for s in top_summaries)
        passed += hit
        mark = "✅" if hit else "❌"
        print(f"{mark} {name}: {q}")
        for s in top_summaries[:2]:
            print(f"     → {s[:60]}")
        print()
    print(f"回归验收: {passed}/{len(tests)}")
    print("\n=== 联表视图（005域 × 各行为分布） ===")
    for (d, b), n in sorted(cross_view(domain_prefix="005").items()):
        print(f"  {d} × {b}({schema.BEHAVIOR_TREE.get(b,'?').split()[0] if b in schema.BEHAVIOR_TREE else b}): {n}")