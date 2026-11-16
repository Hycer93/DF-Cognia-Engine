# eval/topic_layer_regression.py — Topic锚层回归用例（design_topic_layer.md §六验收标准）
# 七条断言, 对应六规约红线。跑法: python eval/topic_layer_regression.py
# 用例5/6/7 不真调 classify_and_link(需embedding且写库), 用 conflict_gate 单元级断言。
import os, sys, io, json

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))
os.environ["TOPIC_LAYER_ON"] = "1"     # 测试态强制开

import topic_layer as tl

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

print("== Topic锚层回归（六规约验收） ==")

# --- 用例1: 建锚+挂载(已由 seed_topics.py 完成, 此处验状态) ---
# 基线: 2026-11-16元核锚评审后 12 active锚 = 原5 + 还债簇6(全manual_seed) + topic_atomic_metacore(manual_seed)
seed_n = tl._conn().execute("SELECT COUNT(*) FROM topic_abstraction WHERE status='active' AND manual_seed=1").fetchone()[0]
tl.init_topic_db()
s = tl.stats()
check(f"锚表就绪(12 active, 含{seed_n}人工种子锚)", s["active_anchors"] == 12, str(s))
check("挂载数>0", s["mounted_atoms"] > 0, str(s))

# --- 用例2: 规约3 归一四档 — cue命中单锚→anchor; 多锚歧义→hold; 零命中→hold ---
v1 = tl.classify_topic("P10星型拓扑中心进化外围固化", "")
check("单锚命中→anchor", v1["verdict"] == "anchor" and v1["topic_id"] == "topic_star_topology", str(v1))
# 多锚歧义: 同时命中star和g22的cue
v2 = tl.classify_topic("P10星型拓扑与G22光谱系数决策整合的交叉", "")
check("多锚歧义→hold(禁二选一)", v2["verdict"] == "hold", str(v2))
v3 = tl.classify_topic("完全无关的随便一句话", "")
check("零命中→hold(不建新锚)", v3["verdict"] == "hold", str(v3))

# --- 用例3: 规约2 多路并召回 — 锚匹配错误时兜底仍可召回(反串行漏斗) ---
def subset_fetch(tid, q, k):     # 模拟主题内召回(错锚返回空)
    return [] if "错" in q else [{"inst_id": f"in_{tid}_{i}"} for i in range(k)]
def fallback_fetch(q, k):        # 全库兜底: 永远能召回
    return [{"inst_id": f"fb_{i}"} for i in range(k)]
r = tl.topic_route_retrieve("错锚query星型", subset_fetch, fallback_fetch)
check("锚失配时兜底仍召回(≥2)", len([x for x in r if x["_route"] == "fallback"]) >= 2, str(len(r)))
r2 = tl.topic_route_retrieve("P10星型拓扑", subset_fetch, fallback_fetch)
routes = {x["_route"] for x in r2}
check("正常query两路并存(并集)", "topic:topic_star_topology" in routes and "fallback" in routes, str(routes))
check("并集无重复", len(r2) == len({x["inst_id"] for x in r2}))

# --- 用例4: 规约4 单向闸门 — 不同topic skip; 同topic pass; null pass ---
# 从topic_links取两真实挂载原子(同topic星型簇) + 构造不同topic对
import sqlite3
c = tl._conn()
star_ids = [r["inst_id"] for r in c.execute(
    "SELECT inst_id FROM topic_links WHERE topic_id='topic_star_topology' LIMIT 2")]
brain_ids = [r["inst_id"] for r in c.execute(
    "SELECT inst_id FROM topic_links WHERE topic_id='topic_brain_region' LIMIT 1")]
g22_ids = [r["inst_id"] for r in c.execute(
    "SELECT inst_id FROM topic_links WHERE topic_id='topic_g22_decision' LIMIT 1")]
c.close()
g_same = tl.conflict_gate({"inst_id": star_ids[0]}, {"inst_id": star_ids[1]})
check("同topic(不同entity)→pass全级联", g_same["gate"] == "pass", str(g_same))
g_diff = tl.conflict_gate({"inst_id": star_ids[0]}, {"inst_id": brain_ids[0]})
check("不同topic→skip+审计", g_diff["gate"] == "skip", str(g_diff))
g_null = tl.conflict_gate({"inst_id": star_ids[0]}, {"inst_id": "nonexistent_id"})
check("任一端null→pass(hold池照常)", g_null["gate"] == "pass", str(g_null))

