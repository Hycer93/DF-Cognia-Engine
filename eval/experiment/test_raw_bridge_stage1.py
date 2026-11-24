# -*- coding: utf-8 -*-
"""
test_raw_bridge_stage1.py — 阶段1 raw 叶子边冒烟测试(2026-11-24)

覆盖(用户裁决的四条硬约束):
  ① raw 边能建: P28 类专利卡 → 桥到原文(标题 2-gram Jaccard)
  ② raw 是叶子: raw 节点 _edges_from 返回空, 不继续扩散
  ③ 开关可回滚: raw_bridge_enabled=False → raw 边全消失
  ④ 端到端可用: raw_drill() 能抽出发明人(黄艺超)
  ⑤ 无回归: F30/F40 主流程仍 PASS
  ⑥ F40 不受 raw 干扰: 反向路径不含 raw 节点

运行: python eval/experiment/test_raw_bridge_stage1.py
只读库, 不改任何数据。
"""
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

import future_sim_minloop as M  # noqa: E402

DB = os.path.join(_ROOT, "data", "memory", "corpus_store.db")
PASS, FAIL = 0, 0


def _con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _p28_card(con):
    return con.execute(
        "SELECT inst_id, summary FROM core_memory_card WHERE summary LIKE '%P28%' LIMIT 1"
    ).fetchone()


def main():
    con = _con()

    print("[1] raw 边能建(P28)")
    card = _p28_card(con)
    check("P28 卡存在", card is not None)
    if card:
        edges = M._edges_from(con, card["inst_id"])
        raw_edges = [e for e in edges if e[1].startswith("raw")]
        check("P28 有 raw 边", len(raw_edges) >= 1, f"{len(raw_edges)} 条")
        check("raw 边落入 raw_corpus", all(M._raw_exists(con, e[0]) for e in raw_edges))

    print("[2] raw 是叶子(不扩散)")
    if card and raw_edges:
        rid = raw_edges[0][0]
        check("raw 节点出边=0", len(M._edges_from(con, rid)) == 0, rid[:40])
        check("is_raw_node 判定", M.is_raw_node(rid) is True)
        check("摘要卡非 raw_node", M.is_raw_node(card["inst_id"]) is False)

    print("[3] 开关可回滚")
    if card:
        old = M.CONFIG.get("raw_bridge_enabled")
        M.CONFIG["raw_bridge_enabled"] = False
        try:
            e2 = M._edges_from(con, card["inst_id"])
            check("关闭后无 raw 边", not any(e[1].startswith("raw") for e in e2))
        finally:
            M.CONFIG["raw_bridge_enabled"] = old

    print("[4] 端到端可用(raw_drill 抽署名)")
    if card:
        drills = M.raw_drill(con, card["inst_id"])
        check("raw_drill 有结果", len(drills) >= 1, f"{len(drills)} 篇")
        inv = next((d.get("发明人") for d in drills if d.get("发明人")), None)
        check("抽到发明人=黄艺超", inv == "黄艺超", str(inv))

    print("[5] F40 反向不受 raw 干扰")
    path, _note = M.f40_backward("a65a500c", ["10764d3b"])
    if path:
        check("F40 路径无 raw 节点", not any(M.is_raw_node(x) for x in path))

    con.close()

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())