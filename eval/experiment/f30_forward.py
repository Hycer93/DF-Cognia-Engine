# -*- coding: utf-8 -*-
"""
f30_forward.py — F30 推演层·正向演化 (design_F30_fwd_evolution.md 2026-11-24 定稿)

架构(方案C 混合模式):
  内层  纯确定性图推进: S_t -> S_{t+1} 全由图谱真实边 + F20 score_branch 贪心决定,
        LLM 零参与任何一步状态转移。
  外层  LLM 终态叙述(可开关 enable_llm_epilog): 仅对 reached_T/frozen 分支生成附属文本,
        不回流 path/state/w, F50 聚合显式忽略该字段; 关闭后结构化结果字节级等价。

复用契约(与 F40 边规则 100% 对齐):
  - 边枚举复用 future_sim_minloop._edges_from (高敏簇过滤/大簇横穿裁剪/双向边/Ω(x) 全一致)
  - 每步 f20_weight.score_branch 打分, 完整 factors 写入 trace 审计(含 F_fresh_source)
  - 输入 F10 top-K 分支, 输出 S_T^(k) 原生终态喂 F50; F30 不做聚合/不 softmax/不选基线

红线: 只读库, 不写卡/不挂簇/不造库外虚拟节点, 所有跳转落 core_memory_card 已有实体。

状态机五态: active / discarded / frozen / reached_T / dead
  终止区分(风险点1 守卫):
    - 当前卡无任何合法邻居(排 Ω(x)/高敏/跨类后为空) → discarded / exhausted   (真死路)
    - 有合法邻居但全已 visited                     → frozen   / local_max   (局部极大)
    - 有未访邻居但权重全 < prune_w                 → frozen   / local_max   (收敛/低质)
    - step >= max_steps                            → frozen   / step_limit
    - 命中预定义 T 卡                              → reached_T / reached_T
    - 读取异常                                     → dead     / error
"""
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "eval", "experiment"))
sys.path.insert(0, os.path.join(_HERE, "memory"))

DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")

# 惰性导入（避免模块加载期就拉起重依赖；测试注入 fake 时不需要真模块）
def _default_edges_fn(con, iid, in_topic_jump):
    from future_sim_minloop import _edges_from
    return _edges_from(con, iid, in_topic_jump=in_topic_jump)


def _default_mc_fn(con, iid):
    from future_sim_minloop import _mc_of
    return _mc_of(con, iid)


def _default_topics_fn(con, iid):
    return {t for (t,) in con.execute(
        "SELECT topic_id FROM topic_links WHERE inst_id=?", (iid,))}


def _default_score_fn(con, branch, step, profile):
    import f20_weight
    return f20_weight.score_branch(con, branch, step=step, profile=profile)


def _default_card_fn(con, iid):
    # raw 叶子节点(阶段1): 不在 core_memory_card, 从 raw_corpus 取标题作 summary 展示
    from future_sim_minloop import is_raw_node
    if is_raw_node(iid):
        r = con.execute("SELECT id, title FROM raw_corpus WHERE id=?", (iid,)).fetchone()
        if not r:
            return None
        return {"inst_id": r[0], "summary": (r[1] or "")[:80],
                "memory_class": None, "confidence": None, "is_raw": True}
    r = con.execute(
        "SELECT inst_id, summary, confidence, dfcp_tag FROM core_memory_card WHERE inst_id=?",
        (iid,)).fetchone()
    if not r:
        return None  # 卡不存在(悬空边目标): 返回 None, 调用方据此判定非法跳转
    mc = None
    try:
        mc = json.loads(r[3] or "{}").get("memory_class")
        if mc is None:
            mc = r[3]
    except Exception:
        mc = None
    return {"inst_id": r[0], "summary": r[1], "memory_class": mc, "confidence": r[2]}


def _default_exists_fn(con, iid):
    """跳转落点是否真实存在。摘要卡查 core_memory_card; raw 叶子节点查 raw_corpus
    (阶段1: raw 叶子边目标不在摘要表, 但合法存在于原文表)。"""
    from future_sim_minloop import is_raw_node
    if is_raw_node(iid):
        return con.execute(
            "SELECT 1 FROM raw_corpus WHERE id=? LIMIT 1", (iid,)).fetchone() is not None
    return con.execute(
        "SELECT 1 FROM core_memory_card WHERE inst_id=? LIMIT 1", (iid,)).fetchone() is not None


def _sensitive_topics():
    from future_sim_minloop import CONFIG
    return set(CONFIG.get("sensitive_topics") or [])


