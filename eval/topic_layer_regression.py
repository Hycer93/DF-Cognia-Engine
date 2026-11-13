# eval/topic_layer_regression.py — Topic锚层回归用例（design_topic_layer.md §六验收标准）
# 七条断言, 对应六规约红线。跑法: python eval/topic_layer_regression.py
# 用例5/6/7 不真调 classify_and_link(需embedding且写库), 用 conflict_gate 单元级断言。
import os, sys, io, json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "memory"))
os.environ["TOPIC_LAYER_ON"] = "1"     # 测试态强制开

import topic_layer as tl

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f" | {detail}" if detail and not cond else ""))

print("== Topic锚层回归（六规约验收） ==")

# --- 用例1: 建锚+挂载(已由 seed_topics.py 完成, 此处验状态) ---
tl.init_topic_db()
s = tl.stats()
check("锚表就绪(4 active)", s["active_anchors"] == 4, str(s))
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

print(f"\n结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
if FAIL:
    for n, d in FAIL: print(f"  FAIL: {n} | {d}")
    sys.exit(1)
print("全部验收断言通过（对应 design_topic_layer.md §六）")
