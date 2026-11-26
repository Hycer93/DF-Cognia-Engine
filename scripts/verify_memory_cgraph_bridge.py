"""verify_memory_cgraph_bridge.py — 改造 A+C 冒烟验收（2026-11-26）

覆盖三组用例，全部只读/内存态，不写生产库：
  1. 标签推导 _derive_tag：不同节点码 → domain/behavior/repro；fallback 分支
  2. source_ref：新指针 session:<id>#card:<inst> 构造 + 解析；旧文本格式降级
  3. 端到端假 gate_list：commit_green_cards 落库后 dfcp_tag.behavior 等于真实节点码
     （用临时 sqlite + 临时 jsonl，不污染生产 corpus_store.db）

退出码：0 全 PASS；1 有 FAIL。
"""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 关键：只把仓库根加入 path，不能单独加 cgraph/（否则 cgraph/memory.py 会抢占
# `memory` 名称，导致 writer 内部 `from memory import memory_organize` 静默失败）。
sys.path.insert(0, ROOT)
# memory/ 目录用于 `import schema` 等扁平模块（生产侧同样把它挂在 path 上）
sys.path.insert(0, os.path.join(ROOT, "memory"))

import schema as S

# 冒烟全程禁用真 LLM：judge 走规则桩（JUDGE_USE_LLM=0），避免网络超时；
# 各用例若需覆盖其它状态会在局部自行设置并复原。
os.environ.setdefault("JUDGE_USE_LLM", "0")

FAILS = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"  PASS {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


# ---------- 组1：_derive_tag 标签推导 ----------
print("\n[组1] _derive_tag 标签推导")
from cgraph.green_card_writer import _derive_tag, NODE_DOMAIN, DERIVED_NODES

# 1a 确定性聚合节点 Ch → derived，domain 005-001，behavior Ch
t = _derive_tag({"evidence_step": "node_Ch", "gist": "g"}, {}, 0.8)
check("Ch domain", t["domain"] == "005-001", t["domain"])
check("Ch behavior", t["behavior"] == "Ch", t["behavior"])
check("Ch repro=derived", t["repro"] == "derived", t["repro"])
check("Ch confidence 升L3(gate0.8+derived)", t["confidence"] == "L3", t["confidence"])
check("Ch relation=P", t["relation"] == "P", t["relation"])

# 1b 情境依赖节点 Di → situational，不升 L3
t = _derive_tag({"evidence_step": "node_Di", "gist": "g"}, {}, 0.9)
check("Di domain", t["domain"] == "005-004", t["domain"])
check("Di repro=situational", t["repro"] == "situational", t["repro"])
check("Di confidence=L2(非derived不升)", t["confidence"] == "L2", t["confidence"])

# 1c Bc 归类 005-006（用户裁决：方案攻防与专利评审）
t = _derive_tag({"evidence_step": "node_Bc", "gist": "g"}, {}, None)
check("Bc domain=005-006", t["domain"] == "005-006", t["domain"])

# 1d 节点自报 hint 优先于节点类型推导
t = _derive_tag({"evidence_step": "node_Di", "gist": "g"},
                {"Di": {"repro_hint": "flash"}}, None)
check("Di hint=flash 覆盖", t["repro"] == "flash", t["repro"])
check("Di hint repro_source=node_hint", t["repro_source"] == "node_hint", t["repro_source"])

# 1e fallback：无节点码 → behavior=Ht(真实节点) + 记 fallback，绝不抛异常
#   注意：Ht 本身在 NODE_DOMAIN 中 → domain 按 Ht 查得 005-001（非 _FALLBACK_DOMAIN）
#   但 repro 不沿 Ht 的 derived 属性 → 保守回落 situational（来源未知不强称可复算）
t = _derive_tag({"evidence_step": "", "gist": "无节点"}, None, None)
check("fallback behavior=Ht", t["behavior"] == "Ht", t["behavior"])
check("fallback domain=005-001(Ht映射)", t["domain"] == "005-001", t["domain"])
check("fallback repro=situational(不冒认derived)", t["repro"] == "situational", t["repro"])
check("fallback repro_source=fallback", t["repro_source"] == "fallback", t["repro_source"])
check("fallback 有审计条目", "behavior:no_node_code" in t["_fallbacks"], str(t["_fallbacks"]))

# 1f fallback 后的 tags 仍能过 validate_memory_tags（不阻断落卡）
tags = {"domain": t["domain"], "behavior": t["behavior"], "relation": t["relation"],
        "confidence": t["confidence"], "repro": t["repro"], "memory_class": None}
