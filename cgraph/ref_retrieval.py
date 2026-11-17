# -*- coding: utf-8 -*-
"""
ref_retrieval.py — P-C4 参考素材简易检索(2026-11-17 段二2.4 契约定稿)

定位: 长材料(>REF_INLINE_THRESHOLD 2000字符)消费侧片段检索——不塞全文进
  每轮 prompt, 只拉取与当前子问题相关的片段。素材本体永远完整保留在
  ThinkState.ref_material, 检索只是 prompt 输入层面裁剪, 原始数据不丢失。

契约:
  retrieve(query, material, top_k=3) -> list[str]
  实现: 空行/换行切段落 → 中文 2-gram + 英文词交集重叠计分 → top_k 段落。
  不引入第三方库(BM25/向量检索属 planned P-retrieval-upgrade, 先跑 2-gram
  基线拿质量数据)。

降级(段三3.3): 任何内部异常一律返回空列表, 禁止向上抛异常——技能拿到空
  片段时行为等价于 ref_material="", 照常继续执行任务。检索质量差/召回不足
  不属异常降级域, 归后续迭代。
"""
from __future__ import annotations


def _tokens(text: str) -> set:
    """中文按 2-gram, 英文/数字按词。最简分词, 无第三方依赖。"""
    out = set()
    buf = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                out.add(buf.lower())
                buf = ""
            out.add(ch)
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                out.add(buf.lower())
                buf = ""
    if buf:
        out.add(buf.lower())
    # 2-gram: 相邻字符对(单字粒度太碎, 大字集无判别力)
    grams = set()
    toks = sorted(out)
    s = text
    pairs = set()
    for i in range(len(s) - 1):
        a, b = s[i], s[i + 1]
        if ("\u4e00" <= a <= "\u9fff") and ("\u4e00" <= b <= "\u9fff"):
            pairs.add(a + b)
    return out | pairs


def _segments(material: str) -> list:
    """空行优先切段; 无空行时按单换行切; 仍无则整段。段过短(无 2-gram 意义)
    的合并策略不引入——保持最简, top_k 已天然聚合。"""
    paras = [p.strip() for p in material.split("\n\n") if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in material.split("\n") if p.strip()]
    return paras


def retrieve(query: str, material: str, top_k: int = 3) -> list:
    """重叠计分检索 top_k 段落。任何异常返回 [](永不 raise, 段三3.3)。"""
    try:
        if not query or not material or not isinstance(material, str):
            return []
        qt = _tokens(query)
        if not qt:
            return []
        segs = _segments(material)
        if not segs:
            return []
        scored = []
        for i, seg in enumerate(segs):
            st = _tokens(seg)
            score = len(qt & st)
            if score > 0:
                scored.append((score, i, seg))
        if not scored:
            return []
        scored.sort(key=lambda x: (-x[0], x[1]))    # 分数降序, 同分按原文顺序(不做优先级排序=裁决倾向)
        return [seg for _, _, seg in scored[:max(1, int(top_k))]]
    except Exception:
        return []