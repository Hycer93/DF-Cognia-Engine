# memory_organize.py — DFCP 记忆模块 v0.3：三层分层架构（用户第六轮裁决 2026-11-07）
# 分层：原子存储层（刚性账本）/ 认知关联层（软关系图谱）/ 经验聚类层（沿链归纳）
# 旧 Op1/Op2/Op3/Op5 编号体系废弃，统一三层命名。
#
# 本文件 = 原子存储层。写入：inst_id(uuid4 永不变) + full_hash + 三级文本 + dfcp_tag(无极性)。
# 认知关联层见 memory_relations.py；经验聚类层见 memory_distill.py（Week2）。
#
# ---- 多主题拆分裁决（2026-11-08 用户终裁，先存规约暂不编码）----
# ① 语义主题拆分的裁定权保留在主 AI，atomic_store 永远不主动切割一条输入为多条记忆。
# ② 护栏⑥ multi_topic_suspect 检测：Week2 自动摘要流水线上线时同步落地，作摘要质检门——
#    触发时机=atomic_store 入库前；行为=仅检测不切割不拒收；判据=摘要内≥2个无强因果关联的
#    DFCP 锚点（递进子问题/同主题分支推演不算多主题）；结果=标记 multi_topic_suspect:true
#    送人工复核隔离队列，复核通过才进认知关联池。
# ③ 漏拆预案（现在即生效）：混主题条目不删不改 inst_id，人工补打 multi_topic=true 冻结；
#    拆解出的多条新记忆各自独立 add，origin_ids 指向原始杂合记录；冻结条目仅归档溯源，
#    认知关联层过滤 multi_topic=true（见 memory_relations.classify_and_link）。

import json
import io
import os
import uuid
import hashlib
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema
import constants as C

# ---- 存储布局（本地 jsonl = source of truth，索引/图谱均为可重建投影）----
MEM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory")
MEMORIES_JSONL = os.path.join(MEM_DIR, "memories.jsonl")          # 原子存储层主记录（唯一刚性账本）
CHANGELOG_JSONL = os.path.join(MEM_DIR, "code_changelog.jsonl")   # dfcp_tag 变更 append-only 日志
QUARANTINE_JSONL = os.path.join(MEM_DIR, "quarantine.jsonl")      # 编码非法拒收隔离队列
REPLICA_LOG_JSONL = os.path.join(MEM_DIR, "replica_intercepts.jsonl")  # full_hash 精确副本拦截日志


def _full_hash(summary: str, detail: str, source: str) -> str:
    return hashlib.sha256((summary + "\x1f" + (detail or "") + "\x1f" + (source or "")).encode("utf-8")).hexdigest()


def _converge_summary(text: str) -> tuple:
    """摘要收敛（非截断）：超限时压到句子边界，保主干。
    Week1 简化=边界截取；真正语义压缩改写 Week2 接 LLM 后替换。"""
    if len(text) <= C.SUMMARY_MAX_CHARS:
        return text, False
    cut = text[: C.SUMMARY_MAX_CHARS]
    for sep in ("。", "；", ";", "，"):
        i = cut.rfind(sep)
        if i > C.SUMMARY_MAX_CHARS * 0.6:
            return cut[: i + 1], True
    return cut, True


# ---- 保护清单（硬保护，命中即 skip 一切整理操作）----
def is_protected(entry: dict) -> bool:
    return bool(
        entry.get("held_out")
        or entry.get("evidence_chain")
        or entry.get("skeleton_lineage")
        or (entry.get("tier") == "precedent" and (entry.get("merged_ref_count") or 0) > 10)
        or (entry.get("last_cited")
            and datetime.fromisoformat(entry["last_cited"]) > datetime.now() - timedelta(days=C.RECENT_CITED_DAYS))
    )


# ---- 可复现性判定（专利A，编码预筛短路）----
def judge_repro(content: str, tags: dict) -> str:
    b = tags.get("behavior", "")
    if b in ("B00", "Fp", "Fq", "Fr"):
        return "derived"
    if tags.get("affect_tags"):
        return "flash"
    if tags.get("domain", "").startswith("005"):
        return "flash"
    return "situational"


