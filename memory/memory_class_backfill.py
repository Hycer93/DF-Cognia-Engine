# memory/memory_class_backfill.py — 件②存量回填提案器（2026-11-10 用户终裁）
# 纪律：E5 每晚批量提案 → 写 pending 队列 → judge 确认（review_holds 消化）→ 才写 dfcp_tag。
#       提案器本身永不直写主库。null 是合法提案（不属五类不硬塞——S015 无 other 兜底）。
# 判据：behavior 维先验 + summary 特征词（桩版结构性判断；LLM 判定卡上线后语义化）。

import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MEM = os.path.join(os.path.dirname(HERE), "data", "memory")

# behavior 先验（与 schema 注释同步；仅提案参考非判定）
_PRIOR = {"Dl": "scar_case", "Bc": "scar_case", "Jw": "skill_refine", "Cf": "skill_refine",
          "Ht": "strategy_evolve", "Iv": "key_pact", "Em": "relation_milestone", "Eo": "relation_milestone"}
# strategy_evolve 无专属 behavior 先验（Jw/Ht 与 skill_refine/key_pact 共享），走特征词通道
_FEATURES = {
    "strategy_evolve": ("路线", "范式", "架构切换", "终裁", "定案", "升级为", "重构定案"),
    "skill_refine": ("阈值", "调优", "微调", "优化", "改进"),
    "scar_case": ("事故", "bug", "失效", "误", "踩坑", "错"),
    "relation_milestone": ("实体", "血缘", "关联打通", "链路", "入池"),
    "key_pact": ("规约", "契约", "准入", "边界约束", "口径", "纪律"),
}


def _load():
    with io.open(os.path.join(MEM, "memories.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _pending_path():
    return os.path.join(MEM, "class_backfill_pending.jsonl")


def propose_batch(limit=8):
    """扫无 class 存量（双读：dfcp_tag.memory_class 与顶层都空）→ 提案写 pending 队列。"""
    mems = _load()
    pending_path = _pending_path()
    done_ids = set()
    if os.path.exists(pending_path):
        with io.open(pending_path, encoding="utf-8") as f:
            done_ids = {json.loads(l)["inst_id"] for l in f if l.strip()}
    todo = [m for m in mems
            if not (m.get("dfcp_tag") or {}).get("memory_class")
            and not m.get("memory_class")
            and m["inst_id"] not in done_ids]
    props = []
    for m in todo[:limit]:
        tag = m.get("dfcp_tag") or {}
        beh = tag.get("behavior")
        s = m.get("summary") or ""
        # 特征词优先（内容判断 > 坐标先验），无命中再查 behavior 先验，都无 → null 提案（普通记录）
        prop = next((k for k, ws in _FEATURES.items() if any(w in s for w in ws)), None)
        basis = "feature_words" if prop else None
        if prop is None and beh in _PRIOR:
            prop = _PRIOR[beh]; basis = "behavior_prior"
        if prop is None:
            prop = None; basis = "null_normal_record"  # 普通记录，合法空
        props.append({"inst_id": m["inst_id"], "proposed_class": prop, "basis": basis,
                      "summary": s[:60], "ts": datetime.now().isoformat()})
    if props:
        with io.open(pending_path, "a", encoding="utf-8") as f:
            for p in props:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    total_null = sum(1 for m in todo if True)
    return {"proposed": len(props), "null_proposals": sum(1 for p in props if p["proposed_class"] is None),
            "remaining_after": max(0, len(todo) - len(props)), "remaining_total": len(todo)}


def confirm_and_write(inst_id, mc, reviewer="human"):
    """judge 确认后写 dfcp_tag.memory_class（code_changelog append-only 审计）。
    mc=None 的确认=确认它就是普通记录，写 null 进 pending 完结标记即可不改主记录。"""
    mems = _load()
    changed = False
    for m in mems:
        if m["inst_id"] == inst_id:
            m.setdefault("dfcp_tag", {})["memory_class"] = mc
            changed = True
            break
    if changed:
        with io.open(os.path.join(MEM, "memories.jsonl"), "w", encoding="utf-8") as f:
            for m in mems:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        with io.open(os.path.join(MEM, "code_changelog.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "inst_id": inst_id,
                                "op": "memory_class_backfill", "value": mc,
                                "reviewer": reviewer}, ensure_ascii=False) + "\n")
    return {"ok": changed, "inst_id": inst_id, "written": mc}
