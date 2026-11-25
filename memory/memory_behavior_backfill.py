# memory/memory_behavior_backfill.py — 核心编码(behavior)存量回填提案器（2026-11-25）
# 纪律（对齐 memory_class_backfill.py 铁律）：E5 每晚批量提案 → 写 pending 队列 →
#       judge 确认（confirm_and_write）→ 才写 dfcp_tag.behavior。
#       提案器本身永不直写主库；code_changelog append-only 审计留痕。
# 触发背景: 历史 15 张旧卡 dfcp_tag 仅 {domain,relation} 缺 behavior 字段(绿卡写入源
#       硬编码遗留, 见 schema.py 注释 + memory_relations.py:75 同批 11-23 补丁)。
#       validate_memory_tags 上线后新卡会拒收隔离, 但存量老卡仍缺码——须回炉补码。
# 判据: summary 类型标签特征词优先（复盘/闭环/实现→Jw 经验进化闭环; 定案/结论→Ht
#       决策整合）; 都无命中 → 提案 null 交人工(不硬塞骨架码, 防污染聚类归属)。

import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MEM = os.path.join(os.path.dirname(HERE), "data", "memory")


def _load():
    with io.open(os.path.join(MEM, "memories.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _pending_path():
    return os.path.join(MEM, "behavior_backfill_pending.jsonl")


# 类型标签 → 骨架码 提案先验（仅提案参考，非判定；confirm 前必经人审）
# 依据 schema.BEHAVIOR_TREE 语义: Jw=G21经验进化闭环, Ht=G22决策整合
# 判定优先级: ①先看 summary 开头的 [类型标签]（最可靠，卡自带分类）
#            ②无标签或标签为宽泛[类型]时，才退回内容关键词（避开"结论充分性"这类技能名误伤）
# 定案产出标签(定稿/定案/终裁) → Ht决策整合; 过程/闭环/实现类 → Jw经验进化闭环
_TAG_TO_BEHAVIOR = {
    "定稿": "Ht", "定案": "Ht", "终裁": "Ht",
    "复盘": "Jw", "实证": "Jw", "实验": "Jw", "对照": "Jw",
    "实战": "Jw", "验收闭环": "Jw", "闭环": "Jw", "实现": "Jw",
}
# 无明确标签时的内容关键词（仅 [类型]/无标签 卡退回使用）
_CONTENT_DECISION_KW = ("定稿", "定案", "确立", "终裁")   # 定案产出 → Ht


def _suggest_behavior(summary: str) -> tuple:
    """返回 (proposed_behavior, basis)。按开头类型标签优先判定；无据 → (None,'manual_review')。"""
    s = (summary or "").strip()
    # ① 解析开头 [标签]（兼容 "[对照+自举]" / "[实战replay]" / "[验收闭环]"）
    tag = ""
    if s.startswith("["):
        end = s.find("]")
        if end > 0:
            tag = s[1:end]
    if tag and tag != "类型":
        for key, beh in _TAG_TO_BEHAVIOR.items():
            if key in tag:
                return beh, f"tag:{key}"
    # ② 无标签 / [类型] 宽标签：退回内容关键词（定案产出→Ht）。注意避开技能名误伤：
    #    pc5"结论充分性校验"是技能名不属定案——故只在无过程类标签时才用内容兜底。
    if tag in ("", "类型"):
        if any(k in s for k in _CONTENT_DECISION_KW):
            return "Ht", "content_keyword"
        return "Jw", "default_process"   # 专利过程记录默认 Jw（人审可改）
    return None, "manual_review"   # 有标签但无法映射：交人工


def propose_batch(limit=8):
    """扫 dfcp_tag 缺 behavior 的存量 → 提案写 pending 队列（永不直写主库）。"""
    mems = _load()
    pending_path = _pending_path()
    done_ids = set()
    if os.path.exists(pending_path):
        with io.open(pending_path, encoding="utf-8") as f:
            done_ids = {json.loads(l)["inst_id"] for l in f if l.strip()}
    todo = [m for m in mems
            if not (m.get("dfcp_tag") or {}).get("behavior")
            and m["inst_id"] not in done_ids]
    props = []
    for m in todo[:limit]:
        beh, basis = _suggest_behavior(m.get("summary") or "")
        props.append({"inst_id": m["inst_id"], "proposed_behavior": beh,
                      "basis": basis, "summary": (m.get("summary") or "")[:60],
                      "ts": datetime.now().isoformat()})
    if props:
        with io.open(pending_path, "a", encoding="utf-8") as f:
            for p in props:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return {"proposed": len(props),
            "manual_review_proposals": sum(1 for p in props if p["proposed_behavior"] is None),
            "remaining_after": max(0, len(todo) - len(props)),
            "remaining_total": len(todo)}


def confirm_and_write(inst_id, beh, reviewer="human"):
    """judge 确认后写 dfcp_tag.behavior（code_changelog append-only 审计）。
    beh=None 的确认=确认它不属任何骨架(维持缺省)，写 null 进 pending 完结标记不改主记录。
    写入前再校验骨架码合法性（validate_behavior），非法码拒绝写库。"""
    if beh is not None:
        import schema
        ok, msg = schema.validate_behavior(beh)
        if not ok:
            return {"ok": False, "inst_id": inst_id, "error": msg}
    mems = _load()
    changed = False
    for m in mems:
        if m["inst_id"] == inst_id:
            if beh is None:
                # 人工裁定维持缺省：不改主记录，仅记审计完结
                changed = True
                break
            m.setdefault("dfcp_tag", {})["behavior"] = beh
            changed = True
            break
    if changed:
        with io.open(os.path.join(MEM, "memories.jsonl"), "w", encoding="utf-8") as f:
            for m in mems:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        with io.open(os.path.join(MEM, "code_changelog.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "inst_id": inst_id,
                                "op": "behavior_backfill", "value": beh,
                                "reviewer": reviewer}, ensure_ascii=False) + "\n")
    return {"ok": changed, "inst_id": inst_id, "written": beh}


if __name__ == "__main__":
    print(json.dumps(propose_batch(limit=50), ensure_ascii=False, indent=2))