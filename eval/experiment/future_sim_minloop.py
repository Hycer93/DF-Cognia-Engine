# -*- coding: utf-8 -*-
"""
future_sim_minloop.py — F系公式最小闭环冒烟 (F00→F10→F40+正演校验)
2026-11-15 用户批准七条细规约 · 真值集考题=切换全链路(topic_memory_ops 簇 9 卡)

主干:
  F00 现状锚定: DFCP检索 + 图谱邻接 + 置信分级 → 一次性冻结快照(全程不刷新, 序列化落日志)
  F10 分支发散: K=3, 权重 (1+δ)^β, δ∈[-1,1](平行程度因子), 低权分支按阈值裁剪
  F40 逆向回溯: 从目标G反向走库内真实边——
      边优先级: relation显式边(augment/equivalent)>topic_links弱时序(单独标记降权)
      Ω(x)可行域: ①转移必须在库存在(禁凭空造边) ②逆向单步禁跨memory_class强冲突
  正演校验: 三独立指标不合并——正向可达(布尔)/路径相交(布尔)/步数偏差(数值)
      可达=false 直接判失败, 不语言圆场

规约遵循:
  只读: 不写 memory_card/topic/范式, 唯一落盘 logs/future_sim_smoke.md + snapshot json
  参数全配置化: CONFIG 块, 日志头部记录全套参数供消融
  报告明示: 真值集冒烟=可行性预实验, 非泛化能力验证(r108 保密: 本文件不外发)
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "memory"))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
OUT_MD = os.path.join(_HERE, "logs", "future_sim_smoke.md")
OUT_SNAP = os.path.join(_HERE, "logs", "future_sim_snapshot.json")

# ---- 配置块(全参数集中, 消融实验改这里) ----
CONFIG = {
    "K": 3,                    # F10 分支数(冒烟固定3)
    "beta": 1.0,               # (1+δ)^β 的 β(v2.1 软约束: 推荐基准不锁死)
    "branch_prune_w": 0.3,     # F10 低权裁剪阈值(权重<(1+δ)^β 低于此值丢弃)
    "topic_edge_w": 0.5,      # F40 topic_links 弱时序边降权系数
    "relation_edge_w": 1.0,   # F40 relation 显式边权重
    "max_steps": 12,          # 逆向/正向最大步数(防无界游走)
    "confidence_tier": {"L0": 1.0, "L1": 0.7, "L2": 0.4},  # F00 置信分级
    # 用户终审裁决(2026-11-15 13:30):
    # ① 高敏簇开关: F40/F10 推演默认过滤, 仅显式置 True 才纳入推演图(隔绝情感/人格记录)
    # ② topic 环内横穿裁剪: 仅作用于 topic_links 弱边; 显式 relation 演化边
    #    (superseded_by 等)是强可信边, 同簇也永不裁剪——代码层卡死, 不做成可配开关
    "sensitive_topics": ["topic_user_relation"],
    # topic 环内横穿裁剪: 仅对大簇(>prune_cluster_size 卡)的 topic 弱边生效;
    # 小簇横穿边信息量正常, 保留全互联。显式 relation 演化边永不被裁剪(用户终审约束)
    "prune_cluster_size": 25,
    # 考题(真值集): 切换全链路簇(17卡, 已补挂8张失挂卡, topic边连通已修复)
    "anchor_query": "记忆入库断档事故",          # F00 锚定检索(链头 10764d3b)
    "goal_prefix": "a65a500c",                   # 目标G(F系实现待办, 链尾)
    "truth_topic": "topic_memory_ops",          # 真值链所在簇
    # F30 正向演化(design_F30_fwd_evolution.md 2026-11-24): 叠加层, 默认开; 回滚=置 False
    "enable_f30": True,          # F20 排序后调用 F30 推进 top-K 分支到终态
    "f30_enable_llm_epilog": False,  # LLM 终态叙述开关(默认关, 关闭结构化结果完整)
}


def _ts():
    return datetime.now().isoformat(timespec="seconds")


def _short(iid):
    return (iid or "")[:8]


# ---------- F00 现状锚定(一次性冻结) ----------

def f00_anchor():
    """DFCP检索(链头锚) + 图谱邻接 + 置信分级 → 静态快照。
    快照冻结规约: 本函数只在仿真开始调一次, 产物序列化落盘, 全程不复检。"""
    import memory_query as mq
    hits = mq.ask(CONFIG["anchor_query"], top_k=8)
    anchor_cards = [{"inst_id": h[1]["inst_id"], "summary": h[1]["summary"][:80],
                     "score": round(h[0], 4),
                     "confidence": h[1].get("confidence", "L1"),
                     "memory_class": (h[1].get("dfcp_tag") or {}).get("memory_class"),
                     "entity_id": h[1].get("entity_id")}
                    for h in hits]
    # 图谱邻接: anchor 卡的 relation 邻居
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    anchor_ids = [c["inst_id"] for c in anchor_cards]
    neighbors = {}
    qmarks = ",".join("?" * len(anchor_ids))
    for me, other, rel in con.execute(
            f"SELECT me_inst, other_inst, relation FROM core_relation WHERE status='active' "
            f"AND me_inst IN ({qmarks})", anchor_ids):
        neighbors.setdefault(me, []).append({"other": other, "rel": rel})
    con.close()
    # 置信分级投影
    for c in anchor_cards:
        c["conf_weight"] = CONFIG["confidence_tier"].get(c["confidence"], 0.5)
        c["graph_degree"] = len(neighbors.get(c["inst_id"], []))
    snapshot = {
        "ts": _ts(), "anchor_query": CONFIG["anchor_query"],
        "anchor_cards": anchor_cards,
        "anchor_state_features": {
            "n_cards": len(anchor_cards),
            "n_with_graph_neighbor": sum(1 for c in anchor_cards if c["graph_degree"] > 0),
            "avg_conf_weight": round(sum(c["conf_weight"] for c in anchor_cards) /
                                     max(1, len(anchor_cards)), 3),
            "memory_classes": sorted({str(c["memory_class"]) for c in anchor_cards}),
        },
    }
    return snapshot


# ---------- F10 分支发散 ----------

def f10_diverge(snapshot):
    """从 F00 锚卡出发, 沿库内真实边发散 K 条分支, (1+δ)^β 赋权。
    δ = 分支与锚主题的偏离度(平行程度因子口径):
      锚卡簇内邻居(同topic/relation相连) → δ≈0 贴主线;
      relation 'new' 边连出簇外 → δ 按 memory_class 差异放大;
      'conflict' 边 → δ 取负(反向/矛盾分支)。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    anchor_ids = [c["inst_id"] for c in snapshot["anchor_cards"]]
    qmarks = ",".join("?" * len(anchor_ids))
    # 锚卡归属主题集
    topic_of = {}
    for iid, tid in con.execute(
            f"SELECT inst_id, topic_id FROM topic_links WHERE inst_id IN ({qmarks})", anchor_ids):
        topic_of[iid] = tid
    # 候选转移 = 锚卡的 relation 邻居 + 同 topic 簇内卡
    branches = []
    anchor_mcs = {c["memory_class"] for c in snapshot["anchor_cards"]}
    anchor_topics = set(topic_of.values())
    for me, other, rel in con.execute(
            f"SELECT me_inst, other_inst, relation FROM core_relation WHERE status='active' "
            f"AND me_inst IN ({qmarks})", anchor_ids):
        omc, osum = con.execute(
            "SELECT (SELECT dfcp_tag FROM core_memory_card WHERE inst_id=?), summary FROM core_memory_card WHERE inst_id=?",
            (other, other)).fetchone() or (None, None)
        omc = json.loads(omc or "{}").get("memory_class")
        other_topics = [t for (t,) in con.execute(
            "SELECT topic_id FROM topic_links WHERE inst_id=?", (other,))]
        # δ 计算
        if rel == "conflict":
            delta = -0.8                                   # 反向/矛盾分支
        elif anchor_topics & set(other_topics):
            delta = 0.05 * len(anchor_topics & set(other_topics))  # 簇内贴主线
        else:
            delta = 0.6 if (omc and omc not in anchor_mcs) else 0.3  # 簇外偏离
        w = (1 + delta) ** CONFIG["beta"]
        branches.append({"from": me, "to": other, "rel": rel, "delta": round(delta, 3),
                         "weight": round(w, 4), "to_memory_class": omc,
                         "to_summary": (osum or "")[:50]})
    # 同 topic 簇内转移(弱时序, 直接邻居)
    for tid in anchor_topics:
        for (iid,) in con.execute("SELECT inst_id FROM topic_links WHERE topic_id=?", (tid,)):
            if iid in anchor_ids:
                continue
            s = con.execute("SELECT summary FROM core_memory_card WHERE inst_id=?", (iid,)).fetchone()
            branches.append({"from": None, "to": iid, "rel": "topic_sibling",
                             "delta": 0.1, "weight": round((1 + 0.1) ** CONFIG["beta"], 4),
                             "to_summary": (s[0] if s else "")[:50]})
    con.close()
    # 裁剪: 低权丢弃 + 去重 + 取 K 条最高权
    branches = [b for b in branches if b["weight"] >= CONFIG["branch_prune_w"]]
    seen, uniq = set(), []
    for b in sorted(branches, key=lambda x: -x["weight"]):
        k = (b["from"], b["to"], b["rel"])
        if k not in seen:
            seen.add(k)
            uniq.append(b)
    return uniq[:CONFIG["K"]], len(uniq), uniq


