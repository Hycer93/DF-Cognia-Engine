# -*- coding: utf-8 -*-
"""
f30_regression.py — F30 正向演化层回归 (T30-1 ~ T30-13)

策略: 全部用注入的假 edges_fn/score_fn/mc_fn/topics_fn/card_fn + 假 con, 不读真库、不调真 LLM。
验证 F30 确定性推演逻辑与契约, mock LLM 桩验证开关等价/不回流/discarded不触发。

运行: python eval/audit/f30_regression.py  (退出码 0=全过 1=有失败)
"""
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "eval", "experiment"))

import f30_forward as F30  # noqa: E402

_RESULTS = []


def _check(name, cond, detail=""):
    _RESULTS.append({"name": name, "ok": bool(cond), "detail": detail})
    print(("  ✅ " if cond else "  ❌ ") + name + (" | " + detail if detail and not cond else ""))


# ---------------- 可复用假件 ----------------
class _Graph:
    """邻接图: edges[a] = [(b, etype), ...]; mc[a]=memory_class; topics[a]={topic}"""
    def __init__(self, edges, mc=None, topics=None, cards=None):
        self.edges = edges
        self.mc = mc or {}
        self.topics = topics or {}
        self.cards = cards or {}

    def edges_fn(self, con, iid, in_topic_jump):
        return [(b, et, 1.0 if et.startswith("rel") else 0.5, et)
                for (b, et) in self.edges.get(iid, [])]

    def mc_fn(self, con, iid):
        return self.mc.get(iid)

    def topics_fn(self, con, iid):
        return set(self.topics.get(iid, set()))

    def card_fn(self, con, iid):
        return self.cards.get(iid, {"inst_id": iid, "summary": "s_" + str(iid),
                                    "memory_class": self.mc.get(iid), "confidence": "L1"})

    def exists_fn(self, con, iid):
        # 测试默认所有提及节点都"存在"; 专门测悬空场景时置 self.missing
        return iid not in getattr(self, "missing", set())


def _score_const(val=0.9):
    def _f(con, branch, step, profile):
        return val, {"C_conf": 1.0, "F_fresh": 0.7, "F_fresh_source": "neutral",
                     "D_density": 1.0, "R_edge": 1.0, "beta": 1.0, "delta_step": 0.6, "step": step}
    return _f


def _score_map(mapping):
    """mapping: {to_inst: w}"""
    def _f(con, branch, step, profile):
        return mapping[branch["to"]], {"step": step, "F_fresh_source": "neutral"}
    return _f


def _run(branch_to, graph, **kw):
    kw.setdefault("sensitive", set())
    return F30.f30_forward([{"from": "A0", "to": branch_to, "rel": "rel:x",
                             "weight": 1.0, "f20_factors": {}}],
                           con=object(), edges_fn=graph.edges_fn, mc_fn=graph.mc_fn,
                           topics_fn=graph.topics_fn, card_fn=graph.card_fn, exists_fn=graph.exists_fn, **kw)


def _one(branch_to, graph, **kw):
    kw.setdefault("sensitive", set())
    r = F30.f30_forward([{"from": "A0", "to": branch_to, "rel": "rel:x",
                          "weight": 1.0, "f20_factors": {}}],
                        con=object(), edges_fn=graph.edges_fn, mc_fn=graph.mc_fn,
                        topics_fn=graph.topics_fn, card_fn=graph.card_fn, exists_fn=graph.exists_fn, **kw)
    return r["branches"][0]


# ---------------- T30-1 单分支推进可达 ----------------
def t30_1():
    print("[T30-1] 单分支推进可达")
    g = _Graph({"S": [("B", "rel:augment")], "B": [("C", "rel:augment")], "C": []})
    rec = _one("S", g, score_fn=_score_const(0.9), max_steps=12)
    _check("T30-1 3步内到 C, stop=local_max(终态无邻居→discarded?)",
           rec["state"] in ("frozen", "discarded") and rec["S_T"]["inst_id"] == "C",
           f"state={rec['state']} S_T={rec['S_T']['inst_id']} path={[p['to'] for p in rec['path']]}")
    _check("T30-1b path 每跳是真实边",
           [p["to"] for p in rec["path"]] == ["S", "B", "C"],
           str([p["to"] for p in rec["path"]]))


