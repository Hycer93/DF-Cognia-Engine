# -*- coding: utf-8 -*-
"""memory_quality_gate — 入库质量门（用户裁决2026-11-11四缺口之2/3落地）

缺口2 E5前置入库范式分两支:
- 稳定正式文档(交底书/校准单/专利说明): 强制完整链路, detail须有差异化信息+绑定evidence文件
- 会话闪念类: 允许轻量detail, 打 fragile=True; 不参与高权重冲突判定, 级联下钻降采信

缺口3 占位卡复合检测(防误伤极简规约):
- 占位 = 文本相似度高(detail≈summary复读) AND detail无新增独立实体/区分性谓词/溯源证据指针
- 单纯相似度高 → 只打提示(hint), 不直接判占位——固定常量/开关规约天然极简, 不是偷懒

范式入口: ingest_corpus_evidence() —— 语料→registry→evidence→三层卡片一次建齐
(取代11-10的错误做法: 拿清单状态行当detail建43张占位卡)
"""
import io
import os
import re
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import corpus_registry

# 区分性谓词关键词（schema.PREDICATES 同源思路的轻量版——占位检测用, 不做编码校验）
# 命中即说明 detail 含机制级叙述而非 summary 复读
_PREDICATE_MARKERS = ("→", "→", "=", ":", "公式", "阈值", "流程", "步骤", "规则",
                     "分为", "构成", "触发", "条件", "机制", "架构", "判定", "因果")


# 连接性胶水 2-gram：占位复读句的"新增"部分常是语法胶水（"是基于""的协议"），
# 剔除后再算新增占比，防止"summary+一句胶水扩展"骗过 new_ratio
_GLUE_GRAMS = set()
for _w in ("是基于", "了一种", "基于结构", "具体见", "详见", "以及", "通过", "进行",
           "相关", "属于", "的方法", "的方式", "一种", "及系统"):
    for _i in range(len(_w) - 1):
        _GLUE_GRAMS.add(_w[_i:_i + 2])
_GLUE_GRAMS |= {"的协", "于结", "构化", "化指", "是基", "基于", "令的", "的方",
                "方法", "方式", "一及", "及系", "系统", "个的", "了这", "了该"}


def _char_ngrams(s: str, n: int = 2) -> set:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}


def text_similarity(a: str, b: str) -> float:
    """2-gram Dice 系数（与 embedding 无关, 零成本; 占位检测只看文本复读度）。"""
    if not a or not b:
        return 0.0
    ga, gb = _char_ngrams(a), _char_ngrams(b)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


# "见清单/详见/具体见"类无信息指针——detail 出现即视为占位信号(不依赖相似度阈值)。
# 注意"略"必须整词匹配("策略/省略号"里的略不算——P6误拦教训);
# "同上/待补充"要求 detail 短(长detail含TODO可能是正文讨论TODO机制, 不是占位)。
_VOID_POINTER_MARKS = ("具体见", "详见清单", "见清单", "见归元仔清单",
                       "见交底书清单")


def _has_void_pointer(detail: str) -> bool:
    d = detail.strip()
    if any(m in d for m in _VOID_POINTER_MARKS):
        return True
    if len(d) <= 40 and ("待补充" in d or "TODO" in d or d in ("略", "同上")):
        return True
    return False


def _has_new_entities(summary: str, detail: str) -> bool:
    """detail 是否含 summary 没有的实质新信息：
    新 2-gram 剔除语法胶水后占比>30% 才算差异化内容（防"summary+胶水扩展"骗过检测）。"""
    gs, gd = _char_ngrams(summary), _char_ngrams(detail)
    if not gd:
        return False
    if not gs:
        return True
    new = (gd - gs) - _GLUE_GRAMS
    return len(new) / len(gd) > 0.30


def _has_predicate_or_pointer(summary: str, detail: str, source_ref=None) -> bool:
    """区分性谓词或溯源证据指针: detail 出现机制词/符号, 或 detail/source 指向真实文件。

    注意: 极简规约常含 ":"(开关S01: 上限20条)——所以本项只作为"复合判定的否定项之一"，
    单独命中不算充实; 反之占位复读句若含裸 ":" 也不因此免拦(由 _has_new_entities 主导)。"""
    if any(k in detail for k in _PREDICATE_MARKERS):
        return True
    # 溯源指针: source_ref 或 detail 内含真实文件路径(含扩展名)
    for t in (source_ref, detail):
        if t and re.search(r"[\w\-/\\]+\.(txt|md|jsonl|py)\b", t):
            return True
    return False


