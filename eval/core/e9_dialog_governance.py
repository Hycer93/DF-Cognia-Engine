# -*- coding: utf-8 -*-
# eval/core/e9_dialog_governance.py — E9 夜间对话治理(2026-11-15 用户定稿规约新增)
#
# 定位: 归档侧【主通道】(用户裁决正视工程现实——会话收尾信号不可靠, 夜间固定时序才是主力):
#   1. 遍历当日新增 raw_corpus(kind=session_dialog, 热区=近90天; 冷区跳常规扫描)
#   2. 复跑 should_archive_highvalue 判定(memory_gate.judge 同款逻辑)
#   3. 高价值 → 拟提炼卡送 store_pending(机器只提案不裁决, 终审=人闸晨报)
#      【红线: E9 绝不直写 core_memory_card】
#   4. 优先级规约(与会话旁路判定冲突时):
#      旁路true+夜间同源hash → 跳过不重复生成
#      旁路false+夜间true   → 覆盖补提
#      旁路true+夜间false   → 保留已提卡片, 打风险标送 E7 复审(不自动删)
#   5. miss_case 审计: 昨日 gate_miss_case.jsonl 列入晨报(hint 校准样本, 卡片ID人工补)
#   6. 判定日志审计: gate_log.jsonl 当日 probe/judge 分布入晨报, 假阴性模式攒 scar_case
#   7. transcript 兜底扫描(2026-11-15 用户批准新增):
#      扫描 CodeChat 平台日志 applog.log, 提取当日用户提交原文(inputSubmit 行),
#      组段后 simhash 与当日已有 session_dialog 比对, 非重复段兜底入 raw_corpus。
#      三条硬规约(写死在代码, 不可配置):
#        ① 只读: 对 applog.log 零写入零删除零改名
#        ② 复本隔离: 进 DFCP 的只有 raw_corpus 副本, 平台文件永不被反向修改
#        ③ 来源指针: source_ref 必须指向 transcript 路径+时间窗, 可追溯可剔除
#      (助手正文不落平台本地盘——applog 仅含"回答结束"标记, 用户原话侧由本步
#       兜底, 助手侧由 PreCompact hook dialog 命令覆盖, 两源互补)
#
# 编号账本: E9 续编不重排(E3 缺号记录退役); core 主链由六脚本变七脚本, 三处同步
# (nightly_run STEPS / README 管线分级 / CodeChat 记忆条目——由会话AI执行)
#
# 产物: logs/e9_dialog_governance.json + stdout 摘要; nightly_run.py STEPS 尾部

import io
import json
import os
import sys
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))

import corpus_store as cs          # noqa: E402
import memory_gate as gate         # noqa: E402

OUT = os.path.join(_HERE, "logs", "e9_dialog_governance.json")
DB_DIR = os.path.join(_HERE, "data", "memory")
PENDING = os.path.join(DB_DIR, "store_pending.jsonl")
MISS = os.path.join(DB_DIR, "gate_miss_case.jsonl")
GATE_LOG = os.path.join(DB_DIR, "gate_log.jsonl")
# transcript 兜底扫描源(只读!): CodeChat 平台日志, 当日用户提交原文在此
# ---- P-e9-path-config(2026-11-18 运维合并段二裁决): 路径收拢 config/paths.json ----
# 优先级: 环境变量 CODECHAT_APPLOG > paths.json > 默认值。
# 探测失败不再静默——打出明确告警行(e9_transcript_offline), 晨报高亮标记
# transcript 通道断流; _scan_transcript 仍降级空列表不阻断主链(只告警)。
_TRANSCRIPT_DEFAULT = r"C:/Users/uidq0280/AppData/Roaming/DSV CodeChat Desktop/logs/applog.log"
def _resolve_transcript_path():
    import os as _os
    p = _os.environ.get("CODECHAT_APPLOG")
    if p:
        return p, "env"
    try:
        cfg = json.load(io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))), "config", "paths.json"),
            encoding="utf-8"))
        v = cfg.get("transcript_applog")
        if v:
            return v, "paths.json"
    except Exception:
        pass
    return _TRANSCRIPT_DEFAULT, "default"
