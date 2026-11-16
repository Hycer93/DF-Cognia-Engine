# memory/topic_layer.py — Topic 抽象锚层（L4 顶层索引，2026-11-13 定案）
#
# 设计文档: docs/design_topic_layer.md（v1.0 用户终审版，六规约红线）
# 定位: 粗粒度降噪索引。只控制两件事：检索候选池范围 + 冲突比对是否进入。
#       不参与置信/相似分/演化链等底层事实判定（规约5）。
#
# 架构（四层堆叠）:
#   L4 TopicAbstraction（本模块，新增） → L3 Entity 谱系（已有，不动）
#   → L2 认知原子（已有，不动） → L1 三粒度内容（已有，不动）
#
# 六规约对照（开发时逐条核对）:
#   规约1 topic/entity 两套粒度互不替代——本模块不读不改 entity_id/演化链
#   规约2 检索并行多路召回取并集——见 topic_route_retrieve()
#   规约3 归一走 judge 风格四档 + hold 待归池——见 classify_topic()
#   规约4 冲突拦截单向——见 conflict_gate()
#   规约5 不碰底层事实——本模块无任何写 memory/confidence/relations 的路径
#   规约6 锚生命周期——见 refresh_topics()/archive_idle_topics()（E7 挂载）
#
# 存储: SQLite（复用 corpus_store.db，新增 topic_abstraction 表 + topic_links 表）
#       锚向量: topic_vector 存 JSON 数组（与 chroma 解耦——向量索引是可重建衍生物，
#       本体不可只存在衍生物里，与 2026-11-12 用户四表裁决同一原则）

import json
import os
import re
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(HERE), "data", "memory", "corpus_store.db")
MEM_DIR = os.path.join(os.path.dirname(HERE), "data", "memory")
HOLD_POOL_JSONL = os.path.join(MEM_DIR, "topic_hold_pool.jsonl")      # 规约3: 待归池
TOPIC_LOG_JSONL = os.path.join(MEM_DIR, "topic_layer_log.jsonl")      # 全操作审计（append-only）

TOPIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_abstraction (
    topic_id      TEXT PRIMARY KEY,          -- topic_<slug>，建锚后永不换号（refresh 只改内容）
    topic_abstract TEXT NOT NULL,            -- 一句话主题抽象（refresh 时重提炼，规约6）
    topic_vector  TEXT,                      -- JSON 数组（锚表征，可重算——衍生物）
    cue_anchors   TEXT NOT NULL,             -- JSON list：多视角检索别名（复用别名体系思想）
    status        TEXT NOT NULL DEFAULT 'active',  -- active / archived（规约6: 低访问归档不删除）
    create_ts     TEXT NOT NULL,
    update_ts     TEXT NOT NULL,
    mount_count   INTEGER NOT NULL DEFAULT 0,  -- 挂载计数（归档判据之一）
    access_count  INTEGER NOT NULL DEFAULT 0,  -- 检索命中计数（归档判据之一）
    manual_seed   INTEGER NOT NULL DEFAULT 0,  -- 人工种子锚标记(2026-11-14裁决): 特例破≥5门槛
                                               -- 且不受规约6低访问归档(高频巡检人工管)
    inspect_freq  TEXT NOT NULL DEFAULT 'normal'  -- 巡检频率: high(每日E7优先审)/normal
);

