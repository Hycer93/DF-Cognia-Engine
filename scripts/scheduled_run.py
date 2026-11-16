"""
定时调度入口（2026-11-09 未来时间工程）：让夜间管线在指定时刻自动跑。

用法（今晚 23:59 由 schtasks 触发）:
  python scripts/scheduled_run.py            # 立即执行 nightly_run（schtasks 触发时）
  python scripts/scheduled_run.py --at 23:59 # 自带倒计时模式（备用：schtasks 不可用时）

设计契约（design_event_ts.md 未来时间边界）：
- 未来时间=绝对时刻调度（schtasks/倒计时），不是相对时间解析——风险1不适用
- 触发时写 logs/schedule_log.jsonl：计划时刻/实际触发时刻/漂移秒数，可排查
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(HERE, "logs")


def log_schedule(plan_ts: str, drift_s: float, cmd: str):
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "schedule_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"plan": plan_ts, "fired": datetime.now().isoformat(),
                            "drift_s": round(drift_s, 1), "cmd": cmd,
                            "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=None, help="HH:MM 倒计时等待到该时刻再执行（备用模式）")
    ap.add_argument("--note", default="nightly", help="调度标识")
    args = ap.parse_args()

    if args.at:
        hh, mm = args.at.split(":")
        now = datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=now.day + 1)  # 已过则明天
        wait = (target - now).total_seconds()
        print(f"[SCHED] 等待 {wait/3600:.1f}h 到 {target:%H:%M} 再执行", flush=True)
        time.sleep(wait)

    plan_ts = args.at or datetime.now().strftime("%H:%M")
    t0 = time.time()
    env = {**os.environ, "NO_MEMORY": "1", "PYTHONIOENCODING": "utf-8",
           "TOPIC_LAYER_ON": "1"}   # 2026-11-16 用户裁决: 主题锚层全局开关启用(方案A)
    log = open(os.path.join(LOGS, "nightly_console.log"), "ab")
    r = subprocess.run([sys.executable, "scripts/nightly_run.py"], cwd=HERE, env=env,
                       stdout=log, stderr=subprocess.STDOUT)
    log_schedule(plan_ts, time.time() - t0, f"nightly:{args.note}")
    print(f"[SCHED] nightly_run 退出码 {r.returncode}", flush=True)


if __name__ == "__main__":
    main()