TRANSCRIPT, _TRANSCRIPT_SRC = _resolve_transcript_path()
_SUBMIT_MARK = "user submit: "
if not os.path.exists(TRANSCRIPT):
    # 探测失败=晨报可grep的显式告警(非静默): 换环境/换机器立即暴露断流
    print(f"[e9][transcript_offline] applog路径探测失败(src={_TRANSCRIPT_SRC}): {TRANSCRIPT} —— transcript兜底通道断流, 当日仅session_dialog复跑归档; 请核查 config/paths.json 或设置 CODECHAT_APPLOG", flush=True)


def _sanitize_text(text):
    """#20 合规脱敏：落 raw_corpus 副本前清洗常见 PII（只作用于副本，平台原文件只读不变）。
    规约①只读不变；此步仅处理进 DFCP 的副本正文。"""
    import re as _re
    if not text:
        return text
    s = text
    # 手机号（中国大陆 11 位）
    s = _re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号]", s)
    # 身份证（18 位，含 X）
    s = _re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[身份证]", s)
    # 邮箱
    s = _re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[邮箱]", s)
    # 银行卡（16-19 位连续数字）
    s = _re.sub(r"(?<!\d)\d{16,19}(?!\d)", "[卡号]", s)
    # 常见密钥/密码赋值（key=xxx / password: xxx / token=xxx）
    s = _re.sub(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*\S{8,}",
                r"\1=[已脱敏]", s)
    return s


def _scan_transcript(day):
    """从 applog.log 提取当日用户提交原文(只读)。
    格式: [YYYYMMDD HH:MM:SS.mmm|info] [codechat-ui][inputSubmit][log]  user submit: <原文>
    返回 [(hhmmss, text), ...]; 平台文件异常时静默降级为空列表(不阻断主链)。"""
    try:
        lines = io.open(TRANSCRIPT, encoding="utf-8", errors="replace").readlines()
    except OSError:
        return []
    stamp = day.replace("-", "")          # 2026-11-15 -> 20261115
    out = []
    for ln in lines:
        if not ln.startswith("[" + stamp):
            continue
        if _SUBMIT_MARK not in ln:
            continue
        head, _, text = ln.partition(_SUBMIT_MARK)
        text = text.strip()
        if not text:
            continue
        m = head[1:head.find("|")]       # 20261115 15:03:07.244 -> 取时间
        out.append((m.split(" ", 1)[1][:8] if " " in m else "", text))
    return out


def _transcript_backfill(day, subs, report):
    """当日用户提交组段 → simhash 当日去重 → 兜底入 raw_corpus(复本隔离)。"""
    if not subs:
        return
    todays = [cs.get_corpus(r["id"]) for r in
              cs.list_corpus(kind="session_dialog", tree_vol=day)]
    day_sh = [gate._simhash(s["body"]) for s in todays if s]
    stored = 0
    dup = 0
    too_short = 0
    for hhmm, text in subs:
        # 2026-11-21 防护(用户裁决): t 通道前置过滤——≤20 字节纯碎片("改"/"好的")
        # 无检索价值, 入库前拦截不灌主表(历史已留 811 条碎片, 见 maintenance_session-
        # dialog_cleanup_20261121.md)。阈值与碎片归档口径对齐(≤20 字节)。
        if len((text or "").strip().encode("utf-8")) <= 20:
            too_short += 1
            continue
        # 2026-11-16 用户裁决①: 正文与来源前缀解耦——body 存纯净正文, simhash 只作用于
        # 纯正文, 来源通道标签在 title 元字段。杜绝不同通道前缀导致的 hash 形态错位。
        # #20 合规：transcript 通道落库前对副本正文脱敏（只影响副本，平台原文件只读）。
        body = _sanitize_text(text)
        sh = gate._simhash(body)
        if any(gate._hamming(sh, old) <= 6 for old in day_sh):
            dup += 1                       # 同源/相似段: 引用去重(规约四)
            continue
        # 2026-11-16 用户裁决②(方案B): 尾号取全局序列池原子分配, 不再当日计数。
        # id = dialog.<date>.t<全局尾号>(t=transcript通道标识, 保留 seg/tseg 区分)
        n = cs.next_dialog_seq()
        cid = "dialog.%s.t%04d" % (day, n)
        cs.upsert_corpus(cid, "session_dialog", body, tree_vol=day,
                         tree_ch="t%04d" % n,
                         title="codechat-transcript[用户提交]",
                         # 来源指针(规约③): 路径+当日窗口, 可追溯可剔除
                         source_ref="%s; window=%s %s" % (TRANSCRIPT, day, hhmm))
        day_sh.append(sh)
        stored += 1
    report["transcript"] = {"scanned": len(subs), "stored": stored,
                            "dup_skipped": dup, "too_short_skipped": too_short}




