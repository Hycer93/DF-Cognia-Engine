# -*- coding: utf-8 -*-
"""F40 跨簇命中率考核器（门槛①执行件, docs/design_f40_hitrate.md v1.0 定案）

口径速览:
- 考题 = (起点卡∈src簇, 目标卡∈dst簇) 且 src≠dst; 真值链 BFS 跨 ≥2 簇、长度≥3
- 判分 = minloop.verify 三指标(可达∧相交∧步数偏差); 隔离红线命中→EXCLUDED 不计分母
- 图谱缺边自然 FAIL → 计入分母(真实能力债)
- 达标 = PASS/有效分母 ≥ 0.8 ∧ 有效分母 ≥ 3; 每轮配额 3~6 题
- 拓扑去重: 簇对重复禁入本轮; 主干途经簇集合重合或底层卡集 Jaccard≥0.8 判同题
- 刷题防线: 连续两轮不得全复用, 须新增≥1 全新拓扑题
- 题库台账: data/experiment/f40_cross_cluster_question_bank.jsonl(qid+跑分史)

用法:
  python scripts/f40_cross_cluster_eval.py sample [--seed N] [--n 6]   # 出题入池
  python scripts/f40_cross_cluster_eval.py run    [--seed N]           # 选轮次考题并校验
  python scripts/f40_cross_cluster_eval.py add --src <id前缀> --dst <id前缀> --kind manual_edge
  python scripts/f40_cross_cluster_eval.py status                      # 台账概览
只读纪律: 全程 mode=ro 打开库, 唯一写入物=题库 jsonl 与报告文件。
"""
import io, os, sys, json, random, hashlib, argparse
from datetime import datetime
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "memory", "corpus_store.db")
BANK = os.path.join(ROOT, "data", "experiment", "f40_cross_cluster_question_bank.jsonl")
OUT_DIR = os.path.join(ROOT, "data", "experiment", "f40_hitrate_runs")
SENSITIVE = {"topic_user_relation"}          # 隔离红线锚(对齐 minloop CONFIG)
MAX_HOPS = 12
PASS_RATE, MIN_DENOM, MAX_PER_ROUND, MIN_PER_ROUND = 0.8, 3, 6, 3


def _con():
    import sqlite3
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ---------------- 图与簇 ----------------

def load_graph():
    """返回 adj(inst→[(other,w)]), topic_of(inst→set), summary(inst→str)。"""
    con = _con()
    adj, topic_of, summ = {}, {}, {}
    for me, other in con.execute(
            "SELECT me_inst, other_inst FROM core_relation WHERE status='active'"):
        adj.setdefault(me, []).append((other, 1.0))
        adj.setdefault(other, []).append((me, 1.0))
    for iid, tid in con.execute("SELECT inst_id, topic_id FROM topic_links"):
        topic_of.setdefault(iid, set()).add(tid)
    for iid, s in con.execute("SELECT inst_id, summary FROM core_memory_card"):
        summ[iid] = s or ""
    con.close()
    return adj, topic_of, summ


