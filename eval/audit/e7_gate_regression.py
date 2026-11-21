# eval/audit/e7_gate_regression.py — E7 自治闸门回归用例
# 2026-11-16 用户前置约束④: 边界案例覆盖——豁免卡/冲突卡/零锚卡/老化到期卡,
# 自动放行与打入人工队列两条分支都要有断言。
# 隔离测试: 复制主库→临时库, 注入构造行, import e7 模块指向临时库跑分流,
# 不碰主库。跑法: python eval/audit/e7_gate_regression.py
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "eval", "core"))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

MAIN_DB = os.path.join(HERE, "data", "memory", "corpus_store.db")
NOW = datetime.now()

def mk_card(con, iid, summary):
    con.execute(
        "INSERT INTO core_memory_card (inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, summary, status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (iid, NOW.isoformat(timespec="seconds"), "testhash" + iid[-8:], "knowledge", "situational",
         "L1", "{}", summary, "active"))

def mk_hold(con, iid, reason, ref=0, enq_days_ago=0, resolved_by=None):
    con.execute(
        "INSERT INTO core_hold_pool (inst_id, reason, reference_mention, enqueued_ts, verdict, verdict_ts, resolved_by) "
        "VALUES (?,?,?,?,NULL,NULL,?)",
        (iid, reason, ref, (NOW - timedelta(days=enq_days_ago)).isoformat(timespec="seconds"), resolved_by))

print("== E7 自治闸门回归(三路分流边界案例) ==")

tmpdir = tempfile.mkdtemp(prefix="e7_gate_")
test_db = os.path.join(tmpdir, "gate_test.db")
shutil.copy(MAIN_DB, test_db)

# ---- 构造边界案例 ----
# 真卡 id(32位hex 构造)
C = {
    "exempt":  "aabbccdd000000000000000000000001",  # 豁免卡: user-retain 标记
    "conflict": "aabbccdd000000000000000000000002",  # 冲突卡: cluster_conflict 强制人工
    "zero":    "aabbccdd000000000000000000000003",  # 零锚卡: 强制人工
    "stale":   "aabbccdd000000000000000000000004",  # 老化到期卡: 非引用超龄→人工
    "autofresh": "aabbccdd000000000000000000000005", # 清晰可判卡: 非引用未到期→自动 keep
    "refmounted": "aabbccdd000000000000000000000006", # 引用+已挂锚→自动 release
    "refstale": "aabbccdd000000000000000000000007",   # 引用+超龄无核心→人工
}
con = sqlite3.connect(test_db, timeout=5)
con.row_factory = sqlite3.Row
for iid in C.values():
    mk_card(con, iid, "测试卡-" + iid[-4:])
mk_hold(con, C["exempt"], "引用性提及疑似（cue 仅 detail 命中）", ref=1, enq_days_ago=5, resolved_by="user-retain-2026-11-16")
mk_hold(con, C["conflict"], "cluster_conflict: 同簇互斥证据", ref=0, enq_days_ago=5)
mk_hold(con, C["zero"], "零锚命中；建新锚仅限 E7 批量复审", ref=0, enq_days_ago=5)
mk_hold(con, C["stale"], "其他低危原因(测试构造)", ref=0, enq_days_ago=20)   # >AUTO_RELEASE_MAX_AGE(14)
mk_hold(con, C["autofresh"], "其他低危原因(测试构造)", ref=0, enq_days_ago=2)  # 清晰可判
mk_hold(con, C["refmounted"], "引用性提及疑似（cue 仅 detail 命中）", ref=1, enq_days_ago=5)
mk_hold(con, C["refstale"], "引用性提及疑似（cue 仅 detail 命中）", ref=1, enq_days_ago=20)
# refmounted 已挂锚(topic_links 有行) → 自动 release
con.execute("INSERT INTO topic_links VALUES (?,?,?,?)",
            (C["refmounted"], "topic_memory_ops", "test", NOW.isoformat(timespec="seconds")))
con.commit(); con.close()

# ---- 注入 E7 模块跑分流 ----
import e7_nightly_governance as e7
e7.DB = test_db
result = {"ts": "test", "ok": True}
con = e7._conn()
try:
    e7.p1_hold_review(con, result)
finally:
    con.close()

p1 = result["p1_hold"]
by_id = {a["inst_id"]: a for a in p1["audit"]}

