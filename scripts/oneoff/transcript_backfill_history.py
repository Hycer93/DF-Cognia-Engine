# -*- coding: utf-8 -*-
"""一次性回填: 扫 CodeChat applog 全部滚动日志, 把 2026-11-02 ~ 2026-11-14 的
用户提交原文补入 raw_corpus(同 E9 transcript 兜底三规约: 只读/复本隔离/来源指针)。
跑完即弃, 不入管线。"""
import io, json, os, re, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))          # 工程根
sys.path.insert(0, os.path.join(HERE, "..", "..", "memory"))
import corpus_store as cs                                    # noqa: E402
import memory_gate as gate                                   # noqa: E402

LOG_DIR = r"C:/Users/uidq0280/AppData/Roaming/DSV CodeChat Desktop/logs/"
DAYS = ["202611%02d" % d for d in range(2, 15)]              # 1102~1114
MARK = "user submit: "

# 1) 扫全部文件收集 (跨文件同文精确去重)
seen_text = {}          # 原文 -> day
collected = {}          # day -> [(hhmm, text)]
files = sorted(glob.glob(LOG_DIR + "applog*.log"))
for f in files:
    for ln in io.open(f, encoding="utf-8", errors="replace"):
        m = re.match(r"\[(\d{8}) (\d\d:\d\d:\d\d)", ln)
        if not m or MARK not in ln:
            continue
        day, hhmm = m.group(1), m.group(2)
        if day not in DAYS:
            continue
        text = ln.split(MARK, 1)[1].strip()
        if not text:
            continue
        if text in seen_text:                               # 跨文件滚动重复
            continue
        seen_text[text] = day
        collected.setdefault(day, []).append((hhmm, text))

# 2) 按日入库(当日 simhash 判重; 历史日与库内已有 11-12 段也判重)
report = {}
for day in sorted(collected):
    d = "20%s-%s-%s" % (day[2:4], day[4:6], day[6:])
    existing = [cs.get_corpus(r["id"]) for r in
                cs.list_corpus(kind="session_dialog", tree_vol=d)]
    day_sh = [gate._simhash(x["body"]) for x in existing if x]
    stored = dup = 0
    for hhmm, text in collected[day]:
        sh = gate._simhash(text)
        if any(gate._hamming(sh, old) <= 6 for old in day_sh):
            dup += 1
            continue
        seg_n = len(existing) + stored + 1
        cid = "dialog.%s.tseg%02d" % (d, seg_n)
        cs.upsert_corpus(cid, "session_dialog",
                         "[用户提交-transcript兜底] %s" % text,
                         tree_vol=d, tree_ch="tseg%02d" % seg_n,
                         title="codechat-transcript",
                         source_ref="历史回填; %s; window=%s %s"
                                   % (files[-1], d, hhmm))
        day_sh.append(sh)
        stored += 1
    report[d] = (len(collected[day]), stored, dup)

print(json.dumps(report, ensure_ascii=False, indent=1))
print("总收集:", sum(v[0] for v in report.values()),
      "| 入库:", sum(v[1] for v in report.values()),
      "| 去重跳过:", sum(v[2] for v in report.values()))