# ---------------- T30-2 贪心选最高权 ----------------
def t30_2():
    print("[T30-2] 贪心选最高权")
    g = _Graph({"S": [("H", "rel:a"), ("L", "rel:b")], "H": [], "L": []})
    rec = _one("S", g, score_fn=_score_map({"H": 0.9, "L": 0.4}), max_steps=5)
    _check("T30-2 选中高分 H(非 L)", rec["path"][1]["to"] == "H",
           f"选={rec['path'][1]['to']}")


# ---------------- T30-3 同权并列确定序 ----------------
def t30_3():
    print("[T30-3] 同权并列按 inst_id 字典序")
    g = _Graph({"S": [("zzz", "rel:a"), ("aaa", "rel:b")], "zzz": [], "aaa": []})
    rec = _one("S", g, score_fn=_score_map({"zzz": 0.8, "aaa": 0.8}), max_steps=5)
    _check("T30-3 同权选字典序小 aaa", rec["path"][1]["to"] == "aaa",
           f"选={rec['path'][1]['to']}")
    # 复现性: 连跑两次结果一致
    r2 = _one("S", g, score_fn=_score_map({"zzz": 0.8, "aaa": 0.8}), max_steps=5)
    _check("T30-3b 同权跨运行可复现",
           [p["to"] for p in rec["path"]] == [p["to"] for p in r2["path"]],
           "")


# ---------------- T30-4 防环 ----------------
def t30_4():
    print("[T30-4] visited 防环")
    # S->B->S 环; B 只能回 S(已访问)→ 应 frozen local_max, 不死循环
    g = _Graph({"S": [("B", "rel:a")], "B": [("S", "rel:a")]})
    rec = _one("S", g, score_fn=_score_const(0.9), max_steps=12)
    _check("T30-4 环不死循环, 停在 B",
           rec["state"] == "frozen" and rec["stop_reason"] == "local_max"
           and rec["S_T"]["inst_id"] == "B",
           f"state={rec['state']} reason={rec['stop_reason']} S_T={rec['S_T']['inst_id']}")


# ---------------- T30-5 高敏簇过滤 ----------------
def t30_5():
    print("[T30-5] 高敏簇过滤")
    g = _Graph({"S": [("X", "rel:a"), ("OK", "rel:b")]}, topics={"X": {"topic_user_relation"}})
    rec = _one("S", g, score_fn=_score_map({"X": 1.0, "OK": 0.5}),
               sensitive={"topic_user_relation"}, max_steps=5)
    _check("T30-5 高敏卡 X 被排除, 选 OK", rec["path"][1]["to"] == "OK",
           f"选={rec['path'][1]['to']}")


# ---------------- T30-6 topic 弱边跨类拦截 ----------------
def t30_6():
    print("[T30-6] topic 弱边跨类 Ω(x) 拦截")
    g = _Graph({"S": [("X", "topic:weak_ts"), ("OK", "topic:weak_ts")]},
               mc={"S": "strategy_evolve", "X": "scar_case", "OK": "strategy_evolve"},
               topics={"S": {"t1"}, "X": {"t2"}, "OK": {"t1"}})
    rec = _one("S", g, score_fn=_score_map({"X": 1.0, "OK": 0.5}), max_steps=5)
    _check("T30-6 跨类 X 被拦(不同mc+不同簇), 选同簇 OK",
           rec["path"][1]["to"] == "OK", f"选={rec['path'][1]['to']}")


