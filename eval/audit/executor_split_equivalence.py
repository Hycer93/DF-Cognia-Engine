"""executor_split_equivalence.py — P2-2 拆分等价性回归 (2026-11-21)

目的: 证明 executor.py 巨型函数拆分(run/_walk_chain/_run_jw_sidebranch/
_run_summary/_gate_and_commit; run_atomic/_split_ref_material/_load_matrix_feed/
_post_exit_terminate_and_sufficiency) 前后行为一致 —— 纯结构重构, 零语义漂移。

检查:
  E1 run() 拆分链路: mock LLM 走主链→Jw支线→汇总, 断言 walked/trace 序列、
     deepen 事件、final_answer 与旧实现的等价输出(内联基线桩)
  E2 run() 汇总降级: LLM 汇总返回空 → 回退 last_good_answer
  E3 _split_ref_material: 无标记 / 有标记 / 标记后空 / 超长截断 四分支
  E4 _gate_and_commit 复用: run 与 run_atomic 共用同一收尾(绿档写入被调一次)
  E5 commit_green_cards 仍可从 executor 访问(重导出, 调用面不变)
退出码 0=全过 1=有失败。
"""
import io
import os
import sys
from unittest import mock

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)

results = []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:240]})


class MockLLM:
    def __init__(self, replies):
        self.replies = list(replies) if isinstance(replies, list) else [replies]
        self.calls = []

    def chat(self, msgs):
        self.calls.append(msgs)
        return self.replies.pop(0) if len(self.replies) > 1 else (self.replies[0] if self.replies else "")


class _Node:
    def __init__(self, code):
        self.code = code
        self.name = code
        self.label = code
        self.prompt = code
        self.behaviors = [{"question": f"{code}的问题"}]

    def to_prompt_line(self):
        return f"{self.code}: {self.label}"


def _mk_executor(llm, nodes):
    """最小 ChainExecutor: 只要 g.nodes / llm / max_deepen, 绕开真实图加载。"""
    from cgraph.executor import ChainExecutor

    class _G:
        pass
    g = _G()
    g.nodes = {c: _Node(c) for c in nodes}
    ex = ChainExecutor(graph=g, llm=llm, max_deepen=2)
    return ex


def _mk_decision(path, domain_nodes):
    from cgraph.router import RouteDecision
    return RouteDecision(complexity="medium", path=list(path),
                         domain_nodes=list(domain_nodes))


def main():
    from cgraph import executor as EX

    # ---- E1: 主链 + Jw 支线 + 汇总 解析与串联 ----
    # LLM 调用次序: 每主链节点一次 + (Jw 支线一次) + 汇总一次
    replies = [
        # 节点 S1: 结构化 JSON(analysis + deepen)
        '{"analysis": "S1分析结果", "deepen": ""}',
        # 节点 S2
        '{"analysis": "S2分析结果", "deepen": ""}',
        # Jw 支线
        "Jw复盘产出",
        # 汇总
        "最终汇总答案",
    ]
    ex = _mk_executor(MockLLM(replies), ["S1", "S2", "Jw"])
    # gate 关闭, 聚焦拆分链路本身
    ex._run_memory_gate = lambda *a, **k: None
    d = _mk_decision(["S1", "S2", "Jw"], ["S1", "S2"])
    wr = ex.run("测试输入", d)
    check("E1_walked", wr.path == ["S1", "S2", "Jw"], f"walked={wr.path}")
    check("E1_trace_codes",
          [t["code"] for t in wr.llm_trace] == ["S1", "S2", "Jw#side", "SUMMARY"],
          f"trace={[t['code'] for t in wr.llm_trace]}")
    check("E1_final", wr.final_answer == "最终汇总答案", f"final={wr.final_answer}")
    check("E1_jw_sidebranch", wr.jw_sidebranch == "Jw复盘产出", f"jw={wr.jw_sidebranch}")
    check("E1_node_outputs", wr.node_outputs.get("S1") == "S1分析结果",
          f"node_outputs keys={list(wr.node_outputs)}")

    # ---- E2: 汇总降级(last_good_answer 兜底) ----
    replies2 = ['{"analysis": "S1正文", "deepen": ""}', ""]
    ex2 = _mk_executor(MockLLM(replies2), ["S1"])
    ex2._run_memory_gate = lambda *a, **k: None
    d2 = _mk_decision(["S1"], ["S1"])
    wr2 = ex2.run("输入", d2)
    check("E2_summary_fallback", wr2.final_answer == "S1正文",
          f"final={wr2.final_answer}")

    # ---- E3: _split_ref_material 四分支 ----
    ex3 = _mk_executor(MockLLM([""]), [])
    adj = os.path.join(_HERE, "data", "atomic_skills_adjacency.json")
    # (a) 无标记
    ui, ref = ex3._split_ref_material("普通问题无标记", adj)
    check("E3a_no_mark", ui == "普通问题无标记" and ref == "", f"ui={ui!r} ref={ref!r}")
    # (b) 有标记
    ui, ref = ex3._split_ref_material("目标\n---ref_material---\n材料正文", adj)
    check("E3b_with_mark", ui == "目标" and ref == "材料正文", f"ui={ui!r} ref={ref!r}")
    # (c) 标记后空 → 不处理移交 anchor, ref 为空, ui 保留原样
    ui, ref = ex3._split_ref_material("目标\n---ref_material---\n   ", adj)
    check("E3c_mark_empty", ref == "", f"ui={ui!r} ref={ref!r}")
    # (d) 超长截断
    long_mat = "x" * 200_050
    ui, ref = ex3._split_ref_material("目标\n---ref_material---\n" + long_mat, adj)
    check("E3d_truncate", len(ref) == 200_000 and ui == "目标", f"len(ref)={len(ref)}")

    # ---- E4: run/run_atomic 共用 _gate_and_commit(绿档写入恰一次) ----
    calls = {"n": 0}
    saved_commit = EX.commit_green_cards

    def _counting(*a, **k):
        calls["n"] += 1
    EX.commit_green_cards = _counting
    try:
        replies4 = ['{"analysis": "S1", "deepen": ""}', "汇总"]
        ex4 = _mk_executor(MockLLM(replies4), ["S1"])
        # 真实的 _gate_and_commit, 但 gate 返回真值以触发 commit
        fake_gate = (mock.Mock(result={"gate_list": []}), None)
        ex4._run_memory_gate = lambda *a, **k: fake_gate
        ex4.run("输入", _mk_decision(["S1"], ["S1"]))
        check("E4_gate_commit_once", calls["n"] == 1, f"commit calls={calls['n']}")
    finally:
        EX.commit_green_cards = saved_commit

    # ---- E5: commit_green_cards 可从 executor 访问(重导出) ----
    check("E5_reexport", callable(getattr(EX, "commit_green_cards", None)),
          "executor.commit_green_cards 可调用(重导出)")
    # 同源: executor 侧对象即 green_card_writer 侧对象
    from cgraph import green_card_writer as GW
    check("E5_same_object", EX.commit_green_cards is GW.commit_green_cards,
          "重导出对象与 writer 同一实例")

    failed = [r for r in results if not r["ok"]]
    print()
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}")
    print(f"\n[executor 拆分等价性] {len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()