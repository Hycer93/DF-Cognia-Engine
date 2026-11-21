# eval/core/e_hold_review.py — 夜间 hold 复审步骤（A-3，#7 pending 队列可靠写回收尾）
#
# 定位：把 memory_judge.review_holds() 接入 nightly 主链，让 pending hold 池每夜被
# 消费一次（证据补齐→转正 / 仍不足→维持 hold / 反向证据→丢弃）。
#
# 为什么挂进主链（2026-11-22 用户裁决路径A）：
#   review_holds() 之前全仓零调用点（D1 致命缺陷），pending 池只进不出。
#   A-1/A-2/A-4 已把它的原子落盘、跨进程锁、DB 事务重试全部就绪，
#   故可安全接入夜间调度，形成"E9 提案入池 → 本步消费转正"的闭环。
#
# 锁契约（A-2）：review_holds 入口抢 pending 文件锁；若另一进程正消费同一 pending，
#   抢锁失败 → 返回 skipped/lock_busy，本步**告警但不阻断夜间整条流水线**（退出0）。
#   这是有意的：并发消费是正常的（手动+夜间撞车），不是故障。
#
# 退出码：
#   0 = 正常完成 / 锁冲突跳过（均不阻塞夜跑）
#   1 = 执行异常（晨报高亮，提示排查判定通道/DB/路径）
#
# 幂等：review_holds 内部按证据是否补齐决定去留；无新证据的 hold 原样保留，
#   重复跑结果收敛（reviews 追加一条 why 记录，属审计留痕非业务漂移）。

import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))
os.environ.setdefault("NO_MEMORY", "1")

REPORT = os.path.join(HERE, "logs", "e_hold_review.json")


def main():
    try:
        import memory_judge as MJ
    except Exception as e:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "ok": False,
               "error": "import memory_judge 失败: %s: %s" % (type(e).__name__, e)}
        _write(rec)
        print("[E-hold复审] 导入失败(不阻断): %s" % e)
        return 1

    try:
        report = MJ.review_holds()
    except Exception as e:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "ok": False,
               "error": "%s: %s" % (type(e).__name__, e)}
        _write(rec)
        print("[E-hold复审] 执行异常(晨报需排查): %s" % e)
        return 1

    skipped = bool(report.get("skipped"))
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "ok": True,
           "skipped": skipped, "report": report}
    _write(rec)
    if skipped:
        print("[E-hold复审] 锁被占用(另一进程消费中)，本轮跳过: %s"
              % report.get("reason"))
    else:
        print("[E-hold复审] reviewed=%(reviewed)d promoted=%(promoted)d "
              "kept=%(kept)d dropped=%(dropped)d" % {
                  "reviewed": report.get("reviewed", 0),
                  "promoted": report.get("promoted", 0),
                  "kept": report.get("kept", 0),
                  "dropped": report.get("dropped", 0)})
    print("报告:", REPORT)
    return 0


def _write(rec):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with io.open(REPORT, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())