# ---------------- T30-7 低权剪枝 discarded/frozen ----------------
def t30_7():
    print("[T30-7] 低权剪枝")
    g = _Graph({"S": [("L", "rel:a")], "L": []})
    rec = _one("S", g, score_fn=_score_map({"L": 0.1}), prune_w=0.3, max_steps=5)
    _check("T30-7 全邻居权重<prune_w → frozen local_max, 停在 S",
           rec["state"] == "frozen" and rec["stop_reason"] == "local_max"
           and rec["S_T"]["inst_id"] == "S",
           f"state={rec['state']} reason={rec['stop_reason']} S_T={rec['S_T']['inst_id']}")


# ---------------- T30-8 步数上限 frozen ----------------
def t30_8():
    print("[T30-8] step 上限")
    # 长链 5 卡, max_steps=2 → 走 2 步后 step_limit frozen
    g = _Graph({"S": [("B", "rel:a")], "B": [("C", "rel:a")], "C": [("D", "rel:a")], "D": []})
    rec = _one("S", g, score_fn=_score_const(0.9), max_steps=2)
    _check("T30-8 max_steps=2 → frozen step_limit, S_T=C",
           rec["state"] == "frozen" and rec["stop_reason"] == "step_limit"
           and rec["S_T"]["inst_id"] == "C" and rec["n_steps"] == 2,
           f"state={rec['state']} reason={rec['stop_reason']} S_T={rec['S_T']['inst_id']} n={rec['n_steps']}")


# ---------------- T30-9 K 分支独立 ----------------
def t30_9():
    print("[T30-9] K 分支独立")
    # 两分支 S1->A, S2->B, 互不干扰
    g = _Graph({"S1": [("A", "rel:a")], "S2": [("B", "rel:a")], "A": [], "B": []})
    r = F30.f30_forward(
        [{"from": "A0", "to": "S1", "rel": "rel:x", "weight": 1.0},
         {"from": "A0", "to": "S2", "rel": "rel:x", "weight": 0.9}],
        con=object(), edges_fn=g.edges_fn, mc_fn=g.mc_fn, topics_fn=g.topics_fn,
        card_fn=g.card_fn, exists_fn=g.exists_fn, score_fn=_score_const(0.9), max_steps=5, sensitive=set())
    p0 = [p["to"] for p in r["branches"][0]["path"]]
    p1 = [p["to"] for p in r["branches"][1]["path"]]
    _check("T30-9 两分支各走各路", p0 == ["S1", "A"] and p1 == ["S2", "B"],
           f"p0={p0} p1={p1}")


# ---------------- T30-10 汇聚点检测 ----------------
def t30_10():
    print("[T30-10] 汇聚点检测")
    # 两分支从 S1/S2 都汇到 M(各自经不同前提 X1/X2)
    g = _Graph({"S1": [("X1", "rel:a")], "S2": [("X2", "rel:a")],
                "X1": [("M", "rel:a")], "X2": [("M", "rel:a")], "M": []})
    r = F30.f30_forward(
        [{"from": "A0", "to": "S1", "rel": "rel:x", "weight": 1.0},
         {"from": "A0", "to": "S2", "rel": "rel:x", "weight": 0.9}],
        con=object(), edges_fn=g.edges_fn, mc_fn=g.mc_fn, topics_fn=g.topics_fn,
        card_fn=g.card_fn, exists_fn=g.exists_fn, score_fn=_score_const(0.9), max_steps=5, sensitive=set())
    _check("T30-10 汇聚点含 M", "M" in r["convergence_points"],
           f"conv={r['convergence_points']}")
    _check("T30-10b 起点不算汇聚(各不同)",
           "S1" not in r["convergence_points"] and "S2" not in r["convergence_points"],
           f"conv={r['convergence_points']}")


