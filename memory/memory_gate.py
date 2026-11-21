# -*- coding: utf-8 -*-
# memory/memory_gate.py — 会话记忆两级读取/归档判定门 (2026-11-15 用户定稿规约)
#
# 架构(用户收敛版, 五条修正全固化):
#   读取侧两级: ①轻量基线=静态摘要快照无条件加载(等价 MEMORY.md, 杜绝完全失忆)
#              ②深度检索门 should_deep_retrieve 独立判定, 只管"要不要向量检索",
#                false 不损失基本盘(假阴性最多损失深度)
#   归档侧主次: 主通道=夜间扫描(E9); 会话收尾=快速旁路仅优先提交;
#              优先级: 旁路true+夜间同源hash→跳过; 旁路false+夜间true→覆盖补提;
#              旁路true+夜间false→保留卡片打风险标送E7复审不自动删
#   即时归档合法触发仅两类: 用户显式指令(/force-archive,"这条入库") 或
#              脚本侧硬事件(E管线终裁日志推送)。模型自主识别打标记=已废除的自觉依赖后门
#   raw_corpus 治理: 段落 simhash 入库前比对, 重复打引用标记不新增; 热区90天全扫
#              冷区跳常规扫描仅溯源加载; 不删除
#   miss_case: 深度检索空结果记 hint+主题(卡片ID留空人工补), E9晨报列示——
#              第一版不假装自动反馈环(伪闭环)
#
# 独立性: 判定模块单独调用可消融; 判定日志全部入库纳 E6/E7 审计产 scar_case
# LLM 判定: glm-flash 级(高频轻活经济性); 判定失败=保守 true(读侧)/false(归档侧交夜间)
#
# 2026-11-21 碎片防护规约(用户裁决, 见 maintenance_session_dialog_cleanup_20261121.md):
#   - t 通道(transcript 批量导入, E9): 入库前拦截 ≤20 字节纯碎片, 不灌主表
#     (历史曾灌 811 条碎片占表 46%, 已归档 session_dialog_archive_fragment);
#   - h 通道(PreCompact 原生, 本文件 dialog 分支): 入库前同口径长度校验,
#     碎片返回 too-short-skip 不写 raw_corpus;
#   - 退出码纪律: 可预期业务跳过(empty-skip/too-short-skip/duplicate-referenced)=RC0;
#     未捕获异常=RC≠0 且 stderr 打 [memory_hook_abnormal] 告警, 不静默丢记忆;
#   - 定期巡检: 每周跑 eval/core/patrol_dialog_fragments.py 输出碎片占比报告(阈值 15%)。

import io
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "memory"))
DB_DIR = os.path.join(HERE, "data", "memory")
GATE_LOG = os.path.join(DB_DIR, "gate_log.jsonl")
MISS_CASE = os.path.join(DB_DIR, "gate_miss_case.jsonl")


# ---------- simhash (64bit, 纯本地无依赖) ----------

def _simhash(text: str) -> str:
    feats = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z0-9_]{2,}", text.lower())
    if not feats:
        return "0" * 16
    grams = set()
    for f in feats:
        f = f[:32]
        for i in range(len(f) - 1):
            grams.add(f[i:i + 2])
    v = [0] * 64
    for g in grams:
        h = hash(g) & 0xFFFFFFFFFFFFFFFF
        for b in range(64):
            v[b] += 1 if (h >> b) & 1 else -1
    bits = "".join("1" if x > 0 else "0" for x in v)
    return format(int(bits, 2), "016x")


def _hamming(a: str, b: str) -> int:
    if len(a) != len(b) or not a.strip("0") or not b.strip("0"):
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------- 判定日志 ----------

def _log(op: str, payload: dict):
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "op": op, **payload}
    try:
        with io.open(GATE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        # P2-5: 原为 pass 静默丢弃; 改为告警但不阻断判定主链路
        print(f"[memory_gate_log_fail] {type(e).__name__}: {e} | op={op}",
              file=sys.stderr)


def _miss(hint, topic):
    with io.open(MISS_CASE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "hint": hint, "topic": topic, "hit_card": None},
                           ensure_ascii=False) + "\n")


def store_dialog(body: str, session: str, source_tag: str = "compact-boundary") -> dict:
    """对话片段落 raw_corpus(kind=session_dialog) —— #12 提取为可复用函数。

    h 通道(PreCompact)共用：旧 CLI 传 md 文本, 新 hook 传 stdin transcript 解析文本;
    两者走同一 store_dialog, 保证去重/取号/落库口径一致。
    body: 对话正文(已 strip); session: 会话标识(存 title); source_tag: 来源标记。
    规则(2026-11-21 用户裁决): ≤20 字节碎片拦截不灌主表; 当日 simhash 去重打引用标记。
    返回 dict: {op:'dialog', status: stored/empty-skip/too-short-skip/duplicate-referenced,...}
    业务跳过=正常返回(不抛异常), 由调用方决定退出码。
    """
    import corpus_store as cs
    body = (body or "").strip()
    if not body:
        return {"op": "dialog", "status": "empty-skip"}
    nb = len(body.encode("utf-8"))
    if nb <= 20:
        _log("dialog_too_short", {"session": session, "bytes": nb})
        return {"op": "dialog", "status": "too-short-skip", "bytes": nb}
    sh = _simhash(body)
    today = datetime.now().strftime("%Y-%m-%d")
    dups = []
    for row in cs.list_corpus(kind="session_dialog", tree_vol=today):
        old = cs.get_corpus(row["id"])
        if old and _hamming(sh, _simhash(old["body"])) <= 6:
            dups.append({"ref": row["id"], "simhash": sh})
    if dups:
        _log("dialog_dedup", {"session": session, "simhash": sh,
                              "refs": [d["ref"] for d in dups]})
        return {"op": "dialog", "status": "duplicate-referenced",
                "refs": [d["ref"] for d in dups]}
    seq_n = cs.next_dialog_seq()
    cid = "dialog.%s.h%04d" % (today, seq_n)
    h = cs.upsert_corpus(cid, "session_dialog", body, tree_vol=today,
                         tree_ch="h%04d" % seq_n, title=session,
                         source_ref="%s; %s" % (session, source_tag))
    _log("dialog_stored", {"id": cid, "content_hash": h, "simhash": sh,
                           "bytes": nb, "source_tag": source_tag})
    return {"op": "dialog", "status": "stored", "id": cid,
            "simhash": sh, "content_hash": h}


