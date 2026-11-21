"""retrieval_weight_doc_sync_regression.py — P2-4 文档/常量一致性回归 (2026-11-21)

P2-4 产出检索权重标定实验设计文档 docs/design_retrieval_weight_calibration.md,
其中 §1.2 状态表记录了当前线上权重值。本回归固化"文档记载 == 代码常量",
防止标定改值后忘记同步文档(或文档写着旧值误导后续实验)。

检查:
  1. 文档存在且含关键节(现状盘点/实验设计/迭代路线)
  2. 文档记录的权重值与 constants.py 实际值一致
  3. 文档标注 GATE_HARD_PASS 为已标定 / W_CONFIDENCE 等为先验(状态标注不虚高)
退出码 0=全过 1=有失败。
注: 若未来按文档 §2.4 改了常量, 须同步更新文档 §1.2 与此处的期望值。
"""
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "memory"))
DOC = os.path.join(_HERE, "docs", "design_retrieval_weight_calibration.md")


def main():
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), str(detail)[:220]))

    check("doc_exists", os.path.exists(DOC), "文档不存在")
    doc = io.open(DOC, encoding="utf-8").read() if os.path.exists(DOC) else ""

    check("has_sections",
          all(k in doc for k in ("现状盘点", "实验设计", "迭代路线")),
          "缺少关键章节")

    import constants as C
    # 文档 §1.2 表格中记录的权重值, 与 constants 实际值对照
    # 用正则从文档提取 "<常量名> | <值>" 形式的行
    def doc_val(name):
        m = re.search(rf"\|\s*`{re.escape(name)}`\s*\|\s*([0-9.]+)\s*\|", doc)
        return float(m.group(1)) if m else None

    pairs = [
        ("W_CODE_MATCH", getattr(C, "W_CODE_MATCH", None)),
        ("W_CONFIDENCE", getattr(C, "W_CONFIDENCE", None)),
        ("W_CITED_STATS", getattr(C, "W_CITED_STATS", None)),
        ("W_RELATION_ACTIVATE", getattr(C, "W_RELATION_ACTIVATE", None)),
        ("GATE_HARD_PASS", getattr(C, "GATE_HARD_PASS", None)),
        ("GATE_HARD_ASK", getattr(C, "GATE_HARD_ASK", None)),
        ("BIAS_CAP", getattr(C, "BIAS_CAP", None)),
        ("TOPIC_ROUTE_BOOST", getattr(C, "TOPIC_ROUTE_BOOST", None)),
    ]
    for name, actual in pairs:
        dv = doc_val(name)
        ok = (dv is not None and actual is not None and abs(dv - actual) < 1e-9)
        check(f"doc_sync_{name}", ok, f"文档={dv} 代码={actual}")

    # 状态标注: GATE_HARD_PASS 标为已标定, W_CONFIDENCE 标为先验
    check("gate_marked_calibrated",
          "GATE_HARD_PASS" in doc and "实测标定" in doc,
          "文档未标注 GATE_HARD_PASS 为实测标定")
    check("weight_marked_prior",
          "W_CONFIDENCE" in doc and "先验" in doc,
          "文档未标注 W_CONFIDENCE 等为先验")

    failed = [r for r in results if not r[1]]
    print()
    for name, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
    print(f"\n[检索权重文档一致性] {len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()