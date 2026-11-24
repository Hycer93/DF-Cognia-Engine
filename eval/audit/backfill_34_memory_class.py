#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#34 memory_class 回填 dry-run 方案生成器（不写库，只出方案）。

数据背景(2026-11-24 刷新, #46 后):
  - 全库 NULL 61 = 56 active + 5 superseded
  - 22 条有 pending 提案(质量参差: behavior_prior/feature_words 自动推多不准)
  - 35 条无提案需从头判
本脚本内置人工复核后的分类映射(CLASS_MAP), dry-run 输出方案供审。
--apply 才真写(同时写 DB + jsonl + 清 pending)。
"""
import argparse, io, json, os, sqlite3, sys, shutil

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEM_DIR = os.path.join(BASE, "data", "memory")
DB_PATH = os.path.join(MEM_DIR, "corpus_store.db")
JSONL_PATH = os.path.join(MEM_DIR, "memories.jsonl")
PEND_PATH = os.path.join(MEM_DIR, "class_backfill_pending.jsonl")

ENUM = {"strategy_evolve","skill_refine","scar_case","relation_milestone","key_pact"}

# 人工复核分类映射: inst前缀 -> (memory_class, 置信度, 理由)
# 置信度: hi可批量 / mid建议审 / lo存疑人工
CLASS_MAP = {
    # ---- 22条有提案(复核后多改) ----
    "ef590c1519": (None, "mid", "非农金价复盘=投资记录非工程技能(提案skill_refine不准)"),
    "d7d25873cf": (None, "mid", "金价复盘=投资记录(提案skill_refine不准)"),
    "ac25fe3a97": ("strategy_evolve","mid","黄金ETF对冲决策=顶层决策"),
    "a31bddbe82": (None, "mid", "对冲后续跟踪=临时记录(提案strategy_evolve过重)"),
    "fea4d1ad45": ("key_pact","mid","插件发布流程=规约(提案skill_refine偏)"),
    "4304794df5": (None, "mid", "季报情报扫描=临时记录(提案scar_case明显错)"),
    "94c3f62040": ("relation_milestone","mid","'我们一体'人设定调=关系里程碑(提案strategy_evolve不准)"),
    "ef3ef53fe3": ("strategy_evolve","hi","双暮暮对峙裁决=顶层裁决"),
    "7edb944579": ("relation_milestone","mid","用户失败预感洞察=用户辖区里程碑"),
    "63261eae8b": ("strategy_evolve","mid","内生驱动v4构思=架构路线(提案skill_refine偏)"),
    "fa1b54583e": ("skill_refine","hi","F系晋升门槛核对=技能验证"),
    "pat5_tuowei_": ("strategy_evolve","mid","拓维专利评估定案=专利顶层裁决"),
    "pc4_ref_mate": ("skill_refine","hi","P-C4素材通道上线=工程实现"),
    "pc4_patent_p": ("skill_refine","mid","P1专利P-C4对照实证=工程验证(提案scar_case不准)"),
    "pc6_p24_full": ("skill_refine","hi","P-C6阈值调整=工程阈值微调"),
    "pretrieval_u": ("skill_refine","mid","检索2gram→BM25升级=工程优化(提案scar_case不准)"),
    "round4_optim": ("skill_refine","hi","第四轮对照实验=工程迭代"),
    "p_matrix_fee": ("skill_refine","mid","矩阵回灌闭环=技能实现(提案strategy_evolve偏)"),
    "p_skill_synt": ("skill_refine","mid","技能合成闭环=技能实现(提案strategy_evolve偏)"),
    "round5_v3_se": ("skill_refine","hi","第五轮自评审=工程迭代"),
    "1db508d5b2": ("skill_refine","hi","正态环阈值细化=工程阈值微调"),
    # ---- 35条无提案 ----
    "0b272d927c": ("strategy_evolve","hi","双AI对峙最终裁决=顶层决策"),
    "134622f896": ("scar_case","hi","踩坑:风险点未下沉"),
    "17acf52b6b": ("scar_case","hi","踩坑:分类规约缺判定轴"),
    "1885a5311b": ("scar_case","hi","踩坑:素材缺证据"),
    "28fa913717": ("scar_case","hi","踩坑:单态前提被推翻"),
    "2b14c17eba": ("key_pact","mid","复活指南放置位置=规约"),
    "30e469db8a": ("strategy_evolve","hi","脑区映射V1.0=架构范式"),
    "3c15acc6c3": ("strategy_evolve","mid","专利矩阵同步架构映射"),
    "40a25f50fd": ("scar_case","hi","踩坑:未验证假设叠数字结论"),
    "4d26fad657": ("scar_case","mid","观测:P1最脆弱环节=缺陷观测"),
    "570a57f5c3": ("scar_case","hi","踩坑:多维校验不达标仍推进"),
    "63e24d6574": ("scar_case","mid","观测:P1区分度风险=风险观测"),
    "66d6900ad2": ("scar_case","hi","踩坑:风险量化漏算成本"),
    "6ed8586c27": ("strategy_evolve","mid","挂单审查=投资决策"),
    "7177cd609a": (None,"mid","E7晨报held计数=临时观测数字"),
    "7e831a2b27": ("scar_case","hi","踩坑:FL≠隐私保护"),
    "928351ff9c": ("scar_case","hi","踩坑:后段纠偏未回写"),
    "94f7745503": ("scar_case","hi","踩坑:vhal_test未部署"),
    "9963caa5fd": ("scar_case","hi","踩坑:dumpsys回读不可信"),
    "ab16273023": ("scar_case","mid","观测:P1最薄弱环节=缺陷观测"),
    "ab_ref_mater": ("skill_refine","mid","P-C4 A/B实验=工程实验"),
    "cc26c396ad": ("scar_case","hi","踩坑:环境工具失败致证据空"),
    "d25d395cf4": ("relation_milestone","mid","思维跃迁起点=认知里程碑"),
    "db8d3b694a": ("strategy_evolve","hi","前身策略进化裁定=策略路线"),
    "dc06c45494": ("scar_case","hi","踩坑:清单范式致遗漏"),
    "ea4cb0dda9": ("scar_case","mid","观测:时序四时点未确认=风险观测"),
    "f033b4fe4b": ("strategy_evolve","mid","业绩预告评估法=方法论路线"),
    "f4eccbc133": ("scar_case","hi","踩坑:量化覆盖不到未平铺维度"),
    "fb67435681": (None,"mid","拓普定价复核=投资记录"),
    "fd5772c652": ("strategy_evolve","hi","星型拓扑专利编号=专利顶层决策"),
    "p1_patent_pi": ("skill_refine","hi","P1专利管线复盘=工程验证"),
    "pc3_route_pr": ("skill_refine","hi","P-C3路由偏好机制=技能实现"),
    "pc5_p24_repl": ("skill_refine","hi","P24实战replay=工程实战验证"),
    "pc5_sufficie": ("skill_refine","hi","P-C5充分性校验上线=技能实现"),
    "skeleton_nin": ("strategy_evolve","hi","九层骨架理论本体=架构范式"),
}


def resolve(inst):
    for pref,(cls,conf,why) in CLASS_MAP.items():
        if inst.startswith(pref.rstrip('_')) or inst == pref:
            return cls,conf,why
    return "__MISS__", "lo", "未匹配"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", choices=["hi","all"], default="all", help="hi=只写高置信")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--jsonl", default=JSONL_PATH)
    args = ap.parse_args()

    con = sqlite3.connect(args.db); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT inst_id,status,substr(summary,1,42) s FROM core_memory_card "
        "WHERE (memory_class IS NULL OR memory_class='') AND status='active' ORDER BY inst_id").fetchall()
    con.close()

    print(f"[#34] 目标: {len(rows)} 张 active NULL 卡  |  模式={'APPLY' if args.apply else 'DRY-RUN'}  filter={args.only}")
    print()
    plan = {"hi":[], "mid":[], "lo":[], "miss":[]}
    for r in rows:
        cls,conf,why = resolve(r["inst_id"])
        plan[conf if cls!="__MISS__" else "miss"].append((r["inst_id"],cls,why,r["s"]))

    for conf in ["hi","mid","lo","miss"]:
        items = plan[conf]
        if not items: continue
        label = {"hi":"高置信(可批量)","mid":"中置信(建议审)","lo":"低置信(存疑)","miss":"未匹配(需补)"}[conf]
        print(f"=== {label}: {len(items)} 张 ===")
        for iid,cls,why,s in items:
            print(f"  {iid[:12]} -> {str(cls):18} | {why} | {s}")
        print()

    total_set = sum(1 for r in rows if resolve(r["inst_id"])[0] not in ("__MISS__","None",None))
    total_null = sum(1 for r in rows if resolve(r["inst_id"])[0] is None)
    print(f"汇总: 赋类 {total_set} 张 / 保null {total_null} 张 / 未匹配 {len(plan['miss'])} 张")

    if not args.apply:
        print("\n[DRY-RUN] 未写库。--apply 执行(可配 --only hi 只写高置信)。")
        return 0

    # APPLY
    targets = plan["hi"] if args.only=="hi" else (plan["hi"]+plan["mid"]+plan["lo"])
    targets = [(iid,cls) for iid,cls,why,s in targets if cls in ENUM]
    print(f"\n[APPLY] 将写入 {len(targets)} 张 (filter={args.only})")
    if not targets:
        print("无可写项(全为 null/miss)"); return 0
    con = sqlite3.connect(args.db, isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    try:
        for iid,cls in targets:
            con.execute("UPDATE core_memory_card SET memory_class=? WHERE inst_id=? AND (memory_class IS NULL OR memory_class='')",(cls,iid))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise
    finally:
        con.close()
    # jsonl 同步
    tmap = {iid:cls for iid,cls in targets}
    out=[]; n=0
    bak = args.jsonl+".bak_34_"+__import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(args.jsonl, bak)
    for line in io.open(args.jsonl,encoding="utf-8"):
        line=line.strip()
        if not line: continue
        o=json.loads(line)
        if o.get("inst_id") in tmap and not o.get("memory_class"):
            o["memory_class"]=tmap[o["inst_id"]]; n+=1
        out.append(o)
    with io.open(args.jsonl,"w",encoding="utf-8") as f:
        for o in out: f.write(json.dumps(o,ensure_ascii=False)+"\n")
    print(f"DB写入 {len(targets)} 张, jsonl同步 {n} 张, 备份={os.path.basename(bak)}")
    # 校验
    con=sqlite3.connect(args.db)
    left=con.execute("SELECT COUNT(*) FROM core_memory_card WHERE (memory_class IS NULL OR memory_class='') AND status='active'").fetchone()[0]
    con.close()
    print(f"剩余 active NULL: {left} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())