# scripts/seed_topics.py — 存量高置信锚建锚脚本（Topic层上线第一步）
# 设计文档§四: 只建高置信簇(P10/P12/P22、非农复盘、脑区映射、G22决策)，
# 其余 topic_id=null 等夜间收敛——与 memory_class 回填范式对齐（用户定见）
# 伪演示基线: logs/topic_layer_demo_baseline_20261113.json (对比样本)
import os, sys, json, io

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory"))
import topic_layer as tl
from memory_organize import load_memories

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM = os.path.join(HERE, "data", "memory", "memories.jsonl")

# 高置信锚定义（伪演示四个簇，cue_anchors 按实际记忆内容提炼）
SEEDS = [
    {
        "topic_id": "topic_star_topology",
        "abstract": "星型拓扑专利簇：P10中心进化+外围固化、P12初稿、P22星域联邦及编号重排决策",
        "cue_anchors": ["P10", "P12", "P22", "星型拓扑", "星域联邦", "p10vs"],
    },
    {
        "topic_id": "topic_brain_region",
        "abstract": "脑区映射体系：海马体情景记忆/前额叶流水线/杏仁核刹停，情感置后需提炼",
        "cue_anchors": ["脑区", "海马体", "前额叶", "杏仁核"],
    },
    {
        "topic_id": "topic_g22_decision",
        "abstract": "G22决策整合骨架：光谱系数从Ht迁移、诞生链三失误、方案矩阵与目的校验",
        "cue_anchors": ["G22", "光谱系数", "决策整合", "方案矩阵"],
    },
    {
        "topic_id": "topic_feinong_review",
        "abstract": "非农数据复盘簇：金价对非农超预期的反应归因分歧（流动性拐点vs央行购金对冲）",
        "cue_anchors": ["非农", "金价"],
    },
]

def main(dry_run=False):
    mems = load_memories()
    print(f"主记录 {len(mems)} 条")
    tl.init_topic_db()
    report = {}
    for seed in SEEDS:
        # 建锚
        if not dry_run:
            tl.seed_topic(seed["topic_id"], seed["abstract"], seed["cue_anchors"])
        # 挂载: cue 命中即挂（同伪演示口径, method=seed）
        hits = []
        for m in mems:
            text = (m.get("summary") or "") + " " + (m.get("detail") or "" or "")
            if any(cue in text for cue in seed["cue_anchors"]):
                hits.append(m["inst_id"])
        if not dry_run and hits:
            tl.mount_batch(hits, seed["topic_id"], method="seed")
        report[seed["topic_id"]] = {"cue_anchors": seed["cue_anchors"], "mounted": len(hits)}
        print(f"[{seed['topic_id']}] 锚{'(dry)' if dry_run else '✓'} | 挂载 {len(hits)} 条")
    if not dry_run:
        print("\n锚层统计:", json.dumps(tl.stats(), ensure_ascii=False))
        print("对比基线: logs/topic_layer_demo_baseline_20261113.json")
    return report

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry)
