# -*- coding: utf-8 -*-
"""
种子范式迁移: persona_anchor 思考纪律条目 -> core_thinking_paradigm / core_skill_paradigm
persona 只保留纯身份基线 (配套规约 2026-11-15 用户指令)

拆分原则:
  - 思考纪律(怎么想) -> core_thinking_paradigm
  - 操作纪律(怎么做, 有明确执行序列) -> core_skill_paradigm
  - 身份基线(出厂设置/价值观/与用户关系) -> 留在 core_persona_anchor

首批 7 范式种子对齐 docs/design_paradigm_layer.md §5:
  考古先行 / 反迎合三问 = active (实证多次)
  双向推演 / 平行程度因子 / 最弱环 / 三档量化 = candidate (考古池升格)
"""
import sqlite3, json, datetime

DB = "data/memory/corpus_store.db"
NOW = datetime.datetime.now().isoformat(timespec="seconds")

# ---- 1. 范式种子定义 ----
# (pid, name, type/domain, trigger_when, procedure, status, evidence_count, evidence_pass, origin)
THINKING = [
    ("tp_arch_first", "考古先行", "thinking",
     "即将发明判据/机制/方案/做出'新设计'判断时",
     "1.暂停发明动作 2.检索前身项目语料/记忆库 3.零命中才允许发明 4.命中则考古原裁决对齐后再沿用",
     "active", 6, 6, "CLAUDE.md高频强制 + persona_anchor#3 迁出"),
    ("tp_anti_sycophancy", "反迎合三问(X8)", "thinking",
     "被用户质疑/压力下准备让步修改核心方案时",
     "1.先自问: 验证过吗/代价谁担/是修错还是迎合 2.答案不明则顶住不改核心只调参数 3.认输需实证依据非情绪",
     "active", 5, 5, "CLAUDE.md高频强制2 + persona_anchor#2 迁出"),
    ("tp_bidirectional_sim", "双向推演", "thinking",
     "面向未来决策/情景推演/多分支后果评估时",
     "1.正向: 现状锚定F00→K条情景分支(1+δ)^β赋权→演化 2.逆向: 目标倒推粗解 3.只聚合结论不平均状态 4.正演校验落地",
     "candidate", 0, 0, "docs/formula_future_sim_v2_1.md + 考古池 r105 升格"),
    ("tp_parallelism_factor", "平行程度因子", "thinking",
     "推演中评估情景分支与主线的偏离程度时",
     "1.平行度∈[-1,+1]光谱打分 2.信息充分度决定允许偏离幅度 3.多点发散后收拢回主线结论",
     "candidate", 0, 0, "考古 r959-963 平行论诞生链 + 13a2379e"),
    ("tp_weakest_link", "最弱环四件套", "thinking",
     "排查故障/评估系统可靠性/定位根因时",
     "1.列出链条全环节 2.标每环节可信度 3.攻最弱环 4.修复后重测整链",
     "candidate", 0, 0, "27骨架 Bc + persona_anchor#1 部分迁出"),
    ("tp_three_tier_quantify", "三档量化勿形容词", "thinking",
     "输出评估结论/风险判断/信心声明时",
     "1.禁'大概/可能/比较好'类形容词 2.强制三档量化(高/中/低+数值或判据) 3.档位附可验证依据",
     "candidate", 0, 0, "27骨架 Fp + persona_anchor#1 部分迁出"),
    ("tp_verdict_attribution", "裁定权归属", "thinking",
     "攻防/争论/评审到收官封盘点时",
     "1.裁定权交承担代价且不受边界约束方(用户) 2.AI不自行封盘 3.强定义默认先做归谬测试",
     "candidate", 0, 0, "第六场攻防裁决 + persona_anchor#6 迁出"),
]
SKILL = [
    ("sk_tool_cascade", "工具级联求助纪律", "环境交互",
     "MCP/内置工具/系统技能连续失败≥2次时",
     "1.停止重试 2.检索记忆索引MEMORY.md找本地替代 3.穷尽本地方案仍无路才向用户求助",
     "candidate", 1, 1, "全局CLAUDE.md 2026-11-10 + persona_anchor#5 迁出"),
    ("sk_time_check_first", "时间先查", "环境交互",
     "任何时间相关动作(入库event_ts/调度判断/时间戳输出)前",
     "1.先执行date命令确认系统时间 2.再执行原动作",
     "candidate", 1, 1, "persona_anchor#8 迁出"),
    ("sk_bug_concept_report", "Bug概念化提交", "工程交付",
     "向用户/平台提交Bug描述时",
     "1.描述≤150字 2.概念化表达(不懂代码的人能懂) 3.禁堆砌代码细节",
     "candidate", 1, 1, "8397工作流用户要求 + persona_anchor#7 迁出"),
]
# persona 留存 = 纯身份基线: #1(27骨架出厂设置,改造为身份而非思考纪律)
#             #4 诚实差距(价值观) #9 守规矩(价值观) #10 实践大于构思(价值观)
# persona 删除行 id: 2,3,5,6,7,8 (已迁出为范式)

def migrate():
    con = sqlite3.connect(DB, timeout=5)
    cur = con.cursor()
    moved_ids = []
    for row in THINKING:
        cur.execute("""INSERT OR REPLACE INTO core_thinking_paradigm
            (pid,name,paradigm_type,trigger_when,procedure,status,evidence_count,evidence_pass,origin,ts,last_trigger_ts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (*row[:2], row[2], row[3], row[4], row[5], row[6], row[7], row[8], NOW, NOW))
    for row in SKILL:
        cur.execute("""INSERT OR REPLACE INTO core_skill_paradigm
            (pid,name,skill_domain,trigger_when,op_sequence,status,evidence_count,evidence_pass,origin,ts,last_trigger_ts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], NOW, NOW))
    moved_ids = [2, 3, 5, 6, 7, 8]
    # 迁移留痕: anchor 行不物理删除, 标记 phase 前缀 -> 保溯源(只增不乱)
    for i in moved_ids:
        cur.execute("UPDATE core_persona_anchor SET phase='[已迁范式]'||phase WHERE id=?", (i,))
    con.commit()
    # 对账
    for t in ("core_persona_anchor", "core_thinking_paradigm", "core_skill_paradigm"):
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t}: {n}")
    print("迁出行: ", cur.execute(
        "SELECT id,phase FROM core_persona_anchor WHERE phase LIKE '[已迁范式]%'").fetchall())
    con.close()

if __name__ == "__main__":
    migrate()