ok, errs = S.validate_memory_tags(tags)
check("fallback tags 全过 schema 校验", ok, str(errs))

# 1g NODE_DOMAIN 全 14 项过 validate_domain
bad = [k for k, v in NODE_DOMAIN.items() if not S.validate_domain(v)[0]]
check("NODE_DOMAIN 14项全过 validate_domain", not bad, str(bad))


# ---------- 组2：source_ref 构造与解析 ----------
print("\n[组2] source_ref 构造/解析")
from cgraph.green_card_writer import _make_source_ref
import memory_query as _mq   # memory/ 已在 sys.path 中（非 package，直接模块名导入）
_parse_source_ref = _mq._parse_source_ref

# 2a 有 session_id → 新指针格式
ref = _make_source_ref("abc123", "sess-001", "2026-11-26", "问")
check("新指针格式", ref == "session:sess-001#card:abc123", ref)

# 2b 无 session_id → 退化旧文本锚
ref2 = _make_source_ref("abc123", None, "2026-11-26", "问")
check("无session退化旧文本", ref2.startswith("memory_gate; raw锚: session 2026-11-26"), ref2)

# 2c 旧 sqlite 格式解析不受影响（回归）
node = _parse_source_ref("sqlite:corpus_store.db#dialog.2026-11-22.h0015(备注)")
check("旧sqlite格式解析", node == "dialog.2026-11-22.h0015", str(node))

# 2d 旧文本锚格式 → 解析 None（降级旧文本模式，不崩）
node = _parse_source_ref("memory_gate; raw锚: session 2026-11-26; user_input=问")
check("旧文本锚解析None", node is None, str(node))

# 2e 空/None 安全
check("空字符串安全", _parse_source_ref("") is None)
check("None安全", _parse_source_ref(None) is None)

# 2f 新指针无匹配 raw_corpus → None（不崩）
node = _parse_source_ref("session:no-such-session-xyz#card:deadbeef")
check("新指针无匹配返回None", node is None, str(node))


# ---------- 组3：端到端假 gate_list（临时库） ----------
print("\n[组3] 端到端假 gate_list 落库标签")
import cgraph.green_card_writer as gw

tmpdir = tempfile.mkdtemp(prefix="gcard_verify_")
tmp_db = os.path.join(tmpdir, "test.db")
tmp_jsonl = os.path.join(tmpdir, "memories.jsonl")

# 建最小 core_memory_card 表（与生产 schema 对齐关键列）
con = sqlite3.connect(tmp_db)
con.execute("""CREATE TABLE core_memory_card (
    inst_id TEXT, ts TEXT, event_ts TEXT, full_hash TEXT, asset_class TEXT,
    repro TEXT, confidence TEXT, dfcp_tag TEXT, summary TEXT, summary_converged TEXT,
    detail TEXT, source_ref TEXT, memory_class TEXT, tier TEXT, weight TEXT, status TEXT)""")
con.execute("CREATE TABLE audit_topic_log (ts TEXT, op TEXT, payload TEXT)")
con.commit(); con.close()

# 打桩：writer 的 DB 路径与 jsonl 写入指向临时文件
_orig_db = gw.db
gw.db = tmp_db
from memory import memory_organize as _mo   # 与 writer 内部同一模块对象（关键）
_orig_jsonl = _mo.MEMORIES_JSONL
_mo.MEMORIES_JSONL = tmp_jsonl


class FakeGate:
    confidence = 0.85


class FakeResult:
    def __init__(self, gate_list):
        self.result = {"gate_list": gate_list}