# ---------- CLI ----------

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="memory_gate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # 读取侧判定(会话开工首轮工具调用)
    p1 = sub.add_parser("probe", help="深度检索门判定")
    p1.add_argument("--topic", required=True, help="首条用户消息/任务意图摘要")
    p1.add_argument("--force", action="store_true", help="/force-mem-on: 跳判定强制深度检索")
    # 归档侧判定(夜间 E9 / 会话旁路)
    p2 = sub.add_parser("judge", help="归档高价值判定")
    p2.add_argument("--file", required=True, help="对话原文分段文件(raw_corpus 导出)")
    p2.add_argument("--force", action="store_true", help="/force-archive: 强制全套归档")
    # 对话分段归档(PreCompact hook 调用; 只存原文不判定)
    p3 = sub.add_parser("dialog", help="对话原文分段写入 raw_corpus")
    p3.add_argument("--file", required=True, help="对话文本文件(utf-8 md)")
    p3.add_argument("--session", required=True, help="会话标识(如 codechat-session/f-sim)")
    args = ap.parse_args()

    import corpus_store as cs

    if args.cmd == "dialog":
        # PreCompact 专属(旧路径): 读 md 文件 → 复用 store_dialog 落库
        body = io.open(args.file, encoding="utf-8").read().strip()
        r = store_dialog(body, args.session, source_tag="compact-boundary")
        print(json.dumps(r, ensure_ascii=False))

    elif args.cmd == "probe":
        # 深度检索门: 判定失败/超时→保守 true(读侧假阴性代价不对称)
        verdict = _llm_probe(args.topic)
        _log("probe", {"topic": args.topic[:80], "forced": args.force, **verdict})
        print(json.dumps({"op": "probe", "forced": args.force, **verdict},
                         ensure_ascii=False))
        if verdict.get("deep_retrieve") and verdict.get("query_hint"):
            # 消费侧提示(由会话AI执行 memory_hook.py query): 此处不代跑,
            # 空结果 miss_case 由消费侧回调 _miss (规约: 反馈环第一版人工补)
            print("hint> " + "; ".join(verdict["query_hint"]), file=sys.stderr)

    elif args.cmd == "judge":
        body = io.open(args.file, encoding="utf-8").read()
        verdict = (_FORCE if args.force else _llm_archive(body))
        _log("judge", {"file": args.file, "forced": args.force, **verdict})
        print(json.dumps({"op": "judge", "forced": args.force, **verdict},
                         ensure_ascii=False))


# ---------- 判定实现(本地启发第一版; LLM 升级位留接口) ----------

_HIGH_SIG = ["裁决", "定案", "定稿", "终审", "规约", "教训", "bug", "根因",
             "FAIL", "PASS", "方案", "架构", "驳回", "确认", "授权", "红线"]

_FORCE = {"high_value": True, "memory_class": "strategy_evolve",
          "reason": "user-force-archive(人工直通)"}


def _llm_probe(topic: str) -> dict:
    """should_deep_retrieve: 本地启发(信号词+工程语境)——第一版不烧LLM,
    判定日志照常入库供 E9 审计; 升级位: 换 glm-flash 结构化输出"""
    sig = [w for w in ("DFCP", "记忆", "管线", "E7", "E系", "F系", "推演", "topic",
                       "裁决", "judge", "入库", "检索", "图谱", "挂载") if w in topic]
    deep = bool(sig)
    hint = sig[:5] if sig else []
    return {"deep_retrieve": deep, "query_hint": hint,
            "reason": "信号词命中%d个" % len(sig) if deep else "无工程信号词, 仅静态基线"}


def _llm_archive(body: str) -> dict:
    """should_archive_highvalue: 本地启发第一版(信号词密度+长度);
    假阴性交夜间复跑(E9), 失败模式明写风险清单"""
    n = sum(body.count(w) for w in _HIGH_SIG)
    long = len(body) > 800
    high = n >= 3 or (n >= 2 and long)
    return {"high_value": high,
            "memory_class": "strategy_evolve" if high else None,
            "signal_density": n, "length": len(body),
            "reason": "高价值信号密度%d%s" % (n, "(长文)" if long else "")}


if __name__ == "__main__":
    # 2026-11-21 修复(用户裁决): 未捕获异常须 RC≠0 且打告警, 不静默丢记忆。
    # 业务跳过(empty-skip/duplicate-referenced)在分支内正常返回 0, 不经过这里。
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"[memory_hook_abnormal] gate 未捕获异常: {type(e).__name__}: {e}",
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(4)