# ---------- F40 逆向回溯 ----------

def _edges_from(con, iid, in_topic_jump=False):
    """iid 的库内真实转移边(双向: 出边+入边)。返回 [(other, rel, prio, w)]
    边优先级: relation显式演化/关联边=1.0 > topic_links弱时序=0.5

    用户终审硬约束(2026-11-15, 不做成可配开关):
    ① 高敏簇默认过滤: sensitive_topics 中的簇(topic_user_relation)不产生任何推演边
    ② topic 环内横穿裁剪(仅 topic 弱边): 同簇横穿边默认裁剪, 只保留"入簇第一跳"。
       显式 relation 演化边(superseded_by 等)是强可信边, 同簇也永不裁剪。
       in_topic_jump=True 表示本节点是刚经 topic 边入簇的——此时同簇出边合法(入簇跳),
       下一跳起恢复裁剪, 防止 12800 边级大簇全互联拖垮 BFS。
    """
    out = []
    # relation 显式边: 强可信, 永不过滤(含高敏簇成员——由簇过滤在 BFS 层单独拦)
    for other, rel in con.execute(
            "SELECT other_inst, relation FROM core_relation WHERE status='active' AND me_inst=?", (iid,)):
        out.append((other, f"rel:{rel}", CONFIG["relation_edge_w"], rel))
    for (other,) in con.execute(
            "SELECT me_inst FROM core_relation WHERE status='active' AND other_inst=?", (iid,)):
        out.append((other, "rel:back", CONFIG["relation_edge_w"], "back"))
    # topic 弱边: 高敏簇过滤 + 大簇环内横穿裁剪
    for (tid,) in con.execute("SELECT topic_id FROM topic_links WHERE inst_id=?", (iid,)):
        if tid in CONFIG["sensitive_topics"]:
            continue  # 高敏簇: 推演默认隔离
        same = [x for (x,) in con.execute(
            "SELECT inst_id FROM topic_links WHERE topic_id=?", (tid,))]
        # 大簇才裁剪: 仅保留"刚入簇节点的一次簇内出边"(入簇首跳), 否则横穿边全免
        # 小簇(≤prune_cluster_size)保留全互联——横穿边本身承载真实时序语义
        big = len(same) > CONFIG["prune_cluster_size"]
        if big and not in_topic_jump:
            continue  # 大簇横穿裁剪: 有 relation 边就不动用弱边(逃生口逻辑并入)
        for other in same:
            if other != iid:
                out.append((other, "topic:weak_ts", CONFIG["topic_edge_w"], "topic"))
    return out