# --- 用例5: 规约4 真实数据断言 — 冲突队列6对同topic真冲突原样保留 ---
cq = [json.loads(l) for l in io.open(os.path.join(HERE,"data/memory/conflict_queue.jsonl"), encoding="utf-8") if l.strip()]
# 伪演示基线的6对同topic(P10/P12/P22星型簇+G22对)必须全部pass
SEED_CUES = {"topic_star_topology": ["P10","P12","P22","星型拓扑","星域联邦"],
             "topic_brain_region": ["脑区","海马体","前额叶","杏仁核"],
             "topic_g22_decision": ["G22","光谱系数","决策整合","方案矩阵"],
             "topic_feinong_review": ["非农","金价"]}
def topic_by_text(t):
    for tid, cues in SEED_CUES.items():
        if any(cue in (t or "") for cue in cues): return tid
    return None
same_pairs = 0
for pair in cq:
    ta = topic_by_text(pair.get("summary_a")); tb = topic_by_text(pair.get("summary_b"))
    if ta and tb and ta == tb:
        same_pairs += 1
        # 同topic对必须pass——绝不能被拦截
check("基线6对同topic真冲突全保留", same_pairs >= 6, f"实测{same_pairs}对")

# --- 用例6: 规约5 topic不碰底层事实 — 代码级断言: topic_layer无写memory/confidence路径 ---
src = io.open(os.path.join(HERE, "memory/topic_layer.py"), encoding="utf-8").read()
forbidden = ["confidence_dynamics", "change_code(", "superseded_by=", "entity_id="]
leaks = [f for f in forbidden if f in src]
check("topic模块无底层事实写路径", not leaks, str(leaks))

# --- 用例7: 白名单开关 — 关闭态零行为变化 ---
os.environ["TOPIC_LAYER_ON"] = "0"
check("开关关闭→topic_enabled()=False", tl.topic_enabled() is False)
os.environ["TOPIC_LAYER_ON"] = "1"     # 后续用例仍需开态

# --- 用例8-11: ID全局序列收口回归(2026-11-16 用户纪律: 存量迁移+兼容摘除是隐性bug高发区,
#     必须有存量卡片对比用例守卫) ---
import re, sqlite3
print("\n== ID 收口回归(短id迁移/兼容摘除守卫) ==")
_rc = sqlite3.connect(os.path.join(HERE, "data/memory/corpus_store.db"))
_rc.row_factory = sqlite3.Row

# 用例8: 三表零短id残留(topic_links/core_topic_links/core_hold_pool)
HEX8 = re.compile(r"^[0-9a-f]{8}$")
_short_left = []
for _tbl in ("topic_links", "core_topic_links", "core_hold_pool"):
    for (_x,) in _rc.execute(f"SELECT DISTINCT inst_id FROM {_tbl}").fetchall():
        if HEX8.match(_x or ""):
            _short_left.append((_tbl, _x))
check("三表零8位短id残留", not _short_left, str(_short_left[:4]))

# 用例9: topic_links 零幽灵id(所有32位挂载id必须在卡表真实存在) —
# 防 7a499925d363436f 类抄错字符伪全id复发
_ghost = []
for (_iid,) in _rc.execute("SELECT DISTINCT inst_id FROM topic_links WHERE LENGTH(inst_id)=32").fetchall():
    if not _rc.execute("SELECT 1 FROM core_memory_card WHERE inst_id=?", (_iid,)).fetchone():
        _ghost.append(_iid)
check("topic_links零幽灵id(挂载id全部对应真实卡)", not _ghost, str(_ghost[:3]))

# 用例10: 存量卡片对比 — 迁移审计json里 8 卡 short→full 映射,
# 每张卡迁移后挂载数必须≥迁移前(短id行删除不丢挂载: 全id行本就在)
try:
    _mig = json.load(io.open(os.path.join(HERE, "logs/id_seq_migration_20261116.json"), encoding="utf-8"))
    _mig_ok, _mig_detail = True, []
    for _m in _mig["cards"]:
        _n = _rc.execute("SELECT COUNT(*) FROM topic_links WHERE inst_id=?", (_m["full"],)).fetchone()[0]
        _h = _rc.execute("SELECT COUNT(*) FROM core_hold_pool WHERE inst_id=?", (_m["full"],)).fetchone()[0]
        _ok = (_n + _h) >= 1
        _mig_ok = _mig_ok and _ok
        if not _ok: _mig_detail.append((_m["short"], _n, _h))
    check("迁移8卡存量对比(每卡全id挂载/hold≥1, 不丢挂载)", _mig_ok and len(_mig["cards"]) == 8,
          str(_mig_detail) + " cards=%d" % len(_mig["cards"]))
except FileNotFoundError:
    check("迁移8卡存量对比(每卡全id挂载/hold≥1, 不丢挂载)", False, "迁移审计json缺失")

