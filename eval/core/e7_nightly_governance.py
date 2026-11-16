# -*- coding: utf-8 -*-
"""
e7_nightly_governance.py — E7 夜间治理阶段 (2026-11-15 用户指令 #30)

职能: 串装夜间提案 → hold 池复审 → 范式标定/复审 → 证据回写 四环节。
设计原则(对齐既有 nightly 分工):
  - 机器只提案不裁决: 所有 verdict 变更走 pending 状态, 终裁=人闸门(晨报)
  - 幂等可重跑: 已带 verdict 的行不重复处理
  - 不删数据只降权/上交(不删除哲学): 复审不过 = 标记 pending 上交, 不删行
  - inspect_freq='high' 簇优先巡检(2026-11-14 三条枷锁裁决)

四环节:
  P1 夜间提案: hold 池(verdict IS NULL)逐条复审——引用性提及(reference_mention=1)
     者查其后是否出现"核心讨论"卡片(锚词命中 summary), 出现则 propose_release
     提案; 仍无则 propose_stale(老化上交)。proposal 状态不直接改 verdict。
  P2 高频巡检: inspect_freq='high' 簇(topic_memory_ops)逐卡核对锚词,
     混入无关卡 propose_pending 上交(不自动踢出)。
  P3 范式标定: 范式两表 calibration_pending 复审(连续2fail 进来的),
     连续衰减压到90d+ 且 evidence_pass>0 的 propose_recalibrate(重新激活提案);
     candidate 满 3 独立实证全 pass → propose_promote(晋升提案, 不直接升 active)。
  P4 证据回写: maintenance_cases.jsonl 同型 ≥3 的 paradigm_hint 自动写
     audit_paradigm_evidence(候选证据账, outcome='proposed' 非实证结论)。

输出: logs/e7_governance.json + 晨报节(由 nightly_run.py 渲染)。
用法: python eval/e7_nightly_governance.py
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))

DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
LOG = os.path.join(_HERE, "logs", "e7_governance.json")
HOLD_STALE_DAYS = 14        # [先验-业务设定] hold 老化上交阈值
PARADIGM_MIN = 3            # 与 maintenance_ledger.PARADIGM_MIN 对齐
DECAY_DAYS = 90             # 与 design_paradigm_layer.md / render_persona.py 对齐

# ---------- 紧急总开关(2026-11-16 用户裁决⑤) ----------
# 触发态文件 data/memory/E7_EMERGENCY_STOP(存在即停); 支持一键暂停:
#   自动挂载(P2 不上交)/自动治理消池(P1 不自动放行)。
# 回滚通道: python eval/core/e7_nightly_governance.py --rollback <card_id|all> [--since YYYY-MM-DD]
#   只回滚 resolved_by='e7-auto' 的治理动作(人工裁决永不被机器回滚)。
EMERGENCY_STOP_FILE = os.path.join(_HERE, "data", "memory", "E7_EMERGENCY_STOP")

# cue 区分度阈值: 与 topic_layer.CUE_DISCRIM_MIN 保持一致(单一来源, import 引用)
from memory.topic_layer import CUE_DISCRIM_MIN as _CUE_DISCRIM_MIN_E7

def emergency_stop_active():
    return os.path.exists(EMERGENCY_STOP_FILE)


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _log(op, payload):
    """复用 topic_layer 的审计日志格式, 落 audit_topic_log"""
    con = _conn()
    try:
        con.execute(
            "INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
            (op, json.dumps(payload, ensure_ascii=False), _ts()))
        con.commit()
    except Exception:
        pass
    finally:
        con.close()


# ---------- P1 hold 池夜间复审(2026-11-16 自治闸门三路分流) ----------
# 用户裁决的前置约束(写死在代码, 非补丁——分流逻辑重构为单入口 classify_hold):
#   ① 清晰可判案例自动 release/keep(E7 直写 verdict, resolved_by='e7-auto')
#   ② zero_anchor / multi_anchor_ambiguity / cluster_conflict 强制送人工复审队列
#      (只提案不改 verdict, 终裁=人闸门)
#   ③ user-retain-* 豁免标记(p2_exempt 同机制)正常生效, 不重复提案
#   ④ 审计字段完整: 裁决主体(resolved_by)/时间(verdict_ts)/理由(reason)
#   ⑤ ID 层解耦: 全部 32 位全 id 等值查询, 零 LIKE 兼容
# 放行比例纪律(用户): 先连续多晚观察 hold 池流入流出, 不急于放大自动放行。
AUTO_RELEASE_MAX_AGE = 14   # 自动 release 上限年龄: 超龄转人工(老化案例边界不清)

# 强制人工的进池原因分类(check_type 口径, 晨报固定字段对齐)
MANUAL_REASONS = ("零锚命中", "多锚歧义", "cluster_conflict")


def classify_hold(con, r, age_days):
    """单卡分流裁决: 返回 (route, action, reason)。
    route ∈ {auto, manual}: auto=E7 可直接执行; manual=强制人闸。
    分流判据=进池原因(reason 头部) + reference_mention + 豁免标记, 全部客观可审计。"""
    reason = r["reason"] or ""
    # 豁免标记优先: 人工留置卡(若池行 resolved_by=user-retain-* 但 verdict 仍 NULL 的历史态)
    if (r["resolved_by"] or "").startswith("user-retain"):
        return "auto", "keep", "豁免标记生效(user-retain), 不再提案"
    # 强制人工三类(2026-11-16 用户裁决): 建新锚资格/锚二义性/簇冲突都不是机器可判
    if any(reason.startswith(k) for k in MANUAL_REASONS):
        return "manual", "propose_review", f"强制人工: {reason.split('；')[0]}"
    # 引用性提及: 查该卡此后是否已挂锚(核心讨论出现)
    if r["reference_mention"]:
        has_core = con.execute(
            "SELECT COUNT(*) FROM topic_links WHERE inst_id=?", (r["inst_id"],)).fetchone()[0]
        if has_core:
            return "auto", "release", "锚词已现核心讨论卡(挂载在簇), 引用性提及自动放行"
        if age_days > HOLD_STALE_DAYS:
            return "manual", "propose_stale", f"引用性提及滞留{age_days}d无核心讨论, 老化上交人裁"
        return "manual", "propose_review", f"引用性提及滞留{age_days}d, 语境裁断需人闸"
    # 其余(reference_mention=0 且非三类疑难): 清晰可判
    if age_days > AUTO_RELEASE_MAX_AGE:
        return "manual", "propose_review", f"非引用hold滞留{age_days}d超自动放行上限, 边界老化转人裁"
    return "auto", "keep", f"非引用hold, 年龄{age_days}d, 维持留置观察"


def p1_hold_review(con, result):
    if emergency_stop_active():
        result["p1_hold"] = {"emergency_stop": True,
                             "note": "紧急总开关触发: P1 自动放行暂停, 全部转人工提案"}
        _log("e7_p1_hold_review", {"emergency_stop": True})
        return
    rows = con.execute(
        "SELECT * FROM core_hold_pool WHERE verdict IS NULL ORDER BY enqueued_ts").fetchall()
    auto_released, auto_kept, manual_q, audit_rows = 0, 0, [], []
    for r in rows:
        enq = r["enqueued_ts"]
        try:
            age_days = (datetime.now() - datetime.fromisoformat(enq[:19])).days
        except ValueError:
            age_days = 0
        route, action, why = classify_hold(con, r, age_days)
        rec = {"inst_id": r["inst_id"], "route": route, "action": action,
               "reason": why, "age_days": age_days,
               "check_type": _check_type_of(r)}
        if route == "auto":
            if action == "release":
                # 自动放行: 直写 verdict(裁决主体/时间/理由三字段全记)
                con.execute(
                    "UPDATE core_hold_pool SET verdict='released', verdict_ts=?, resolved_by='e7-auto', "
                    "reason=reason || ' | e7-auto-release: ' || ? WHERE inst_id=? AND verdict IS NULL",
                    (_ts(), why, r["inst_id"]))
                # 放行即挂锚? 不——release 只出池, 挂载走 classify_topic 正常管道(解耦)
                auto_released += 1
                rec["state"] = "released"
            else:
                auto_kept += 1
                rec["state"] = "kept"
        else:
            manual_q.append(rec)
            rec["state"] = "pending_review"
        audit_rows.append(rec)
    con.commit()
    # 标准化审计记录在数据源头生成(不依赖打印阶段), 落盘进 e7_governance.json
    # 字段契约(2026-11-16 用户约定, 不随意增删):
    # card_id/summary/check_type/rule_triggered/state/last_judge/expire_at/evidence/next_action
    result["p1_hold"] = {
        "pending": len(rows), "auto_released": auto_released,
        "auto_kept": auto_kept, "manual_queue": manual_q, "audit": audit_rows,
        "audit_records": [_standard_audit_record(con, r_audit) for r_audit in audit_rows]}
    _log("e7_p1_hold_review", {"pending": len(rows), "auto_released": auto_released,
                               "auto_kept": auto_kept, "manual": len(manual_q)})


def _standard_audit_record(con, p):
    """单卡标准化审计记录——供晨报渲染与导出复盘(字段契约见调用方注释)"""
    hr = con.execute(
        "SELECT reason, enqueued_ts, verdict, verdict_ts, resolved_by, reference_mention FROM core_hold_pool "
        "WHERE inst_id=? ORDER BY enqueued_ts DESC LIMIT 1",
        (p["inst_id"],)).fetchone()
    r = con.execute("SELECT summary FROM core_memory_card WHERE inst_id=?",
                    (p["inst_id"],)).fetchone()
    cs = r["summary"] if r else "!!卡不存在"
    cs = (cs[:80] + "…") if len(cs) > 80 else cs
    return {
        "card_id": p["inst_id"],
        "summary": cs,
        "check_type": p.get("check_type", "unclassified"),
        "rule_triggered": p.get("reason", "?"),
        "state": p.get("state", "?"),
        "last_judge": {"by": (hr["resolved_by"] if hr else None) or p.get("route", "pending"),
                       "at": (hr["verdict_ts"] if hr and hr["verdict_ts"] else None) or _ts(),
                       "action": p.get("action", "?")},
        "expire_at": ((datetime.fromisoformat(hr["enqueued_ts"][:19]) + timedelta(days=HOLD_STALE_DAYS)
                       ).isoformat(timespec="seconds") if hr else None),
        "evidence": {"enqueued_ts": hr["enqueued_ts"] if hr else None,
                     "age_days": p.get("age_days"),
                     "reference_mention": 1 if hr and hr["reference_mention"] else 0},
        "next_action": p.get("action", "?"),
    }


def _check_type_of(r):
    """进池原因 → check_type 标准枚举(晨报固定字段, 不随意增删)"""
    reason = r["reason"] or ""
    if reason.startswith("零锚命中"):
        return "zero_anchor"
    if reason.startswith("多锚歧义"):
        return "multi_anchor_ambiguity"
    if "cluster_conflict" in reason:
        return "cluster_conflict"
    if reason.startswith("引用性提及"):
        return "reference_mention"
    return "unclassified"


# ---------- P2 高频簇巡检 ----------

def p2b_cue_discrimination_recheck(con, result):
    """P2b cue 区分度周期复检（2026-11-16 用户裁决 B 案: 只读告警, 不改配置）。
    背景: cue 判别力自证规约(topic_layer._cue_discrimination)只在
    seed_topic 创建/更新锚时校验一次; 随库演化他簇卡片增多, 达标 cue 会
    慢慢变质跌破阈值成为高误吸源。本步每轮 E7 全量复检 active 锚 cue。
    红线: 只产出告警写入审计日志+可疑清单——绝不自动修改/删除锚 cue
    (锚属元配置, 变更必须人闸)。"""
    from memory.topic_layer import _cue_discrimination
    warnings = []
    checked = 0
    try:
        anchors = con.execute(
            "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE status='active'").fetchall()
        for tid, cues_json in anchors:
            samples = [r[0] for r in con.execute(
                "SELECT inst_id FROM topic_links WHERE topic_id=?", (tid,)).fetchall()]
            for cu in json.loads(cues_json):
                r = _cue_discrimination(cu, samples)
                total = r["n_in"] + r["n_other"]
                if total == 0:
                    continue          # 前瞻零命中: 无现有数据危害, 跳过
                checked += 1
                if not r["ok"]:
                    warnings.append({"topic_id": tid, "cue": cu,
                                     "n_in": r["n_in"], "n_other": r["n_other"],
                                     "disc": round(r["ratio"], 2),
                                     "note": "cue区分度跌破阈值 %.2f——只告警不自动删改, 人工复审裁决" % _CUE_DISCRIM_MIN_E7})
    except Exception as e:
        result["p2b_cue_recheck"] = {"ok": False, "error": str(e), "warnings": []}
        return
    result["p2b_cue_recheck"] = {"ok": True, "checked": checked,
                                 "warnings": warnings}


def p2_high_freq_inspect(con, result):
    """inspect_freq='high' 簇逐卡核对锚词; 无关卡上交 pending 提案(不自动踢)"""
    if emergency_stop_active():
        result["p2_high_freq"] = {"emergency_stop": True,
                                  "note": "紧急总开关触发: 自动挂载巡检暂停"}
        _log("e7_p2_high_freq", {"emergency_stop": True})
        return
    high_topics = con.execute(
        "SELECT topic_id, cue_anchors FROM topic_abstraction WHERE inspect_freq='high' AND status='active'").fetchall()
    suspicious = []
    for t in high_topics:
        cues = json.loads(t["cue_anchors"] or "[]")
        cards = con.execute(
            "SELECT l.inst_id, m.summary, m.dfcp_tag FROM topic_links l "
            "JOIN core_memory_card m ON m.inst_id = l.inst_id WHERE l.topic_id=?",
            (t["topic_id"],)).fetchall()
        for c in cards:
            # p2_exempt 豁免(2026-11-16 用户裁决): 人工裁决留簇的边界卡
            # (正文核心=入库判定规则但摘要无锚), dfcp_tag.p2_exempt=user-retain-<date>
            try:
                tag = json.loads(c["dfcp_tag"] or "{}")
            except (TypeError, ValueError):
                tag = {}
            if tag.get("p2_exempt"):
                continue
            if not any(cue in (c["summary"] or "") for cue in cues):
                suspicious.append({"topic_id": t["topic_id"], "inst_id": c["inst_id"],
                                   "note": "summary无锚词命中, 疑似混入"})
    result["p2_high_freq"] = {"topics": [t["topic_id"] for t in high_topics],
                              "cards_checked": sum(len(con.execute(
                                  "SELECT 1 FROM topic_links WHERE topic_id=?", (t["topic_id"],)).fetchall())
                                  for t in high_topics),
                              "suspicious": suspicious}
    _log("e7_p2_high_freq", {"suspicious": len(suspicious)})


# ---------- P3 范式标定复审 ----------

def _parse_ts(s):
    try:
        return datetime.fromisoformat((s or "")[:19])
    except ValueError:
        return None


def p3_paradigm_calibration(con, result):
    proposals = []
    for table in ("core_thinking_paradigm", "core_skill_paradigm"):
        # calibration_pending 复审
        for p in con.execute(f"SELECT * FROM {table} WHERE status='calibration_pending'").fetchall():
            recent_evidence = con.execute(
                "SELECT COUNT(*) FROM audit_paradigm_evidence WHERE pid=? AND outcome='pass'",
                (p["pid"],)).fetchone()[0]
            proposals.append({"pid": p["pid"], "table": table,
                              "proposal": "propose_recalibrate" if recent_evidence > 0 else "propose_keep_pending",
                              "note": f"夜审中, 新增pass实证{recent_evidence}"})
        # candidate 晋升检查(3 独立实证全 pass → 提案, 不直接升)
        for p in con.execute(f"SELECT * FROM {table} WHERE status='candidate' AND evidence_pass>0").fetchall():
            if p["evidence_pass"] >= PARADIGM_MIN and p["evidence_pass"] == p["evidence_count"]:
                proposals.append({"pid": p["pid"], "table": table, "proposal": "propose_promote",
                                  "note": f"3独立实证全pass ({p['evidence_pass']}/{p['evidence_count']}), 待人终审"})
        # 衰减标定: active 超90d未触发且无近期实证 → 提案重新标定
        for p in con.execute(f"SELECT * FROM {table} WHERE status='active'").fetchall():
            last = _parse_ts(p["last_trigger_ts"]) or _parse_ts(p["ts"])
            if last and (datetime.now() - last).days > DECAY_DAYS:
                proposals.append({"pid": p["pid"], "table": table, "proposal": "propose_recalibrate",
                                  "note": f"active超{DECAY_DAYS}d未触发, 建议重激活标定或降权排尾"})
    result["p3_calibration"] = {"proposals": proposals}
    _log("e7_p3_calibration", {"n": len(proposals)})


# ---------- P4 证据回写(候选证据账) ----------

def p4_evidence_writeback(result):
    """maintenance_cases 同型 paradigm_hint ≥3 → 写候选证据(outcome='proposed')"""
    cases_path = os.path.join(_HERE, "data", "memory", "maintenance_cases.jsonl")
    if not os.path.exists(cases_path):
        result["p4_evidence"] = {"cases": 0, "written": 0}
        return
    from collections import Counter
    cases = []
    with open(cases_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    cases.append(json.loads(line))
                except ValueError:
                    continue
    hints = Counter(c.get("paradigm_hint") for c in cases if c.get("paradigm_hint"))
    con = _conn()
    written = 0
    try:
        for hint, n in hints.items():
            if n < PARADIGM_MIN:
                continue
            exists = con.execute(
                "SELECT COUNT(*) FROM audit_paradigm_evidence WHERE context LIKE ? AND outcome='proposed'",
                (f"%{hint}%",)).fetchone()[0]
            if exists:
                continue
            con.execute(
                "INSERT INTO audit_paradigm_evidence (pid, ts, outcome, context, delta, source_log) "
                "VALUES (?,?,?,?,?,?)",
                (f"auto:{hint}", _ts(), "proposed",
                 f"维护案例同型×{n} 触发范式候选(非实证结论, 待人工核)",
                 json.dumps({"case_count": n}, ensure_ascii=False), "maintenance_cases.jsonl"))
            written += 1
        con.commit()
    finally:
        con.close()
    result["p4_evidence"] = {"cases": len(cases), "hints": dict(hints), "written": written}
    _log("e7_p4_evidence", {"written": written})


def rollback_governance(target, since=None):
    """批量回滚治理动作(2026-11-16 用户裁决⑤)。
    target: card_id(全 id) 或 'all'; since: YYYY-MM-DD 时间窗下限(可选)。
    只回滚 resolved_by='e7-auto' 的行——人工裁决(user-*)永不被机器回滚。
    回滚动作: verdict/verdict_ts/resolved_by 置回 NULL(回 hold 待审态)。
    回滚记录写 audit_topic_log, 不删任何数据。"""
    con = _conn()
    try:
        q = "SELECT inst_id, verdict_ts, resolved_by FROM core_hold_pool " \
            "WHERE verdict='released' AND resolved_by='e7-auto'"
        args = []
        if target != "all":
            q += " AND inst_id=?"
            args.append(target)
        if since:
            q += " AND verdict_ts>=?"
            args.append(since)
        rows = con.execute(q, args).fetchall()
        con.execute("BEGIN")
        for r in rows:
            con.execute(
                "UPDATE core_hold_pool SET verdict=NULL, verdict_ts=NULL, resolved_by=NULL "
                "WHERE inst_id=? AND resolved_by='e7-auto'", (r["inst_id"],))
        con.commit()
        _log("e7_rollback", {"target": target, "since": since, "rolled_back": len(rows),
                             "cards": [r["inst_id"] for r in rows]})
        return len(rows)
    finally:
        con.close()


def main():
    # 回滚子命令: 优先于夜间主流程
    args = sys.argv[1:]
    if args and args[0] == "--rollback":
        if len(args) < 2:
            print("用法: --rollback <card_id|all> [--since YYYY-MM-DD]")
            return 2
        target = args[1]
        since = args[args.index("--since") + 1] if "--since" in args else None
        n = rollback_governance(target, since)
        print(f"[E7-rollback] 回滚 {n} 条 e7-auto 治理动作 (人工裁决不动)")
        return 0
    result = {"ts": _ts(), "ok": True,
              "emergency_stop": emergency_stop_active()}
    con = _conn()
    try:
        p1_hold_review(con, result)
        p2_high_freq_inspect(con, result)
        p2b_cue_discrimination_recheck(con, result)
        p3_paradigm_calibration(con, result)
    finally:
        con.close()
    p4_evidence_writeback(result)  # 独立连接(内部自管)
    # 落盘
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    p1 = result.get("p1_hold", {})
    p2 = result.get("p2_high_freq", {})
    if p1.get("emergency_stop") or p2.get("emergency_stop"):
        print(f"[E7] ⚠ 紧急总开关生效中 ({EMERGENCY_STOP_FILE}) — 自动治理全暂停, 仅输出观察提案")
    else:
        p2b = result.get("p2b_cue_recheck", {})
        print(f"[E7] hold分流: 自动放行 {p1.get('auto_released',0)} | 自动留置 {p1.get('auto_kept',0)} "
              f"| 人工队列 {len(p1.get('manual_queue',[]))} | 高频簇可疑 {len(p2.get('suspicious',[]))} "
              f"| 证据回写 {result['p4_evidence']['written']} 条"
              + (f" | cue复检 {p2b.get('checked',0)}项/告警{len(p2b.get('warnings',[]))}条" if p2b else ""))
    _print_audit_detail(result)   # 人闸审计明细(带卡摘要, 可读性规约 2026-11-16)


def _print_audit_detail(result):
    """人闸可读明细: 每条提案/疑点带卡摘要+原因, 支持晨报直接审计。
    2026-11-16 用户裁决: 仅 inst_id 短前缀+计数无法审计, 必须输出详情。"""
    con = _conn()
    try:
        def card_summary(iid):
            # 2026-11-16 ID收口: LIKE 兼容摘除, 全id等值查询(短id存量已迁移)
            r = con.execute("SELECT summary FROM core_memory_card WHERE inst_id=?",
                            (iid,)).fetchone()
            return (r["summary"][:80] + "…") if r and len(r["summary"]) > 80 else (r["summary"] if r else "!!卡不存在")

        p1 = result.get("p1_hold", {})
        hold_audit = p1.get("audit", [])
        if hold_audit:
            print("\n— P1 hold池自治分流明细 (待审%d | 自动放行%d | 自动留置%d | 人工队列%d) —"
                  % (p1.get("pending", 0), p1.get("auto_released", 0),
                     p1.get("auto_kept", 0), len(p1.get("manual_queue", []))))
            for p in hold_audit:
                a = _standard_audit_record(con, p)
                print("  [%s] %s/%s → %s" % (a["card_id"][:8], a["check_type"], a["state"], a["next_action"]))
                print("    卡: %s" % a["summary"])
                print("    规则: %s | 裁决: %s@%s | 到期: %s"
                      % (a["rule_triggered"][:60], a["last_judge"]["by"], a["last_judge"]["at"][:16],
                         (a["expire_at"] or "?")[:10]))

        p2b = result.get("p2b_cue_recheck", {})
        p2b_warn = p2b.get("warnings", [])
        if p2b_warn:
            print("\n— P2b cue区分度变质告警 (%d条, 只告警不删改, 人工裁决) —" % len(p2b_warn))
            for w in p2b_warn:
                print("  [%s] cue: %s (簇内%d/他簇%d, 区分度%.2f < %.2f)"
                      % (w["topic_id"], w["cue"], w["n_in"], w["n_other"], w["disc"], _CUE_DISCRIM_MIN_E7))
                print("    %s" % w["note"])

        susp = result.get("p2_high_freq", {}).get("suspicious", [])
        if susp:
            print("\n— P2 高频簇可疑混入明细 (%d张) —" % len(susp))
            for s in susp:
                print("  [%s] 簇: %s" % (s["inst_id"][:8], s["topic_id"]))
                print("    卡: %s" % card_summary(s["inst_id"]))
                print("    %s" % s["note"])

        p3 = result.get("p3_calibration", {}).get("proposals", [])
        if p3:
            print("\n— P3 范式标定提案明细 (%d条) —" % len(p3))
            for p in p3:
                print("  [%s] %s: %s (%s)" % (p.get("pid", "?"), p["proposal"],
                                              p.get("note", ""), p.get("table", "")))
    finally:
        con.close()


if __name__ == "__main__":
    main()