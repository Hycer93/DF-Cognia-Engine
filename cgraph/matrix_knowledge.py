# -*- coding: utf-8 -*-
"""P-matrix-feed T2: 外部领域知识矩阵装载与注入(2026-11-18 段二契约)。

契约红线(代码级):
1. state.matrix_knowledge 独立字段, 绝不混入 ref_material;
   sufficiency 同源判据只消费 ref_material, 零读取本模块产出。
2. 仅注入 skill_f_reason / skill_weakest_link; 其余技能(含 sufficiency)零注入。
3. 所有异常/超限/零命中 → 静默降级恒 [], 业务链路行为与改造前完全一致,
   只在 trail 埋观测标记(matrix_injected / matrix: degraded(reason)), 不抛业务告警。
4. verified=false 条目不注入(蒸馏素材只入 jsonl 挂 false, 人工核对翻 true 才生效)。
5. token 硬上限 MATRIX_TOKEN_CAP=1200, 超限从尾部截断;
   token 估算 len//4, 与任务素材 token 分账(matrix_tokens 单独计数)。

风险锚(段三确认写入):
- scan_summary 为空(scope_scan 降级/失败场景)时, 以 goal_txt 单独兜底输入,
  不传空字符串进 BM25。
"""
import io
import json
import os

MATRIX_TOKEN_CAP = 1200
_MATRIX_PATH = os.path.join("data", "matrix", "patent_prior_art.jsonl")
_MATRIX_HEADER = "---matrix_knowledge(外部领域参考, 非任务素材)---"


def _load_entries() -> list:
    """读 jsonl; 任何异常(文件缺失/格式坏/空)恒 []。"""
    try:
        out = []
        with io.open(_MATRIX_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if isinstance(e, dict) and e.get("matrix_id") and e.get("verified") is True:
                    out.append(e)
        return out
    except Exception:
        return []


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _entry_text(e: dict) -> str:
    """条目注入文本(供 token 估算与 block 渲染共用, 保证口径一致)。"""
    return "[%s] %s: %s (风险: %s)" % (
        e.get("matrix_id", "?"), e.get("tech_name", ""),
        e.get("mapping_note", ""), e.get("audit_risk", ""))


def load_matrix(goal: str, scan_summary: str = "",
                token_cap: int = MATRIX_TOKEN_CAP) -> list:
    """知识条目匹配 → token 截断。

    匹配口径(2026-11-18 段三裁决): 小知识库(≤10条)用 trigger_kw 词元交集计数,
    规避小规模集合 BM25-IDF 失效(每词 DF≤5 无区分度); 对外签名保持不变,
    扩充到 15-20 条时内部切换为真实 bm25_scores, 上层零改动(兼容设计)。

    返回注入条目列表; 读取失败/零命中/超限全降级 → [] 或截断尾部。
    不抛异常、不告警, 降级理由由调用方埋 trail。
    """
    # 风险锚: scan_summary 空时以 goal 单独兜底, 不进空串
    query = (goal or "").strip()
    if not query and not (scan_summary or "").strip():
        return []
    entries = _load_entries()
    if not entries:
        return []
    try:
        from cgraph.ref_retrieval import bm25_scores, _tokens
        # trigger_kw 展开成词元后与 query 词元对齐(同 BM25 词元切分口径);
        # 段四重验发现的链路缺陷修复: 单字词元过滤(min len 2)——
        # "四川泡菜"与"四级记忆"会经单字"四"假命中(P-retrieval-upgrade S5 同款),
        # 交集只认 len>=2 词元
        q_tokens = {t for t in (_tokens(query) | _tokens(scan_summary or ""))
                    if len(t) >= 2}
        if not q_tokens:
            return []
        scored = []
        for i, e in enumerate(entries):
            kw_tokens = set()
            for kw in e.get("trigger_kw", []):
                kw_tokens |= {t for t in _tokens(kw) if len(t) >= 2}
            if not kw_tokens:
                continue
            s = sum(1 for t in kw_tokens & q_tokens)  # 交集计数(词条粒度足够小集合)
            if s > 0:
                scored.append((s, i, e))
        scored.sort(key=lambda x: (-x[0], x[1]))
    except Exception:
        return []
    out = []
    used = 0
    for _, _, e in scored:
        cost = _estimate_tokens(_entry_text(e))
        if used + cost > token_cap:
            continue  # 超预算: 该条丢弃(小集合下 continue 保后续更小条目机会)
        out.append(e)
        used += cost
    return out


def matrix_tokens(entries: list) -> int:
    """matrix 注入 token 单独计数(trail 分账用)。"""
    return sum(_estimate_tokens(_entry_text(e)) for e in entries or [])


def matrix_block(state, skill) -> str:
    """注入块组装: 与 _ref_material_block 并列但物理隔离(不同分隔符头)。

    state.matrix_knowledge 为空 → 恒 ''(prompt 与改造前逐字节一致, R15c 回归锚)。
    每条强制携带 [source=matrix_id] 溯源标记。
    """
    try:
        entries = getattr(state, "matrix_knowledge", None) or []
        if not entries:
            return ""
        lines = [_MATRIX_HEADER]
        for e in entries:
            lines.append("%s [source=%s]" % (_entry_text(e), e.get("matrix_id", "?")))
        return "\n".join(lines) + "\n"
    except Exception:
        return ""