# ---- 原子存储层：入库（三层职责之第一层）----
def atomic_store(
    summary: str,
    detail: str,
    source_ref: str,
    tags: dict,
    asset_class: str = "runtime",       # corpus | runtime 护栏字段
    origin_ids: list = None,
    event_ts: str = None,               # 事件真实发生时间（2026-11-09 时间概念工程）
) -> tuple:
    """返回 (status, message)。status: stored | replica | quarantined。

    职责边界（用户方案四）：只认 inst_id/full_hash/三级文本/dfcp_tag；
    幂等、稳定、适合 A/B、专利举证、故障追溯；相似/同源/冲突判定不在本层。

    event_ts 口径（2026-11-09 裁决：记忆需要时间概念）：
    - ts = 入库时间（系统时刻，永不改）
    - event_ts = 记忆所述事件的真实发生时间；缺省 None（不伪造）。
      可来源：会话发生时间（hook 自动传）、语料内显式日期、用户口述。
      None 的条目在时间过滤中被视为"时间未知"，不参与时间窗排序但保留在纯语义检索。
    """
    # 编码校验（护栏①）：非法拒收进隔离队列
    ok, errors = schema.validate_memory_tags(tags)
    if not ok:
        append_jsonl(QUARANTINE_JSONL, {
            "ts": datetime.now().isoformat(), "summary": summary[:200], "tags": tags,
            "reason": errors, "detail_preview": (detail or "")[:200],
        })
        return "quarantined", "; ".join(errors)

    # 摘要收敛（契约：压缩保主干，非截断砍尾）
    summary, converged = _converge_summary(summary)

    # full_hash：精确副本拦截（纯副本不入主记录）
    fh = _full_hash(summary, detail, source_ref)
    if any(e.get("full_hash") == fh for e in load_memories()):
        append_jsonl(REPLICA_LOG_JSONL, {
            "ts": datetime.now().isoformat(), "full_hash": fh, "summary": summary,
        })
        return "replica", f"精确副本拦截: {fh[:12]}"

    inst_id = uuid.uuid4().hex
    repro = tags.get("repro") or judge_repro(detail, tags)

    # 三层占用（§4.7 嵌套：判定三级决定对结构三层的占用权限）
    layers = {
        "flash":       {"summary": True, "detail": True, "source": "full"},
        "situational": {"summary": True, "detail": True, "source": "restricted_entry"},
        "derived":     {"summary": True, "detail": False, "source": "retrieval_path"},
    }[repro]

    entry = {
        "inst_id": inst_id,
        "ts": datetime.now().isoformat(),
        "event_ts": event_ts,                          # 事件真实发生时间，缺省 None（不伪造）
        "full_hash": fh,
        "asset_class": asset_class,
        "repro": repro,
        "confidence": tags["confidence"],
        "dfcp_tag": {                                  # 编码坐标（极性已移除——裁决1）
            "domain": tags["domain"],                   # 五维枚举坐标（件②，2026-11-10 用户终裁）：
            "behavior": tags["behavior"],               # domain/behavior/relation=思维坐标，
            "relation": tags["relation"],               # memory_class=记忆资产角色第五维，
            "memory_class": tags.get("memory_class"),   # null 合法=普通记录（S015：非万能分类）
        },
        "affect_tags": tags.get("affect_tags"),        # 情感树可选层，独立于 dfcp_tag
        "memory_class": tags.get("memory_class"),      # 【迁移期双读】件②后权威位在 dfcp_tag.memory_class；顶层保留供 E4/E5 旧口径过渡，迁移完成（E4 memory_class_missing=0）后清退
        "summary": summary,
        "summary_converged": converged,
        "detail": detail if layers["detail"] else None,
        "source_ref": source_ref if layers["source"] in ("full", "restricted_entry") else None,
        "origin_ids": origin_ids or [],
        "entity_id": None,                              # 认知关联层回填（关系层非主键）
        "derived_from": None,                           # 经验聚类层产出，原子层恒 None
        "tier": "active",
        "unclassified_cycles": 1 if tags["domain"].startswith("000") else 0,
        "weight": {"match": 0.0, "cited": 0},
        "protected": False,
    }
    append_jsonl(MEMORIES_JSONL, entry)
    # 双轨写(2026-11-15 #31 切换): core_memory_card 唯一真相主写;
    # memories.jsonl 保留为审计导出流(应急回切读源可用)
    try:
        _write_card_to_db(entry)
    except Exception as _e:
        # 写库失败不阻断返回(jsonl 已落, 下轮增量同步补)——但落审计可查
        try:
            append_jsonl(os.path.join(MEM_DIR, "dual_write_errors.jsonl"),
                          {"ts": datetime.now().isoformat(), "inst_id": entry["inst_id"],
                           "err": repr(_e)})
        except Exception as _el:
            # P2-5: 原为 pass, 双写错误记录本身失败也须留痕, 否则错误链完全断裂
            print(f"[dual_write_error_log_fail] {type(_el).__name__}: {_el} | "
                  f"orig_err={_e!r}", file=sys.stderr)
    return "stored", f"{inst_id[:8]} 入库（repro={repro}）"