def _mc_of(con, iid):
    r = con.execute("SELECT dfcp_tag FROM core_memory_card WHERE inst_id=?", (iid,)).fetchone()
    return json.loads(r[0] or "{}").get("memory_class") if r else None


def f40_backward(goal_prefix, anchor_ids):
    """从 G 反向回溯到 F00 锚集。Ω(x): ①只走库内真实边 ②禁跨 memory_class 强冲突
    (无显式演化边时 scar_case↔strategy_evolve 类跨跳一律拒绝)。
    返回 (路径[inst_id 序列, G在前], 边类型记录) 或 (None, 失败原因)。
    BFS 等权搜索(优先扩展 relation 边), 找到任一锚卡即停。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    goal = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                       (goal_prefix + "%",)).fetchone()
    if not goal:
        con.close()
        return None, f"目标 {goal_prefix} 不在库"
    goal = goal[0]
    anchor_set = set(anchor_ids)
    from collections import deque
    # 标准 BFS: 全局 visited + parent 回溯(避免 per-path visited 集合复制导致分支爆炸)
    # 队列: (当前卡, 边类型); parent 链重建路径
    dq = deque([(goal, None)])
    parent = {goal: (None, None)}
    best_fail = "无路径"
    depth = {goal: 0}
    while dq:
        cur, _e = dq.popleft()
        if depth[cur] >= CONFIG["max_steps"]:
            continue
        if cur in anchor_set and cur != goal:
            # 回溯重建
            path, edges, node = [], [], cur
            while node is not None:
                path.append(node)
                node, et = parent[node]
                if et:
                    edges.append(f"{_short(path[-1]) if False else ''}{etype_lbl(et)}")
            con.close()
            return list(reversed(path)), list(reversed(edges))
        # in_topic_jump: 上一跳是 topic 弱边入簇 -> 本节点允许一次簇内 topic 出边(入簇首跳)
        in_jump = (_e or "").startswith("topic:")
        for other, etype, w, _raw in _edges_from(con, cur, in_topic_jump=in_jump):
            if other in parent:
                continue
            # 高敏簇成员: 即使经 relation 边到达也拒绝(推演默认隔离, 用户终审裁决)
            other_topics = [t for (t,) in con.execute(
                "SELECT topic_id FROM topic_links WHERE inst_id=?", (other,))]
            if set(other_topics) & set(CONFIG["sensitive_topics"]):
                continue
            omc, cmc = _mc_of(con, other), _mc_of(con, cur)
            same_topic = con.execute(
                "SELECT COUNT(*) FROM topic_links a JOIN topic_links b ON a.topic_id=b.topic_id "
                "WHERE a.inst_id=? AND b.inst_id=?", (other, cur)).fetchone()[0] > 0
            if omc and cmc and omc != cmc and etype.startswith("topic:") and not same_topic:
                continue  # Ω(x): topic 弱边禁跨类
            parent[other] = (cur, etype)
            depth[other] = depth[cur] + 1
            dq.append((other, etype))
            best_fail = "未及锚"
    con.close()
    return None, best_fail + f"(>max_steps={CONFIG['max_steps']} 或边耗尽)"


def etype_lbl(t):
    return t


# ---------- 正演校验 ----------

def verify(path, branches, truth_seq):
    """三独立指标: 正向可达 / 路径相交 / 步数偏差。可达=false 直接判失败。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    # ① 正向可达: 路径反转后, 每步相邻转移是否库内真实存在
    fwd = list(reversed(path))
    unreachable = []
    for a, b in zip(fwd, fwd[1:]):
        ok = con.execute(
            "SELECT COUNT(*) FROM core_relation WHERE (me_inst=? AND other_inst=?) "
            "OR (me_inst=? AND other_inst=?)", (a, b, b, a)).fetchone()[0] > 0
        ok = ok or con.execute(
            "SELECT COUNT(*) FROM topic_links x JOIN topic_links y ON x.topic_id=y.topic_id "
            "WHERE x.inst_id=? AND y.inst_id=?", (a, b)).fetchone()[0] > 0
        if not ok:
            unreachable.append((_short(a), _short(b)))
    con.close()
    forward_reachable = not unreachable
    # ② 路径相交: 逆向路径与 F10 分支目标有交集
    path_set = set(path)
    intersect = any(b["to"] in path_set for b in branches if b["to"])
    # ③ 步数偏差: |逆向步数 - 真值步数|
    n_back = len(path) - 1
    n_truth = len(truth_seq) - 1
    return {"forward_reachable": forward_reachable, "unreachable_edges": unreachable,
            "path_intersect_with_F10": intersect,
            "steps_backward": n_back, "steps_truth": n_truth,
            "step_deviation": n_back - n_truth,
            "verdict": "PASS" if (forward_reachable and intersect) else "FAIL"}


