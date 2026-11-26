"""green_card_writer.py — 绿档落库 writer（P2-2 从 cgraph/executor.py 迁出）

职责单一：把 memory_gate 产出的绿档清单机械写入 core_memory_card，
并落一条独立审计行 op=memory_commit:green（段六b案：成败一律可追溯）。

迁出动机：executor.py 承担编排主链，DB 写入细节属独立职责（P2-2 职责分离）。
业务逻辑自 cgraph/executor.py:498-581 原样迁移，未改任何 SQL / 阈值 / 幂等语义。

DB 路径同源硬约束（原 R17 回归锚，2026-11-18 round5 自举审计）：
必须用 cgraph.atomic_skill.AUDIT_DB 绝对路径，禁止相对路径硬编码
（CWD 漂移会静默落错库，曾是 E4 对账 drift 嫌疑根因）。
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid as _uuid
import sys

# 同源绝对路径（R17 锚点：AUDIT_DB as db）
from cgraph.atomic_skill import AUDIT_DB as db

# ---- 改造A（2026-11-26 用户裁决）：green 卡标签动态化 ----
# 节点码 → domain 映射。14 项全部过 memory.schema.validate_domain()（2026-11-26 核验）。
# Bc 归 005-006（方案攻防与专利评审）——它是最弱环节/风险校验节点，不属通用元思域。
NODE_DOMAIN = {
    "Aa": "005-004",  # 任务空间扫描   → 认知科学-元思域
    "Bb": "005-004",  # 信息源头意图   → 认知科学-元思域
    "Bc": "005-006",  # 最弱环节定律   → 认知科学-方案攻防与专利评审
    "Cf": "005-004",  # 三步认知法     → 认知科学-元思域
    "Cg": "004-002",  # 利益者全景图   → 组织行为学-跨部门沟通
    "Ch": "005-001",  # 多维状态机校验 → 认知科学-设计裁决
    "Di": "005-004",  # 第一因溯源链   → 认知科学-元思域
    "Dj": "005-002",  # 逆向第一因     → 认知科学-用户洞察
    "Dk": "005-008",  # 直觉可验证锚定 → 认知科学-自我锚定与利害结算
    "Eo": "005-004",  # 上下文等价性   → 认知科学-元思域
    "Fp": "005-006",  # 动态风险量化   → 认知科学-方案攻防与专利评审
    "Fq": "005-001",  # 周期性出清     → 认知科学-设计裁决
    "Ht": "005-001",  # 决策整合       → 认知科学-设计裁决
    "Jw": "005-003",  # 经验进化闭环   → 认知科学-记忆自我管理
}
# 未知节点/解析失败回落（与原写死值一致，宁用旧默认不漏卡）
_FALLBACK_DOMAIN = "001-003"
_FALLBACK_BEHAVIOR = "Ht"
# 确定性聚合节点（有 Python 公式，可重算出同一结论）→ derived；其余 → situational
DERIVED_NODES = {"Bc", "Ch", "Ht", "Fp", "Fq"}
# 标签回落审计日志（不阻断落卡，仅可追溯）
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TAG_FALLBACK_LOG = os.path.join(_HERE, "logs", "green_card_tag_fallback.jsonl")


def _jaccard(a: str, b: str) -> float:
    """bigram 重叠度: LLM 措辞漂移的语义级幂等兜底

    2026-11-24 保留 0.6 阈值不变：实测对短文本(几十字)措辞漂移样本
    (未部署→未预置等)全部有效拦截；换 embedding≥0.95 反而漏拦(掉到0.93-0.95)。
    """
    wa = {a[i:i + 2] for i in range(len(a) - 1)}
    wb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0


def _extract_node(text: str):
    """从 detail/summary 提取 evidence node 标识(如 node_Ch)。取不到返回 None。

    用于同-session+同-node 硬去重(第二层兜底, 2026-11-24)。
    """
    if not text:
        return None
    m = re.search(r"node_[A-Za-z0-9]+", text)
    return m.group(0) if m else None


def _green_detail(x: dict) -> str:
    """#44（2026-11-24）：绿档卡 detail 生成——不再硬编码占位模板。

    历史写法是固定前缀串"绿档自动立卡(...)"，被 detail 口径查重当作模板噪音
    （96 对全撞车）。改为：以 proposed_action 实义内容为主，拼 evidence_step/node，
    保留可追溯信息但不再是可复制的模板前缀。
    """
    action = (x.get("proposed_action") or "").strip()
    step = (x.get("evidence_step") or "").strip()
    gist = (x.get("gist") or "").strip()
    parts = []
    if action:
        parts.append(action)
    if gist:
        parts.append(f"要点: {gist}")
    if step:
        parts.append(f"证据步: {step}")
    body = "；".join(parts) if parts else step or gist or "绿档立卡(无实义内容)"
    return body[:400]


# ---- 改造A（2026-11-26）：标签动态化——由真实路径推导 dfcp_tag/repro，替代写死 ----

def _node_code(evidence_step: str, detail: str = "") -> str | None:
    """从 evidence_step(形如 'node_Ch') 或 detail 提取真实节点码；取不到返回 None。"""
    for text in (evidence_step, detail):
        nd = _extract_node(text or "")          # 复用既有 node_[A-Za-z0-9]+ 提取
        if nd:
            return nd[len("node_"):] if nd.startswith("node_") else nd
    return None


def _log_tag_fallback(payload: dict):
    """标签回落审计（不阻断落卡，尽力而为）。"""
    try:
        os.makedirs(os.path.dirname(_TAG_FALLBACK_LOG), exist_ok=True)
        with open(_TAG_FALLBACK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                **payload}, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 审计失败不影响落卡


def _derive_tag(card: dict, repro_hints: dict | None = None,
                gate_confidence: float | None = None) -> dict:
    """由绿档卡真实来源推导 dfcp_tag / confidence / repro（改造A）。

    推导口径（对应设计文档 §2 改造A）：
      behavior : node_Ch 前缀剥离 → Ch；解析失败回落 behavior="Ht" + 审计日志，绝不阻断落卡
      domain   : 按节点码查 NODE_DOMAIN；未知 → _FALLBACK_DOMAIN + 审计
      confidence: green 卡 L2 起步；gate_confidence≥0.7 且 repro=derived → 升 L3
      repro    : 优先 repro_hints[node]["repro_hint"]；缺则 derived 节点→derived，其余→situational
      relation : 默认 "P"（本轮不改 relation 语义）
    返回 {domain, behavior, relation, confidence, repro, repro_source, _fallbacks: [...]}。
    任何字段推导失败都回落默认值并记 _fallbacks，调用方保证落卡不被阻断。
    """
    repro_hints = repro_hints or {}
    fallbacks = []

    node = _node_code(card.get("evidence_step", ""), card.get("gist", ""))
    if node:
        behavior = node
        node_resolved = True
    else:
        behavior = _FALLBACK_BEHAVIOR
        node_resolved = False
        fallbacks.append("behavior:no_node_code")

    domain = NODE_DOMAIN.get(behavior)
    if not domain:
        domain = _FALLBACK_DOMAIN
        fallbacks.append(f"domain:unknown_node_{behavior}")

    # repro：节点自报 hint 优先（试点 repro_hints），否则按节点类型推导。
    # 关键：若节点码未解析（fallback 到 Ht），不得沿 Ht 的 derived 属性——
    # 来源未知的弱卡不能声称"可确定性重算"，保守回落 situational（与原写死口径一致）。
    hint = (repro_hints.get(behavior) or {}).get("repro_hint")
    if not node_resolved:
        repro = "situational"
        repro_source = "fallback"
    elif hint in ("derived", "situational", "flash"):
        repro = hint
        repro_source = "node_hint"
    elif behavior in DERIVED_NODES:
        repro = "derived"
        repro_source = "node_type"
    else:
        repro = "situational"
        repro_source = "node_type"

    confidence = "L2"
    if repro == "derived" and isinstance(gate_confidence, (int, float)) and gate_confidence >= 0.7:
        confidence = "L3"

    return {
        "domain": domain, "behavior": behavior, "relation": "P",
        "confidence": confidence, "repro": repro,
        "repro_source": repro_source, "_fallbacks": fallbacks,
    }


def _make_source_ref(inst_id: str, session_id: str | None, day: str,
                     user_input: str) -> str:
    """构造可解析的 source_ref（改造C，2026-11-26 用户裁决）。

    新格式: session:<session_id>#card:<inst_id>  —— 可被 _parse_source_ref 解析回原文
    退化:   无 session_id（executor 侧本就拿不到）→ 保留旧文本形态，不破坏既有溯源。
    """
    if session_id:
        return f"session:{session_id}#card:{inst_id}"
    # 退化：无 session 上下文，沿用旧文本锚（解析器降级旧文本模式已兼容）
    return f"memory_gate; raw锚: session {day}; user_input={user_input[:60]}"


# ---- 改造B（2026-11-26 用户裁决）：五问 judge 接入（shadow 双跑 → 灰度） ----
# 开关 memory.cgraph_gate_via_judge（环境变量 CGRAPH_GATE_VIA_JUDGE 覆盖）默认 false：
#   false = shadow 双跑：原绿卡直写链路【完全不变】，额外并行跑一遍 judge 纯判定，
#           只产出对比日志（不改变入库结果）。
#   true  = judge 真判定：绿卡须过五问，store→落库 / hold→pending / discard→日志。
# 硬约束：无论开关何值，judge 异常（超时/报错/畸形）一律路由 hold + 写 judge_log，
#        【绝不丢弃绿卡原始产出】。绝不因开关 false 替换入库链路。
# shadow 复用 memory_judge._judge_call（纯判定、零副作用），
#        不调 judge_and_store（其 store 分支会真实 memory_hook.add 入库）。
_SHADOW_LOG = os.path.join(_HERE, "logs", "cgraph_judge_shadow.jsonl")


def _cgraph_gate_via_judge() -> bool:
    """开关读取：环境变量优先（便于灰度/测试），否则读 config（缺省 false）。"""
    env = os.environ.get("CGRAPH_GATE_VIA_JUDGE")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        import yaml
        cfg_path = os.path.join(_HERE, "config", "config.yaml")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(_HERE, "config", "config.example.yaml")
        cfg = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        return bool((cfg.get("memory") or {}).get("cgraph_gate_via_judge", False))
    except Exception:
        return False   # 任何读取失败 → 保守 false（不改变既有入库行为）


def _cards_to_candidates(cards: list, tags_map: dict, day: str) -> list:
    """绿档卡 → judge candidates 格式（供 _judge_call / judge_and_store 消费）。

    tags_map: {card_index: tag_dict}（由 _derive_tag 产出的动态标签）。
    与人工 add() 的候选字段对齐，五问可据此判 Q1-Q5。
    """
    cands = []
    for i, x in enumerate(cards):
        tag = tags_map.get(i, {})
        cands.append({
            "summary": x.get("gist", ""),
            "detail": _green_detail(x),
            "source": f"memory_gate; session {day}",
            "event_ts": None,
            "memory_class": None,
            "domain": tag.get("domain", _FALLBACK_DOMAIN),
            "behavior": tag.get("behavior", _FALLBACK_BEHAVIOR),
            "relation": "P",
            "confidence": tag.get("confidence", "L2"),
            "asset_class": "runtime",
            "repro": tag.get("repro", "situational"),
        })
    return cands


def _shadow_judge(candidates: list, context: dict):
    """shadow 双跑：跑一遍 judge 纯判定并落观测日志（零副作用，不入库）。

    复用 memory_judge._judge_call（只判定，不 store/hold/discard 落任何库）。
    异常统一记 judge_failure（等价 hold 观测），绝不影响主链路。
    返回观测统计 dict（写 _SHADOW_LOG，不返回给调用方依赖）。
    """
    started = time.time()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    verdicts, judge_fail, judge_source = {}, False, "n/a"
    try:
        from memory import memory_judge as _mj
        verdicts = _mj._judge_call(candidates, context) or {}
        judge_source = next(iter(verdicts.values()), {}).get("_source", "unknown") \
            if verdicts else "unknown"
    except Exception as e:
        judge_fail = True
        verdicts = {}

    n = len(candidates)
    cnt = {"store": 0, "hold": 0, "discard": 0}
    for v in verdicts.values():
        vd = v.get("verdict")
        if vd in cnt:
            cnt[vd] += 1
    elapsed_ms = round((time.time() - started) * 1000, 1)
    # 未判定的条目按 hold 观测（异常/无 verdict 都算 hold 口径）
    judged = sum(cnt.values())
    hold_obs = cnt["hold"] + (n - judged)
    rec = {
        "ts": now, "op": "shadow_judge", "context": context,
        "candidates": n, "judged": judged,
        "store": cnt["store"], "hold": hold_obs, "discard": cnt["discard"],
        "pass_rate": round(cnt["store"] / n, 4) if n else 0.0,
        "hold_rate": round(hold_obs / n, 4) if n else 0.0,
        "discard_rate": round(cnt["discard"] / n, 4) if n else 0.0,
        "elapsed_ms": elapsed_ms, "judge_source": judge_source,
        "judge_failure": judge_fail,
        "verdicts": [{"summary": (c.get("summary") or "")[:80],
                      "verdict": verdicts.get(i, {}).get("verdict", "hold(no_verdict)"),
                      "reasons": verdicts.get(i, {}).get("reasons")}
                     for i, c in enumerate(candidates)],
    }
    try:
        os.makedirs(os.path.dirname(_SHADOW_LOG), exist_ok=True)
        with open(_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 观测日志失败不影响主链路
    return rec


def _judge_gate_commit(candidates: list, context: dict):
    """开关 true 时：绿卡经原版 judge_and_store 真判定（store/hold/discard）。

    复用 memory_judge.judge_and_store 完整原版 LLM 五问逻辑（不实现规则桩）。
    judge 异常在其内部已路由 hold + 写 judge_log，绝不丢弃。
    返回 judge 报告 dict。
    """
    from memory import memory_judge as _mj
    return _mj.judge_and_store(candidates, context)


def _sync_committed_to_jsonl(committed, ts: str, day: str, user_input: str, errs):
    """2026-11-24 任务②：DB INSERT 成功后同步追加 memories.jsonl。

    修复"记忆更新只写 DB 不同步 jsonl"的管线缺陷。jsonl 是审计导出快照
    (非真相源)，但仍需与 DB 保持一致, 避免后续巡检对账漂移。
    7 项 jsonl 独有审计字段(code_changelog/provenance/verified_via/
    superseded_reason/corpus_snapshot_date/superseded_at/unclassified_cycles)
    新建 green 卡均无值, 按现有 green 卡样例置默认(无 provenance/changelog→不写或空)。
    单条失败不阻断整体(尽力而为), 记录到 errs。
    """
    try:
        from memory import memory_organize as _mo
    except (ImportError, ModuleNotFoundError) as e:
        # 2026-11-26 治理: 命名空间冲突 / memory 包缺失必须暴露, 不静默。
        # 正常生产从仓库根启动, memory/ 命名空间包可正常导入; 一旦被 cgraph/
        # 抢先命中(或 memory 包不可用), 这里直接炸出, 提示排查 sys.path。
        raise ImportError(
            "green_card_writer 无法导入 memory.memory_organize"
            "(疑似 sys.path 抢占/命名空间冲突, 或 memory 包不可用); "
            "DB 已落库, jsonl 同步未执行, 需人工核查。" f"原始异常: {e}"
        ) from e
    except Exception as e:  # 其它导入期异常不阻断主流程(DB 已落库)
        errs.append(f"jsonl_import_fail:{type(e).__name__}")
        return
    for inst, summary, fh, x, tag, src_ref in committed:
        entry = {
            "inst_id": inst,
            "ts": ts,
            "event_ts": ts,
            "full_hash": fh,
            "asset_class": "runtime",
            "repro": tag["repro"],
            "confidence": tag["confidence"],
            "dfcp_tag": {"domain": tag["domain"], "behavior": tag["behavior"],
                         "relation": tag["relation"], "memory_class": None,
                         "repro_source": tag.get("repro_source"),
                         **({"derived_only": True} if tag["repro"] == "derived" else {})},
            "memory_class": None,
            "affect_tags": None,
            "summary": summary[:120],
            "summary_converged": "0",
            "detail": _green_detail(x),
            "source_ref": src_ref,
            "origin_ids": None,
            "entity_id": None,
            "derived_from": None,
            "tier": "active",
            "weight": {"match": 0.0, "cited": 0},
            "protected": False,
            "status": "active",
            "superseded_by": None,
            "unclassified_cycles": 0,
        }
        try:
            _mo.append_jsonl(_mo.MEMORIES_JSONL, entry)
        except Exception as e:
            errs.append(f"jsonl_write_fail:{type(e).__name__}")


def commit_green_cards(result, gate, user_input: str,
                       walked: list | None = None,
                       repro_hints: dict | None = None,
                       session_id: str | None = None):
    """绿档落库 writer(段二C案: 编排层机械执行, 技能外)。
    仅写绿档条目(asset_type ∈ 观测/测试结果/踩坑), 逐卡 full_hash 去重防重
    (段七红线5幂等), raw 回链锚=本会话 user_input+日期窗口(0f2f4458 硬约束)。
    段六b案: 成败一律落独立审计行 op=memory_commit:green

    改造A/C（2026-11-26 用户裁决）：
      walked/repro_hints → 驱动 _derive_tag 推导真实标签（不再写死 Ht/L2/situational）。
      session_id → 构造可解析 source_ref（session:<id>#card:<inst>）；无则退化旧文本锚。
      所有推导失败均回落 + 审计，绝不阻断落卡。"""
    cards = [x for x in (result.result.get("gate_list") or []) if x["tier"] == "green"]
    if not cards or result.result.get("_degraded"):
        return {"written": 0, "skipped": len(cards)}
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    day = time.strftime("%Y-%m-%d")
    written, ids, errs = 0, [], []
    committed = []  # 本事务内成功插入的 (inst_id, summary, full_hash, card, tag)(供 jsonl 同步)
    tags_map = {}   # {card_index: tag}（改造B：shadow/judge 复用动态标签）
    gate_conf = getattr(gate, "confidence", None)
    if not isinstance(gate_conf, (int, float)):
        gate_conf = (getattr(gate, "result", {}) or {}).get("confidence")

    # ---- 改造B：预推导标签（两分支共用，纯函数无副作用）----
    for ci, x in enumerate(cards):
        tags_map[ci] = _derive_tag(x, repro_hints, gate_conf)

    # ---- 开关 true：五问 judge 判定入库（替代下方直写，避免双写）----
    # 复用原版 judge_and_store（store→落库/hold→pending/discard→日志，异常内部路由 hold）。
    if _cgraph_gate_via_judge():
        ctx = {"trigger": "cgraph_gate", "session_id": session_id or f"day:{day}"}
        cands = _cards_to_candidates(cards, tags_map, day)
        try:
            rep = _judge_gate_commit(cands, ctx)
        except Exception as e:  # judge_and_store 自身异常 → 兜底按 hold 计（其内部已防丢）
            rep = {"error": f"{type(e).__name__}: {e}", "stored": 0,
                   "held": len(cands), "discarded": 0, "judge_failures": len(cands)}
        return {"written": rep.get("stored", 0), "skipped": [],
                "judge": {"mode": "judge", "report": rep}}

    # ---- 开关 false：原直写链路（下方行为与改前完全一致）+ 末尾 shadow 观测 ----

    try:
        con = sqlite3.connect(db, timeout=10)
        # 显式事务完全接管：关闭 python sqlite3 的隐式 BEGIN，避免
        # "BEGIN IMMEDIATE" 与外层隐式事务冲突("事务内开事务")。
        con.isolation_level = None
        # 2026-11-24 并发根治：查询-插入整块包裹 BEGIN IMMEDIATE 排他事务。
        # 根因：collab_pipeline 用 multiprocessing.Pool(spawn) 多进程并行 run_atomic，
        # 各进程独立 memory_gate，旧代码"先查后插"无事务 → 互相看不见对方未提交的写入
        # → 各算各的、同时插入副本。IMMEDIATE 立即取写锁，后到者排队、其查询能看到
        # 前者已提交的最新卡，从根源杜绝并发重复。
        con.execute("BEGIN IMMEDIATE")
        # 语义级幂等补强(段七红线5编排侧实现): full_hash 只防完全同文,
        # 真实 LLM 两次盘点同资产措辞漂移→bigram-Jaccard>0.6 视为同资产跳过。
        # 2026-11-24 加固：在 Jaccard 之外叠加"同-session+同-node"硬去重(第二层兜底)。
        # 对称口径: existing summary 剥 "[类型] "前缀与 "(evidence:...)"后缀后再比
        existing = []
        existing_nodes = set()  # (day, node) 组合已立卡集合
        for r in con.execute(
                "SELECT summary, detail FROM core_memory_card WHERE source_ref LIKE 'memory_gate%' "
                "AND ts LIKE ?", (day + "%",)).fetchall():
            e = r[0]
            e = e.split("] ", 1)[-1] if e.startswith("[") else e
            e = e.split("(evidence:", 1)[0]
            existing.append(e.strip())
            nd = _extract_node(r[0]) or _extract_node(r[1])
            if nd:
                existing_nodes.add((day, nd))
        for ci, x in enumerate(cards):
            s = f"[{x['asset_type']}] {x['gist']}(evidence: {x['evidence_step']})"
            fh = hashlib.sha256((s + "\x1f" + user_input).encode()).hexdigest()
            if con.execute("SELECT 1 FROM core_memory_card WHERE full_hash=?",
                           (fh,)).fetchone():
                errs.append(f"dup:{x['gist'][:20]}")
                continue
            if any(_jaccard(x["gist"], e) > 0.6 for e in existing):
                errs.append(f"semidup:{x['gist'][:20]}")
                continue
            # 第二层兜底：同-session+同-node 硬去重(2026-11-24)
            nd = _extract_node(x.get("evidence_step", "")) or _extract_node(s)
            if nd and (day, nd) in existing_nodes:
                errs.append(f"nodedup:{x['gist'][:20]}")
                continue
            inst = _uuid.uuid4().hex
            # 改造A：动态推导标签（失败回落+审计，绝不阻断落卡）
            tag = tags_map[ci]
            for fb in tag["_fallbacks"]:
                _log_tag_fallback({"inst_id": inst, "fallback": fb,
                                   "evidence_step": x.get("evidence_step", ""),
                                   "gist": x.get("gist", "")[:60]})
            tags_map[ci] = tag   # 改造B：保留动态标签供 shadow/judge 复用
            src_ref = _make_source_ref(inst, session_id, day, user_input)
            con.execute(
                "INSERT INTO core_memory_card (inst_id, ts, event_ts, full_hash, "
                "asset_class, repro, confidence, dfcp_tag, summary, summary_converged, "
                "detail, source_ref, memory_class, tier, weight, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inst, ts, ts, fh, "runtime", tag["repro"], tag["confidence"],
                 json.dumps({"domain": tag["domain"], "behavior": tag["behavior"],
                             "relation": tag["relation"], "memory_class": None},
                            ensure_ascii=False),
                 s[:120], "0",
                 _green_detail(x),
                 src_ref,
                 None, "active", '{"match": 0.0, "cited": 0}', "active"))
            ids.append(inst[:16])
            nd2 = _extract_node(x.get("evidence_step", "")) or _extract_node(s)
            if nd2:
                existing_nodes.add((day, nd2))  # 本事务内也加入, 防同批多卡同node
            committed.append((inst, s, fh, x, tag, src_ref))
            written += 1
        con.commit()
        con.close()
    except Exception as e:
        errs.append(f"db_error:{type(e).__name__}")
        committed = []  # 回滚后 DB 未落库, 禁止向 jsonl 同步幻影记录
        try:
            con.rollback()
            con.close()
        except Exception:
            pass
    # 2026-11-24 任务②：DB 写入成功后同步追加 memories.jsonl（保留 7 审计字段）。
    # jsonl 是审计快照非真相源，但仍需与 DB 保持一致, 避免后续漂移。
    if committed:
        _sync_committed_to_jsonl(committed, ts, day, user_input, errs)
    # 段六b案: 成败一律落独立审计行
    try:
        con = sqlite3.connect(db, timeout=5)
        con.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                    (ts, "memory_commit:green",
                     json.dumps({"written": written, "cards": ids,
                                 "skipped": errs, "raw": f"session {day}",
                                 "user_input": user_input[:60]}, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:
        # 段六b案要求成败一律可追溯——审计失败不阻断链路但必须可见
        print(f"[memory_commit] 审计行落库失败(不阻断, 需人工核): {type(e).__name__}: {e}",
              file=sys.stderr)
    if written:
        print(f"[memory_gate] 绿档落库 {written} 张: {ids}")

    # ---- 改造B：shadow 双跑观测（开关 false 专属；true 已在函数开头 return）----
    # 硬约束：此处只旁路跑 judge 纯判定写对比日志，原入库链路（上方 DB/jsonl）已完整执行，零改动。
    judge_obs = None
    try:
        ctx = {"trigger": "cgraph_gate", "session_id": session_id or f"day:{day}"}
        cands = _cards_to_candidates(cards, tags_map, day)
        judge_obs = {"mode": "shadow", "report": _shadow_judge(cands, ctx)}
    except Exception as e:
        # 观测失败绝不阻断已完成的入库
        judge_obs = {"mode": "error", "error": f"{type(e).__name__}: {e}"}
        print(f"[memory_gate] shadow 观测异常(不阻断入库): {type(e).__name__}", file=sys.stderr)

    out = {"written": written, "skipped": errs}
    if judge_obs is not None:
        out["judge"] = judge_obs
    return out