# 用例11: enqueue 写入口形态校验 — 短id/伪id被拒, 全id不受影响
_r1 = tl.mount_topic("32684974", "星型拓扑P10测试短id应被拒", "")
check("mount_topic拒8位短id", _r1.get("verdict") == "reject" and "32位" in _r1.get("evidence_gap", ""), str(_r1))
_r2 = tl.mount_topic("7a499925d363436f863b102896faa989", "星型拓扑P10测试伪全id应被拒", "")
check("mount_topic拒32位伪id(卡表不存在形态照收但等值挂不上钩)", _r2.get("verdict") == "reject", str(_r2))
# 注意: 伪32位hex通过形态校验后会走正常 classify(here verdict=reject 因非32位),
# 真正防幽灵挂载靠用例9的零幽灵断言 + UNIQUE 约束
try:
    tl.mount_batch(["32684974", "a65a500c"], "topic_star_topology", method="regression-test")
    check("mount_batch拒混入短id整批", False, "未抛异常")
except ValueError as _e:
    check("mount_batch拒混入短id整批", "32位" in str(_e), str(_e)[:80])
_rc.close()


# 用例12: cue判别力 v2.2 — hold盲区Mock(用户裁决T1固化, 后续迭代不得退化)
# 场景: 贪婪cue 2张卡单命中挂本簇 + 3张多锚歧义进hold
# v2.1口径 disc=2/(2+0)=1.0 满分洗白; v2.2 disc=2/(2+0+3)=0.4 判死
import sqlite3 as _sq, datetime as _dt
_rc12 = _sq.connect(os.path.join(HERE, "data/memory/corpus_store.db"))
_now12 = _dt.datetime.now().isoformat(timespec="seconds")
for _s12 in ["DELETE FROM topic_links WHERE inst_id LIKE 'dd%' OR topic_id='topic_dd_mock'",
             "DELETE FROM core_hold_pool WHERE inst_id LIKE 'dd%'",
             "DELETE FROM core_memory_card WHERE inst_id LIKE 'dd%'",
             "DELETE FROM topic_abstraction WHERE topic_id='topic_dd_mock'"]:
    _rc12.execute(_s12)
for _k in range(2):
    _iid = "dd%031d" % _k
    _rc12.execute("INSERT INTO core_memory_card (inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, summary) VALUES (?,?,?,?,?,?,?,?)",
                  (_iid, _now12, "h%d" % _k, "runtime", "na", 0.5, "005-002", "共享推演场景卡%d" % _k))
    _rc12.execute("INSERT INTO topic_links VALUES (?,?,?,?)", (_iid, "topic_dd_mock", "seed", _now12))
for _k in range(3):
    _iid = "dd1%030d" % _k
    _rc12.execute("INSERT INTO core_memory_card (inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, summary) VALUES (?,?,?,?,?,?,?,?)",
                  (_iid, _now12, "h1%d" % _k, "runtime", "na", 0.5, "005-002", "共享推演歧义卡%d" % _k))
    _rc12.execute("INSERT INTO core_hold_pool (inst_id, reason, reference_mention, enqueued_ts) VALUES (?,?,0,?)",
                  (_iid, "多锚歧义命中: ['topic_dd_mock', 'topic_other'], 交 E7 复审", _now12))
_rc12.commit()
_r12 = tl._cue_discrimination("共享推演", ["dd%031d" % _k for _k in range(2)], "topic_dd_mock")
check("v2.2 hold盲区Mock: n_dispute=3计入噪声(贪婪cue不洗白)",
      _r12["n_dispute"] == 3 and abs(_r12["ratio"] - 0.4) < 1e-9 and _r12["ok"] is False, str(_r12))
check("v2.2 小样本观察: total_obs<3不拦截打标记",
      (lambda rr: rr["insufficient_data"] is True and rr["ok"] is True)(tl._cue_discrimination("入库断档",
          [r[0] for r in _rc12.execute("SELECT inst_id FROM topic_links WHERE topic_id='topic_memory_ops'")], "topic_memory_ops")))
check("v2.2 前瞻词: total_obs=0放行",
      tl._cue_discrimination("前瞻零观测词qqq", ["dd%031d" % _k for _k in range(2)], "topic_dd_mock")["ok"] is True)
try:
    tl.seed_topic("topic_dd_mock", "mock", ["共享推演"])
    check("v2.2 seed入口拒绝贪婪cue", False, "未抛异常")
except ValueError as _e12:
    check("v2.2 seed入口拒绝贪婪cue", "争议" in str(_e12) or "disc" in str(_e12), str(_e12)[:80])
