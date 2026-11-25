# 记忆系统表关系图 + 入口说明

> 读者：未来会话的我 / 任何要理解这套系统的人。
> 配套：`db_table_registry.md`（逐表定性台账）——本文讲**关系与流程**，台账讲**单表死活**。
> 快照：2026-11-25

---

## 一、入口总览（谁在使用这套系统）

四类入口，性质完全不同：

```mermaid
flowchart TB
    subgraph ENTRY["四类入口"]
        E1["① 写入入口<br/>memory_hook.py<br/>(CLI手动 / CodeChat Stop钩子)"]
        E2["② 查询入口<br/>memory_query.py (四维加权检索)<br/>entity_resolve.py (别名/实体锚)"]
        E3["③ 夜间自维护<br/>nightly_run.py<br/>(无人值守总链)"]
        E4["④ 协同推演<br/>collab_pipeline.py<br/>(多子Agent扇出+F50聚合)"]
    end

    E1 -->|"atomic_store<br/>classify_and_link<br/>distill"| CORE
    E2 -->|"只读投影"| CORE
    E3 -->|"E2→E4/E5/E6→E7治理→T1归档"| CORE
    E4 -->|"F50聚合写入经验/结论"| CORE

    subgraph CORE["核心主库 corpus_store.db"]
        direction LR
        MC[(core_memory_card<br/>认知原子)]
        REL[(core_relation<br/>关系边)]
        TL[(topic_links)]
        TA[(topic_abstraction)]
    end
```

| 入口 | 文件 | 干什么 | 触发方式 |
|---|---|---|---|
| **① 写入** | `memory/memory_hook.py` | 采集会话中的裁决/纠错/洞察，走 `atomic_store→classify_and_link→distill` 三管道 | CLI 手动 `add` / CodeChat Stop 钩子 `session` |
| **② 查询** | `memory/memory_query.py` | 四维加权检索投影（编码0.3+置信0.25+调用0.25+关联0.2） | 只读，被上层调用 |
| | `memory/entity_resolve.py` | 别名归并 + 实体锚查找（#49 阶段2） | `python entity_resolve.py <名字>` |
| **③ 夜间** | `scripts/nightly_run.py` | E2分钟级→E4/E5/E6→E7治理→T1归档，全程无人值守 | 定时 `NO_MEMORY=1 python scripts/nightly_run.py` |
| **④ 推演** | `scripts/collab_pipeline.py` | N路独立子Agent各跑run_atomic，F50聚合抽象指标 | 按需手动 |

---

## 二、记忆形成链（数据自下而上蒸馏）

```mermaid
flowchart LR
    RAW[/"raw_corpus<br/>(原始语料)<br/>+corpus_tags 书签"/]
    MC[("core_memory_card<br/>认知原子<br/>唯一真相")]
    REL[("core_relation<br/>概念突触")]
    TA[("topic_abstraction<br/>主题簇")]
    TL[("topic_links<br/>归属边")]
    FTS[["fts_memory_card<br/>检索索引"]]

    RAW -->|"蒸馏/蒸馏提炼"| MC
    MC -->|"建关系边"| REL
    MC -->|"归纳成主题"| TA
    TA -.->|"挂载"| TL
    MC -.->|"归属"| TL
    MC -.->|"建全文索引"| FTS
```

**概念**：原文（raw）→ 蒸馏成认知原子（memory_card，这是**唯一真相**）→ 原子之间连关系（relation）、归到主题（topic）。

---

## 三、身份与经验层（"这是谁、我学到了什么"）

```mermaid
flowchart TB
    MC[("core_memory_card<br/>认知原子")]
    ER["entity_resolve.py<br/>(读侧)"]
    ENT[("core_entity_timeline<br/>实体锚")]
    ALI[("core_term_alias<br/>别名映射")]
    EXP[("core_experience<br/>经验层")]
    
    ER -->|"resolve_alias"| ALI
    ER -->|"resolve_entity"| ENT
    MC -->|"多案例归纳"| EXP
    
    ALI -.->|"暮暮=蒸馏仔=归元仔"| ENT
```

**概念**：`entity_timeline` 回答"这个名字指的是哪个真实对象"，`term_alias` 回答"同一对象的多个叫法"，`experience` 回答"我从多个案例里学到了什么"（复盘/定案/踩坑）。

---

## 四、方法论与性格层

```mermaid
flowchart LR
    TP[("core_thinking_paradigm<br/>思维范式")]
    SP[("core_skill_paradigm<br/>技能范式")]
    EV[("audit_paradigm_evidence<br/>范式成绩单")]
    PA[("persona_anchor /<br/>core_persona_anchor<br/>性格锚点")]
    PE[("core_persona_evolution<br/>性格演化")]

    TP --> EV
    SP --> EV
    PA -.->|"部分已迁入范式"| SP
    PA --> PE
```

