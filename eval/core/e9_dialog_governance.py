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
TRANSCRIPT = r"C:/Users/uidq0280/AppData/Roaming/DSV CodeChat Desktop/logs/applog.log"
_SUBMIT_MARK = "user submit: "


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
    for hhmm, text in subs:
        sh = gate._simhash(text)
        if any(gate._hamming(sh, old) <= 6 for old in day_sh):
            dup += 1                       # 同源/相似段: 引用去重(规约四)
            continue
        seg_n = len(todays) + stored + 1
        cid = "dialog.%s.tseg%02d" % (day, seg_n)
        body = "[用户提交-transcript兜底] %s" % text
        cs.upsert_corpus(cid, "session_dialog", body, tree_vol=day,
                         tree_ch="tseg%02d" % seg_n,
                         title="codechat-transcript",
                         # 来源指针(规约③): 路径+当日窗口, 可追溯可剔除
                         source_ref="%s; window=%s %s" % (TRANSCRIPT, day, hhmm))
        day_sh.append(sh)
        stored += 1
    report["transcript"] = {"scanned": len(subs), "stored": stored,
                            "dup_skipped": dup}




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

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    t = report.get("transcript", {})
    print("[E9] 扫描%d段 | 高价值%d | 提案%d(送pending) | 重复跳过%d | miss_case%d | 假阴性疑点%d "
          "| transcript兜底: 提取%d/入库%d/去重%d"
          % (report["scanned"], report["high_value"], report["proposed"],
             report["dup_skipped"], len(report["miss_cases"]),
             len(report["risk_flag_e7"]),
             t.get("scanned", 0), t.get("stored", 0), t.get("dup_skipped", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())