def main():
    # CLI 覆盖: --anchor QUERY --goal PREFIX --out FILE(冒烟基线不传即用 CONFIG)
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--anchor":
            CONFIG["anchor_query"] = args[i+1]; i += 2
        elif args[i] == "--goal":
            CONFIG["goal_prefix"] = args[i+1]; i += 2
        elif args[i] == "--out":
            global OUT_MD
            OUT_MD = os.path.join(_HERE, "logs", args[i+1]); i += 2
        elif args[i] == "--profile":
            # β 档位(纪律②: 命名档位, 只认 f20.BETA_PROFILES 三档; baseline=回归锚点)
            pv = args[i+1]
            import sys as _s, os as _o
            _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
            import f20_weight as _f20
            if pv not in _f20.BETA_PROFILES:
                print("非法档位: %s (合法: %s)" % (pv, "/".join(_f20.BETA_PROFILES)))
                return 2
            CONFIG["beta_profile"] = pv; i += 2
        else:
            i += 1
    # F00(全程一次, 冻结)
    snapshot = f00_anchor()
    with io.open(OUT_SNAP, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    # F10(锚定 top-1 中心卡, 防锚集混入近目标卡导致 F40 假收敛)
    branches, n_avail, all_branches = f10_diverge({**snapshot, "anchor_cards": snapshot["anchor_cards"][:1]})
    # 真值链(考题已知答案): 锚→G 的库内最短路径(BFS 双边), 非整簇时序
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    anchor0 = snapshot["anchor_cards"][0]["inst_id"]
    goal_full = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                            (CONFIG["goal_prefix"] + "%",)).fetchone()[0]
    from collections import deque as _dq
    dq = _dq([(anchor0, [anchor0])]); seen = {anchor0}; truth = None
    while dq and not truth:
        cur, p = dq.popleft()
        for o, _et, _w, _raw in _edges_from(con, cur):
            if o in seen: continue
            if o == goal_full:
                truth = p + [o]; break
            seen.add(o); dq.append((o, p + [o]))
    truth = truth or [anchor0, goal_full]
    con.close()
    # F20 接线(纪律④: 叠加层不改发散主干, 回滚=删本块):
    # 对 all_branches 复合权重重排, top-K 用 F20 排序替代均匀 w; 原分支数据保留
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 同目录 f20_weight
    import f20_weight as f20
    _profile = CONFIG.get("beta_profile", f20.BASELINE_PROFILE)   # 纪律①: 默认 baseline
    _con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    _scored = []
    for b in all_branches:
        w2, fac = f20.score_branch(_con, b, step=1, profile=_profile)
        _scored.append({**b, "w_uniform": b["weight"], "weight": w2, "f20_factors": fac})
    _con.close()
    # 次级键 inst_id: 权重并列(如同簇同因子兄弟卡)时定序确定化,
    # 否则 DB 行序漂移会让守卫的"位次稳定"校验在同权并列处抖动
    _scored.sort(key=lambda x: (-x["weight"], x["to"] or ""))
    branches = _scored[:CONFIG["K"]]
    all_branches = _scored
    # F30 正向演化(叠加层; CONFIG.enable_f30=False 回滚): F20 排序后的 top-K 分支
    # 各自独立推进到终态 S_T^(k)。纯确定性图推进, LLM 只做可选终态叙述(默认关)。
    f30_result = None
    if CONFIG.get("enable_f30", True):
        import f30_forward as f30
        _con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        _con.row_factory = sqlite3.Row
        try:
            f30_result = f30.f30_forward(
                branches, profile=_profile, max_steps=CONFIG["max_steps"],
                prune_w=CONFIG["branch_prune_w"], con=_con,
                enable_llm_epilog=CONFIG.get("f30_enable_llm_epilog", False))
        finally:
            _con.close()
    # F40
    path, note = f40_backward(CONFIG["goal_prefix"], [c["inst_id"] for c in snapshot["anchor_cards"][:1]])
    # 纪律③: 失败归因四枚举(与权重并行)——失败时必打标签, 成功链也记录归因=空
    failure_attr = None
    if path is None:
        _con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        _con.row_factory = sqlite3.Row
        fa_code, fa_note = f20.attribute_failure(
            _con, CONFIG["goal_prefix"],
            [c["inst_id"] for c in snapshot["anchor_cards"][:1]], CONFIG["max_steps"])
        _con.close()
        failure_attr = {"code": fa_code, "note": fa_note}
    # 校验 + 报告(相交口径: 对照裁剪后存活的全部 F10 分支, top-K 仅作发散展示)
    md = ["# F系最小闭环冒烟报告", f"生成: {_ts()}",
          "性质声明: 真值集冒烟=可行性预实验, 非泛化能力验证(对全新无预置边场景的逆向推演能力不在本考题范围)", "",
          "## 参数(CONFIG 消融基线)", *[f"- {k}: {v}" for k, v in CONFIG.items()], "",
          "## F00 冻结快照", f"- 快照已序列化: {os.path.basename(OUT_SNAP)}(全程不复检)",
          f"- 锚定: {snapshot['anchor_state_features']}", ""]
    md.append("## F10 分支发散")
    md.append(f"- F20 权重层: profile={_profile} (叠加排序, 均匀权重保留于 w_uniform)")
    md.append(f"- 可用分支 {n_avail} 条, F20 排序后取 top-{CONFIG['K']}:")
    for b in branches:
        fac = b.get("f20_factors", {})
        md.append(f"  - {_short(b['from']) if b['from'] else '(锚簇)'} → {_short(b['to'])} "
                  f"[{b['rel']}] w={b['weight']} (旧均匀={b.get('w_uniform')}) "
                  f"C={fac.get('C_conf')} F={fac.get('F_fresh')} D={fac.get('D_density')} "
                  f"| {b['to_summary'][:40]}")
    md.append("")
    # F30 正向演化报告段
    if f30_result is not None:
        md.append("## F30 正向演化(确定性图推进)")
        _fm = f30_result["f30_meta"]
        md.append(f"- profile={_fm['profile']} max_steps={_fm['max_steps']} "
                  f"prune_w={_fm['prune_w']} LLM叙述={'开' if _fm['enable_llm_epilog'] else '关'}")
        md.append(f"- 到达T {f30_result['n_reached_T']} / 冻结 {f30_result['n_frozen']} "
                  f"/ 淘汰 {f30_result['n_discarded']} / 死亡 {f30_result['n_dead']}"
                  + (f" | 汇聚点 {[_short(x) for x in f30_result['convergence_points']]}"
                     if f30_result['convergence_points'] else ""))
        for br in f30_result["branches"]:
            md.append(f"  - 分支#{br['k']} 起点{_short(br['start'])} → 终态{_short((br['S_T'] or {}).get('inst_id'))} "
                      f"state={br['state']}({br['stop_reason']}) {br['n_steps']}步 "
                      f"路径: " + " → ".join(_short(p['to']) for p in br['path']))
            if br.get("llm_epilog"):
                md.append(f"      [LLM情景] {str(br['llm_epilog'])[:80]}")
        md.append("")
    md.append("## F40 逆向回溯")
    if path is None:
        md.append(f"- **失败**: {note}")
        # 纪律③: 失败归因四枚举入报告与审计
        if failure_attr:
            md.append(f"- **归因**: `{failure_attr['code']}` — {failure_attr['note']}")
        md.append("- 判定: 本次逆向求解失败(可达性未达成, 不做语言解释)")
        verdict = "FAIL"
    else:
        md.append(f"- 路径({len(path)}卡, G→锚): " + " → ".join(_short(i) for i in path))
        for e in reversed(path) and []:
            pass
        v = verify(path, all_branches, truth)
        md.append("## 正演校验(三独立指标)")
        md.append(f"- 正向可达: **{v['forward_reachable']}**" +
                  (f" 断边: {v['unreachable_edges']}" if v["unreachable_edges"] else ""))
        md.append(f"- 与F10路径相交: **{v['path_intersect_with_F10']}**")
        md.append(f"- 步数: 逆向 {v['steps_backward']} vs 真值 {v['steps_truth']} "
                  f"(偏差 {v['step_deviation']:+d})")
        verdict = v["verdict"]
    md.append("")
    md.append(f"## 总判定: {verdict}")
    if failure_attr:
        md.append(f"失败归因: {failure_attr['code']} (审计元信息已同步 snapshot json)")
    # 纪律③: 归因与因子写进 snapshot 审计元信息(晨报/置信度修正输入)
    snapshot["f20_meta"] = {"beta_profile": _profile,
                            "failure_attr": failure_attr,
                            "top_k_factors": [b.get("f20_factors") for b in branches]}
    if f30_result is not None:
        snapshot["f30_meta"] = f30_result["f30_meta"]
        snapshot["f30_branches"] = f30_result["branches"]
        snapshot["f30_convergence_points"] = f30_result["convergence_points"]
    with io.open(OUT_SNAP, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    md.append("记忆红线: 本链路只读, 未新增卡/主题/范式; 快照与报告为唯一落盘物")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[F冒烟] {verdict} — {OUT_MD}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())