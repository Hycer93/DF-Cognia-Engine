# -*- coding: utf-8 -*-
"""
ref_retrieval.py — P-C4 参考素材检索 → P-retrieval-upgrade BM25 升级(2026-11-18 段二①E组合)

定位: 长材料(>REF_INLINE_THRESHOLD 8000字符, P-C6 段二裁决)消费侧片段检索——不塞全文进
  每轮 prompt, 只拉取与当前子问题相关的片段。素材本体永远完整保留在
  ThinkState.ref_material, 检索只是 prompt 输入层面裁剪, 原始数据不丢失。

P-retrieval-upgrade 契约(段二五项裁决):
  ① BM25 打分(k1=1.5,b=0.75, DF域=单文档段集合实时统计, 无持久索引, 无第三方依赖)
     + 仅前邻段扩展(标题在上/正文在下, 严格规避步骤序列下一段噪声; 拒绝双向扩展);
     词元体系沿用 2-gram中文+英文原词(_tokens/_segments 零改动)。
  ② bm25_scores 为同源打分函数——检索选段与 sufficiency 判据(retrieval_dependent/
     material_unmapped)共用同一把尺; wl_addressed 不属检索链路维持 _tokens 交集。
  ⑤ 降级契约不变: BM25计算异常/空输入统一返回 [](与旧 2-gram 基线行为完全对齐);
     邻段扩展仅字符串拼接无异常面。

风险锚(段三用户裁决, 写入代码注释):
  - 短碎标题段 IDF 偏低分数易下沉, 依靠前邻扩展兜底; R11 为该病灶专属哨兵用例;
  - 同源切换后 retrieval_dependent 判定门槛收紧——E12 N1 告警链若 FAIL,
    优先定位 sufficiency 打分阈值, 不要直接改动检索主逻辑。
"""
from __future__ import annotations
import math
from collections import Counter

# BM25 参数(业界标准初值, 段四校准域)
BM25_K1 = 1.5
BM25_B = 0.75


def _tokens(text: str) -> set:
    """中文按 2-gram, 英文/数字按词。最简分词, 无第三方依赖。(P-C4 原版, 零改动)"""
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
    pairs = set()
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if ("\u4e00" <= a <= "\u9fff") and ("\u4e00" <= b <= "\u9fff"):
            pairs.add(a + b)
    return out | pairs


def _token_bag(text: str) -> Counter:
    """与 _tokens 同逻辑的词袋版(保留词频, 供 BM25 tf 项)。
    set 语义 tf 恒为 1, Counter 承载"段落重复强调同词"信号(如'帧格式'×5)。"""
    c = Counter()
    buf = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                c[buf.lower()] += 1
                buf = ""
            c[ch] += 1
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                c[buf.lower()] += 1
                buf = ""
    if buf:
        c[buf.lower()] += 1
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if ("\u4e00" <= a <= "\u9fff") and ("\u4e00" <= b <= "\u9fff"):
            c[a + b] += 1
    return c


def _segments(material: str) -> list:
    """空行优先切段; 无空行时按单换行切; 仍无则整段。(P-C4 原版, 零改动)"""
    paras = [p.strip() for p in material.split("\n\n") if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in material.split("\n") if p.strip()]
    return paras


def bm25_scores(query: str, segments: list, min_token_len: int = 1) -> list:
    """同源打分函数(段二②裁决): 检索选段与 sufficiency 判据共用同一把尺。
    返回 [(score, idx), ...] 仅 score>0 项, 分数降序同分按原文序。
    异常/空输入返 []。DF 域=segments 单文档实时统计, 无持久索引。
    词元体系与旧 2-gram 完全一致(2-gram 中文+英文原词)。
    min_token_len: 判据侧调用传 2 过滤单字词元(无判别力, S5实证"题"单字误命中);
      检索主链默认 1 不过滤(段召回不受影响)。"""
    try:
        if not query or not segments:
            return []
        qb = _token_bag(query)
        q_toks = [t for t in qb.keys() if len(t) >= min_token_len]
        if not q_toks:
            return []
        seg_bags = [_token_bag(s) for s in segments]
        N = len(segments)
        lens = [len(s) for s in segments]
        avgdl = (sum(lens) / N) if N > 0 else 1.0
        # DF: 仅统计 query 词元(单文档检索, 全词元 DF 无收益徒增计算)
        df = {}
        for bag in seg_bags:
            for t in q_toks:
                if t in bag:
                    df[t] = df.get(t, 0) + 1
        scored = []
        for idx, bag in enumerate(seg_bags):
            dl = lens[idx]
            norm = BM25_K1 * (1 - BM25_B + BM25_B * (dl / avgdl if avgdl > 0 else 0))
            score = 0.0
            for t in q_toks:
                tf = bag.get(t)
                if not tf:
                    continue
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * (tf * (BM25_K1 + 1)) / (tf + norm)
            if score > 0:
                scored.append((score, idx))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored
    except Exception:
        return []


def retrieve(query: str, material: str, top_k: int = 3) -> list:
    """BM25 检索 top_k 段 + 仅前邻扩展 + 前邻去重(段二①裁决)。
    契约不变(段二⑤): 任何异常返回 [](永不 raise, 与旧基线行为完全对齐)。
    top_k=3 硬编码保留(段二③: 段四 8295 字真实样本对拍 3/5/8 实测定值, R14-a)。"""
    try:
        if not query or not material or not isinstance(material, str):
            return []
        segs = _segments(material)
        if not segs:
            return []
        ranked = bm25_scores(query, segs)
        if not ranked:
            return []
        selected = [idx for _, idx in ranked[:max(1, int(top_k))]]
        used_prev = set()
        out = []
        for sid in selected:
            curr = segs[sid]
            pid = sid - 1
            if pid >= 0 and pid not in used_prev:
                out.append(f"{segs[pid]}\n…(上文)\n{curr}")
                used_prev.add(pid)
            else:
                out.append(curr)
        return out
    except Exception:
        return []
