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
import re
import sqlite3
import time
import uuid as _uuid
import sys

# 同源绝对路径（R17 锚点：AUDIT_DB as db）
from cgraph.atomic_skill import AUDIT_DB as db


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
    except Exception as e:  # import 失败不阻断主流程(DB 已落库)
        errs.append(f"jsonl_import_fail:{type(e).__name__}")
        return
    for inst, summary, fh, x in committed:
        entry = {
            "inst_id": inst,
            "ts": ts,
            "event_ts": ts,
            "full_hash": fh,
            "asset_class": "runtime",
            "repro": "situational",
            "confidence": "L2",
            "dfcp_tag": {"domain": "001-003", "behavior": "Ht",
                         "relation": "P", "memory_class": None},
            "memory_class": None,
            "affect_tags": None,
            "summary": summary[:120],
            "summary_converged": "0",
            "detail": _green_detail(x),
            "source_ref": f"memory_gate; raw锚: session {day}; user_input={user_input[:60]}",
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


def commit_green_cards(result, gate, user_input: str):
    """绿档落库 writer(段二C案: 编排层机械执行, 技能外)。
    仅写绿档条目(asset_type ∈ 观测/测试结果/踩坑), 逐卡 full_hash 去重防重
    (段七红线5幂等), raw 回链锚=本会话 user_input+日期窗口(0f2f4458 硬约束)。
    段六b案: 成败一律落独立审计行 op=memory_commit:green。"""
    cards = [x for x in (result.result.get("gate_list") or []) if x["tier"] == "green"]
    if not cards or result.result.get("_degraded"):
        return {"written": 0, "skipped": len(cards)}
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    day = time.strftime("%Y-%m-%d")
    written, ids, errs = 0, [], []
    committed = []  # 本事务内成功插入的 (inst_id, summary, full_hash, card)(供 jsonl 同步)

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
        for x in cards:
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
            con.execute(
                "INSERT INTO core_memory_card (inst_id, ts, event_ts, full_hash, "
                "asset_class, repro, confidence, dfcp_tag, summary, summary_converged, "
                "detail, source_ref, memory_class, tier, weight, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inst, ts, ts, fh, "runtime", "situational", "L2",
                 json.dumps({"domain": "001-003", "behavior": "Ht", "relation": "P",
                             "memory_class": None}, ensure_ascii=False),
                 s[:120], "0",
                 _green_detail(x),
                 f"memory_gate; raw锚: session {day}; user_input={user_input[:60]}",
                 None, "active", '{"match": 0.0, "cited": 0}', "active"))
            ids.append(inst[:16])
            nd2 = _extract_node(x.get("evidence_step", "")) or _extract_node(s)
            if nd2:
                existing_nodes.add((day, nd2))  # 本事务内也加入, 防同批多卡同node
            committed.append((inst, s, fh, x))
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
    return {"written": written, "skipped": errs}