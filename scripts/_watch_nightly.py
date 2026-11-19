# -*- coding: utf-8 -*-
# 夜跑完成监听: 每60s轮询, 结束后把关键行汇总写入结果文件(回调通知用)
# 用法: python -X utf8 scripts/_watch_nightly.py <日志路径> <结果文件路径>
# (2026-11-18 修复: tasklist GBK输出在text=True下炸utf-8解码线程→
#  改errors='replace'且处理r.stdout=None; 参数化日志路径避免硬编码)
import io, os, sys, time, subprocess

LOG = sys.argv[1] if len(sys.argv) > 1 else "logs/nightly_manual.log"
OUT = sys.argv[2] if len(sys.argv) > 2 else "logs/watch_nightly_result.txt"
KEYS = ("OK 用时", "FAIL", "超时", "熔断", "transcript_offline", "Jaccard", "drift",
        "全部结束", "晨间速读报告", "402", "SKIPPED-FUSED")

def _write(msg):
    with io.open(OUT, "a", encoding="utf-8", newline="\n") as f:
        f.write(msg + "\n")
    print(msg)

while True:
    try:
        s = io.open(LOG, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        time.sleep(60); continue
    if "全部结束" in s:
        _write("=== NIGHTLY COMPLETED %s ===" % time.strftime("%Y-%m-%d %H:%M"))
        for l in s.split("\n"):
            if any(k in l for k in KEYS):
                _write(l[:200])
        break
    # 进程死但日志无"全部结束" = 异常退出 (encoding='replace'吞GBK; capture非text自己解码)
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"], capture_output=True)
    txt = r.stdout.decode("gbk", errors="replace") if r.stdout else ""
    if "python.exe" not in txt:
        _write("=== NIGHTLY ABNORMAL EXIT %s === 进程已消失但日志无完成标记" % time.strftime("%Y-%m-%d %H:%M"))
        _write(io.open(LOG, encoding="utf-8", errors="replace").read()[-1500:])
        break
    time.sleep(60)