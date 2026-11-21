# -*- coding: utf-8 -*-
"""collab_forward_check_regression.py — #8 forward_check 四枚举白盒回归 (2026-11-22)

覆盖 scripts/collab_pipeline.py:forward_check() 的四个输出 verdict:
  PASS / PASS_WITH_GAPS / FAIL / NO_CONSENSUS
外加单路模式退化分支(divergent/actions 计入语料)。

判据(纯内存, 零 DB 读写、零 LLM、零网络):
  1. 各枚举 verdict 判定正确
  2. covered 计数格式 "n/m" 正确
  3. goal_keyword_coverage 逐关键词布尔齐全
  4. 单路模式退化: consensus 为空但 divergent 命中 → 计入覆盖

纪律: 不改动 forward_check 业务逻辑(仅 import 调用); 不碰真实 corpus_store.db。
运行: python eval/audit/collab_forward_check_regression.py [--selftest]
退出码 0=全过 1=有失败。
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "scripts"))
try:                                    # GBK 控制台兜底(本仓历史 emoji 崩溃教训)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import collab_pipeline as cp  # noqa: E402

_R = []


def _check(name, cond, detail=""):
    _R.append({"name": name, "ok": bool(cond)})
    print(("  OK  " if cond else "  FAIL") + " " + name
          + (" | " + detail if detail and not cond else ""))


def _mk_dx(*, consensus=None, shared_weak=None, divergent=None, actions=None,
           consensus_clusters=0, single_branch_mode=False, n=2):
    """构造 forward_check 所需最小合法 dx。"""
    return {
        "dx_candidates": {
            "consensus_hypotheses": consensus or [],
            "shared_weak_points": shared_weak or [],
            "candidate_actions_dedup": actions or [],
            "divergent_hypotheses": divergent or [],
        },
        "vote_stats": {"n_live": n, "hyp_clusters": consensus_clusters,
                       "consensus_clusters": consensus_clusters},
        "single_branch_mode": single_branch_mode,
        "note": "fixture",
    }


def case_pass():
    """全覆盖 → PASS。"""
    print("[1] PASS 全命中")
    dx = _mk_dx(consensus=[{"statement": "缓存击穿导致数据库压力激增"}],
                consensus_clusters=1)
    r = cp.forward_check(dx, ["缓存", "数据库"])
    _check("PASS verdict", r["verdict"] == "PASS", str(r))
    _check("PASS covered 2/2", r["covered"] == "2/2", str(r["covered"]))
    _check("PASS coverage 逐词命中",
           r["goal_keyword_coverage"] == {"缓存": True, "数据库": True},
           str(r["goal_keyword_coverage"]))


def case_pass_with_gaps():
    """部分命中 → PASS_WITH_GAPS。"""
    print("[2] PASS_WITH_GAPS 部分命中")
    dx = _mk_dx(consensus=[{"statement": "缓存击穿导致数据库压力激增"}],
                consensus_clusters=1)
    r = cp.forward_check(dx, ["缓存", "线程池"])
    _check("PASS_WITH_GAPS verdict", r["verdict"] == "PASS_WITH_GAPS", str(r))
    _check("PASS_WITH_GAPS covered 1/2", r["covered"] == "1/2", str(r["covered"]))
    _check("PASS_WITH_GAPS coverage 缓存True/线程池False",
           r["goal_keyword_coverage"] == {"缓存": True, "线程池": False},
           str(r["goal_keyword_coverage"]))


def case_fail():
    """零命中 → FAIL（有共识但关键词全不覆盖）。"""
    print("[3] FAIL 零命中")
    dx = _mk_dx(consensus=[{"statement": "缓存击穿导致数据库压力激增"}],
                consensus_clusters=1)
    r = cp.forward_check(dx, ["内存泄漏", "线程死锁"])
    _check("FAIL verdict", r["verdict"] == "FAIL", str(r))
    _check("FAIL covered 0/2", r["covered"] == "0/2", str(r["covered"]))
    _check("FAIL coverage 全 False",
           r["goal_keyword_coverage"] == {"内存泄漏": False, "线程死锁": False},
           str(r["goal_keyword_coverage"]))


def case_no_consensus():
    """无共识簇且非单路 → NO_CONSENSUS（优先级最高, 即使关键词本可命中）。"""
    print("[4] NO_CONSENSUS 无共识")
    dx = _mk_dx(consensus=[], shared_weak=[], consensus_clusters=0,
                single_branch_mode=False)
    r = cp.forward_check(dx, ["缓存"])   # 关键词无源可命中
    _check("NO_CONSENSUS verdict", r["verdict"] == "NO_CONSENSUS", str(r))


def case_single_branch_divergent():
    """单路模式退化: consensus 为空但 divergent 命中 → 计入覆盖, 不判 NO_CONSENSUS。"""
    print("[5] 单路模式 divergent 计入覆盖")
    dx = _mk_dx(consensus=[], shared_weak=[], consensus_clusters=0,
                divergent=["缓存预热策略缺失导致回源风暴"], single_branch_mode=True)
    r = cp.forward_check(dx, ["缓存"])
    _check("单路: 不判 NO_CONSENSUS", r["verdict"] != "NO_CONSENSUS", str(r))
    _check("单路: divergent 命中→PASS", r["verdict"] == "PASS", str(r))
    _check("单路: covered 1/1", r["covered"] == "1/1", str(r["covered"]))


def case_empty_keywords():
    """空/空白关键词: 全部过滤, hits 空 → covered 0/0; 有共识→FAIL。"""
    print("[6] 空关键词边界")
    dx = _mk_dx(consensus=[{"statement": "缓存击穿"}], consensus_clusters=1)
    r = cp.forward_check(dx, ["", "  "])
    _check("空关键词: covered 0/0", r["covered"] == "0/0", str(r["covered"]))
    _check("空关键词: 有共识但零覆盖→FAIL", r["verdict"] == "FAIL", str(r))


def run_selftest():
    case_pass()
    case_pass_with_gaps()
    case_fail()
    case_no_consensus()
    case_single_branch_divergent()
    case_empty_keywords()
    failed = [r for r in _R if not r["ok"]]
    print("\n[#8 forward_check 回归] %d/%d 通过" % (len(_R) - len(failed), len(_R)))
    out = os.path.join(_HERE, "eval", "audit", "logs", "collab_forward_check_regression.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        io.open(out, "w", encoding="utf-8").write(
            __import__("json").dumps(
                {"total": len(_R), "passed": len(_R) - len(failed), "results": _R},
                ensure_ascii=False, indent=1))
    except Exception:
        pass  # 落盘失败不影响 CI 退出码判定
    return not failed


def main(argv):
    # 本脚本仅内存用例, --selftest 即全量; 不带参数亦跑全量(与 CI 一致)
    return 0 if run_selftest() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))