"""attitude_regression.py — #11 回执态度元标签子通路回归 (2026-11-22)

覆盖 #11 方案A 的可验证性质:
  1. record_attitude 落库: approve/disapprove/neutral 三型 + 非法输入防御
  2. list_attitudes / summarize_attitudes 查询与汇总
  3. LLM 通路 _judge_call_llm 解析 attitude → _attitude 键(verdict 枚举不变)
  4. judge_and_store 端到端: LLM 带 attitude → 落附属表; verdict 仍 discard
  5. stub 通路零副作用: JUDGE_USE_LLM=0 → 无任何 attitude 记录
  6. 红线: 态度记录不改 core_memory_card 可信度/存活/数量, 不产新记忆

**全部临时沙箱, 不碰真实 data/memory 与主库。**
运行: python eval/audit/attitude_regression.py
退出码 0=全过 1=有失败。
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_HERE, "memory"))
sys.path.insert(0, _HERE)

_R = []


def _check(name, cond, detail=""):
    _R.append({"name": name, "ok": bool(cond)})
    print(("  ✅ " if cond else "  ❌ ") + name + (" | " + detail if detail and not cond else ""))


def _sandbox():
    tmp = tempfile.mkdtemp(prefix="attreg_")
    real_db = os.path.join(_HERE, "data", "memory", "corpus_store.db")
    real_mem = os.path.join(_HERE, "data", "memory", "memories.jsonl")
    shutil.copy(real_db, os.path.join(tmp, "corpus_store.db"))
    shutil.copy(real_mem, os.path.join(tmp, "memories.jsonl"))
    import corpus_store as cs
    import memory_organize as mo
    import memory_judge as mj
    import memory_attitude as ma
    orig = {"cs_db": cs.DB_PATH, "mo_mem": mo.MEM_DIR, "mo_jsonl": mo.MEMORIES_JSONL,
            "mj_data": mj.DATA_DIR, "mj_sp": mj.STORE_PENDING_JSONL,
            "mj_jl": mj.JUDGE_LOG_JSONL, "ma_att": ma.ATTITUDE_JSONL}
    cs.DB_PATH = os.path.join(tmp, "corpus_store.db")
    mo.MEM_DIR = tmp
    mo.MEMORIES_JSONL = os.path.join(tmp, "memories.jsonl")
    mj.DATA_DIR = tmp
    mj.STORE_PENDING_JSONL = os.path.join(tmp, "sp.jsonl")
    mj.JUDGE_LOG_JSONL = os.path.join(tmp, "jl.jsonl")
    ma.ATTITUDE_JSONL = os.path.join(tmp, "att.jsonl")

    def restore():
        cs.DB_PATH = orig["cs_db"]; mo.MEM_DIR = orig["mo_mem"]
        mo.MEMORIES_JSONL = orig["mo_jsonl"]; mj.DATA_DIR = orig["mj_data"]
        mj.STORE_PENDING_JSONL = orig["mj_sp"]; mj.JUDGE_LOG_JSONL = orig["mj_jl"]
        ma.ATTITUDE_JSONL = orig["ma_att"]
        shutil.rmtree(tmp, ignore_errors=True)

    return tmp, restore


def case_record_and_query():
    print("[1] record_attitude 落库 + 查询")
    tmp, restore = _sandbox()
    try:
        import memory_attitude as ma
        r1 = ma.record_attitude("approve", "card_aaa", "认可", "dialog.x.h001")
        r2 = ma.record_attitude("disapprove", "card_aaa", "不同意")
        r3 = ma.record_attitude("neutral", "card_bbb", "嗯")
        _check("三型落库成功", all([r1, r2, r3]))
        _check("非法类型返回 None", ma.record_attitude("maybe", "card_x") is None)
        _check("缺 target 返回 None", ma.record_attitude("approve", "") is None)
        _check("大小写归一", ma.record_attitude("APPROVE", "card_ccc")["attitude_type"] == "approve")
        _check("全部查询=4", len(ma.list_attitudes()) == 4, str(len(ma.list_attitudes())))
        _check("按 target 查 card_aaa=2", len(ma.list_attitudes(target_memory_id="card_aaa")) == 2)
        _check("按类型查 approve=2", len(ma.list_attitudes(attitude_type="approve")) == 2)
        s = ma.summarize_attitudes("card_aaa")
        _check("汇总计数", s["counts"] == {"approve": 1, "disapprove": 1, "neutral": 0}, str(s["counts"]))
        _check("汇总 total=2", s["total"] == 2)
    finally:
        restore()


def case_llm_parse_attitude():
    print("[2] LLM 解析 attitude (verdict 枚举不变)")
    import memory_judge as mj
    import cgraph.llm_codechat as L
    orig_llm = L.CodeChatLLM
    try:
        class FakeLLM:
            def __init__(self, **kw):
                pass

            def chat(self, msgs):
                return json.dumps({"verdicts": [{
                    "idx": 0, "verdict": "discard", "memory_class": None,
                    "conflict_suspect": False, "reasons": ["回执"], "evidence_gap": None,
                    "attitude": {"attitude_type": "approve", "target_memory_id": "iid_xyz",
                                 "comment": "好"}}]}, ensure_ascii=False)

        L.CodeChatLLM = FakeLLM
        out = mj._judge_call_llm([{"summary": "我认可这条", "detail": "足够长的细节内容测试用词啊"}],
                                 {"trigger": "hook", "entity_hint": ["iid_xyz"]})
        _check("_attitude 被提取", isinstance(out[0].get("_attitude"), dict))
        _check("_attitude 字段完整",
               out[0]["_attitude"].get("target_memory_id") == "iid_xyz"
               and out[0]["_attitude"].get("attitude_type") == "approve")
        _check("verdict 仍 discard", out[0]["verdict"] == "discard")
        # 无 attitude 的普通 verdict 不带 _attitude 键
        class FakeLLM2:
            def __init__(self, **kw):
                pass

            def chat(self, msgs):
                return json.dumps({"verdicts": [{
                    "idx": 0, "verdict": "store", "memory_class": "scar_case",
                    "conflict_suspect": False, "reasons": ["Q1-Q5"], "evidence_gap": None}]},
                    ensure_ascii=False)

        L.CodeChatLLM = FakeLLM2
        out2 = mj._judge_call_llm([{"summary": "正常新认知", "detail": "足够长的细节内容测试用词啊"}],
                                  {"trigger": "hook"})
        _check("无 attitude 时不带 _attitude 键", "_attitude" not in out2[0])
    finally:
        L.CodeChatLLM = orig_llm


def case_end_to_end_llm():
    print("[3] judge_and_store 端到端 (LLM带attitude→落附属表, verdict=discard)")
    tmp, restore = _sandbox()
    try:
        import memory_judge as mj
        import memory_attitude as ma
        import memory_organize as mo
        n_before = len(mo.load_memories())
        real_iid = mo.load_memories()[0]["inst_id"]

        def fake_judge_call(cands, ctx):
            return {0: {"verdict": "discard", "memory_class": None, "conflict_suspect": False,
                        "reasons": ["回执"], "evidence_gap": None, "_source": "llm",
                        "_attitude": {"attitude_type": "disapprove",
                                      "target_memory_id": real_iid, "comment": "不同意"}}}
        orig = mj._judge_call
        mj._judge_call = fake_judge_call
        try:
            r = mj.judge_and_store([{"summary": "我不同意这条", "detail": "", "source": "会话"}],
                                   context={"trigger": "hook", "session_id": "s1"})
        finally:
            mj._judge_call = orig
        _check("verdict 走 discard", r["discarded"] == 1 and r["stored"] == 0, str(r))
        atts = ma.list_attitudes(target_memory_id=real_iid)
        _check("态度落附属表", len(atts) == 1, str(len(atts)))
        _check("态度类型正确", atts[0]["attitude_type"] == "disapprove" if atts else False)
        _check("红线: 不产新记忆", len(mo.load_memories()) == n_before,
               "%d->%d" % (n_before, len(mo.load_memories())))
    finally:
        restore()


def case_stub_zero_sideeffect():
    print("[4] stub 通路零副作用")
    tmp, restore = _sandbox()
    try:
        import memory_judge as mj
        import memory_attitude as ma
        os.environ["JUDGE_USE_LLM"] = "0"
        try:
            r = mj.judge_and_store([{"summary": "我认可这条", "detail": "x" * 40,
                                     "memory_class": "scar_case"}], context={"trigger": "hook"})
        finally:
            os.environ.pop("JUDGE_USE_LLM", None)
        _check("stub 无 attitude 记录", len(ma.list_attitudes()) == 0, str(len(ma.list_attitudes())))
        _check("stub verdict 正常(不因attitude异常)", r["discarded"] + r["stored"] + r["held"] == 1, str(r))
    finally:
        restore()


def case_main_table_untouched():
    print("[5] 红线: 主表 core_memory_card 零改动")
    tmp, restore = _sandbox()
    try:
        import memory_judge as mj
        import memory_attitude as ma
        db = os.path.join(tmp, "corpus_store.db")
        c = sqlite3.connect(db)
        before = c.execute("SELECT COUNT(*), SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END)"
                           " FROM core_memory_card").fetchone()
        c.close()
        # 对一张真实卡写三条态度
        import memory_organize as mo
        iid = mo.load_memories()[0]["inst_id"]
        for t in ("approve", "disapprove", "neutral"):
            ma.record_attitude(t, iid, "测试")
        c = sqlite3.connect(db)
        after = c.execute("SELECT COUNT(*), SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END)"
                          " FROM core_memory_card").fetchone()
        c.close()
        _check("主表卡数不变", before[0] == after[0], "%s->%s" % (before[0], after[0]))
        _check("superseded_by 分布不变", before[1] == after[1])
        _check("态度记录已写附属(3条)", len(ma.list_attitudes(target_memory_id=iid)) == 3)
    finally:
        restore()


def main():
    case_record_and_query()
    case_llm_parse_attitude()
    case_end_to_end_llm()
    case_stub_zero_sideeffect()
    case_main_table_untouched()
    failed = [r for r in _R if not r["ok"]]
    print("\n[#11 态度子通路回归] %d/%d 通过" % (len(_R) - len(failed), len(_R)))
    out = os.path.join(_HERE, "logs", "attitude_regression.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(
        json.dumps({"total": len(_R), "passed": len(_R) - len(failed), "results": _R},
                   ensure_ascii=False, indent=1))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()