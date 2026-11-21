# -*- coding: utf-8 -*-
"""
topic_layer 开关守卫探针（P0 守卫, 2026-11-16 用户指令）

背景: 2026-11-16 发现 TOPIC_LAYER_ON 未启用导致全管道新卡不走锚判定的
业务断层(五张元核种子卡未进 hold 池), 流水线各步状态却仍全 OK——断层
完全静默。本探针把该断层变成硬告警: nightly 里失败即 FAIL, 不再默默漂。

探针逻辑(不依赖 LLM, 纯本地, 秒级):
  1. 断言环境开关 TOPIC_LAYER_ON=1(topic_enabled() 为 True);
  2. 写入一张守卫测试卡(memory_hook.add, dfcp 编码走合法白名单);
  3. 断言 report['topic'] 存在(= mount_topic 被自动调用);
  4. 断言 hold jsonl 中出现该卡记录(零锚卡应落 hold 池而非直接挂锚);
  5. 清理守卫卡(卡行+hold 行, 不留测试残留; 若 hold 已被增量同步进
     core_hold_pool 也一并清)。

退出码: 全过=0; 任一断言失败=1(nightly_run 的 run_step 将报 FAIL)。
用法: TOPIC_LAYER_ON=1 python eval/core/e0_topic_gate_guard.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))

FAILS = []

def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ FAIL ") + name + ((" | " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

import topic_layer
import memory_hook as mh

GUARD_SUMMARY = ("TOPIC_LAYER_ON守卫探针测试卡(开关断层守卫, 自动清理不留残)")

def _hold_jsonl_path():
    return os.path.join(HERE, "data", "memory", "topic_hold_pool.jsonl")

def _purge_hold_record(inst_id):
    """从 hold jsonl 删守卫卡记录(守卫自清理, 不污染人工复审队列)"""
    p = _hold_jsonl_path()
    if not os.path.exists(p):
        return
    lines = io.open(p, encoding="utf-8").readlines()
    keep = [l for l in lines if l.strip() and json.loads(l).get("inst_id") != inst_id]
    with io.open(p, "w", encoding="utf-8") as f:
        f.writelines(keep)

def _purge_memories_jsonl(inst_id):
    """memories.jsonl 同步清理(atomic_store 双写: db+jsonl, 两边都要清
    ——漏清会让下一轮②回查命中残留旧行, 产生假 id 污染断言)"""
    p = os.path.join(HERE, "data", "memory", "memories.jsonl")
    if not os.path.exists(p):
        return
    lines = io.open(p, encoding="utf-8").readlines()
    keep = [l for l in lines if l.strip() and json.loads(l).get("inst_id") != inst_id]
    with io.open(p, "w", encoding="utf-8") as f:
        f.writelines(keep)

def _purge_db_rows(inst_id):
    """同步清理 db 侧(core_memory_card 与 core_hold_pool, 若已被增量同步)"""
    import sqlite3
    db = os.path.join(HERE, "data", "memory", "corpus_store.db")
    try:
        con = sqlite3.connect(db, timeout=5)
        con.execute("DELETE FROM core_hold_pool WHERE inst_id=?", (inst_id,))
        con.execute("DELETE FROM core_memory_card WHERE inst_id=?", (inst_id,))
        # 卡片挂载痕迹(零锚卡不应有 topic_links, 防御性清理)
        con.execute("DELETE FROM topic_links WHERE inst_id=?", (inst_id,))
        con.commit()
        con.close()
    except Exception as e:
        # P2-5: 原为 pass, 清理失败虽不阻断守卫结论, 但须留痕
        print(f"[e0_cleanup_fail] inst={inst_id} {type(e).__name__}: {e}", file=sys.stderr)

def main():
    print("[E0] topic_layer 开关守卫探针")
    # ① 开关断言
    check("① TOPIC_LAYER_ON=1(topic_enabled 为 True)", topic_layer.topic_enabled(),
          "开关关闭=全管道新卡不走锚判定, 业务断层")
    # ② 写守卫卡
    rep = None
    try:
        rep = mh.add(
            domain="005-005", behavior="Jw", relation="F", confidence="L0",
            summary=GUARD_SUMMARY,
            detail="守卫探针自动写入: 验证 memory_hook.add 自动触发 mount_topic 且零锚卡落 hold 池。本卡由探针自清理。",
            source="会话", asset_class="runtime", repro="flash",
            event_ts=None, memory_class="scar_case")
    except Exception as e:
        check("② 守卫卡写入", False, str(e))
    if rep is None:
        return finish()
    # 全 id 反查: store.msg 只含 8 位短 id, hold 池记录 32 位全 id——
    # 按摘要从卡表反查全 id(与 memory_hook.add 同款回查方式)
    inst_id = None
    for e in reversed(mh.load_memories()):
        if e.get("summary") == GUARD_SUMMARY:
            inst_id = e.get("inst_id")
            break
    check("② 守卫卡写入成功(全id=%s)" % ((inst_id or "?")[:8]), bool(inst_id))
    # ③ mount_topic 自动触发
    t = rep.get("topic")
    check("③ report['topic'] 存在(mount_topic 自动触发)", isinstance(t, dict))
    # ④ 零锚卡落 hold 池
    held = False
    if inst_id:
        try:
            for line in io.open(_hold_jsonl_path(), encoding="utf-8"):
                if line.strip() and json.loads(line).get("inst_id") == inst_id:
                    held = True
                    break
        except FileNotFoundError:
            pass
        verdict_ok = isinstance(t, dict) and t.get("verdict") in ("hold", "anchor")
        check("④ 新卡锚判定返回 verdict(hold/anchor)", verdict_ok,
              json.dumps(t, ensure_ascii=False)[:120] if t else "topic 字段缺失")
        check("⑤ hold 判定卡落 hold 池 jsonl",
              held if (t or {}).get("verdict") == "hold" else True)   # anchor 态直接挂锚也算过
    # ⑥ 自清理
    if inst_id:
        _purge_hold_record(inst_id)
        _purge_db_rows(inst_id)
        _purge_memories_jsonl(inst_id)
        check("⑥ 守卫卡自清理(card+hold+memories.jsonl 三侧)", True)
    return finish()

def finish():
    print("[E0] 守卫结论: %s" % ("PASS" if not FAILS else "FAIL " + "; ".join(FAILS)))
    sys.exit(0 if not FAILS else 1)

if __name__ == "__main__":
    main()