class FwdEvolver:
    """单分支正向状态机。K 分支各持一个独立实例, visited 互不干扰。"""

    def __init__(self, con, branch, profile="baseline", max_steps=12, prune_w=0.3,
                 t_card=None, enable_llm_epilog=False,
                 edges_fn=None, score_fn=None, mc_fn=None, topics_fn=None,
                 card_fn=None, llm_fn=None, sensitive=None, exists_fn=None):
        self.con = con
        self.branch = branch
        self.start = branch.get("to")
        self.profile = profile
        self.max_steps = max_steps
        self.prune_w = prune_w
        self.t_card = t_card
        self.enable_llm_epilog = enable_llm_epilog
        self.edges_fn = edges_fn or _default_edges_fn
        self.score_fn = score_fn or _default_score_fn
        self.mc_fn = mc_fn or _default_mc_fn
        self.topics_fn = topics_fn or _default_topics_fn
        self.card_fn = card_fn or _default_card_fn
        self.exists_fn = exists_fn or _default_exists_fn
        self.llm_fn = llm_fn
        self.sensitive = sensitive if sensitive is not None else _sensitive_topics()

        # 运行态
        self.path = [{"from": branch.get("from"), "to": self.start, "rel": branch.get("rel"),
                      "w": branch.get("weight"), "factors": branch.get("f20_factors"),
                      "step": 0}]
        self.visited = {self.start}
        self.cur = self.start
        self.step = 0
        self.prev_etype = None
        self.state = "active"
        self.stop_reason = None

    # ---- 候选计算：所有合法邻居(Ω(x)/高敏/跨类 过滤后)，但**不**排除 visited ----
    def _neighbors(self, cur, step, prev_etype):
        in_jump = bool((prev_etype or "").startswith("topic:"))
        try:
            raw = self.edges_fn(self.con, cur, in_jump)
        except Exception as e:  # noqa: BLE001
            self._err = repr(e)[:200]
            raise
        cur_topics = self.topics_fn(self.con, cur)
        cur_mc = self.mc_fn(self.con, cur)
        out = []
        for item in raw:
            other, etype = item[0], item[1]
            # 红线: 跳转必须落已有实体——DB 存在悬空边(指向已不存在卡), 跳过目标卡不存在的边
            if not self.exists_fn(self.con, other):
                continue
            other_topics = self.topics_fn(self.con, other)
            # 高敏簇: 所有边(含 relation)一律隔离(与 F40 BFS 层一致)
            if self.sensitive and (other_topics & self.sensitive):
                continue
            omc = self.mc_fn(self.con, other)
            same_topic = bool(other_topics & cur_topics)
            # Ω(x): topic 弱边禁跨 memory_class 强冲突(无显式同簇)
            if omc and cur_mc and omc != cur_mc and etype.startswith("topic:") and not same_topic:
                continue
            try:
                w, factors = self.score_fn(self.con, {"to": other, "rel": etype}, step, self.profile)
            except Exception as e:  # noqa: BLE001
                self._err = repr(e)[:200]
                raise
            out.append({"from": cur, "to": other, "rel": etype,
                        "w": w, "factors": factors, "step": step})
        return out

    def run(self):
        try:
            self._run_loop()
        except Exception as e:  # noqa: BLE001
            self.state = "dead"
            self.stop_reason = "error"
            self.error = getattr(self, "_err", repr(e)[:200])
        return self.state

    def _run_loop(self):
        while True:
            if self.step >= self.max_steps:
                self.state, self.stop_reason = "frozen", "step_limit"
                return
            all_nb = self._neighbors(self.cur, self.step + 1, self.prev_etype)
            if not all_nb:
                self.state, self.stop_reason = "discarded", "exhausted"
                return
            cands = [n for n in all_nb if n["to"] not in self.visited]
            if not cands:
                self.state, self.stop_reason = "frozen", "local_max"
                return
            viable = [c for c in cands if c["w"] >= self.prune_w]
            if not viable:
                self.state, self.stop_reason = "frozen", "local_max"
                return
            # 贪心: 最高权, 同权按 inst_id 字典序(确定序, 跨运行可复现)
            # 注意: 用 sorted((-w, to))[0] 而非 max((w,to))——max 在同权时会选字典序**大**者,
            # 与"字典序小优先"的确定序约定相反(回归 T30-3 抓到的 bug)。
            best = sorted(viable, key=lambda c: (-c["w"], c["to"]))[0]
            self.path.append(best)
            self.visited.add(best["to"])
            self.cur, self.step, self.prev_etype = best["to"], self.step + 1, best["rel"]
            # ★ raw 叶子(阶段1): 落到原文节点即停——raw 是叶子, 不继续扩散,
            #   语义为"已下钻到原文终态"→ frozen/raw_leaf(区别于 step_limit/local_max)。
            from future_sim_minloop import is_raw_node
            if is_raw_node(self.cur):
                self.state, self.stop_reason = "frozen", "raw_leaf"
                return
            if self.t_card and self.cur == self.t_card:
                self.state, self.stop_reason = "reached_T", "reached_T"
                return

    def export(self):
        S_T = None
        if self.cur:
            try:
                S_T = self.card_fn(self.con, self.cur)
            except Exception:  # noqa: BLE001
                S_T = None
            if S_T is None:  # 防御: 理论上 exists_fn 已挡, 兜底避免 S_T=None 崩上层
                S_T = {"inst_id": self.cur, "summary": None,
                       "memory_class": None, "confidence": None}
        llm_epilog = None
        # LLM 终态叙述: 仅在 reached_T/frozen 且开启且提供 llm_fn 时; 异常吞掉不影响结构化结果
        # 隔离: 传 path 的**深拷贝**给 llm_fn, 防桩/模型篡改回流污染 self.path(回归 T30-12 抓到)
        if (self.enable_llm_epilog and self.llm_fn
                and self.state in ("reached_T", "frozen")):
            try:
                _path_snapshot = [dict(p) for p in self.path]
                llm_epilog = self.llm_fn(_path_snapshot)
            except Exception:  # noqa: BLE001
                llm_epilog = None
        rec = {
            # ---- 核心确定性字段(进 F50) ----
            "k": self.branch.get("k"),
            "start": self.start,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "S_T": S_T,
            "path": self.path,
            "f20_weight": self.branch.get("weight"),
            "n_steps": self.step,
            # ---- LLM 附属字段(不进 F50, 可为 None) ----
            "llm_epilog": llm_epilog,
        }
        if self.state == "dead":
            rec["error"] = getattr(self, "error", None)
        return rec