# ---------------- T30-11 LLM 关闭等价 ----------------
def t30_11():
    print("[T30-11] LLM 关闭等价")
    g = _Graph({"S": [("B", "rel:a")], "B": []})
    calls = {"n": 0}

    def llm_stub(path):
        calls["n"] += 1
        return "情景描述文本"

    rec_off = _one("S", g, score_fn=_score_const(0.9), max_steps=5,
                   enable_llm_epilog=False, llm_fn=llm_stub)
    rec_on = _one("S", g, score_fn=_score_const(0.9), max_steps=5,
                  enable_llm_epilog=True, llm_fn=llm_stub)
    # 核心字段(去掉 llm_epilog 后)应完全一致
    a = {k: v for k, v in rec_off.items() if k != "llm_epilog"}
    b = {k: v for k, v in rec_on.items() if k != "llm_epilog"}
    _check("T30-11 关闭时 llm_epilog=None", rec_off["llm_epilog"] is None, "")
    _check("T30-11b 开LLM核心字段与关闭字节级一致", a == b,
           f"diff={[k for k in a if a[k] != b.get(k)]}")


# ---------------- T30-12 LLM 不回流 ----------------
def t30_12():
    print("[T30-12] LLM 不回流 path/state/w")
    # S->B->S 环: B 有邻居(回 S, 已访问)→ frozen local_max 触发 LLM(到达终态场景)
    g = _Graph({"S": [("B", "rel:a")], "B": [("S", "rel:a")]})

    def llm_stub(path):
        # 恶意桩: 试图篡改传入 path (若 F30 传引用回流则会污染 self.path)
        path.append({"to": "HACK", "rel": "evil", "w": 999})
        return "x"

    rec = _one("S", g, score_fn=_score_const(0.9), max_steps=5,
               enable_llm_epilog=True, llm_fn=llm_stub)
    nodes = [p["to"] for p in rec["path"]]
    _check("T30-12 LLM 篡改 path 不回流(无 HACK) 且确实触发过LLM(frozen)",
           "HACK" not in nodes and rec["state"] == "frozen" and rec["llm_epilog"] == "x",
           f"nodes={nodes} state={rec['state']}")


# ---------------- T30-13 discarded/dead 不触发 LLM ----------------
def t30_13():
    print("[T30-13] discarded 不触发 LLM")
    g = _Graph({"S": []})   # 无邻居 → discarded
    calls = {"n": 0}

    def llm_stub(path):
        calls["n"] += 1
        return "x"

    rec = _one("S", g, score_fn=_score_const(0.9), max_steps=5,
               enable_llm_epilog=True, llm_fn=llm_stub)
    _check("T30-13 discarded 分支 LLM 调用=0",
           rec["state"] == "discarded" and calls["n"] == 0,
           f"state={rec['state']} calls={calls['n']}")


def t30_14():
    print("[T30-14] 悬空边目标(卡不存在)跳过, 不跳转")
    # S 的邻居 GHO 是悬空边(卡不存在); 只有 REAL 有效 → 应跳过 GHO 走 REAL
    g = _Graph({"S": [("GHO", "rel:a"), ("REAL", "rel:b")], "REAL": []})
    g.missing = {"GHO"}   # GHO 不存在于 core_memory_card
    rec = _one("S", g, score_fn=_score_map({"GHO": 1.0, "REAL": 0.5}), max_steps=5)
    _check("T30-14 跳过悬空目标 GHO(即便权重最高), 走 REAL",
           rec["path"][1]["to"] == "REAL" and rec["S_T"]["inst_id"] == "REAL",
           f"path={[p['to'] for p in rec['path']]}")


def main():
    for fn in (t30_1, t30_2, t30_3, t30_4, t30_5, t30_6, t30_7, t30_8,
               t30_9, t30_10, t30_11, t30_12, t30_13, t30_14):
        fn()
    failed = [r for r in _RESULTS if not r["ok"]]
    print("\n[F30 回归] %d/%d 通过" % (len(_RESULTS) - len(failed), len(_RESULTS)))
    if failed:
        for r in failed:
            print("  FAIL:", r["name"], "|", r["detail"])
        sys.exit(1)
    print("F30 正向演化层回归全过")


if __name__ == "__main__":
    main()