def _write_card_to_db(entry, max_retries=3):
    """A-4（2026-11-21 #7）：core_memory_card 写入加固。

    hold 转正等路径经 atomic_store 落到这里。相比原实现：
      - 显式 BEGIN IMMEDIATE（立即取写锁，避免读事务升级写锁时的 SQLITE_BUSY 竞争）
      - busy_timeout=5000（写锁被占时内部等待而非立即抛 locked）
      - 指数退避重试（0.05s/0.1s/0.2s），3 次仍失败才上抛
    仅加固这一条落卡路径，不做全仓事务改造（约束：A-4 范围最小）。
    """
    import sqlite3 as _sq
    import time as _t
    _db = os.path.join(MEM_DIR, "corpus_store.db")  # 库在 data/memory/ 下(不是上级!)
    _sql = ("INSERT OR IGNORE INTO core_memory_card VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    _args = (entry["inst_id"], entry["ts"], entry["event_ts"], entry["full_hash"],
             entry["asset_class"], entry["repro"], entry["confidence"],
             json.dumps(entry["dfcp_tag"], ensure_ascii=False),
             json.dumps(entry["affect_tags"], ensure_ascii=False) if entry["affect_tags"] else None,
             entry["summary"], entry["summary_converged"], entry["detail"], entry["source_ref"],
             json.dumps(entry["origin_ids"], ensure_ascii=False),
             entry["entity_id"], entry["derived_from"], entry["tier"],
             json.dumps(entry["weight"], ensure_ascii=False), entry["protected"],
             None, "active", entry["memory_class"])
    last_err = None
    for _attempt in range(max_retries):
        _wc = None
        try:
            _wc = _sq.connect(_db, timeout=5, isolation_level=None)  # None=手动管事务
            _wc.execute("PRAGMA busy_timeout=5000")
            _wc.execute("BEGIN IMMEDIATE")
            _wc.execute(_sql, _args)
            _wc.commit()
            return
        except _sq.OperationalError as _oe:
            last_err = _oe
            if _wc is not None:
                try:
                    _wc.rollback()
                except Exception:
                    pass
            # 仅对 locked/busy 退避重试；其他 OperationalError 直接上抛
            if "locked" in str(_oe).lower() or "busy" in str(_oe).lower():
                _t.sleep(0.05 * (2 ** _attempt))
                continue
            raise
        finally:
            if _wc is not None:
                try:
                    _wc.close()
                except Exception:
                    pass
    raise last_err


# ---- dfcp_tag 变更：append-only 日志（护栏②）----
def change_code(inst_id: str, field: str, old: str, new: str, reason: str):
    append_jsonl(CHANGELOG_JSONL, {
        "ts": datetime.now().isoformat(), "inst_id": inst_id,
        "field": field, "old": old, "new": new, "reason": reason,
    })


# ---- 通用 IO ----
def load_memories() -> list:
    return _load_jsonl(MEMORIES_JSONL)


def load_quarantine() -> list:
    return _load_jsonl(QUARANTINE_JSONL)