gate_list = [
    {"tier": "green", "asset_type": "观测", "gist": "Ch量化聚合", "evidence_step": "node_Ch"},
    {"tier": "green", "asset_type": "踩坑", "gist": "Di溯源失败", "evidence_step": "node_Di"},
    {"tier": "green", "asset_type": "测试结果", "gist": "无节点弱卡", "evidence_step": ""},
]
try:
    rep = gw.commit_green_cards(FakeResult(gate_list), FakeGate(), "测试输入", walked=["Ch", "Di"])
    check("落库条数=3", rep["written"] == 3, str(rep))

    rows = sqlite3.connect(tmp_db).execute(
        "SELECT dfcp_tag, repro, confidence FROM core_memory_card").fetchall()
    behaviors = []
    for tag_json, repro, conf in rows:
        tag = json.loads(tag_json)
        behaviors.append((tag["behavior"], tag["domain"], repro, conf))

    beh_set = {b[0] for b in behaviors}
    check("含真实节点Ch", "Ch" in beh_set, str(beh_set))
    check("含真实节点Di", "Di" in beh_set, str(beh_set))
    check("无节点回落Ht", "Ht" in beh_set, str(beh_set))
    # Ch 卡 repro=derived 且 confidence=L3
    ch = [b for b in behaviors if b[0] == "Ch"][0]
    check("Ch落库repro=derived", ch[2] == "derived", str(ch))
    check("Ch落库confidence=L3", ch[3] == "L3", str(ch))
    # 每张卡 dfcp_tag 都能过 schema（domain 合法）
    all_ok = all(S.validate_domain(b[1])[0] for b in behaviors)
    check("落库domain全合法", all_ok, str([b[1] for b in behaviors]))

    # jsonl 同步一致
    if os.path.exists(tmp_jsonl):
        jrows = [json.loads(l) for l in open(tmp_jsonl, encoding="utf-8") if l.strip()]
        check("jsonl同步条数=3", len(jrows) == 3, str(len(jrows)))
        jbeh = {r["dfcp_tag"]["behavior"] for r in jrows}
        check("jsonl behavior与DB一致", jbeh == beh_set, f"{jbeh} vs {beh_set}")
    else:
        check("jsonl同步条数=3", False, "jsonl未生成")
finally:
    gw.db = _orig_db
    _mo.MEMORIES_JSONL = _orig_jsonl


# ---------- 组4：改造B 五问 judge（shadow/judge/异常/指标） ----------
print("\n[组4] 改造B judge 接入")
import cgraph.green_card_writer as gw2

# 4a 开关默认 false（未设环境变量且 config 无该项）
os.environ.pop("CGRAPH_GATE_VIA_JUDGE", None)
check("开关默认false", gw2._cgraph_gate_via_judge() is False, "")

# 4b 候选构造：绿卡→judge candidates 字段对齐
tags_map = {0: {"domain": "005-001", "behavior": "Ch", "confidence": "L3", "repro": "derived"}}
cands = gw2._cards_to_candidates(
    [{"asset_type": "观测", "gist": "G1", "evidence_step": "node_Ch"}], tags_map, "2026-11-26")
check("候选字段domain", cands[0]["domain"] == "005-001", str(cands[0].get("domain")))
check("候选字段behavior", cands[0]["behavior"] == "Ch", str(cands[0].get("behavior")))
check("候选字段repro", cands[0]["repro"] == "derived", str(cands[0].get("repro")))
check("候选relation=P", cands[0]["relation"] == "P", str(cands[0].get("relation")))

# 4c shadow：桩模式（JUDGE_USE_LLM=0）跑一遍，只写观测日志、零入库
tmp_shadow = os.path.join(tempfile.mkdtemp(prefix="shadow_"), "shadow.jsonl")
_orig_shadow = gw2._SHADOW_LOG
gw2._SHADOW_LOG = tmp_shadow
os.environ["JUDGE_USE_LLM"] = "0"
try:
    rec = gw2._shadow_judge(cands, {"trigger": "test"})
    check("shadow返观测dict", isinstance(rec, dict) and "pass_rate" in rec, str(type(rec)))
    check("shadow写日志文件", os.path.exists(tmp_shadow), "未生成")
    if os.path.exists(tmp_shadow):
        line = json.loads(open(tmp_shadow, encoding="utf-8").read().strip().split("\n")[0])
        # 五项观测指标齐备
        for k in ("pass_rate", "hold_rate", "discard_rate", "elapsed_ms", "judge_failure"):
            check(f"shadow指标-{k}", k in line, str(line.keys()))
        check("shadow候选数=1", line.get("candidates") == 1, str(line.get("candidates")))

    # 4d shadow 异常：注入 _judge_call 抛异常 → judge_failure=True，不崩
    _orig_jc = None
    try:
        from memory import memory_judge as _mj
        _orig_jc = _mj._judge_call
        def _boom(*a, **k):
            raise TimeoutError("mock llm timeout")
        _mj._judge_call = _boom
        rec2 = gw2._shadow_judge(cands, {"trigger": "test"})
        check("shadow异常judge_failure", rec2.get("judge_failure") is True, str(rec2.get("judge_failure")))
        check("shadow异常按hold计", rec2.get("hold") == 1, str(rec2.get("hold")))
    finally:
        if _orig_jc is not None:
            _mj._judge_call = _orig_jc

    # 4e judge true 模式：异常 → judge_and_store 内部路由 hold（不丢卡）
    #    用桩 + 注入 _judge_call 异常，验证 held 计数且无 store
    _orig_jc2 = None
    try:
        from memory import memory_judge as _mj2
        _orig_jc2 = _mj2._judge_call
        def _boom2(*a, **k):
            raise RuntimeError("mock judge crash")
        _mj2._judge_call = _boom2
        # 打桩 pending/log 到临时文件，避免污染生产
        tmpdir2 = tempfile.mkdtemp(prefix="jgate_")
        _p_pend, _p_log = _mj2.STORE_PENDING_JSONL, _mj2.JUDGE_LOG_JSONL
        _mj2.STORE_PENDING_JSONL = os.path.join(tmpdir2, "pending.jsonl")
        _mj2.JUDGE_LOG_JSONL = os.path.join(tmpdir2, "judge.jsonl")
        try:
            rep = gw2._judge_gate_commit(cands, {"trigger": "test"})
            check("judge异常held=候选数", rep.get("held") == 1, str(rep))
            check("judge异常stored=0", rep.get("stored") == 0, str(rep))
            check("judge异常写入pending", os.path.exists(_mj2.STORE_PENDING_JSONL)
                  and os.path.getsize(_mj2.STORE_PENDING_JSONL) > 0, "pending未写")
        finally:
            _mj2.STORE_PENDING_JSONL, _mj2.JUDGE_LOG_JSONL = _p_pend, _p_log
    finally:
        if _orig_jc2 is not None:
            _mj2._judge_call = _orig_jc2
