# entity_resolve.py — fragment_assembler 层1 读取逻辑（#49 阶段2，2026-11-24）
#
# 定位：把 core_term_alias 表从「僵数据」变成「活查询」。
#   范围严格限定为「别名归并 + 实体锚查找 + 消歧告警」三件事，
#   不做完整问题拆解/路由/答案组装（那是 #49 阶段3 路线C）。
#
# 对应 sk_fragment_assembler 范式 op_sequence 的 2/4 步：
#   2. 实体锚定（别名表归并）
#   4. 消歧校验（多义名字输出冲突告警，禁强合并）
#
# 设计红线（用户裁决，#49 阶段2 风险提示）：
#   - 只归并、只告警，绝不丢信息：多义时两义都返回，由调用方/用户裁定。
#   - 找不到锚时如实返回空 + 降级提示，不臆造。

import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_HERE, "data", "memory", "corpus_store.db")


def _conn():
    con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _ensure_rows(con):
    """外部传入连接可能未设 row_factory——统一转 Row(与 f20_weight 同惯例)。"""
    if con.row_factory is not sqlite3.Row:
        con.row_factory = sqlite3.Row
    return con


def resolve_alias(term, con=None):
    """别名归并（读 core_term_alias）+ 消歧告警。

    返回 dict:
      term:       原始输入
      canonical:  [规范名,...]（去重；可能多个 = 歧义）
      aliases:    该 term 别名指向的规范名列表（=canonical 原文）
      ambiguous:  True 表示该 term 命中多个不同的 canonical（多义）
      warn:       歧义时的告警文本（None=无歧义）
      found:      是否命中别名表

    语义：term 本身若是某个 canonical（如"归元仔"），也会被反查回来。
          "暮暮" 若既映射到"归元仔"又在别处作为另一实体出现，由层2 实体锚交叉判定；
          本函数只负责别名表内的多义检测。
    """
    own = con is None
    con = con or _conn()
    try:
        con = _ensure_rows(con)
        t = (term or "").strip()
        canon = []
        for r in con.execute(
                "SELECT canonical FROM core_term_alias WHERE alias=? AND status='active'", (t,)):
            c = r["canonical"]
            if c and c != "(pending)" and c not in canon:
                canon.append(c)
        # 反查：term 自身是否是某别名链的 canonical（标准名本身也应被认出）
        is_canon = con.execute(
            "SELECT 1 FROM core_term_alias WHERE canonical=? AND status='active' LIMIT 1", (t,)).fetchone()
        if not canon and is_canon:
            canon = [t]
        ambiguous = len(canon) > 1
        warn = None
        if ambiguous:
            warn = f"'{term}' 命中多个规范实体 {canon}，存在多义，禁止自动合并，需人工裁定"
        return {"term": t, "canonical": canon, "ambiguous": ambiguous,
                "warn": warn, "found": bool(canon)}
    finally:
        if own:
            con.close()


def resolve_entity(term, con=None):
    """实体锚查找（读 core_entity_timeline）+ 别名归一。

    先经 resolve_alias 把 term 归一到 canonical，再在实体表按 entity 名/摘要查。
    返回 dict:
      term / canonical / aliases_hit（是否走了别名）
      entities:  [{entity,event,event_ts,summary}]
      ambiguous: 命中多个实体
      warn:      歧义或未命中提示
      found:     是否找到实体
    """
    own = con is None
    con = con or _conn()
    try:
        con = _ensure_rows(con)
        ra = resolve_alias(term, con=con)
        names = list(ra["canonical"]) if ra["canonical"] else []
        if ra["term"] and ra["term"] not in names:
            names_q = names + [ra["term"]]
        else:
            names_q = names
        ents = []
        seen = set()
        for n in names_q:
            for r in con.execute(
                    "SELECT entity,event,event_ts,summary FROM core_entity_timeline "
                    "WHERE entity=? OR summary LIKE ?", (n, f"%{n}%")):
                key = (r["entity"], r["event"])
                if key in seen:
                    continue
                seen.add(key)
                ents.append({"entity": r["entity"], "event": r["event"],
                             "event_ts": r["event_ts"], "summary": r["summary"]})
        ambiguous = len({e["entity"] for e in ents}) > 1
        warn = None
        if ra["ambiguous"]:
            warn = ra["warn"]
        elif ambiguous:
            warn = f"'{term}' 命中多个实体 {sorted({e['entity'] for e in ents})}，需裁定"
        elif not ents:
            warn = f"'{term}' 无实体锚——降级为纯关键词全文检索（fragment_assembler 第4步降级）"
        return {"term": ra["term"], "canonical": ra["canonical"],
                "aliases_hit": bool(ra["canonical"]), "entities": ents,
                "ambiguous": ambiguous, "warn": warn, "found": bool(ents)}
    finally:
        if own:
            con.close()


if __name__ == "__main__":
    import sys
    term = sys.argv[1] if len(sys.argv) > 1 else "暮暮"
    print(json.dumps(resolve_alias(term), ensure_ascii=False, indent=1))
    print(json.dumps(resolve_entity(term), ensure_ascii=False, indent=1))