# -*- coding: utf-8 -*-
"""#37 event_ts 回填 dry-run 扫描器 v3（只读，不写库）。
甄别"日期≠事件日"的可推导项，输出可安全写入清单。"""
import sqlite3, re, os, datetime, io
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "memory", "corpus_store.db")

RE8 = re.compile(r"(20\d{2})(\d{2})(\d{2})")
RE_DASH = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
RE_CN = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})")

def src_tier(src):
    s = (src or "").lower()
    if any(k in s for k in ("quarantine-restore", "watchlist-promote", "restore", "promote")):
        return "op"
    if "session" in s or "会话" in s:
        return "strong"
    return "date"

def extract(text):
    for rx, mode in ((RE8, "8"), (RE_DASH, "-"), (RE_CN, "cn")):
        m = rx.search(text or "")
        if m:
            y, mo, d = m.groups()
            try:
                datetime.date(int(y), int(mo), int(d))
                return ("%04d-%02d-%02d" % (int(y), int(mo), int(d)), mode)
            except Exception:
                continue
    return None

# summary 中"引用日期"而非本卡事件日的已知清单
REFDATE = {"aa1d785b75": "引用专利①日期(2026-05-08)", "ddd691156d": "引用第九章G22事件日(2026-05-21)"}

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
rows = c.execute("SELECT inst_id,status,ts,source_ref,substr(summary,1,220) s "
                 "FROM core_memory_card WHERE (event_ts IS NULL OR event_ts='') ORDER BY inst_id").fetchall()
c.close()

WRITE = {"strong": [], "summary": []}   # 可安全写
SKIP = {"op": [], "date": [], "refdate": [], "B": []}  # 日期≠事件日 或 无据

for r in rows:
    iid = r["inst_id"]; src = r["source_ref"] or ""; s = r["s"] or ""
    hit_src = extract(src) if src else None
    if hit_src:
        t = src_tier(src)
        rec = (iid, r["status"], r["ts"], hit_src[0], src, s, hit_src[1])
        (WRITE["strong"] if t == "strong" else SKIP[t]).append(rec)
        continue
    hit_s = extract(s)
    if hit_s:
        key = next((k for k in REFDATE if iid.startswith(k)), None)
        rec = (iid, r["status"], r["ts"], hit_s[0], src, s, hit_s[1])
        if key:
            SKIP["refdate"].append(rec)
        else:
            WRITE["summary"].append(rec)
    else:
        SKIP["B"].append((iid, r["status"], r["ts"], src, s))

def esc(x): return (x or "").replace("|", "\\|").replace("\n", " ")
BT = chr(96)
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
nW = len(WRITE["strong"]) + len(WRITE["summary"])
nS = sum(len(v) for v in SKIP.values())

L = []; ap = L.append
ap("# #37 event_ts 回填 —— Dry-Run 预览清单 (v3 甄别版)")
ap("")
ap("- 生成时间：%s" % now)
ap("- 性质：**DRY-RUN 只读，未写库**")
ap("- 铁律：**只给可明确推导的、且日期就是本卡事件日的赋值；无可靠来源绝不编造**")
ap("")
ap("## 〇、与旧报告差异 + 关键甄别")
ap("")
ap("旧报告 `dryrun_37_event_ts_20261124.md`：A1=22 / A2=3 / B=66（机械正则，未甄别日期语义）。")
ap("")
ap("本 v3 核查后发现**「含日期」≠「日期是本卡事件日」**，剔除 9 张误判：")
ap("- **op 4 张**：source_ref=`quarantine-restore/watchlist-promote` = 隔离恢复/转正**操作日**。证据：`0b272d92`(紫光国微双AI对峙) source_ref 写 11-23，但同事件兄弟卡 `7ee56837` 的 ts=**2026-11-08** → 事件真发生在 11-08。")
ap("- **date 3 张**：source_ref=`专项扫描回填 2026-11-13` = **回填日**。摘要自述事件实为 2026-04-10 等。")
ap("- **refdate 2 张**：summary 里的日期是**引用他人/他事**：aa1d785b75 引用专利①(2026-05-08)、ddd691156d 引用第九章G22(2026-05-21)，均非本卡事件日。")
ap("")
ap("**结论：真正可安全回填 = strong %d + summary %d = %d 张；其余 %d 张保留空值。**" % (
    len(WRITE["strong"]), len(WRITE["summary"]), nW, nS))
