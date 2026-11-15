# -*- coding: utf-8 -*-
"""
seed_paradigm_batch2.py — paradigm 种子考古迁移收尾 (P3-b, 2026-11-15)

职能: 把 27 骨架中尚未迁入 persona_anchor#1 detail 列出的思考纪律,
     分批导入 core_thinking_paradigm candidate——走 E7 夜间采集实证慢慢积累。
     慢热长期资产, 不急晋升。

本批(骨架考古池 6 条, 全 candidate 0/0):
  tp_diverge_first  Aa 发散先行
  tp_source_peel    L3 溯源剥离锚定自反
  tp_dark_consensus L4 暗共识时序等价
  tp_repr_switch    Gs 破壁换表征
  tp_decision_gate  G22 决策整合+目的校验位
  tp_retro_four     Jw 复盘四步
"""
import sqlite3
import datetime

DB = "data/memory/corpus_store.db"
NOW = datetime.datetime.now().isoformat(timespec="seconds")

BATCH2 = [
    ("tp_diverge_first", "发散先行(Aa)", "thinking",
     "新问题/新设计开局, 尚无收敛方向时",
     "1.先发散枚举多表征/多路径 2.标记各路径信息充分度 3.再收敛收拢主线(配合平行程度因子)",
     "27骨架 Aa + persona_anchor#1 detail 考古迁出"),
    ("tp_source_peel", "溯源剥离锚定自反(L3)", "thinking",
     "结论来源可疑/需验证断言可靠性时",
     "1.剥离表述层找原始锚(谁说的/基于什么) 2.自反: 该锚是否被更高层裁决覆盖 3.未定则标 fragile 降采信",
     "27骨架 L3 + persona_anchor#1 detail 考古迁出"),
    ("tp_dark_consensus", "暗共识时序等价(L4)", "thinking",
     "两方案表述不同但疑似同义/判断是否重复发明时",
     "1.抽两方案骨架时序 2.时序结构等价则判暗共识(同一设计) 3.等价即合并沿用不双轨",
     "27骨架 L4 + persona_anchor#1 detail 考古迁出"),
    ("tp_repr_switch", "破壁换表征(Gs)", "thinking",
     "当前表征下问题无解/讨论卡死时",
     "1.识别卡死表征 2.换表征空间重述问题(如序→图/数值→几何) 3.新表征下求解再回译",
     "27骨架 Gs + persona_anchor#1 detail 考古迁出"),
    ("tp_decision_gate", "决策整合+目的校验位(G22)", "thinking",
     "多路径评估完成待整合决策时",
     "1.汇总各路径结论(不平均状态, 只聚合结论) 2.附目的校验位: 该决策服务哪个目标 G 3.与 G 不符则回炉",
     "27骨架 G22 + persona_anchor#1 detail 考古迁出; 与F50只聚合结论同构"),
    ("tp_retro_four", "复盘四步(Jw)", "thinking",
     "阶段收官/事故复盘/验证决策质量时",
     "1.还原决策时点信息(当时知道什么) 2.对照结果找差距 3.归因到可改流程 4.入案例账(E7范式挖掘源)",
     "27骨架 Jw + persona_anchor#1 detail 考古迁出"),
]


def migrate():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    for row in BATCH2:
        cur.execute("""INSERT OR REPLACE INTO core_thinking_paradigm
            (pid,name,paradigm_type,trigger_when,procedure,status,evidence_count,evidence_pass,origin,ts,last_trigger_ts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (row[0], row[1], row[2], row[3], row[4], "candidate", 0, 0, row[5], NOW, NOW))
    con.commit()
    n = cur.execute("SELECT COUNT(*) FROM core_thinking_paradigm").fetchone()[0]
    print(f"core_thinking_paradigm 总数: {n} (本批 +{len(BATCH2)} candidate)")
    for pid, name, *_ in BATCH2:
        r = cur.execute("SELECT status, evidence_pass FROM core_thinking_paradigm WHERE pid=?", (pid,)).fetchone()
        print(f"  {pid} {name}: {r}")
    con.close()


if __name__ == "__main__":
    migrate()