**概念**：思维范式（怎么推）+ 技能范式（怎么做）+ 性格锚点（我是谁/底线）；`evidence` 记录哪条范式真管用。

---

## 五、免疫系统（记忆治理，防污染）

```mermaid
flowchart TB
    IN["新记忆/待审对象"]
    IN --> CF[("core_conflict_queue<br/>冲突待审")]
    IN --> AL[("core_alias_pending<br/>别名待审")]
    IN --> HP[("core_hold_pool<br/>存疑暂存")]
    IN --> QU[("core_quarantine<br/>隔离区")]
    IN --> GV[("core_governor_pending<br/>治理提案")]
    IN --> CB[("core_class_backfill_pending<br/>分类待补")]
    
    CF & AL & HP & QU & GV & CB -->|"人工/夜间裁决<br/>verdict"| OK["放行→写回 core_memory_card"]
    QU -.->|"问题记忆关起来"| QU
```

**概念**：任何"不干净/不确定"的记忆先进对应队列（`verdict IS NULL`=待审），裁决后放行或隔离。这是防止脏数据污染主库的免疫层。

---

## 六、对话记忆 + 版本 + 观测

```mermaid
flowchart TB
    subgraph DIALOG["对话记忆"]
        S[("core_raw_dialog_session<br/>一次完整会话")]
        T[("_tmp_dialog_fragment<br/>捕获缓冲")]
        A[("session_dialog_archive_fragment<br/>碎片归档")]
        L[("core_dialogue_log<br/>单轮记录")]
        S --> T --> A
        S --> L
    end
    
    subgraph VER["版本演进"]
        IT[("iterations<br/>改版履历 why")]
        RP[("core_revision_pending<br/>修订待批")]
        RP -->|"人审adopt"| IT
    end
    
    subgraph OBS["观测黑匣子"]
        TC[("trace_cited<br/>召回足迹 4.5万")]
        AT[("audit_topic_log<br/>主题变更")]
        SM[("audit_schema_migrate<br/>结构迁移")]
    end
```

**概念**：对话过程本身被记忆（会话→碎片→单轮）；每次改版留`why`（iterations）；每次运行留足迹（trace/audit），供回归和对账。

---

## 七、全局关系总图（一张看全）

```mermaid
flowchart TB
    subgraph L1["感官层"]
        RAW[("raw_corpus")]
    end
    subgraph L2["认知内核"]
        MC[("core_memory_card<br/>★唯一真相")]
        REL[("core_relation")]
        FTS[["fts 索引"]]
    end
    subgraph L3["概念组织"]
        TA[("topic_abstraction")]
        TL[("topic_links")]
    end
    subgraph L4["身份"]
        ENT[("core_entity_timeline")]
        ALI[("core_term_alias")]
    end
    subgraph L5["阅历与方法论"]
        EXP[("core_experience")]
        TP[("thinking_paradigm")]
        SP[("skill_paradigm")]
    end
    subgraph L6["性格"]
        PA[("persona_anchor")]
    end
    subgraph L7["治理免疫"]
        Q[("conflict/alias/hold/<br/>quarantine/governor")]
    end
    subgraph L8["对话/版本/观测"]
        DL[("dialog_*")]
        IT[("iterations")]
        TR[("trace_*/audit_*")]
    end

    RAW --> MC
    MC <--> REL
    MC --> FTS
    MC --> TA --> TL
    MC --> ENT
    ALI -.-> ENT
    MC --> EXP
    EXP --> TP & SP
    SP -.-> PA
    Q -.->|"裁决放行"| MC
    DL -.-> MC
    IT -.-> MC
    MC -.-> TR
```

**读法**：
- 实线 = 数据流向（raw→card→relation/topic/entity/experience→paradigm）
- 虚线 = 弱关联/归档/追溯
- ★ `core_memory_card` 是全系统的中心，一切都围绕它
- 治理层（Q）不在主链上，是"旁路的质检站"，裁决后才放行进主库

---

## 八、关键提醒

1. **中心是 `core_memory_card`**，其余表要么喂它（raw/dialog）、要么从它派生（relation/topic/experience）、要么管它（治理队列）。
2. **入口只有四类**，日常自动跑的是 ③ night/y_run；人工干预走 ① hook 写入和 ② query 读取。
3. **`core_` 前缀不代表活**（见台账）：`raw_corpus`/`topic_links` 无前缀才是活的。
4. **图是概念图**，省略了字段级细节和 FTS/归档等辅助表，完整清单看 `db_table_registry.md`。