def _detect_convergence(results):
    """跨分支汇聚检测: 出现在 ≥2 条分支 path(排除各自起点)中的卡。
    仅统计信号, 不干预走向。"""
    from collections import Counter
    cnt = Counter()
    for r in results:
        # path[0] 是起点, 排除; 其余为中途/终点节点
        nodes = {p["to"] for p in r["path"][1:]}
        for n in nodes:
            cnt[n] += 1
    return sorted([n for n, c in cnt.items() if c >= 2])


def f30_forward(branches, profile="baseline", max_steps=12, prune_w=0.3,
                enable_llm_epilog=False, con=None, t_card=None, llm_fn=None,
                edges_fn=None, score_fn=None, mc_fn=None, topics_fn=None,
                card_fn=None, sensitive=None, exists_fn=None):
    """F30 入口: 对 K 条分支(各自独立状态机)正向推进, 返回结构化结果 + 汇聚检测。

    con: 只读连接; None 时自开真实库连接(调用方负责关闭须显式传入)。
    测试注入 edges_fn/score_fn/mc_fn/topics_fn/card_fn 即不需真库。
    """
    own_con = False
    if con is None:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        own_con = True
    try:
        results = []
        for idx, b in enumerate(branches):
            bb = dict(b)
            bb.setdefault("k", idx)
            ev = FwdEvolver(con, bb, profile=profile, max_steps=max_steps, prune_w=prune_w,
                            t_card=t_card, enable_llm_epilog=enable_llm_epilog,
                            edges_fn=edges_fn, score_fn=score_fn, mc_fn=mc_fn,
                            topics_fn=topics_fn, card_fn=card_fn, llm_fn=llm_fn,
                            sensitive=sensitive, exists_fn=exists_fn)
            ev.run()
            results.append(ev.export())
        conv = _detect_convergence(results)
        states = [r["state"] for r in results]
        return {
            "f30_meta": {
                "profile": profile, "max_steps": max_steps, "prune_w": prune_w,
                "enable_llm_epilog": bool(enable_llm_epilog),
                "n_branches": len(results),
            },
            "branches": results,
            "convergence_points": conv,
            "n_reached_T": states.count("reached_T"),
            "n_frozen": states.count("frozen"),
            "n_discarded": states.count("discarded"),
            "n_dead": states.count("dead"),
        }
    finally:
        if own_con:
            con.close()