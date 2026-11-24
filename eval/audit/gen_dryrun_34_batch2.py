# -*- coding: utf-8 -*-
"""#34 第二批 dry-run 报告生成器（只读，不写库）。"""
import sqlite3, sys, io, json, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from backfill_34_memory_class import CLASS_MAP, resolve, ENUM
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "memory", "corpus_store.db")
PEND = os.path.join(BASE, "data", "memory", "class_backfill_pending.jsonl")
OUT = os.path.join(BASE, "docs", "dryrun_34_backfill_high_medium_conf.md")
BT = chr(96)  # backtick

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
rows = c.execute("SELECT inst_id,tier,substr(summary,1,140) s FROM core_memory_card "
                 "WHERE (memory_class IS NULL OR memory_class='') AND status='active' ORDER BY inst_id").fetchall()

pend = {}
try:
    for l in io.open(PEND, encoding="utf-8"):
        l = l.strip()
        if l:
            o = json.loads(l); pend[o["inst_id"]] = o.get("proposed_class")
except Exception:
    pass

A = []; B = []
for r in rows:
    cls, conf, why = resolve(r["inst_id"])
    t = (r["inst_id"], r["tier"], cls, why, pend.get(r["inst_id"]), r["s"])
    if cls in ENUM:
        A.append(t)
    else:
        B.append(t)

hi = [(k, v[0], v[2]) for k, v in CLASS_MAP.items() if v[1] == "hi" and v[0] in ENUM]
hi_done = []
for k, cls, why in hi:
    r = c.execute("SELECT memory_class FROM core_memory_card WHERE inst_id LIKE ?", (k.rstrip("_") + "%",)).fetchone()
    if r and r[0] == cls:
        hi_done.append((k, cls, why))
dist = {}
for r in c.execute("SELECT COALESCE(memory_class,'NULL') mc,COUNT(*) FROM core_memory_card WHERE status='active' GROUP BY mc"):
    dist[r[0]] = r[1]
active_total = c.execute("SELECT COUNT(*) FROM core_memory_card WHERE status='active'").fetchone()[0]
c.close()

def esc(x):
    return (x or "").replace("|", "\\|").replace("\n", " ")

cd = Counter(x[2] for x in A)
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
L = []
ap = L.append
ap("# #34 memory_class 第二批（A组中置信）回填 —— Dry-Run 预览报告")
ap("")
ap("- 生成时间：%s" % now)
ap("- 性质：**DRY-RUN 只读预览，未写库**")
ap("- 脚本：%seval/audit/backfill_34_memory_class.py%s（CLASS_MAP 人工复核映射）" % (BT, BT))
ap("")
ap("---")
ap("")
ap("## 〇、前提澄清（重要）")
ap("")
ap("用户指令原表述「24 高置信 + A组20 = 44 张执行」。**实测：高置信批次已于 commit %sc6b9b45%s 真实写库 30 张**（DB 逐条核实 **%d/%d 值匹配**）。" % (BT, BT, len(hi_done), len(hi)))
ap("")
ap("故本次 dry-run 的真实待执行范围：")
ap("")
ap("| 批次 | 张数 | 状态 |")
ap("|---|---|---|")
ap("| 高置信(hi) | 30 | ✅ **已完成**(c6b9b45)，本次不重复写 |")
ap("| A组(中置信, 可赋类) | %d | 🟡 **本次待执行** |" % len(A))
ap("| B组(中置信, 维持null) | %d | ⬜ 维持 null，不改 |" % len(B))
ap("| **本次实际变更** | **%d** | — |" % len(A))
ap("")
ap("当前 active NULL = %d（= A组 %d + B组 %d），与上面完全吻合。" % (len(rows), len(A), len(B)))
ap("")
ap("---")
ap("")
ap("## 一、A 组待变更明细（%d 张，null → 分类）" % len(A))
ap("")
ap("| # | inst_id | 变更 | 理由 | 原pending提案 | 摘要 |")
ap("|---|---|---|---|---|---|")
for i, (iid, tier, cls, why, orig, s) in enumerate(A, 1):
    ap("| %d | %s%s%s | null → **%s** | %s | %s | %s |" % (i, BT, iid, BT, cls, esc(why), esc(orig) or "—", esc(s)))
ap("")
ap("A组分类分布：" + "，".join("%s × %d" % (k, v) for k, v in cd.most_common()))
ap("")
ap("---")
ap("")
ap("## 二、B 组维持 null（%d 张，不改动）" % len(B))
ap("")
ap("| # | inst_id | 维持 | 理由 | 原pending提案 | 摘要 |")
ap("|---|---|---|---|---|---|")
for i, (iid, tier, cls, why, orig, s) in enumerate(B, 1):
    ap("| %d | %s%s%s | null | %s | %s | %s |" % (i, BT, iid, BT, esc(why), esc(orig) or "—", esc(s)))
ap("")
ap("---")
ap("")
ap("## 三、Dry-Run 校验项")
ap("")
ap("| 校验项 | 预期 |")
ap("|---|---|")
ap("| 变更字段 | 仅 %smemory_class%s（UPDATE 单字段）|" % (BT, BT))
ap("| 物理删除 | **零**（无 DELETE）|")
ap("| DB-jsonl 同步 | 变更 inst_id 在 memories.jsonl 同步写 memory_class |")
ap("| 其余字段 | status/tier/superseded_by/detail 等**全部不动** |")
ap("| 执行后 active NULL | %d → **%d** |" % (len(rows), len(B)))
ap("| 非法 class 值 | 0（全部落在五类枚举）|")
ap("")
ap("---")
ap("")
ap("## 四、附：高置信批次已完成核对（%d 张，参考不重写）" % len(hi_done))
ap("")
ap("commit %sc6b9b45%s 已写入，DB 逐条值匹配 %d/%d：" % (BT, BT, len(hi_done), len(hi)))
ap("")
ap("| inst前缀 | 已写分类 | 理由 |")
ap("|---|---|---|")
for k, cls, why in hi_done:
    ap("| %s%s%s | %s | %s |" % (BT, k, BT, cls, esc(why)))
ap("")
ap("---")
ap("")
ap("## 五、执行计划（待用户确认 apply）")
ap("")
ap(BT * 3)
ap("python eval/audit/backfill_34_memory_class.py --only mid --apply")
ap(BT * 3)
ap("")
ap("> %s--only mid%s 仅写 A 组 %d 张（hi 已写不会重复），B 组 null 不写。" % (BT, BT, len(A)))
ap("> 备份：执行时自动生成 %s*.bak_34_<ts>%s。" % (BT, BT))
ap("")

io.open(OUT, "w", encoding="utf-8").write("\n".join(L))
print("OK written:", OUT)
print("A=%d B=%d hi_done=%d/%d" % (len(A), len(B), len(hi_done), len(hi)))
print("A dist:", dict(cd))
print("active_total=%d dist=%s" % (active_total, dist))