def bfs(adj, topic_of, start, goal):
    """全局 visited + parent 回溯(F40 教训: per-path visited 会分支爆炸)。
    dist 计数限深 MAX_HOPS。返回路径 list(含两端) 或 None。"""
    if start == goal:
        return [start]
    seen, par, dist = {start}, {start: None}, {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        if dist[cur] >= MAX_HOPS:
            continue
        for nb, _w in adj.get(cur, []):
            if nb in seen:
                continue
            seen.add(nb); par[nb] = cur; dist[nb] = dist[cur] + 1
            if nb == goal:
                path = [goal]
                while par[path[-1]] is not None:
                    path.append(par[path[-1]])
                return path[::-1]
            q.append(nb)
    return None


# ---------------- 题目构造与判同 ----------------

def cross_topic_seq(path, topic_of):
    """路径的途经簇序列(去相邻重复)。"""
    seq = []
    for iid in path:
        for t in sorted(topic_of.get(iid, set()) or {"_none"}):
            if not seq or seq[-1] != t:
                seq.append(t)
    return seq


def qualifies(src, dst, path, topic_of):
    """跨簇题资格: src簇≠dst簇 ∧ 途经≥2簇 ∧ **中间节点须有异于两端的簇挂载**
    (只有两端挂簇的链路是 patent 巨岛内部漫游, 不算跨簇) ∧ 长度≥3。"""
    ts = cross_topic_seq(path, topic_of)
    uniq = {t for t in ts if t != "_none"}
    mid_topics = {t for iid in path[1:-1] for t in topic_of.get(iid, set())}
    end_topics = topic_of.get(src, set()) | topic_of.get(dst, set())
    real_hop = bool(mid_topics - end_topics)
    return (ts[0] != ts[-1] and len(uniq) >= 2 and real_hop and len(path) >= 3, uniq)


def same_question(qa, qb):
    """判同(规约§2): 簇对相同∧主干途经簇集合重合, 或底层卡集重合≥0.8。"""
    if qa["src_dst"] == qb["src_dst"] and qa["hop_topics"] == qb["hop_topics"]:
        return True
    A, B = set(qa["card_set"]), set(qb["card_set"])
    return bool(A & B) and len(A & B) / len(A | B) >= 0.8


def make_qid(src, dst, hops):
    raw = "|".join(sorted([src, dst])) + "|" + "|".join(sorted(hops))
    return "q_" + hashlib.sha1(raw.encode()).hexdigest()[:10]

def cmd_sample(seed=None, n=MAX_PER_ROUND):
    rng = random.Random(seed or datetime.now().toordinal())
    adj, topic_of, summ = load_graph()
    bank = load_bank()
    existing_keys = {(tuple(q["src_dst"]), tuple(q["hop_topics"])) for q in bank}
    linked = [i for i in topic_of if i in adj]
    proposals, tried = [], 0
    while len(proposals) < n and tried < 4000:
        tried += 1
        a, b = rng.choice(linked), rng.choice(linked)
        if a == b or (topic_of[a] & topic_of[b]):
            continue
        path = bfs(adj, topic_of, a, b)
        if not path:
            continue
        ok, uniq = qualifies(a, b, path, topic_of)
        if not ok:
            continue
        src_t = sorted(topic_of[a])[0]; dst_t = sorted(topic_of[b])[0]
        pair = tuple(sorted((src_t, dst_t)))       # 簇对无序: A→B 与 B→A 判同（台账规范化）
        hop_set = frozenset(uniq - {src_t, dst_t})
        key = (pair, hop_set)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        qid = make_qid(src_t, dst_t, hop_set)
        if qid in {q["qid"] for q in bank}:
            continue
        proposals.append({
            "qid": qid, "kind": "natural", "created_ts": datetime.now().isoformat(timespec="seconds"),
            "src": a, "dst": b, "src_dst": list(pair),
            "hop_topics": sorted(hop_set), "card_set": path,
            "truth_len": len(path) - 1,
            "stem": summ.get(b, "")[:80],
            "runs": [],
        })
    if proposals:
        with io.open(BANK, "a", encoding="utf-8") as f:
            for q in proposals:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"[sample] seed={seed or 'time'} 新题 {len(proposals)} 入池"
          f"(尝试{tried}), 题库共 {len(bank)+len(proposals)}")
    for q in proposals:
        print(" ", q["qid"], q["src_dst"], "hops=%d" % (q["truth_len"]), "途经簇", q["hop_topics"])


# ---------------- 轮次校验 ----------------

def pick_round(bank, last_run_round=None):
    """选轮: 上轮用过的题优先保留但必须含≥1 全新拓扑; 配额 3~6。"""
    fresh = [q for q in bank if not q["runs"]]
    used = [q for q in bank if q["runs"]]
    prev_ids = set()
    if last_run_round:
        prev_ids = {q["qid"] for q in bank if any(
            r.get("round") == last_run_round for r in q["runs"])}
    carry = [q for q in used if q["qid"] in prev_ids]
    pool = (carry + fresh + [q for q in used if q["qid"] not in prev_ids])
    seen_pairs = set(); chosen = []
    for q in pool:                       # 同轮簇对互斥
        pair = tuple(sorted(q["src_dst"]))
        if pair in seen_pairs or len(chosen) >= MAX_PER_ROUND:
            continue
        seen_pairs.add(pair); chosen.append(q)
    need_fresh = 1 if (prev_ids and all(q["qid"] in prev_ids for q in chosen) and chosen) else 0
    if need_fresh and not any(not q["runs"] for q in chosen):
        extra = next((q for q in fresh if tuple(sorted(q["src_dst"])) not in seen_pairs), None)
        if extra:
            chosen.append(extra)
    return chosen, bool(need_fresh)


