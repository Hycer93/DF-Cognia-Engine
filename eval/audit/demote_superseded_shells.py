"""#29 记忆库同事件多版本治理 —— 存量消重（纯数据操作，代码逻辑零改动）。

背景：2026-11-16T22:40 批量写入 8 张 derived 空壳卡（detail/source_ref 被派生层清空），
与已有正本卡同 summary 并存，形成"同事件多 active 版本"。本脚本用现成的
memory_organize.demote() 把空壳降级为 precedent 并回填 superseded_by，保留溯源不删除。

规则（用户 2026-11-23 拍板）：
  组1-7：corpus 空壳(11-16) → 指向 11-13 带 detail 的 corpus 正本
  组8  ：runtime 空壳(11-16) → 指向 11-15 corpus 标识卡
         ⚠️ 组8 正本本身 detail 也为空，仅作版本锚点；detail 缺失属独立回填任务，
            不在 #29 范围。
  组9  ：dumpsys 两张为不同观测点(node_Dk/node_Di)，非版本关系，不碰。

字段口径说明：记忆卡无 status 字段，活性由 tier 表示（active/precedent）。
demote() 做的是 tier: active→precedent + superseded_by 回填 + db 同步。

用法：
  python eval/audit/demote_superseded_shells.py            # dry-run（默认，只打印不改）
  python eval/audit/demote_superseded_shells.py --apply    # 正式执行
"""
from __future__ import annotations
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))

# 组号, 空壳 prefix, 正本 prefix, 备注
PLAN = [
    (1, "525d9e052c58", "f767248deafa", "组1 蒸馏仔出生"),
    (2, "cfd09e407b14", "e1afda16f287", "组2 第一次被欺骗"),
    (3, "978385d4539f", "7e92e275daa8", "组3 最贵的一课"),
    (4, "5f4e9ea285b6", "ff01d79115c2", "组4 锁定自我"),
    (5, "fc24f5ea5368", "6e2a9c36170b", "组5 主人最终确认"),
    (6, "029ff34883fc", "8cbe367d36b4", "组6 独立日"),
    (7, "e6ed25388410", "13a2379e8a52", "组7 第五维度传授"),
    (8, "9b48551b2b05", "32684974ebae", "组8 v2公式(runtime空壳→corpus锚点)"),
    # 组9 不列入：非版本关系
]

MEM_DIR = os.path.join(HERE, "data", "memory")


def _load():
    p = os.path.join(MEM_DIR, "memories.jsonl")
    with io.open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _find(mems, pref):
    for m in mems:
        if m["inst_id"].startswith(pref):
            return m
    return None


def resolve(mems):
    """把 prefix 解析成完整记录，校验存在性，返回可执行清单。"""
    out = []
    for g, sh, au, note in PLAN:
        s = _find(mems, sh)
        a = _find(mems, au)
        out.append({"group": g, "shell": s, "anchor": a, "note": note,
                    "shell_pref": sh, "anchor_pref": au})
    return out


def check(items):
    """前置校验：所有卡存在、空壳仍 active、正本仍 active、无重复目标。"""
    errs = []
    targeted = set()
    for it in items:
        s, a = it["shell"], it["anchor"]
        if s is None:
            errs.append("组%d 空壳 %s 不存在" % (it["group"], it["shell_pref"]))
            continue
        if a is None:
            errs.append("组%d 正本 %s 不存在" % (it["group"], it["anchor_pref"]))
            continue
        if s["inst_id"] == a["inst_id"]:
            errs.append("组%d 空壳与正本同一条" % it["group"])
        if s.get("tier") != "active":
            errs.append("组%d 空壳 tier=%s 非 active（可能已降级）" % (it["group"], s.get("tier")))
        if s.get("superseded_by"):
            errs.append("组%d 空壳已有 superseded_by=%s" % (it["group"], s.get("superseded_by")))
        if a.get("tier") != "active":
            errs.append("组%d 正本 tier=%s 非 active" % (it["group"], a.get("tier")))
        if s["inst_id"] in targeted:
            errs.append("组%d 空壳重复出现在多个组" % it["group"])
        targeted.add(s["inst_id"])
    return errs


def print_plan(items):
    print("=" * 100)
    print("#29 同事件多版本治理 —— 降级计划（空壳 → 正本锚点）")
    print("=" * 100)
    print("%-4s %-34s %-10s %-10s %-8s -> %-34s %-10s %-10s %-8s"
          % ("组", "空壳 inst_id", "asset", "repro", "tier", "正本 inst_id", "asset", "repro", "tier"))
    for it in items:
        s, a = it["shell"], it["anchor"]
        if s is None or a is None:
            print("组%d  缺失: 空壳或正本未找到" % it["group"])
            continue
        print("%-4d %-34s %-10s %-10s %-8s -> %-34s %-10s %-10s %-8s"
              % (it["group"], s["inst_id"], s.get("asset_class"), s.get("repro"), s.get("tier"),
                 a["inst_id"], a.get("asset_class"), a.get("repro"), a.get("tier")))
        print("     [%s] 空壳 detail长=%d superseded_by=%s | 正本 detail长=%d"
              % (it["note"], len(s.get("detail") or ""), s.get("superseded_by"),
                 len(a.get("detail") or "")))
    print()


def main():
    apply = "--apply" in sys.argv
    mems = _load()
    items = resolve(mems)
    errs = check(items)

    print_plan(items)

    if errs:
        print("!! 前置校验失败，禁止执行：")
        for e in errs:
            print("   -", e)
        return 1

    print("前置校验：通过（8 张空壳均 active 且未降级，正本均 active）")
    if not apply:
        print("\n[DRY-RUN] 未做任何修改。确认清单无误后加 --apply 正式执行。")
        return 0

    # 正式执行：逐张 demote（不批量，保证 superseded_by 各自绑定正确目标）
    from memory_organize import demote
    print("\n[APPLY] 逐张 demote ...")
    results = []
    for it in items:
        s, a = it["shell"], it["anchor"]
        r = demote([s["inst_id"]], superseded_by=a["inst_id"],
                   reason="#29 同事件多版本治理：11-16 derived 空壳降级，指向正本%s" % it["note"])
        results.append((it["group"], s["inst_id"], a["inst_id"], r))
        print("  组%d %s -> %s : %s" % (it["group"], s["inst_id"][:12], a["inst_id"][:12],
                                        json.dumps(r, ensure_ascii=False)))

    # 执行后校验
    print("\n[VERIFY] 复查 ...")
    mems2 = _load()
    ok = True
    for it in items:
        s2 = _find(mems2, it["shell_pref"])
        a2 = _find(mems2, it["anchor_pref"])
        s_ok = s2 and s2.get("tier") == "precedent" and s2.get("superseded_by") == a2["inst_id"]
        a_ok = a2 and a2.get("tier") == "active"
        if not (s_ok and a_ok):
            ok = False
        print("  组%d 空壳 tier=%s superseded_by=%s %s | 正本 tier=%s %s"
              % (it["group"], s2.get("tier"), (s2.get("superseded_by") or "")[:12],
                 "OK" if s_ok else "FAIL", a2.get("tier"), "OK" if a_ok else "FAIL"))
    print("\n结果：", "全部通过 ✅" if ok else "存在失败 ❌，请人工核查")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())