def _load_pending_ids():
    """旁路已提交的提案判重源(按 summary 粗判重——旁路卡无 simhash 字段, 用标题前缀)"""
    ids = set()
    try:
        for l in io.open(PENDING, encoding="utf-8"):
            d = json.loads(l)
            if d.get("summary"):
                ids.add(d["summary"][:24])
    except FileNotFoundError:
        pass
    return ids


def _check_h_channel_stalled(lookback_days=3):
    """#12 h 通道断流审计：统计最近 N 天 tree_ch 以 'h' 开头的 session_dialog 记录数。
    h 通道=PreCompact hook 原生落对话原文; t 通道=E9 applog 兜底(仅用户原文)。
    h 长期为 0 说明 PreCompact hook 可能异常, 模型回复会有丢失风险。
    返回 {h_records, t_records, days, stalled}；stalled=True 时 E9 打印告警进晨报。"""
    from datetime import datetime as _dt, timedelta as _td
    days = []
    for i in range(lookback_days):
        days.append((_dt.now() - _td(days=i)).strftime("%Y-%m-%d"))
    h_cnt = t_cnt = 0
    for d in days:
        for row in cs.list_corpus(kind="session_dialog", tree_vol=d):
            seg = cs.get_corpus(row["id"])
            if not seg:
                continue
            ch = seg.get("tree_ch") or ""
            if ch.startswith("h"):
                h_cnt += 1
            elif ch.startswith("t"):
                t_cnt += 1
    return {"h_records": h_cnt, "t_records": t_cnt,
            "days": lookback_days, "stalled": h_cnt == 0}


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    report = {"ts": datetime.now().isoformat(timespec="seconds"), "date": today,
              "scanned": 0, "cold_skipped": 0, "high_value": 0,
              "proposed": 0, "dup_skipped": 0, "risk_flag_e7": [],
              "miss_cases": [], "probe_stats": {}}

    # 热区窗口: 近90天(冷区默认跳过——规约四)
    heat_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    segs = cs.list_corpus(kind="session_dialog", tree_vol=today)
    # list_corpus 按 vol 精确查; 热区判定对当日段天然满足, 冷区统计:
    report["scanned"] = len(segs)

    pending_ids = _load_pending_ids()
    for row in segs:
        seg = cs.get_corpus(row["id"])
        if not seg:
            continue
        v = gate._llm_archive(seg["body"])
        if v["high_value"]:
            # 拟卡提案(只提案不写主库——两步链)
            summary = "会话裁决提炼(夜间E9): %s" % (seg["title"] or seg["id"])
            if summary[:24] in pending_ids:
                report["dup_skipped"] += 1      # 旁路已提: 跳过(优先级规则①)
                continue
            proposal = {"summary": summary,
                        "detail": "E9夜间扫描主通道自动提案(来源=%s, simhash见gate_log)。"
                                  "原文: %s。终审人闸——此卡仅为提案, 请晨报确认后放行,"
                                  "确认后由人工或旁路通道补全 detail 血缘。" % (seg["id"], seg["source_ref"]),
                        "source": "E9夜间扫描 %s" % today,
                        "domain": "002-003", "behavior": "Jw", "relation": "P",
                        "confidence": "L1", "memory_class": "strategy_evolve",
                        "repro": "situational", "event_ts": today,
                        "origin_corpus": seg["id"], "priority": "nightly"}
            with io.open(PENDING, "a", encoding="utf-8") as f:
                f.write(json.dumps(proposal, ensure_ascii=False) + "\n")
            report["proposed"] += 1
        report["high_value"] += 1 if v["high_value"] else 0
        # 旁路true夜间false 的风险标(规则③): 检查 pending 中带 priority=session_close
        # 且昨日判定 true 的——保留但列 E7 复审(此处只统计不删)

    # transcript 兜底扫描(第七步; 只读扫描→复本隔离入 raw_corpus)
    subs = _scan_transcript(today)
    _transcript_backfill(today, subs, report)

    # miss_case 晨报列示(昨日窗口)
    if os.path.exists(MISS):
        for l in io.open(MISS, encoding="utf-8"):
            d = json.loads(l)
            if d.get("ts", "").startswith(yesterday) or d.get("ts", "").startswith(today):
                report["miss_cases"].append(d)

    # probe 判定审计(当日 gate_log 分布)
    if os.path.exists(GATE_LOG):
        for l in io.open(GATE_LOG, encoding="utf-8"):
            d = json.loads(l)
            if not d.get("ts", "").startswith(today):
                continue
            k = d.get("op")
            report["probe_stats"][k] = report["probe_stats"].get(k, 0) + 1
            if k == "probe" and not d.get("deep_retrieve") and \
                    any(w in d.get("topic", "") for w in ("E7", "F系", "DFCP", "管线", "图谱")):
                report["risk_flag_e7"].append({"topic": d.get("topic", "")[:60],
                                               "note": "疑似probe假阴性(工程信号但判定false), 攒scar_case"})

    # #12 h 通道断流审计(2026-11-22): h=PreCompact原生, t=applog兜底。
    # h 最近 N 天为 0 → 告警(模型回复有丢失风险), 进晨报。
    try:
        hstat = _check_h_channel_stalled(3)
        report["h_channel"] = hstat
        if hstat["stalled"]:
            print("[e9][h_channel_stalled] 最近%d天 h通道(PreCompact) 0 条新对话, "
                  "t通道兜底 %d 条(仅用户原文)。PreCompact hook 可能异常, "
                  "模型回复有丢失风险, 请核查 ~/.codechat/settings.json PreCompact 配置。"
                  % (hstat["days"], hstat["t_records"]))
    except Exception as e:  # noqa: BLE001
        report["h_channel"] = {"error": repr(e)}

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    t = report.get("transcript", {})
    hc = report.get("h_channel", {})
    print("[E9] 扫描%d段 | 高价值%d | 提案%d(送pending) | 重复跳过%d | miss_case%d | 假阴性疑点%d "
          "| transcript兜底: 提取%d/入库%d/去重%d | h通道近%d天%d条%s"
          % (report["scanned"], report["high_value"], report["proposed"],
             report["dup_skipped"], len(report["miss_cases"]),
             len(report["risk_flag_e7"]),
             t.get("scanned", 0), t.get("stored", 0), t.get("dup_skipped", 0),
             hc.get("days", 3), hc.get("h_records", -1),
             "(⚠断流)" if hc.get("stalled") else ""))
    _print_audit_detail(today, report)
    return 0