def detect_placeholder(summary: str, detail: str, source_ref=None) -> dict:
    """缺口3复合占位检测。返回 {is_placeholder, similarity, hint}。

    判定规则(用户裁决原文):
      文本相似度高 AND detail 没有新增独立实体/区分性谓词/溯源证据指针
      → 两条件同时满足才判占位。
      单纯相似度高 → 只打提示, 不判占位(防误伤固定常量/开关规约类极简记忆)。
    """
    detail = (detail or "").strip()
    summary = (summary or "").strip()
    if not detail:
        # 空 detail 不在本门职责(空×空假信号已由 _cascade_sem 的 d_a and d_b 短路防御);
        # 这里报 hint 供 E4 巡逻归类
        return {"is_placeholder": False, "similarity": 0.0,
                "hint": "empty_detail"}
    sim = text_similarity(summary, detail)
    # 无信息指针直接拦(detail 主体是"见XX"而不含实际内容——11-10 占位卡真实形态之一)
    if _has_void_pointer(detail):
        return {"is_placeholder": True, "similarity": round(sim, 3),
                "hint": "detail含无信息指针(具体见/见清单/待补充类)"}
    high_sim = sim >= 0.60
    has_new = _has_new_entities(summary, detail)

    # 极简规约豁免(边界补充): summary 是完整命题或量词型规约时,
    # detail 与之相同 = "规约即全部信息"(如"每日产出上限20条"), 不是偷懒。
    # 豁免口径: ①含谓词性标点的完整句(≥10字); ②量词型规约(数字+量词字, 如"20条")。
    # 标题型短语(实体名/编号, 如"P24专利CIPP协议")不豁免——编号数字≠量词。
    _has_quant = bool(re.search(r"\d+[条个次字天小时%倍张份环节月年]", summary))
    is_full_proposition = (
        (len(summary) >= 10 and any(p in summary for p in (":", "：", "，", "。", "=", "→")))
        or _has_quant)
    if high_sim and not has_new and is_full_proposition:
        return {"is_placeholder": False, "similarity": round(sim, 3),
                "hint": "极简规约豁免: summary为完整命题/量词型规约, detail=规约全文"}

    # 复合判定(用户裁决): 相似高 AND 无新增独立实体 AND 无区分性谓词/溯源指针
    # 三条件同时成立才判占位。谓词项仅在"无新增实体"时复核(裸":"等弱谓词不单独救卡)
    if high_sim and not has_new:
        if not _has_predicate_or_pointer(summary, detail, source_ref):
            return {"is_placeholder": True, "similarity": round(sim, 3),
                    "hint": "detail≈summary复读且无新增实体/谓词/指针"}
        return {"is_placeholder": False, "similarity": round(sim, 3),
                "hint": "高相似+强谓词/指针——疑似极简规约, 不判占位"}
    if high_sim:
        return {"is_placeholder": False, "similarity": round(sim, 3),
                "hint": "高相似但有差异化内容——疑似极简规约, 不判占位"}
    return {"is_placeholder": False, "similarity": round(sim, 3), "hint": ""}


def classify_source(content_type: str) -> dict:
    """缺口2分支: 按语料内容类型定入库约束。

    稳定正式文档(stable): 交底书/校准单/专利说明/进化史/矩阵口径快照之外的正式件
      → 强制完整链路: detail须充实(≥120字)且绑定evidence文件
    会话闪念类(ephemeral): 会话碎片/临时想法/口头讨论
      → 允许轻量detail, 但 fragile=True
    """
    ct = content_type or ""
    stable_marks = ("交底书", "专利", "校准", "正式文档", "文档·", "规约", "契约")
    if any(m in ct for m in stable_marks):
        return {"class": "stable", "fragile": False,
                "require_detail_chars": 120, "require_evidence": True}
    return {"class": "ephemeral", "fragile": True,
            "require_detail_chars": 0, "require_evidence": False}


def ingest_corpus_evidence(corpus_id: str, extract_fn=None, **store_kwargs) -> dict:
    """范式入口: 语料 → registry核验 → 提取evidence → 三层卡片一次建齐。

    extract_fn: 可调用(text, rec) -> [(summary, detail, tags), ...]
      稳定类: 每条须含充实detail+source_ref指向evidence文件
      闪念类: detail 可轻量, 自动打 fragile
    store_kwargs: 透传 atomic_store(classify_and_link 由调用方或E5补挂)。
    返回 {class, fragile, items, placeholders, skipped_sim_not_ok}。
    本函数不直接写 memories——由调用方拿 items 走 atomic_store,
    保证账本写入仍走唯一入口(不旁路 quarantine/replica 护栏)。
    """
    rec = next((r for r in corpus_registry.load_registry()
                if r["corpus_id"] == corpus_id), None)
    if rec is None:
        return {"error": f"corpus_id 未登记: {corpus_id}——先 register()"}
    if not rec.get("distill_ok"):
        return {"error": f"{corpus_id} 契约禁止蒸馏入库(distill_ok=False, "
                         f"用途={rec.get('note', '')[:60]})"}
    if not rec.get("sim_ok"):
        return {"error": f"{corpus_id} 契约禁止相似判定(sim_ok=False)"}

    rule = classify_source(rec.get("content_type", ""))
    path = rec["path"] if os.path.isabs(rec["path"]) else \
        os.path.join(os.path.dirname(_HERE), rec["path"])
    text = io.open(path, encoding="utf-8", errors="ignore").read()

    if extract_fn is None:
        return {"error": "extract_fn 必传——提取是模型能力活, 范式只约束产物结构"}

    items, placeholders = [], []
    for summary, detail, tags in extract_fn(text, rec):
        ph = detect_placeholder(summary, detail, tags.get("source_ref"))
        if ph["is_placeholder"]:
            placeholders.append(summary[:40])
            continue  # 占位卡不带病入库(11-10的43张占位卡教训)
        entry = dict(summary=summary, detail=detail, tags=tags,
                     fragile=rule["fragile"], source_class=rule["class"])
        if rule["class"] == "stable":
            if len((detail or "").strip()) < rule["require_detail_chars"]:
                placeholders.append(summary[:40] + "(stable但detail<120字)")
                continue
        items.append(entry)
    return {"class": rule["class"], "fragile": rule["fragile"],
            "items": items, "placeholders": placeholders}


if __name__ == "__main__":
    # 自检: 极简规约不误伤 + 占位卡必拦
    ok_rule = detect_placeholder("开关S01: 每日产出上限20条", "开关S01: 每日产出上限20条")
    ph = detect_placeholder("P24专利CIPP协议",
                            "P24专利CIPP协议是基于结构化指令的协议")
    rich = detect_placeholder("P39三公式", "C(x)=Σw·x / T(·)为拓扑映射 / W(t)动态权重, "
                             "全文见 data/patent_evidence/xxx.txt")
    print("极简规约(应不误伤):", ok_rule)
    print("占位复读(应拦截):", ph)
    print("充实卡(应通过):", rich)