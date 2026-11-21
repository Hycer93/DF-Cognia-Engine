# -*- coding: utf-8 -*-
"""corpus_registry — 语料资产登记表: 权限/可见性契约 + 版本锚

用户裁决(2026-11-11)四缺口之1:
- registry 不只是索引, 是契约: sim_ok(可否做相似判定)/distill_ok(可否蒸馏入库)/readonly/expiry
- 版本锚: content_hash 记录, 语料改动后晨报校验告警(卡片指针还在但底层证据文本已变→判定漂移)
- fragile 语料(短瞬会话)不参与高权重冲突判定

用法:
  register(path, ...)        新语料落盘时必登记(挂E5前置)
  load_registry()            读全部
  verify_hashes() -> list    校验版本漂移, 返回漂移条目(晨报E段调用)
  refresh_hash(corpus_id)    语料合法更新后刷新锚+记prev
"""
import hashlib
import io
import json
import os
import time

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "memory", "corpus_registry.jsonl")

def _now():
    return time.strftime("%Y-%m-%d %H:%M")

def content_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]

# db 语料的行级聚合锚(2026-11-16 漂移根修):
# 两语料(novel_szt/patent)注册时 path 指向 corpus_store.db 整库文件, 但库内
# E9 每晚写 session_dialog → 整库 hash 天然漂移, 与"语料内容版本"无关。
# 修法: db 类语料锚改为「kind 前缀行集的 (id, content_hash) 聚合」——语料行
# 不动则锚稳定, 库里其他表/其他 kind 的写入不再造成假漂移。
def db_corpus_hash(path, kind_prefix):
    import sqlite3
    con = sqlite3.connect(path, timeout=5)
    try:
        rows = con.execute(
            "SELECT id, content_hash FROM raw_corpus WHERE kind LIKE ? ORDER BY id",
            (kind_prefix + "%",)).fetchall()
    finally:
        con.close()
    h = hashlib.sha256()
    for rid, rhash in rows:
        h.update(str(rid).encode("utf-8"))
        h.update((rhash or "").encode("utf-8"))
    return "%s[%d]" % (h.hexdigest()[:16], len(rows))   # 行数嵌入锚, 增删行也暴露

def _effective_hash(rec):
    """取条目当前锚: db 行级(声明 kind_anchor)优先, 否则整文件。"""
    p = rec["path"] if os.path.isabs(rec["path"]) else \
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rec["path"])
    if rec.get("kind_anchor") and p.endswith((".db", ".sqlite", ".sqlite3")):
        return db_corpus_hash(p, rec["kind_anchor"])
    return content_hash(p)

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return []
    out = []
    with io.open(REGISTRY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def register(path, corpus_id, content_type, time_span,
             sim_ok=True, distill_ok=True, readonly=True, expiry=None,
             fragile=False, note="", anchor="", registered_ts=None):
    """新语料登记。挂 E5 前置: 语料落盘→register→再走提取/建卡。"""
    p = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)) \
        if not os.path.isabs(path) else path
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    rec = dict(corpus_id=corpus_id, path=path,
               chars=len(io.open(p, encoding="utf-8", errors="ignore").read()),
               content_type=content_type, time_span=time_span,
               sim_ok=sim_ok, distill_ok=distill_ok, readonly=readonly, expiry=expiry,
               fragile=fragile, note=note, anchor=anchor,
               content_hash=content_hash(p), hash_algo="sha256[:16]",
               registered_ts=registered_ts or _now())
    with io.open(REGISTRY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

def verify_hashes():
    """晨报调用: 返回漂移条目。语料变了但锚没刷 = 证据文本已变,卡片判定可能漂移。
    db 类语料走 kind_anchor 行级聚合(库内其他 kind 写入不触发假漂移)。"""
    drifted = []
    for r in load_registry():
        p = r["path"] if os.path.isabs(r["path"]) else \
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), r["path"])
        if not os.path.exists(p):
            drifted.append(dict(corpus_id=r["corpus_id"], path=r["path"], status="missing"))
            continue
        cur = _effective_hash(r)
        if cur != r["content_hash"]:
            drifted.append(dict(corpus_id=r["corpus_id"], path=r["path"], status="hash_changed",
                                anchored=r["content_hash"], current=cur,
                                hint="证据文本已变: 指向此语料的卡片判定需复检, 或属合法更新应 refresh_hash"))
    return drifted

def refresh_hash(corpus_id):
    """语料合法更新后刷新锚, prev_hashes 留审计痕迹。
    db 类语料(kind_anchor 声明)刷行级聚合锚。"""
    recs = load_registry()
    rec = next((r for r in recs if r["corpus_id"] == corpus_id), None)
    if rec is None:
        raise KeyError(corpus_id)
    new = _effective_hash(rec)
    rec.setdefault("prev_hashes", []).append(rec["content_hash"])
    rec["content_hash"] = new
    rec["hash_refreshed_ts"] = _now()
    recs = [r for r in recs if r["corpus_id"] != corpus_id] + [rec]
    with io.open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rec

def eligible_for_sim():
    """做相似判定前过滤: 只允许 sim_ok 的语料产物参与。"""
    return {r["corpus_id"] for r in load_registry() if r.get("sim_ok")}

if __name__ == "__main__":
    for d in verify_hashes():
        print(json.dumps(d, ensure_ascii=False))
    print("total:", len(load_registry()), "| sim_ok:", len(eligible_for_sim()))