def append_jsonl(path: str, obj: dict, fsync: bool = False):
    """追加一行 JSONL。

    A-1（2026-11-21 #7）：新增 fsync 参数（默认 False，行为等价）。
    fsync=True 时在关闭前 flush 并 os.fsync 刷盘——仅调用方对可靠性有强要求时使用
    （如 pending 队列落盘），普通审计日志保持默认 false 不引入额外 IO 开销。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        if fsync:
            f.flush()
            os.fsync(f.fileno())


def _load_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---- 000 待分类时限 ----
def enforce_unclassified_limit() -> list:
    expired = [e["inst_id"] for e in load_memories()
               if e["dfcp_tag"]["domain"].startswith("000")
               and e.get("unclassified_cycles", 0) >= C.UNCLASSIFIED_MAX_CYCLES]
    if expired:
        print(f"[000超期] {len(expired)} 条待强制处理: {expired}")
    return expired


# ---- 外部校准降级（2026-11-10 DS 对碰定稿，logs/ds_calibrate_r1/r2.txt）----
# demote 三态：applied/skipped/conflicts，事务性，conflict 不自动追加链。
# 批量降级（>1 条）必须带 calibration_source（校准单 inst_id），缺失拒绝。
# supersede 链上限 8 跳，超限报警不自动合并。考古入账 provenance=speculative
# + corpus_snapshot_date（门禁2，落 schema/入库侧，此处只管降级动作）。


def _sync_demote_to_db(applied, superseded_by, reason):
    """#14（2026-11-22）：demote 的 db 同步（根因修复）。

    背景：demote() 原只重写 memories.jsonl，不 UPDATE core_memory_card，导致
    tier=precedent 靠迁移脚本灌入、但 superseded_by 列永久为空（读侧读 db 时
    溯源链断裂）。本函数把降级状态同步进 db，使 demote 后读写一致。

    只 UPDATE 已存在卡（jsonl 独有卡 db 无则不 INSERT，交由 #14 存量对账工单）；
    逐条事务，单条失败留痕不阻断其余（审计可查）。
    返回 (synced_n, failed_n)。
    """
    import sqlite3 as _sq
    _db = os.path.join(MEM_DIR, "corpus_store.db")
    synced, failed = 0, 0
    for iid in applied:
        wc = None
        try:
            wc = _sq.connect(_db, timeout=5, isolation_level=None)
            wc.execute("PRAGMA busy_timeout=5000")
            wc.execute("BEGIN IMMEDIATE")
            wc.execute(
                "UPDATE core_memory_card SET tier='precedent', superseded_by=? "
                "WHERE inst_id=?", (superseded_by, iid))
            wc.commit()
            synced += 1
        except Exception as _e:  # noqa: BLE001
            failed += 1
            if wc is not None:
                try:
                    wc.rollback()
                except Exception:
                    pass
            # 降级必留痕：db 同步失败落审计，不静默
            try:
                append_jsonl(os.path.join(MEM_DIR, "dual_write_errors.jsonl"),
                             {"ts": datetime.now().isoformat(), "op": "demote_sync_db",
                              "inst_id": iid, "superseded_by": superseded_by,
                              "reason": reason, "err": repr(_e)})
            except Exception:
                pass
        finally:
            if wc is not None:
                try:
                    wc.close()
                except Exception:
                    pass
    return synced, failed


def demote(inst_ids, superseded_by, reason, calibration_source=None):
    """外部校准降级：tier→precedent + superseded_by 指校准单，零删除可回溯。

    返回 {applied:[], skipped:[], conflicts:[]}；遇 conflict 整批回滚。
    - superseded_by 为空/相同 → skipped（真幂等）
    - 非空且不同 → conflicts，不写，抛调用方裁决
    #14（2026-11-22）：jsonl 落盘后同步 UPDATE core_memory_card，修复半截同步缺陷。
    """
    if len(inst_ids) > 1 and not calibration_source:
        return {"error": "批量降级缺失 calibration_source（硬门禁1）"}
    mems = load_memories()
    res = {"applied": [], "skipped": [], "conflicts": []}
    changed = False
    for m in mems:
        if m.get("inst_id") not in inst_ids:
            continue
        cur = m.get("superseded_by")
        if not cur:
            m["tier"] = "precedent"
            m["superseded_by"] = superseded_by
            m["superseded_reason"] = reason
            m["superseded_at"] = datetime.now().isoformat(timespec="seconds")
            res["applied"].append(m["inst_id"])
            changed = True
        elif cur == superseded_by:
            res["skipped"].append(m["inst_id"])
        else:
            res["conflicts"].append(m["inst_id"])
    if res["conflicts"]:
        return res  # 事务性：conflict 存在则整批不落盘
    if changed:
        bak = MEMORIES_JSONL + ".bak_demote_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        with io.open(bak, "w", encoding="utf-8") as f:
            for m in load_memories():
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        with io.open(MEMORIES_JSONL, "w", encoding="utf-8") as f:
            for m in mems:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        # #14：db 同步（根因修复）——补全 superseded_by，使读写一致
        synced, failed = _sync_demote_to_db(res["applied"], superseded_by, reason)
        res["db_synced"] = synced
        res["db_failed"] = failed
        # 维护案例自动采集（§2026-11-11 案例层：demote 落账即记案例，不依赖人工）
        try:
            from maintenance_ledger import append_case
            append_case(
                case_type="calibration",
                trigger=f"demote×{len(res['applied'])} reason={reason[:60]}",
                evidence=f"superseded_by={str(superseded_by)[:16]} calibration_source={str(calibration_source or '-')[:16]}",
                action="tier→precedent+superseded_by 指校准单+db同步",
                outcome=f"applied={len(res['applied'])} skipped={len(res['skipped'])} db_synced={synced} db_failed={failed}",
                paradigm_hint="外部校准降级三步走")
        except Exception:
            pass   # 案例采集失败不阻断主流程
    return res


def supersede_chain_length(inst_id, mems=None):
    """链上限检测：沿 superseded_by 跳数，>8 报警（不自动合并）。"""
    if mems is None:
        mems = load_memories()
    by_id = {m.get("inst_id"): m for m in mems}
    n, cur = 0, by_id.get(inst_id)
    while cur and cur.get("superseded_by") and n < 64:
        n += 1
        cur = by_id.get(cur["superseded_by"])
    return n


if __name__ == "__main__":
    # 自检1：正常入库（三层占位+摘要收敛）
    st, msg = atomic_store(
        "紫光国微建仓分歧双AI对峙：新暮暮与原型暮暮在71-72元被动挂单与节前资金面流出风险上分歧，最终由厂长裁决建仓方案。",
        "双暮暮对峙完整现场，含挂单去留分析。",
        "origin0-r025",
        {"domain": "001-003", "behavior": "Ht", "relation": "H", "confidence": "L1"},
        asset_class="corpus",
    )
    print("自检1:", st, "|", msg)
    # 自检2：full_hash 精确副本拦截
    st, msg = atomic_store(
        "紫光国微建仓分歧双AI对峙：新暮暮与原型暮暮在71-72元被动挂单与节前资金面流出风险上分歧，最终由厂长裁决建仓方案。",
        "双暮暮对峙完整现场，含挂单去留分析。",
        "origin0-r025",
        {"domain": "001-003", "behavior": "Ht", "relation": "H", "confidence": "L1"},
        asset_class="corpus",
    )
    print("自检2:", st, "|", msg)
    # 自检3：非法编码拒收
    st, msg = atomic_store("t", "d", "s",
        {"domain": "999-001", "behavior": "Ht", "relation": "H", "confidence": "L1"})
    print("自检3:", st, "|", msg)
    # 自检4：同坐标不同内容（旧幂等bug场景）——现在应各自成条
    st, msg = atomic_store("光谱系数从Ht迁移至G22的架构决策现场，模块归属冲突解法=迁移而非并存",
        "content", "sft_s7#S7-02",
        {"domain": "005-001", "behavior": "Ht", "relation": "P", "confidence": "L0"})
    st2, msg2 = atomic_store("脑区四系统联动最早现场：太浅了+分阶段融合=当前施工路线原型决策",
        "content2", "sft_s7#S7-10",
        {"domain": "005-002", "behavior": "Ht", "relation": "P", "confidence": "L1"})
    print("自检4:", st, "/", st2, "→ 同坐标不同内容各自成条 ✅" if st == st2 == "stored" else "❌")
    print("当前库条数:", len(load_memories()))