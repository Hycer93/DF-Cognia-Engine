# e10_semantic_outlier_patrol.py — E10 语义离群巡检（2026-11-16 用户裁决立项）
#
# 定位: 补 v2.2 cue 判别力的残留局限——错挂进本簇的脏卡贡献 n_in, 纯统计
# 无法核验卡片内容真伪。E10 用本地 bge-small-zh embedding 做无监督离群
# 检测: 簇质心 + 余弦距离 + μ+kσ 动态阈值, 离群卡=疑似脏卡, 只告警。
#
# 用户裁决固化的规约:
#   1. 独立 E10(不并入E7): 故障隔离——embedding加载/推理异常不拖垮E7统计
#      巡检主干; 可单独开关。
#   2. embedding: 本地 bge-small-zh onnx-int8(复用 memory/embed_semantic.py),
#      禁外部API——审计结果必须可复现。依赖只在 E10 进程内加载, 不注入
#      topic_layer 在线链路。
#   3. 调度: 增量触发(簇挂载变更才重算) + 每日全量兜底(--full)。
#   4. 小簇保护: 正式挂载卡 <5 张跳过(与 P2b CUE_MIN_OBS 同理, 质心
#      统计无意义)。
#   5. 红线: 全程只读——只输出告警/审计日志/可疑清单, 绝不自动解挂/删卡/
#      改挂载关系。人闸终审。
#   6. 阈值: μ+kσ 动态(固定余弦阈值跨簇泛化差), k初始2.0 可配。
#   7. 输入文本: summary 优先, 不足(空/过短)截 detail 补, 不喂超长文本。
#   8. 告警携带对照: 卡摘要+到质心距离+簇内最近邻卡摘要, 人工一眼判。
#   9. 向量不持久化: 每轮按需计算, 只留距离结果, 控存储。
#  10. FNN 搁置(技术债): 标注样本积累后再启动。
#
# 跑法:
#   python eval/core/e10_semantic_outlier_patrol.py            # 增量(默认)
#   python eval/core/e10_semantic_outlier_patrol.py --full     # 全量兜底
#
# 规约备注(2026-11-16 终审裁决): 本批 4 条解挂全部归类【引用性提及假阳性】
# ——卡片正文仅举例引用簇关键词(版本史提'星型拓扑'/日记提'G22季报'),
# 本体主题不属于该簇, cue 单命中+统计合格但语义离群。此类是 E10 的
# 核心捕获对象(统计链路天然盲区), 已录回归用例14 固化防退化。
#
# 风险归档(用户裁决): embedding 语义有偏差(短文本/术语/代码), 告警永远
# 是辅助参考非判定依据; 天然松散簇(杂项收集)会误报, 后续可加
# skip_semantic_patrol 标记跳过。

import os
import sys
import json
import io
import numpy as np
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

# ---- 配置(用户裁决项) ----
MIN_CLUSTER_SIZE = 5      # 小簇保护: <5卡跳过
SIGMA_K = 2.0             # 默认 μ-kσ 倍数; 可被 TOPIC_SIGMA_K 覆盖
# 每 topic 独立 k 系数(2026-11-16 用户终审裁决: patent簇k=2.5放宽离群阈值,
# 抑制不同技术方向专利的持续擦边误告警; 登记须附裁决来源)
TOPIC_SIGMA_K = {
    "topic_patent_matrix": 2.5,   # 高内聚窄σ簇(μ=0.75 σ=0.045), 2.0擦边误报P13/P16/P19
}
MIN_COS_TO_CENTROID = 0.30  # 绝对下限: 即使σ很窄, 距质心低于此也报(防全簇同漂)
STATE_FILE = os.path.join(HERE, "logs", "e10_semantic_state.json")
LOG = os.path.join(HERE, "logs", "e10_semantic_audit.json")


def _conn():
    import sqlite3
    return sqlite3.connect(os.path.join(HERE, "data", "memory", "corpus_store.db"), timeout=5)


def _card_text(summary, detail):
    """规约7: summary 优先, 不足截 detail 补。"""
    s = (summary or "").strip()
    if len(s) >= 12:
        return s[:128]
    d = (detail or "").strip()
    return (s + " " + d)[:128] if d else s[:128]


