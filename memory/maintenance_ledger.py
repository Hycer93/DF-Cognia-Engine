# -*- coding: utf-8 -*-
"""维护案例账本：记忆系统自身运维操作的案例层。

三层定位（2026-11-11 用户定方向）：
- memories.jsonl      = 内容层（业务/专利/决策记忆）
- experiences.jsonl   = 内容蒸馏层（同实体案例归纳，如金价复盘）
- maintenance_cases   = 运维案例层（本文件）：记忆系统怎么被维护的案例
- paradigms.jsonl     = 范式层：≥3 同型案例抽象出的可复用技能/方式/范式

案例采集原则：自动挂点（demote/裁决/考古/修复落码时 append_case），
不依赖人工记得写。抽象触发：同 case_type+同触发模式 ≥PARADIGM_MIN →
晨报列范式候选，用户终裁后 frozen。
"""
import io
import json
import os
import uuid
from datetime import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_PATH = os.path.join(_BASE, "data", "memory", "maintenance_cases.jsonl")
PARADIGMS_PATH = os.path.join(_BASE, "data", "memory", "paradigms.jsonl")

PARADIGM_MIN = 3   # [先验-业务设定] 抽象门槛：3 同型案例（两案定势太险，三案成范式）


def _now():
    return datetime.now().isoformat(timespec="seconds")


def append_case(case_type: str, trigger: str, evidence: str,
                action: str, outcome: str, paradigm_hint: str = "",
                ts: str = None, case_id: str = None) -> dict:
    """追加一条维护案例。字段全字符串、供晨报与抽象器消费。
    case_type 见 TYPE_TAXONOMY；paradigm_hint=该案例指向的候选范式名。"""
    case = {
        "case_id": case_id or ("mc-" + uuid.uuid4().hex[:12]),
        "ts": ts or _now(),
        "case_type": case_type,
        "trigger": trigger,
        "evidence": evidence,
        "action": action,
        "outcome": outcome,
        "paradigm_hint": paradigm_hint,
        "derived_paradigm": None,   # 抽象后回填范式 id
    }
    with io.open(CASES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case


def load_cases() -> list:
    if not os.path.exists(CASES_PATH):
        return []
    return [json.loads(l) for l in io.open(CASES_PATH, encoding="utf-8") if l.strip()]


def load_paradigms() -> list:
    if not os.path.exists(PARADIGMS_PATH):
        return []
    return [json.loads(l) for l in io.open(PARADIGMS_PATH, encoding="utf-8") if l.strip()]


def paradigm_candidates(min_n: int = PARADIGM_MIN) -> list:
    """按 paradigm_hint 分组数案例，≥min_n 的 hint 即范式候选。
    返回 [(hint, 案例数, 案例id列表)]，已 frozen 的 hint 不再报。"""
    frozen = {p["hint"] for p in load_paradigms() if p["status"] == "frozen"}
    cases = load_cases()
    groups = {}
    for c in cases:
        h = c.get("paradigm_hint")
        if h and h not in frozen:
            groups.setdefault(h, []).append(c["case_id"])
    return sorted(((h, len(ids), ids) for h, ids in groups.items() if len(ids) >= min_n),
                  key=lambda x: -x[1])


def freeze_paradigm(hint: str, name: str, action_template: str,
                    preconditions: str, case_ids: list) -> dict:
    """用户终裁后固化范式；回填案例的 derived_paradigm。"""
    p = {
        "paradigm_id": "pd-" + uuid.uuid4().hex[:8],
        "hint": hint,
        "name": name,
        "action_template": action_template,
        "preconditions": preconditions,
        "evidence_chain": case_ids,
        "status": "frozen",
        "frozen_at": _now(),
    }
    with io.open(PARADIGMS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
    # 回填案例
    cases = load_cases()
    for c in cases:
        if c["case_id"] in case_ids and not c.get("derived_paradigm"):
            c["derived_paradigm"] = p["paradigm_id"]
    with io.open(CASES_PATH, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return p


# 案例类型分类学（开放枚举，新类型随手加）
TYPE_TAXONOMY = {
    "calibration":       "外部权威校准（外部清单/口径推翻内部账）",
    "engine_fix":        "引擎修复（公式/门槛/隔离逻辑 bug 修）",
    "data_enrichment":   "数据增肥（占位卡按交底书/语料充实）",
    "corpus_archaeology": "语料考古（在库语料的未挖内容提取）",
    "dedup_merge":       "去重合并（同件两份记录清理）",
    "adjudication":      "冲突裁决（用户逐对/批量终裁落账）",
    "threshold_recal":   "阈值重标（换基座/换公式后的全量重标）",
    "quarantine_recover": "隔离区捞回（误拒条目恢复）",
}