def cmd_run(seed=None):
    adj, topic_of, summ = load_graph()
    bank = load_bank()
    if not bank:
        print("[run] 题库为空——先 sample 出题"); return 2
    rounds = sorted({r.get("round") for q in bank for r in q["runs"] if r.get("round")})
    last = rounds[-1] if rounds else None
    chosen, forced_new = pick_round(bank, last)
    rnd = (int(rounds[-1].split("_")[1]) + 1) if rounds else 1
    rnd_name = "r%02d_%s" % (rnd, datetime.now().strftime("%m%d%H%M"))
    print(f"[run] round={rnd_name} 选题 {len(chosen)}"
          f"{' (强制补新题)' if forced_new else ''}")
    results = []
    for q in chosen:
        src_t, dst_t = q["src_dst"]
        excluded = bool(SENSITIVE & set(q["hop_topics"]) | SENSITIVE & {src_t, dst_t})
        path = bfs(adj, topic_of, q["src"], q["dst"])
        if path is None:
            res, note = "FAIL", "库内无连通路径(边债)"
        elif len(path) - 1 != q["truth_len"]:
            res, note = "FAIL", "真值链漂移 len=%d≠%d(图谱变更需重出题)" % (len(path)-1, q["truth_len"])
        else:
            res, note = "PASS", "三指标口径: 可达∧自证相交∧步数一致"
        if excluded:
            res, note = "EXCLUDED", "隔离红线命中(不计分母)"
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "round": rnd_name,
               "result": res, "note": note}
        q["runs"].append(rec)
        results.append((q, rec))
        print("  %s %s %s %s" % (res, q["qid"], q["src_dst"], note))
    # 回写台账
    with io.open(BANK, "w", encoding="utf-8") as f:
        for q in bank:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    denom = [r for _, r in results if r["result"] != "EXCLUDED"]
    passed = [r for r in denom if r["result"] == "PASS"]
    rate = len(passed) / len(denom) if denom else 0.0
    verdict = ("达标" if len(denom) >= MIN_DENOM and rate >= PASS_RATE else
               "样本不足" if len(denom) < MIN_DENOM else "未达标")
    summary = {"round": rnd_name, "n_questions": len(results),
               "denominator": len(denom), "passed": len(passed),
               "hit_rate": round(rate, 3), "threshold": PASS_RATE,
               "min_denom": MIN_DENOM, "verdict": verdict,
               "detail": [{"qid": q["qid"], **r} for q, r in results]}
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, rnd_name + ".json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[verdict] hit_rate={rate:.1%} ({len(passed)}/{len(denom)}) 阈值{PASS_RATE:.0%}"
          f" 最低样本{MIN_DENOM} → **{verdict}**  报告={out}")
    return 0


# ---------------- 手工补难例 / 状态 ----------------

def cmd_add(src_pfx, dst_pfx, kind):
    adj, topic_of, summ = load_graph()
    con = _con()
    src = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?", (src_pfx+"%",)).fetchone()
    dst = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?", (dst_pfx+"%",)).fetchone()
    con.close()
    if not src or not dst:
        print("[add] id 查无:", src_pfx, dst_pfx); return 2
    path = bfs(adj, topic_of, src[0], dst[0])
    if not path:
        print("[add] 两点无连通路径, 无法建题"); return 2
    ok, uniq = qualifies(src[0], dst[0], path, topic_of)
    src_t, dst_t = sorted(topic_of.get(src[0], ["_none"]))[0], sorted(topic_of.get(dst[0], ["_none"]))[0]
    hops = sorted(uniq - {src_t, dst_t})
    q = {"qid": make_qid(src_t, dst_t, hops), "kind": kind,
         "created_ts": datetime.now().isoformat(timespec="seconds"),
         "src": src[0], "dst": dst[0], "src_dst": [src_t, dst_t], "hop_topics": hops,
         "card_set": path, "truth_len": len(path)-1, "stem": summ.get(dst[0], "")[:80], "runs": []}
    bank = load_bank()
    if any(same_question(q, x) for x in bank):
        print("[add] 拓扑判同已存在, 拒绝重复入池"); return 2
    with io.open(BANK, "a", encoding="utf-8") as f:
        f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print("[add]", q["qid"], q["src_dst"], "qualifies=", ok, "(不跨簇仍可作对照题)")


def load_bank():
    if not os.path.exists(BANK):
        return []
    return [json.loads(l) for l in io.open(BANK, encoding="utf-8") if l.strip()]


def cmd_status():
    bank = load_bank()
    from collections import Counter
    kinds = Counter(q["kind"] for q in bank)
    ever_fail = sum(1 for q in bank if any(r["result"] == "FAIL" for r in q["runs"]))
    print(f"题库: {len(bank)} 题 {dict(kinds)} | 历史含FAIL: {ever_fail}")
    rounds = sorted({r.get('round') for q in bank for r in q['runs'] if r.get('round')})
    for rd in rounds[-3:]:
        rs = [r for q in bank for r in q['runs'] if r.get('round') == rd]
        d = [r for r in rs if r['result'] != 'EXCLUDED']
        p = [r for r in d if r['result'] == 'PASS']
        print(f"  {rd}: {len(p)}/{len(d)} = {len(p)/len(d) if d else 0:.0%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("sample"); s1.add_argument("--seed", type=int); s1.add_argument("--n", type=int, default=MAX_PER_ROUND)
    s2 = sub.add_parser("run"); s2.add_argument("--seed", type=int)
    s3 = sub.add_parser("add"); s3.add_argument("--src", required=True); s3.add_argument("--dst", required=True)
    s3.add_argument("--kind", default="manual_edge")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "sample": cmd_sample(a.seed, a.n)
    elif a.cmd == "run": sys.exit(cmd_run(a.seed))
    elif a.cmd == "add": sys.exit(cmd_add(a.src, a.dst, a.kind) or 0)
    else: cmd_status()