# 分流断言: 每类卡走对通道
check("豁免卡→auto/keep(user-retain 生效)",
      by_id.get(C["exempt"], {}).get("route") == "auto" and by_id[C["exempt"]]["action"] == "keep",
      str(by_id.get(C["exempt"])))
check("冲突卡(cluster_conflict)→manual 强制人工",
      by_id.get(C["conflict"], {}).get("route") == "manual",
      str(by_id.get(C["conflict"])))
check("零锚卡→manual 强制人工",
      by_id.get(C["zero"], {}).get("route") == "manual",
      str(by_id.get(C["zero"])))
check("老化到期卡(>14d 非引用)→manual(边界老化转人裁)",
      by_id.get(C["stale"], {}).get("route") == "manual",
      str(by_id.get(C["stale"])))
check("清晰可判卡(2d 非引用)→auto/keep",
      by_id.get(C["autofresh"], {}).get("route") == "auto" and by_id[C["autofresh"]]["action"] == "keep",
      str(by_id.get(C["autofresh"])))
check("引用+已挂锚卡→auto/release(自动放行分支)",
      by_id.get(C["refmounted"], {}).get("route") == "auto" and by_id[C["refmounted"]]["action"] == "release",
      str(by_id.get(C["refmounted"])))
check("引用+超龄无核心→manual(老化上交人裁)",
      by_id.get(C["refstale"], {}).get("route") == "manual",
      str(by_id.get(C["refstale"])))

# 自动放行落库断言: verdict=released + resolved_by='e7-auto' + verdict_ts 非空
con = e7._conn()
try:
    r = con.execute("SELECT verdict, verdict_ts, resolved_by FROM core_hold_pool WHERE inst_id=?",
                    (C["refmounted"],)).fetchone()
    check("自动放行三审计字段落库(released/e7-auto/时间)",
          r["verdict"] == "released" and r["resolved_by"] == "e7-auto" and r["verdict_ts"],
          str(dict(r)) if r else "无行")
    # 幂等: 复跑不重复处理已放行行
    e7.p1_hold_review(con, result2 := {"ts": "test2", "ok": True})
    r2 = con.execute("SELECT COUNT(*) FROM core_hold_pool WHERE inst_id=? AND verdict='released'",
                     (C["refmounted"],)).fetchone()[0]
    check("幂等复跑: 已放行行不重复处理(仍1条released)", r2 == 1, str(r2))
finally:
    con.close()

# ---- 紧急总开关 ----
e7.EMERGENCY_STOP_FILE = os.path.join(tmpdir, "STOP")
io.open(e7.EMERGENCY_STOP_FILE, "w").write("test")
result3 = {"ts": "test3", "ok": True}
con = e7._conn()
try:
    e7.p1_hold_review(con, result3)
finally:
    con.close()
check("紧急总开关: P1 暂停自动放行(全部转应急态)",
      result3["p1_hold"].get("emergency_stop") is True, str(result3.get("p1_hold", {}).keys()))
os.remove(e7.EMERGENCY_STOP_FILE)

# ---- 回滚通道 ----
n = e7.rollback_governance(C["refmounted"])
con = e7._conn()
try:
    r = con.execute("SELECT verdict, resolved_by FROM core_hold_pool WHERE inst_id=?",
                    (C["refmounted"],)).fetchone()
    check("回滚: e7-auto 行回 hold 待审态(verdict=NULL)", n == 1 and r["verdict"] is None and r["resolved_by"] is None,
          f"n={n} {dict(r) if r else None}")
    # 主库人工裁决行不可回滚(在测试库中 f7a20eb7 无 e7-auto 行, 应 0)
    n2 = e7.rollback_governance(C["zero"])
    check("人工裁决/未裁决行永不被机器回滚", n2 == 0, str(n2))
finally:
    con.close()

# ---- 审计字段契约 ----
recs = p1.get("audit_records", [])
EXPECT = {"card_id", "summary", "check_type", "rule_triggered", "state",
          "last_judge", "expire_at", "evidence", "next_action"}
check("标准化审计 9 字段契约(不增不删)",
      recs and all(set(a.keys()) == EXPECT for a in recs),
      str(set(recs[0].keys()) - EXPECT) if recs else "空")

# 清理
shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n_, d_ in FAIL: print(f"  FAIL: {n_} | {d_}")
    sys.exit(1)
print("E7 自治闸门回归全过")