def _load_state():
    try:
        with io.open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"cluster_sigs": {}}     # topic_id -> 挂载集合签名(增量触发用)


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def run(full=False):
    """巡检主流程。full=True 全量重算; 默认增量(挂载签名未变的簇跳过)。"""
    from memory.embed_semantic import embed, cosine   # 规约2: E10进程内加载

    con = _conn()
    try:
        anchors = con.execute(
            "SELECT topic_id FROM topic_abstraction WHERE status='active'").fetchall()
        state = _load_state()
        skipped_small, skipped_unchanged, checked, alerts = [], [], 0, []
        for (tid,) in anchors:
            cards = con.execute(
                "SELECT l.inst_id, c.summary, c.detail FROM topic_links l "
                "JOIN core_memory_card c ON c.inst_id = l.inst_id "
                "WHERE l.topic_id=? AND (c.status IS NULL OR c.status != 'superseded')",
                (tid,)).fetchall()
            if len(cards) < MIN_CLUSTER_SIZE:                 # 规约4: 小簇保护
                skipped_small.append((tid, len(cards)))
                continue
            sig = sorted(x[0] for x in cards)
            if not full and state["cluster_sigs"].get(tid) == sig:  # 规约3: 增量
                skipped_unchanged.append(tid)
                continue
            texts = [_card_text(s, d) for _, s, d in cards]
            vecs = embed(texts)                               # 规约9: 不持久化
            centroid = np.mean(vecs, axis=0)
            centroid = centroid / (np.linalg.norm(centroid) + 1e-12)
            dists = np.array([cosine(v, centroid) for v in vecs])
            mu, sd = float(dists.mean()), float(dists.std())
            k = TOPIC_SIGMA_K.get(tid, SIGMA_K)                # per-topic k(用户裁决)
            thr = max(mu - k * sd, MIN_COS_TO_CENTROID)        # 规约6: μ-kσ 动态+绝对下限
            checked += len(cards)
            for (iid, s, d), v, dist in zip(cards, vecs, dists):
                if dist < thr:
                    # 规约8: 告警带对照(最近邻卡摘要)
                    nn_idx = int(np.argmax([cosine(v, w) for k, w in enumerate(vecs) if k != cards.index((iid, s, d))] or [0]))
                    nn_summary = cards[nn_idx][1][:60] if cards else "?"
                    alerts.append({
                        "topic_id": tid, "inst_id": iid,
                        "summary": (s or "")[:80],
                        "dist_to_centroid": round(float(dist), 3),
                        "cluster_mu": round(mu, 3), "cluster_sd": round(sd, 3), "sigma_k": k,
                        "threshold": round(float(thr), 3),
                        "nearest_neighbor": nn_summary,
                        "note": "语义离群疑似脏卡(到质心%.3f < 阈值%.3f)——只告警, 人工复审裁决"
                                % (dist, thr)})
            state["cluster_sigs"][tid] = sig
        _save_state(state)
    finally:
        con.close()

    result = {"ts": datetime.now().isoformat(timespec="seconds"),
              "mode": "full" if full else "incremental",
              "checked_cards": checked, "alerts": alerts,
              "skipped_small": skipped_small, "skipped_unchanged": skipped_unchanged}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"[E10] 语义离群巡检({result['mode']}): 检查{checked}卡 | 告警{len(alerts)}条 "
          f"| 小簇跳过{len(skipped_small)} | 未变跳过{len(skipped_unchanged)}")
    if alerts:
        print("— 语义离群告警明细(只告警不删改, 人闸裁决) —")
        for a in alerts:
            print("  [%s] 簇: %s | dist=%.3f (μ=%.3f σ=%.3f 阈=%.3f k=%.1f)"
                  % (a["inst_id"][:8], a["topic_id"], a["dist_to_centroid"],
                     a["cluster_mu"], a["cluster_sd"], a["threshold"], a["sigma_k"]))
            print("    卡: %s" % a["summary"])
            print("    最近邻: %s" % a["nearest_neighbor"])
    return result


if __name__ == "__main__":
    run(full="--full" in sys.argv)