def _print_audit_detail(today, report):
    """人闸审计明细(2026-11-16 用户裁决: 计数式摘要无法审计, 必须逐条带内容)。"""
    # 提案: 逐条给拟卡 summary + 来源段
    if report["proposed"]:
        print("\n— E9 提案明细 (%d条, 终审=人闸) —" % report["proposed"])
        try:
            for l in io.open(PENDING, encoding="utf-8"):
                d = json.loads(l)
                if (d.get("source") or "").startswith("E9夜间扫描 %s" % today):
                    print("  拟卡: %s" % d["summary"][:90])
                    print("    %s" % d["detail"][:150])
        except FileNotFoundError:
            pass
    # 高价值段但旁路重复的: 展示跳过原因
    if report["dup_skipped"]:
        print("— 旁路重复跳过 %d 条(优先级规约①: 旁路已提, 夜间不重复) —" % report["dup_skipped"])
    # transcript 兜底: 当日入库段的样例(前3条+时间范围)
    tr = report.get("transcript", {})
    if tr.get("stored"):
        rows = cs.list_corpus(kind="session_dialog", tree_vol=today)
        tsegs = [cs.get_corpus(r["id"]) for r in rows
                 if ".tseg" in r["id"] or ".t1" in r["id"] or ".t2" in r["id"]]
        print("— transcript 兜底入库 %d 段, 样例: —" % tr["stored"])
        for s in tsegs[:3]:
            print("  [%s] %s" % (s["id"][-6:], s["body"][:90]))
    if report["miss_cases"]:
        print("— miss_case 明细 (%d) —" % len(report["miss_cases"]))
        for d in report["miss_cases"]:
            print("  %s | hint: %s" % (d.get("ts", "?")[:16], str(d.get("hint", ""))[:70]))
    if report["risk_flag_e7"]:
        print("— probe 假阴性疑点 (%d, 攒 scar_case) —" % len(report["risk_flag_e7"]))
        for d in report["risk_flag_e7"]:
            print("  topic: %s — %s" % (d["topic"], d["note"]))


if __name__ == "__main__":
    sys.exit(main())