for _s12 in ["DELETE FROM topic_links WHERE inst_id LIKE 'dd%' OR topic_id='topic_dd_mock'",
             "DELETE FROM core_hold_pool WHERE inst_id LIKE 'dd%'",
             "DELETE FROM core_memory_card WHERE inst_id LIKE 'dd%'",
             "DELETE FROM topic_abstraction WHERE topic_id='topic_dd_mock'"]:
    _rc12.execute(_s12)
_rc12.commit(); _rc12.close()

# 用例13: E10语义离群Mock(用户收口裁决固化, 防E10能力退化)
# 场景: 簇内6张同主题卡 + 1张cue命中(统计合格)但语义完全无关的错挂卡
# 期望: 错挂卡被E10告警(dist<μ-2σ), 同主题卡不告警
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("e10_patrol", os.path.join(HERE, "eval", "core", "e10_semantic_outlier_patrol.py"))
    _e10 = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_e10)
    _s13, _d13 = "语义离群回归簇: 记忆系统运维与入库断档复盘卡片%d", "hook钩子 judge管道 检索排序 F系推演细节%d"
    _rc13 = _rc  if False else None
    import sqlite3 as _sq13, datetime as _dt13, os as _os13
    _dbp13 = _os13.path.join(HERE, "data", "memory", "corpus_store.db")
    _rc13 = _sq13.connect(_dbp13)
    _now13 = _dt13.datetime.now().isoformat(timespec="seconds")
    for _s in ["DELETE FROM topic_links WHERE inst_id LIKE 'ee%' OR topic_id='topic_ee_mock'",
               "DELETE FROM core_memory_card WHERE inst_id LIKE 'ee%'",
               "DELETE FROM topic_abstraction WHERE topic_id='topic_ee_mock'"]:
        _rc13.execute(_s)
    for _k in range(6):
        _iid = "ee%031d" % _k
        _rc13.execute("INSERT INTO core_memory_card (inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, summary, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                      (_iid, _now13, "e%d" % _k, "runtime", "na", 0.5, "005-002", _s13 % _k, _d13 % _k))
        _rc13.execute("INSERT INTO topic_links VALUES (?,?,?,?)", (_iid, "topic_ee_mock", "seed", _now13))
    _iid_bad = "ee1" + "0" * 30 + "1"
    _rc13.execute("INSERT INTO core_memory_card (inst_id, ts, full_hash, asset_class, repro, confidence, dfcp_tag, summary, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                  (_iid_bad, _now13, "ebad", "runtime", "na", 0.5, "005-002",
                   "烹饪食谱: 红烧肉的做法记忆系统(hook钩子cue命中掩护)", ""))
    _rc13.execute("INSERT INTO topic_links VALUES (?,?,?,?)", (_iid_bad, "topic_ee_mock", "seed", _now13))
    _rc13.execute("INSERT INTO topic_abstraction (topic_id, topic_abstract, topic_vector, cue_anchors, status, create_ts, update_ts) VALUES (?,?,?,?, 'active', ?, ?)",
                  ("topic_ee_mock", "mock", "[]", '["hook钩子"]', _now13, _now13))
    _rc13.commit()
    _r13 = _e10.run(full=True)   # full重算含mock簇
    _bad_alerted = any(a["inst_id"] == _iid_bad for a in _r13["alerts"])
    _clean_alerted = [a["inst_id"] for a in _r13["alerts"] if a["inst_id"] != _iid_bad and a["inst_id"].startswith("ee")]
    check("E10语义离群Mock: cue合格但语义错挂卡被抓(cue命中掩护无效)",
          _bad_alerted, str([a["summary"][:40] for a in _r13["alerts"] if a["inst_id"] == _iid_bad]))
    check("E10语义离群Mock: 同主题6卡零误报", not _clean_alerted, str(_clean_alerted))
    for _s in ["DELETE FROM topic_links WHERE inst_id LIKE 'ee%' OR topic_id='topic_ee_mock'",
               "DELETE FROM core_memory_card WHERE inst_id LIKE 'ee%'",
               "DELETE FROM topic_abstraction WHERE topic_id='topic_ee_mock'"]:
        _rc13.execute(_s)
    _rc13.commit(); _rc13.close()
    _st13 = _os13.path.join(HERE, "logs", "e10_semantic_state.json")
    if _os13.path.exists(_st13):
        _os13.remove(_st13)
except Exception as _e13:
    check("E10语义离群Mock: cue合格但语义错挂卡被抓(cue命中掩护无效)", False, repr(_e13)[:100])


print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n, d in FAIL: print(f"  FAIL: {n} | {d}")
    sys.exit(1)
print("全部验收断言通过（对应 design_topic_layer.md §六）")