ap("")
ap("## 一、总览（合计 %d 张空值 → 可写 %d / 留空 %d）" % (len(rows), nW, nS))
ap("")
ap("| 归档 | 依据 | 张数 | 处置 |")
ap("|---|---|---|---|")
ap("| ★★★ strong | source_ref `session/会话`+日期（会话记录日=事件日） | %d | ✅ 写 |" % len(WRITE["strong"]))
ap("| ★★ summary | summary 正文日期=本卡事件日 | %d | ✅ 写 |" % len(WRITE["summary"]))
ap("| ✗ op | 隔离恢复/转正操作日(≠事件日) | %d | ⬜ 留空 |" % len(SKIP["op"]))
ap("| ✗ date | 扫描/回填标注日(≠事件日) | %d | ⬜ 留空 |" % len(SKIP["date"]))
ap("| ✗ refdate | summary 引用他人日期(≠本卡) | %d | ⬜ 留空 |" % len(SKIP["refdate"]))
ap("| ✗ B | 无任何日期线索 | %d | ⬜ 留空 |" % len(SKIP["B"]))
ap("| **合计** | — | **%d** | 写 %d |" % (len(rows), nW))
ap("")
ap("---")
ap("")
ap("## 二、✅ 可安全写入（%d 张）" % nW)
ap("")
ap("| # | inst_id | 推导 event_ts | 依据 | source_ref 原文 | 摘要 |")
ap("|---|---|---|---|---|---|")
i = 0
for key, label in (("strong", "会话"), ("summary", "正文")):
    for (iid, st, ts, got, src, s, mode) in WRITE[key]:
        i += 1
        ap("| %d | %s%s%s | **%s** | %s | %s | %s |" % (i, BT, iid, BT, got, label, esc(src or "(空)")[:45], esc(s)[:50]))
ap("")
dW = Counter(x[3] for v in WRITE.values() for x in v)
ap("日期分布：" + "，".join("%s×%d" % (k, v) for k, v in sorted(dW.items())))
ap("")
ap("---")
ap("")
ap("## 三、⬜ 需留空 / 人工另判（%d 张）" % nS)
ap("")

def dump_skip(title, key, extra_hdr):
    items = SKIP[key]
    ap("### %s（%d 张）" % (title, len(items)))
    ap("")
    if not items:
        ap("（无）"); ap(""); return
    if key in ("op", "date", "refdate"):
        ap("| # | inst_id | 机械提取到的\"伪日期\" | 为何不是事件日 | source_ref 原文 |")
        ap("|---|---|---|---|---|")
        for j, (iid, st, ts, got, src, s, mode) in enumerate(items, 1):
            note = REFDATE.get(next((k for k in REFDATE if iid.startswith(k)), ""), extra_hdr)
            ap("| %d | %s%s%s | %s | %s | %s |" % (j, BT, iid, BT, got, note, esc(src or s)[:52]))
    else:
        ap("| # | inst_id | source_ref | 摘要（无日期线索）|")
        ap("|---|---|---|---|")
        for j, (iid, st, ts, src, s) in enumerate(items, 1):
            ap("| %d | %s%s%s | %s | %s |" % (j, BT, iid, BT, esc(src)[:40], esc(s)[:60]))
    ap("")
    ap("---")
    ap("")

dump_skip("✗ op：操作日≠事件日", "op", "隔离恢复/转正操作日")
dump_skip("✗ date：回填/扫描标注日≠事件日", "date", "回填/扫描标注日")
dump_skip("✗ refdate：summary 引用他人日期", "refdate", "引用日期")
dump_skip("✗ B：无任何日期线索", "B", "")

ap("## 四、校验与执行计划")
ap("")
ap("| 项 | 预期 |")
ap("|---|---|")
ap("| 变更张数 | %d |" % nW)
ap("| 变更字段 | 仅 %sevent_ts%s |" % (BT, BT))
ap("| 其余字段 | status/ts/source_ref/summary/detail 全不动 |")
ap("| 物理删除 | 零 |")
ap("| 无据不编造 | 留空 %d 张（op/date/refdate/B）|" % nS)
ap("| 执行后空值 | %d → %d |" % (len(rows), nS))
ap("")
ap("```")
ap("python eval/audit/backfill_37_event_ts.py --tiers strong,summary --apply")
ap("```")
ap("")

OUT = os.path.join(BASE, "docs", "dryrun_37_a1a2_preview_20261124.md")
io.open(OUT, "w", encoding="utf-8").write("\n".join(L))
print("OK ->", OUT)
print("可写 strong=%d summary=%d = %d | 留空 op=%d date=%d refdate=%d B=%d = %d | total=%d" % (
    len(WRITE["strong"]), len(WRITE["summary"]), nW,
    len(SKIP["op"]), len(SKIP["date"]), len(SKIP["refdate"]), len(SKIP["B"]), nS, len(rows)))
print("可写日期分布:", dict(dW))