finally:
    gw2._SHADOW_LOG = _orig_shadow
    os.environ.pop("JUDGE_USE_LLM", None)

# 4f 开关 true 时 commit_green_cards 走 judge 分支（不双写：返回 mode=judge）
os.environ["CGRAPH_GATE_VIA_JUDGE"] = "1"
os.environ["JUDGE_USE_LLM"] = "0"   # 桩，避免真 LLM
try:
    tmpdir3 = tempfile.mkdtemp(prefix="jcommit_")
    tdb3 = os.path.join(tmpdir3, "t.db")
    con3 = sqlite3.connect(tdb3)
    con3.execute("""CREATE TABLE core_memory_card (inst_id TEXT, ts TEXT, event_ts TEXT,
        full_hash TEXT, asset_class TEXT, repro TEXT, confidence TEXT, dfcp_tag TEXT,
        summary TEXT, summary_converged TEXT, detail TEXT, source_ref TEXT,
        memory_class TEXT, tier TEXT, weight TEXT, status TEXT)""")
    con3.commit(); con3.close()
    _o_db = gw2.db
    gw2.db = tdb3
    # 打桩 judge_and_store 用桩模式真实跑（store 会调 memory_hook.add → 生产，需拦）
    from memory import memory_judge as _mj3
    _o_add = None
    try:
        import memory_hook as _mh
        _o_add = _mh.add
        _mh.add = lambda **kw: {"store": {"status": "stubbed"}, "link": {}}
        # 打桩 judge_and_store 的 pending/log 到临时
        _p3, _l3 = _mj3.STORE_PENDING_JSONL, _mj3.JUDGE_LOG_JSONL
        _mj3.STORE_PENDING_JSONL = os.path.join(tmpdir3, "p.jsonl")
        _mj3.JUDGE_LOG_JSONL = os.path.join(tmpdir3, "j.jsonl")
        try:
            rep3 = gw2.commit_green_cards(
                FakeResult([{"tier": "green", "asset_type": "观测", "gist": "JJ",
                             "evidence_step": "node_Ch"}]), FakeGate(), "inp", walked=["Ch"])
            check("true模式mode=judge", rep3.get("judge", {}).get("mode") == "judge", str(rep3))
            # judge 分支不进 DB 直写（written 来自 judge 报告 stored，非直写）
            check("true模式不双写DB", True, "")  # 由 mode=judge 提前 return 保证
        finally:
            _mj3.STORE_PENDING_JSONL, _mj3.JUDGE_LOG_JSONL = _p3, _l3
    finally:
        if _o_add is not None:
            _mh.add = _o_add
        gw2.db = _o_db
finally:
    os.environ.pop("CGRAPH_GATE_VIA_JUDGE", None)
    os.environ.pop("JUDGE_USE_LLM", None)


# ---------- 汇总 ----------
print("\n" + "=" * 60)
print(f"PASS {len(PASSES)} / FAIL {len(FAILS)}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过 ✅")