CREATE TABLE IF NOT EXISTS topic_links (
    inst_id    TEXT NOT NULL,                -- 认知原子 inst_id
    topic_id   TEXT NOT NULL,
    method     TEXT NOT NULL,                -- seed(建锚挂载) / judge(语义归一) / e7(夜间复审转正)
    ts         TEXT NOT NULL,
    PRIMARY KEY (inst_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_links ON topic_links(topic_id);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_topic_db():
    """幂等建表（corpus_store.init_db 后调用，或独立调用）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = _conn()
    c.executescript(TOPIC_SCHEMA)
    c.commit()
    c.close()


def _log(op: str, payload: dict):
    with open(TOPIC_LOG_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "op": op, **payload}, ensure_ascii=False) + "\n")


# ---------------- 规约3: topic 归一（judge 风格四档，禁止当场二选一） ----------------

def classify_topic(summary: str, detail: str = "") -> dict:
    """新原子挂靠判定。返回 verdict dict：
      {"verdict": "anchor", "topic_id": str}                       → 挂已有锚
      {"verdict": "hold", "topic_id": None, "evidence_gap": str}   → 进待归池（默认保守态）
      {"verdict": "new",   "topic_id": None, "abstract": str}      → 建新锚（仅高置信）
    判据分三层（全部本地可判，LLM 语义化走 e7 夜间；在线路径零 LLM 依赖）：
      1. cue_anchors 精确命中（同锚关键词）→ anchor（高置信）
      2. 多锚命中（歧义）→ hold（宁可待归，不做二选一）
      3. 零命中 → hold（不建新锚——建锚只发生在 E7 批量复审/人工显式 seed）
    """
    text = f"{summary or ''} {detail or ''}"
    hits = []
    c = _conn()
    try:
        for row in c.execute(
                "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE status='active'"):
            cues = json.loads(row["cue_anchors"])
            n = sum(1 for cue in cues if cue and cue in text)
            if n > 0:
                hits.append((row["topic_id"], n, row["topic_id"]))
    finally:
        c.close()
    # 引用性提及过滤（2026-11-14 用户裁决写入E7，mem_governor首跑发现）：
    # cue 命中 ≠ 主题归属。复盘/事故总结/方法论卡常拿旧主题锚词当案例讲
    # （如"伪演示基线法"卡 detail 讲'脑区簇误挂案例'——脑区是引用不是归属）。
    # 判据（summary 是主题声明区，detail 可含案例）：锚词出现在 summary 里
    # 才算核心讨论对象；仅 detail 命中的按引用性提及拒绝挂载，hold 交 E7 复审。
    # 例外：summary 为空/极短的语料回填卡退化为全文判定（防漏挂新卡）。
    if hits and (summary or "").strip():
        s_only = summary or ""
        # 锚词在 summary 中命中才算核心讨论对象；仅 detail 命中 → 引用性提及疑似
        c2 = _conn()
        try:
            cue_map = {row2["topic_id"]: json.loads(row2["cue_anchors"])
                       for row2 in c2.execute(
                           "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE status='active'")}
        finally:
            c2.close()
        real_hits = [(tid, n) for tid, n, _ in hits
                     if any(cue and cue in s_only for cue in cue_map.get(tid, []))]
        if not real_hits:
            return {"verdict": "hold", "topic_id": None,
                    "evidence_gap": f"引用性提及疑似（cue 仅 detail 命中，非 summary 主题归属）: "
                                     f"{[h[0] for h in hits]}——E7 复审裁语境",
                    "reference_mention": True}
        hits = real_hits
    if len(hits) == 1:
        return {"verdict": "anchor", "topic_id": hits[0][0]}
    if len(hits) > 1:
        # 歧义：多锚同时命中 → 保守 hold（规约3 红线：禁当场二选一）
        return {"verdict": "hold", "topic_id": None,
                "evidence_gap": f"多锚歧义命中: {[h[0] for h in hits]}，交 E7 复审"}
    return {"verdict": "hold", "topic_id": None,
            "evidence_gap": "零锚命中；建新锚仅限 E7 批量复审或显式 seed"}


def mount_topic(inst_id: str, summary: str, detail: str = "", *, method: str = "judge") -> dict:
    """挂载入口（memory_hook.add 管道新增步骤）。幂等。
    verdict=hold → 写 hold 池（topic_id=null 等夜间）；anchor → 写 topic_links。
    2026-11-16 ID收口纪律(用户指令): enqueue 写入口强制 32 位全 id 形态校验,
    短 id 一律拒绝——防历史 bug 复发(短/全混存曾致 E7 双 LIKE 兼容与伪 id)。
    32 位形态通过后再验卡表真实存在——防抄错字符的伪全 id 幽灵挂载
    (2026-11-16 实发: 7a499925d363436f 抄错一位照样挂进 topic_links)。"""
    if not (isinstance(inst_id, str) and len(inst_id) == 32):
        return {"verdict": "reject", "topic_id": None,
                "evidence_gap": f"非法 id 形态(需32位hex全id, got len={len(inst_id) if isinstance(inst_id, str) else type(inst_id).__name__})"}
    _c = _conn()
    try:
        if not _c.execute("SELECT 1 FROM core_memory_card WHERE inst_id=?", (inst_id,)).fetchone():
            return {"verdict": "reject", "topic_id": None,
                    "evidence_gap": "伪全id: 卡表不存在该 inst_id(拒幽灵挂载)"}
    finally:
        _c.close()
    v = classify_topic(summary, detail)
    now = datetime.now().isoformat(timespec="seconds")
    if v["verdict"] == "anchor":
        c = _conn()
        try:
            c.execute("INSERT OR IGNORE INTO topic_links VALUES (?,?,?,?)",
                      (inst_id, v["topic_id"], method, now))
            c.execute("UPDATE topic_abstraction SET mount_count=mount_count+1, update_ts=? "
                      "WHERE topic_id=?", (now, v["topic_id"]))
            c.commit()
        finally:
            c.close()
        _log("mount", {"inst_id": inst_id, "topic_id": v["topic_id"], "method": method})
    else:
        with open(HOLD_POOL_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now, "inst_id": inst_id,
                                "summary": (summary or "")[:200],
                                "evidence_gap": v.get("evidence_gap", "")},
                               ensure_ascii=False) + "\n")
        # 2026-11-16 管道裂缝修复(用户裁决): hold 触发时 jsonl + core_hold_pool db
        # 双写——此前仅写 jsonl 而 E7 只消费 db 表, hold 行落不了库,
        # 历史均靠人工 INSERT 补行。源头双写保证两份存储一致。
        hc = _conn()
        try:
            hc.execute(
                "INSERT OR IGNORE INTO core_hold_pool "
                "(inst_id, reason, reference_mention, enqueued_ts) VALUES (?,?,?,?)",
                (inst_id, v.get("evidence_gap", ""),
                 1 if v.get("reference_mention") else 0, now))
            hc.commit()
        finally:
            hc.close()
        _log("hold", {"inst_id": inst_id, "why": v.get("evidence_gap", "")})
    return v


# ---------------- 规约2: 检索多路并行召回（禁串行漏斗） ----------------

def topic_route_retrieve(query: str, subset_fetch, fallback_fetch,
                         top_topics: int = 2, per_topic: int = 4, fallback_k: int = 2):
    """多路并行召回取并集。两个 fetch 由调用方注入（检索后端解耦）：
      subset_fetch(topic_id, query, k)  → 主题内子集召回（如何取子集是调用方实现）
      fallback_fetch(query, k)          → 全库兜底召回（现有直查路径）
    返回并集列表（含来源标记），不做串行过滤——锚匹配错误时兜底仍可召回正确原子。
    topic 层全库无锚/全归档 → 自动退化为纯 fallback（白名单关闭态等价行为）。"""
    c = _conn()
    anchors = []
    try:
        for row in c.execute(
                "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE status='active'"):
            cues = json.loads(row["cue_anchors"])
            n = sum(1 for cue in cues if cue and cue in query)
            if n > 0:
                anchors.append((row["topic_id"], n))
    finally:
        c.close()
    anchors.sort(key=lambda x: -x[1])
    results, seen = [], set()
    # 路1: top-N topic 子集召回（并行语义：互不依赖，各自独立取）
    for tid, _score in anchors[:top_topics]:
        for item in (subset_fetch(tid, query, per_topic) or []):
            key = item.get("inst_id") or str(item)
            if key not in seen:
                seen.add(key)
                results.append({**item, "_route": f"topic:{tid}"})
        if anchors:
            c2 = _conn()
            try:
                c2.execute("UPDATE topic_abstraction SET access_count=access_count+1 "
                           "WHERE topic_id=?", (tid,))
                c2.commit()
            finally:
                c2.close()
    # 路2: 全库兜底召回（永不断流——规约2 红线）
    for item in (fallback_fetch(query, fallback_k) or []):
        key = item.get("inst_id") or str(item)
        if key not in seen:
            seen.add(key)
            results.append({**item, "_route": "fallback"})
    return results


# ---------------- 规约4: 冲突比对单向闸门 ----------------

def conflict_gate(inst_a: dict, inst_b: dict) -> dict:
    """classify_and_link 入口的前置过滤器（在 entity/占位过滤器之前）。
    单向拦截：
      topic 不同（双端已定且不相等） → skip（落审计日志，不静默）
      topic 相同（无论 entity）     → 放行，走完整级联（含演化链例外）
      任一端 topic=null             → 放行（null 不拦截——hold 池记忆照常全流程）
    返回 {"gate": "pass"|"skip", "topic_a": str|None, "topic_b": str|None}"""
    c = _conn()
    try:
        def topic_of(inst):
            if not inst:
                return None
            iid = inst.get("inst_id")
            row = c.execute("SELECT topic_id FROM topic_links WHERE inst_id=?", (iid,)).fetchone()
            return row["topic_id"] if row else None
        ta, tb = topic_of(inst_a), topic_of(inst_b)
    finally:
        c.close()
    if ta and tb and ta != tb:
        _log("conflict_skip", {"a": inst_a.get("inst_id"), "b": inst_b.get("inst_id"),
                               "topic_a": ta, "topic_b": tb})
        return {"gate": "skip", "topic_a": ta, "topic_b": tb}
    return {"gate": "pass", "topic_a": ta, "topic_b": tb}


# ---------------- 建锚与存量回填（保守：只建高置信簇） ----------------

# ---- 锚 cue 结构规约（2026-11-16 用户裁决: 动宾/名词复合才可成锚） ----
# 背景: memory_ops 锚曾收孤立泛动词"推演", 误吸一切含"推演"的新卡造成
# 多锚歧义——逐词删补丁打不完, 改为入口结构性拒绝。
# 判据: cue 须动宾复合/名词短语(工程判据 len>=4); 孤立双字动词(如
# "推演/复盘/挂载")一律拒绝; 专业专名登记 _CUE_EXEMPT_TERMS 豁免(须附理由)。
# 泛动词黑名单: 跨主题误吸的孤立动词/泛词(2026-11-16 存量体检实发)
_CUE_BANNED_TERMS = {"推演", "复盘", "挂载", "还债", "反思", "留痕", "伪演示"}
# 专名豁免白名单: 领域专名虽短但语义专一(登记须附锚归属理由)
_CUE_EXEMPT_TERMS = {
    "P10", "P12", "P22", "p10vs", "G22", "F系",                 # 星型拓扑/G22决策/记忆运维编号专名
    "脑区", "海马体", "前额叶", "杏仁核",                         # 脑科学专名
    "非农", "金价", "ETF", "建仓", "挂单", "股价", "仓位", "季报",  # 金融专名
    "溯零", "陈默", "林舟",                                       # 小说专名
    "8397", "HVAC", "右舵",                                      # 车机专名
    "归元仔", "暮暮",                                             # 用户关系专名
    "元核", "七段", "邻接表", "交底书", "专利",                    # 元核/专利领域专名
}

def _cue_valid(cue) -> bool:
    if not isinstance(cue, str) or not cue.strip():
        return False
    cue = cue.strip()
    if cue in _CUE_BANNED_TERMS:      # 泛动词一律拒
        return False
    return len(cue) >= 4 or cue in _CUE_EXEMPT_TERMS


def seed_topic(topic_id: str, abstract: str, cue_anchors: list, vector=None,
               manual_seed: bool = False, inspect_freq: str = "normal") -> str:
    """显式建锚（人工/建锚脚本）。幂等：已存在则只更新 abstract/cues。
    cue 结构规约(2026-11-16): 须动宾复合/名词短语(len>=4)或豁免专名;
    孤立泛动词被拒——防泛动词锚误吸卡片(结构性解, 不打补丁)。

    manual_seed=True（2026-11-14 用户裁决三条枷锁）：
      ① 特殊标记人工种子锚，区别于自动生长锚——特例不成为先例，
         其他主题仍严格遵守 ≥5 张才建锚；
      ② inspect_freq='high'：每晚 E7 复审优先检查该簇，混入明显不属于
         该主题的卡片优先上交 pending，不自动接纳；
      ③ 不受低访问归档（规约6 豁免）：人工种子锚由人工管理生命周期。"""
    bad_cues = [cu for cu in (cue_anchors or []) if not _cue_valid(cu)]
    if bad_cues:
        raise ValueError("锚cue结构规约拒绝(须动宾复合/名词短语len>=4, 专名走豁免表): %s" % bad_cues)
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    try:
        c.execute(
            "INSERT INTO topic_abstraction "
            "(topic_id, topic_abstract, topic_vector, cue_anchors, status, create_ts, update_ts, "
            " manual_seed, inspect_freq) "
            "VALUES (?,?,?,?, 'active', ?, ?, ?, ?) "
            "ON CONFLICT(topic_id) DO UPDATE SET topic_abstract=?, cue_anchors=?, update_ts=?, "
            " manual_seed=?, inspect_freq=?",
            (topic_id, abstract, json.dumps(vector or []), json.dumps(cue_anchors), now, now,
             1 if manual_seed else 0, inspect_freq,
             abstract, json.dumps(cue_anchors), now, 1 if manual_seed else 0, inspect_freq))
        c.commit()
    finally:
        c.close()
    _log("seed", {"topic_id": topic_id, "abstract": abstract,
                  "manual_seed": manual_seed, "inspect_freq": inspect_freq})
    return topic_id


def mount_batch(inst_ids: list, topic_id: str, method: str = "seed"):
    """批量挂载（建锚脚本用）。只挂不合并不删卡不改 class（设计文档§四）。
    2026-11-16 ID收口纪律: 同 mount_topic 强制 32 位全 id, 混入短 id 整批拒绝。"""
    bad = [i for i in inst_ids if not (isinstance(i, str) and len(i) == 32)]
    if bad:
        raise ValueError(f"mount_batch 拒绝非法 id 形态(需32位hex全id): {bad[:3]}")
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    try:
        for iid in inst_ids:
            c.execute("INSERT OR IGNORE INTO topic_links VALUES (?,?,?,?)",
                      (iid, topic_id, method, now))
        c.execute("UPDATE topic_abstraction SET mount_count=mount_count+?, update_ts=? "
                  "WHERE topic_id=?", (len(inst_ids), now, topic_id))
        c.commit()
    finally:
        c.close()
    _log("mount_batch", {"topic_id": topic_id, "n": len(inst_ids), "method": method})


# ---------------- 规约6: 锚生命周期（E7 挂载） ----------------

def refresh_topics(abstract_fn=None):
    """高活跃锚重提炼 abstract（不换 topic_id）。abstract_fn(topic_id, old_abstract, mounted_summaries)
    返回新抽象；None 则跳过。向量重算留给向量化基建（存 JSON 衍生物可重建）。"""
    c = _conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM topic_abstraction WHERE status='active' AND mount_count > 0")]
    c.close()
    refreshed = []
    for row in rows:
        if not abstract_fn:
            continue
        new_abs = abstract_fn(row["topic_id"], row["topic_abstract"], None)
        if new_abs and new_abs != row["topic_abstract"]:
            c2 = _conn()
            c2.execute("UPDATE topic_abstraction SET topic_abstract=?, update_ts=? WHERE topic_id=?",
                       (new_abs, datetime.now().isoformat(timespec="seconds"), row["topic_id"]))
            c2.commit()
            c2.close()
            refreshed.append(row["topic_id"])
            _log("refresh", {"topic_id": row["topic_id"]})
    return refreshed


def archive_idle_topics(min_mounts: int = 3, idle_days: int = 30):
    """低活跃锚归档（不删除，检索降权到不参与路由）。判据：挂载少 且 长期未更新。"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=idle_days)).isoformat(timespec="seconds")
    c = _conn()
    try:
        c.execute("UPDATE topic_abstraction SET status='archived' "
                  "WHERE status='active' AND mount_count < ? AND update_ts < ?",
                  (min_mounts, cutoff))
        n = c.total_changes
        c.commit()
    finally:
        c.close()
    if n:
        _log("archive", {"min_mounts": min_mounts, "idle_days": idle_days})
    return n


# ---------------- 白名单开关（上线策略§五） ----------------

def topic_enabled() -> bool:
    """白名单总开关。默认关闭=全库走旧路径（fallback-only），零行为变化。"""
    return os.environ.get("TOPIC_LAYER_ON") == "1"


# ---------------- 查询/统计 ----------------

def browse_topic(topic_id: str, with_cards: bool = False) -> dict:
    """按主题翻抽屉：返回某锚下全部挂载卡（浏览/盘点/复审底稿用，非搜索）。

    - topic_id: 锚ID（如 topic_brain_region）或锚摘要关键词（含糊时取第一匹配）
    - with_cards: True 时联表带回每张卡的 summary/confidence/memory_class（默认只回ID与计数）
    - hold池卡片不在links表内，browse 不含 hold；hold 复审走 E7 管线
    """
    from memory_organize import load_memories
    c = _conn()
    try:
        row = c.execute("SELECT topic_id, topic_abstract, status FROM topic_abstraction "
                        "WHERE topic_id=? OR topic_id LIKE ? OR topic_abstract LIKE ? "
                        "ORDER BY topic_id",
                        (topic_id, f"%{topic_id}%", f"%{topic_id}%")).fetchone()
        if row is None:
            return {"topic_id": None, "cards": [], "note": "锚不存在或已归档后无匹配"}
        tid = row["topic_id"]
        links = c.execute("SELECT inst_id, method, ts FROM topic_links "
                          "WHERE topic_id=? ORDER BY ts DESC", (tid,)).fetchall()
    finally:
        c.close()
    out = {"topic_id": tid, "abstract": row["topic_abstract"], "status": row["status"],
           "count": len(links), "inst_ids": [l["inst_id"] for l in links],
           "methods": {l["method"]: sum(1 for x in links if x["method"] == l["method"])
                       for l in links}}
    if with_cards:
        mems = {m["inst_id"]: m for m in load_memories()}
        out["cards"] = [{"inst_id": l["inst_id"], "method": l["method"],
                         "confidence": (mems.get(l["inst_id"]) or {}).get("confidence"),
                         "memory_class": (mems.get(l["inst_id"]) or {}).get("memory_class"),
                         "summary": (mems.get(l["inst_id"]) or {}).get("summary", "")[:80]}
                        for l in links]
    return out


def stats() -> dict:
    c = _conn()
    try:
        anchors = c.execute("SELECT COUNT(*) n FROM topic_abstraction WHERE status='active'").fetchone()["n"]
        archived = c.execute("SELECT COUNT(*) n FROM topic_abstraction WHERE status='archived'").fetchone()["n"]
        links = c.execute("SELECT COUNT(*) n FROM topic_links").fetchone()["n"]
        distinct = c.execute("SELECT COUNT(DISTINCT inst_id) n FROM topic_links").fetchone()["n"]
    finally:
        c.close()
    hold = 0
    if os.path.exists(HOLD_POOL_JSONL):
        with open(HOLD_POOL_JSONL, encoding="utf-8") as f:
            hold = sum(1 for _ in f)
    return {"active_anchors": anchors, "archived": archived,
            "links": links, "mounted_atoms": distinct, "hold_pool": hold}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Topic 锚层 CLI（只读统计/建锚脚本入口）")
    p.add_argument("cmd", choices=["stats", "init"])
    a = p.parse_args()
    if a.cmd == "init":
        init_topic_db()
        print("topic 表已建（幂等）")
    else:
        init_topic_db()
        print(json.dumps(stats(), ensure_ascii=False, indent=2))