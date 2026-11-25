# -*- coding: utf-8 -*-
# memory/memory_attitude.py — 回执态度元标签子通路（#11 方案A，2026-11-22）
#
# 定位：用户对某条已有记忆的「主观态度（认可/不认可/中立）」留存。
#   - 回执本体仍走 memory_judge → 绝大多数 discard（不生成新记忆）；
#   - 本模块仅把「态度 + 目标记忆ID + 评论」作为元标签存**附属存储**，
#     多条回执可对同一记忆叠加多态度（含时间线）。
#
# ⚠️ 红线（硬约束，来自 docs/08_patrol_review/review_20261121_system_status.md §五）：
#   态度是「人主观反馈，只给人看」。机器**绝不**据此自动上调/下调记忆可信度、
#   绝不自动降级/作废记忆。推演 / F20 权重 / 检索打分**默认不消费**本模块数据。
#   仅审计报表、晨报展示、人审参考。
#
# 存储选型：独立 JSONL（data/memory/memory_attitude.jsonl），与 judge_log.jsonl /
#   gate_log.jsonl 审计日志同构——主库 core_memory_card 零改动、主链零侵入。
#   （立项文档「附属表」在此实现为附属存储；不建 sqlite 表以免动 corpus_store SCHEMA。）
#
# 生效范围：仅 LLM 判定通路（_judge_call_llm）解析出结构化 attitude 时落库。
#   规则桩 stub 降级通路**不调用本模块**（stub 无语义识别，避免误判），零副作用。

import io
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_organize import append_jsonl

MEM_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(MEM_DIR), "data", "memory")
ATTITUDE_JSONL = os.path.join(DATA_DIR, "memory_attitude.jsonl")

ATTITUDE_TYPES = ("approve", "disapprove", "neutral")


def _norm_type(t):
    """归一态度类型；非法值返回 None（调用方据此跳过，不落脏数据）。"""
    if not t:
        return None
    tl = str(t).strip().lower()
    return tl if tl in ATTITUDE_TYPES else None


def record_attitude(attitude_type, target_memory_id, comment=None,
                    source_dialog_id=None, context=None):
    """落一条态度记录到附属 JSONL。返回写入的记录 dict，非法输入返回 None。

    attitude_type: approve|disapprove|neutral（非法→None 不写）
    target_memory_id: 被评价的记忆卡 inst_id（必填，缺失则不写——无法归因的态度无意义）
    comment: 用户原话/摘要（可选）
    source_dialog_id: 来源对话 id（可选，弱关联 #12 对话追溯）
    context: 附加上下文 dict（可选，如 session_id/trigger）
    ⚠️ 本函数只写附属存储，绝不触碰 core_memory_card。
    """
    at = _norm_type(attitude_type)
    if at is None:
        return None
    tgt = (str(target_memory_id).strip() if target_memory_id else "")
    if not tgt:
        return None
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "attitude_type": at,
        "target_memory_id": tgt,
        "comment": (str(comment)[:500] if comment else None),
        "source_dialog_id": (str(source_dialog_id).strip() if source_dialog_id else None),
    }
    if isinstance(context, dict) and context:
        rec["context"] = {k: context[k] for k in context if k in
                          ("session_id", "trigger", "judge_source")}
    append_jsonl(ATTITUDE_JSONL, rec)
    return rec


def list_attitudes(target_memory_id=None, attitude_type=None, limit=None):
    """只读查询态度记录。默认按 ts 倒序（最新在前）。
    target_memory_id: 只看某条记忆的态度；attitude_type: 只看某型；limit: 截断条数。"""
    if not os.path.exists(ATTITUDE_JSONL):
        return []
    out = []
    with io.open(ATTITUDE_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if target_memory_id is not None and d.get("target_memory_id") != target_memory_id:
                continue
            if attitude_type is not None and d.get("attitude_type") != attitude_type:
                continue
            out.append(d)
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    if limit is not None:
        out = out[:limit]
    return out


def summarize_attitudes(target_memory_id):
    """某条记忆的态度汇总（人审参考）：计数 approve/disapprove/neutral + 最近一条。
    ⚠️ 仅供人看；返回值**不得**被用于修改记忆可信度/存活状态。"""
    recs = list_attitudes(target_memory_id=target_memory_id)
    cnt = {t: 0 for t in ATTITUDE_TYPES}
    for r in recs:
        t = r.get("attitude_type")
        if t in cnt:
            cnt[t] += 1
    return {"target_memory_id": target_memory_id, "total": len(recs),
            "counts": cnt, "latest": recs[0] if recs else None}