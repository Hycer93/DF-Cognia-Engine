# 01 memory/ 模块代码地图

> 生成日期：2026-11-25 18:02（全部内容来自逐文件通读源码 + Grep 反查调用方，未抽样）
> 覆盖文件数：30 个 .py（不含 API.md / README_hook.md / corpus_store.db）
> 总行数：约 8037 行
> 总体定性：**全函数式**（唯一 class 是 memory_judge.py 内部私有的 `_PendingLock`，跨进程锁，无业务类）；核心角色 = 认知引擎 v3 的「记忆流水线本体」——写入(闸门/判定/关联/落库) + 检索(四维加权投影) + 维护(E4/E5/E7/E9 巡检治理) 三大职责。存储真相源为 **data/memory/corpus_store.db（单 SQLite）**，2026-11-15 起从 jsonl 双轨切换为「DB 唯一真相 + jsonl 审计存档」。

---

## 一、模块总览表

| 文件 | 行数 | 一句话职责 | 关键入口函数 | 依赖模块 | 被谁调用 |
|---|---|---|---|---|---|
| memory_query.py | 984 | 读侧检索投影：四维加权打分、两段式排序、反问门、治理后置过滤、取件原语 | `ask()`, `fetch_full()`, `fetch_by_ref()`, `cards_from_dialog()`, `cross_view()`, `entity_evolution()` | schema, constants, memory_organize, memory_relations, embed_semantic, memory_alias, topic_layer, corpus_registry, corpus_store | memory_hook(CLI query)、cgraph 推演链、eval E 系列 |
| memory_organize.py | 707 | 原子存储层：入库护栏(校验/副本/P0查重)、repro 判定、DB 双写、demote 降级 | `atomic_store()`, `load_memories()`, `load_active_pool()`, `demote()`, `read_card_detail()`, `append_jsonl()` | schema, constants, embed_semantic, maintenance_ledger | 全仓最高被依赖：memory_hook/judge/relations/query/distill/alias、cgraph/green_card_writer、eval E30/E44/E46/E5/E9、scripts/oneoff |
| topic_layer.py | 560 | L4 Topic 抽象锚层（默认关，TOPIC_LAYER_ON=1 启用）：挂载/路由/冲突闸门/cue 判别 | `classify_topic()`, `mount_topic()`, `topic_route_retrieve()`, `conflict_gate()`, `seed_topic()`, `topic_enabled()` | memory_organize(read_card_detail) | memory_hook.add、memory_relations(闸门)、memory_query(路由)、eval E0/E7/topic_layer_regression、scripts/seed_topics |
| memory_judge.py | 538 | 入库判定卡（LLM 五问 + 规则桩降级）：store/hold/discard 三态 | `judge_and_store()`, `review_holds()`, `_judge_call()`, `_stub_one()` | memory_organize, memory_relations(grams), memory_hook(反向调 add), cgraph.llm_codechat, memory_attitude, schema | memory_hook session 命令、CLAUDE.md 记载主AI手动调 |
| memory_relations.py | 507 | 认知关联层：四档判定(equivalent/augment/conflict/new)、三级级联、entity 图谱 | `classify_and_link()`, `load_graph()`, `_embed()/_cosine()`, `grams()`, `rebuild_graph()` | constants, embed_semantic, topic_layer, memory_organize, memory_quality_gate(detect_placeholder) | memory_hook.add、memory_query/memory_distill/memory_judge(引 _embed/grams)、自检 __main__ |
| memory_hook.py | 394 | 会话接入总入口 CLI：add(单条管道)/query/session(PreCompact hook)/stat | `add()`, `main()`, `_resolve_payload_and_candidates()`, `_parse_transcript()` | memory_organize, memory_relations, memory_distill, topic_layer, memory_gate, memory_judge, dialog_evidence_store, memory_query | CodeChat settings hook(PreCompact/Stop)、主 AI subprocess |
| memory_distill.py | 337 | 经验聚类层（第三层）：沿 entity+behavior 分桶归纳经验，DB core_experience 双写 | `distill_all()`, `load_experiences()` | constants, memory_organize, memory_relations | memory_hook.add(auto_distill)、memory_hook stat |
| dialog_evidence_store.py | 335 | #31 对话原始证据库：碎片表兜底→PreCompact 聚合(快照+逐条) | `ingest_transcript()`, `aggregate_session()`, `collect_orphan_sessions()`, `load_session()/load_turns()`, `init_tables()` | （零 memory 内依赖，纯 sqlite） | memory_hook(_evidence_ingest_and_aggregate)、eval E9/e31、CLI |
| v3_schema.py | 333 | 主库全量建表脚本（core_/audit_/trace_ 三前缀 + 治理队列组） | `build()` | （零依赖） | 一次性执行/重放；运行时代码不 import 它 |
| corpus_store.py | 300 | 语料本体层：raw_corpus 版本链 upsert、树级标签、人格锚、对话段全局序列 | `upsert_corpus()`, `get_corpus()`, `list_corpus()/list_dialog()`, `next_dialog_seq()`, `add_anchor()` | （零依赖） | memory_query.fetch_by_ref、memory_gate.store_dialog、corpus_revision、eval、scripts |
| memory_alias.py | 286 | 卡片级二级备用标签（别名）：失败捕获→换词命中→六条件配对沉淀 | `record_miss()`, `try_pair_on_hit()`, `alias_lookup()`, `effective_alias_map()`, `count_active_pending()` | constants, memory_organize, memory_relations | memory_query.ask、eval E4(L226/239/245) |
| memory_gate.py | 224 | 会话记忆两级门：对话原文落库(碎片拦截+simhash去重) + 深检/归档启发判定 | `store_dialog()`, `_llm_probe()`, `_llm_archive()`, `_simhash()` | corpus_store | memory_hook(_session_dialog_from_hook)、eval E9/dialog_trace_regression、CLI |
| memory_quality_gate.py | 221 | 入库质量门：占位卡复合检测 + 语料入库范式入口 | `detect_placeholder()`, `classify_source()`, `ingest_corpus_evidence()` | corpus_registry | memory_relations(_is_placeholder_card)、E5 语料建卡 |
| schema_migrate.py | 217 | schema 版本管理 + 数据规范化迁移执行器（0001 枚举清洗/0002 僵尸表冻结） | `run_migrations()`, `current_version()` | （零依赖） | 一次性 CLI；新增迁移追加 MIGRATIONS |
| corpus_revision.py | 203 | 原文待采纳修订暂存（血缘+提案，人闸采纳才升版） | `propose_revision()`, `adopt_revision()`, `reject_revision()`, `pending_summary()` | corpus_store | 主 AI/人工（未发现 eval 自动调用方） |
| schema.py | 174 | 枚举唯一权威源：领域/行为/关联树、置信度、memory_class 五类、repro 三级、校验函数 | `validate_memory_tags()`, `validate_domain()` 等 | （零依赖） | memory_organize、memory_judge、memory_query；被全仓 import |
| confidence_dynamics.py | 172 | 置信度升降级叠加层（append-only，不改原子层字段） | `mark_verified()`, `mark_falsified()`, `effective_confidence()`, `confidence_dynamics_scan()` | memory_query(读源复用) | eval E5(L86)、主 AI 会话手动调用 |
| trace_archive.py | 151 | trace_* 流水月度归档（剪切式，行数守恒对账） | `do_archive()`, `global_check()`, `dry_run_report()` | （零依赖） | trace_archive_replay_test、定时任务（未发现其他调用方） |
| render_persona.py | 149 | 身份 SystemPrompt 渲染衍生物（anchor+范式两表→prompt md） | `render()`, `write_with_diff()` | （零依赖） | 手动 CLI 一次性；产物 prompts/prompt_persona_v*.md |
| entity_resolve.py | 130 | #49 阶段2 别名归并+实体锚查找（core_term_alias 活查询） | `resolve_alias()`, `resolve_entity()` | （零依赖） | 范式 sk_fragment_assembler 层1；文档记载已自测（暂无生产调用方） |
| term_alias.py | 129 | 术语级受控别名桥（query 翻译通道，与卡片级 alias 正交） | `register_term_alias()`, `expand_query()` | memory_organize | 阶段一登记/CLI；**expand_query 未接入检索主链**（阶段二未落） |
| corpus_registry.py | 129 | 语料资产契约登记表（sim_ok/distill_ok/版本锚 hash） | `register()`, `load_registry()`, `verify_hashes()`, `refresh_hash()` | （零依赖） | memory_query(_governance_families)、memory_quality_gate、eval E4(L319) |
| memory_behavior_backfill.py | 125 | behavior 编码存量回填提案器（E5 提案→judge 确认→写码） | `propose_batch()`, `confirm_and_write()` | schema | eval E5(L78) |
| paradigm_review.py | 122 | 范式提案评审只读视图（晋升/标定/衰减分组） | `collect()`, `main(pid)` | （零依赖） | 人工终审 CLI（只读） |
| maintenance_ledger.py | 113 | 维护案例账本：运维案例自动采集 + 范式抽象 | `append_case()`, `paradigm_candidates()`, `freeze_paradigm()` | （零依赖） | memory_organize.demote(自动挂点)；晨报消费 |
| memory_attitude.py | 111 | #11 态度元标签附属存储（人看，机器禁消费） | `record_attitude()`, `list_attitudes()`, `summarize_attitudes()` | memory_organize(append_jsonl) | memory_judge(LLM attitude 子通路)、eval attitude_regression |
| embed_semantic.py | 101 | bge-small-zh ONNX 语义嵌入（512 维 CLS 池化+缓存） | `embed()`, `embed_cached()`, `warmup()` | onnxruntime, tokenizers | memory_relations、memory_query、memory_organize(P0)、memory_alias |
| constants.py | 100 | 全局常量分级表（阈值/权重/停用词/框架词，标注[先验/实测/冻结]） | —（纯数据） | — | 几乎所有 memory 模块 |
| trace_archive_replay_test.py | 94 | trace 归档影子库回放测试（合并视角计数守恒断言） | `main()`, `cited_counts()` | trace_archive | 独立测试脚本 |
| memory_class_backfill.py | 91 | memory_class 存量回填提案器（E5 提案→judge 确认） | `propose_batch()`, `confirm_and_write()` | （零依赖） | eval E5(L70)、scripts/oneoff/backfill_20261123 |

---

## 二、四条主链路流程图

### 2.1 写入链路

```
会话/主AI候选记忆
   │
   ▼
memory_hook.add(domain,behavior,relation,confidence,summary,detail,...)
   │  tags 组装 (可选 memory_class / repro / event_ts / origin)
   ▼
memory_organize.atomic_store(summary, detail, source_ref, tags, ...)
   ├─① schema.validate_memory_tags()   非法 → quarantine.jsonl (拒收)
   ├─② _detail_quality_issue()         质量差只警告 → gate_log.jsonl
   ├─③ _converge_summary()             摘要收敛(≤200字, 句子边界)
   ├─④ full_hash 精确副本比对 load_active_pool() → replica_intercepts.jsonl
   ├─⑤ #30 P0 语义等价查重 _p0_equiv_check() (DF_SUMMARY_EQUIV_DEDUPE=off/dry-run/on, 阈值0.95)
   │      命中 → equiv_intercepts.jsonl + 返回 replica
   ├─⑥ judge_repro() 三级判定 + repro 溯源(rule/manual/migration) + repro_override 留痕
   ├─⑦ 写 memories.jsonl (审计快照) + _write_card_to_db() → core_memory_card (唯一真相, INSERT OR IGNORE + BEGIN IMMEDIATE + 重试)
   ▼
memory_hook.add(auto_link=True)
   ├─ memory_relations.classify_and_link(inst_id)
   │    ├ 前置过滤: corpus硬跳过 / multi_topic冻结 / topic_gate(规约4) / 同entity折叠 / 占位卡封顶分流
   │    ├ _combined_sim() = sem(bge余弦)×0.7 + DFCP先验×0.3
   │    ├ 过conflict线 → _cascade_sem() 三级级联(summary→detail→source_ref, 回落≥0.15即非同源)
   │    ├ 四档: equivalent/augment/conflict/new (constants.DOMAIN_SIM_THRESHOLDS 分域)
   │    ├ → relation_graph.jsonl (图谱, 带embedding_base可重算) + code_changelog.jsonl(entity_id回填)
   │    └ conflict 档 → conflict_queue.jsonl (去重入队, 占位对分流 summary_only_suspects.jsonl)
   ├─ topic_layer.mount_topic()  [TOPIC_LAYER_ON=1 时]
   │    └ classify_topic: anchor→topic_links表 / hold→topic_hold_pool.jsonl + core_hold_pool双写
   └─ memory_distill.distill_all() (auto_distill=True)
        └ entity+behavior 分桶 → ≥2原子且相干 → experiences.jsonl + core_experience 双写
```

另一条写入旁路（对话原文，不走记忆卡）：
```
PreCompact hook stdin → memory_hook.session
  → _session_dialog_from_hook → memory_gate.store_dialog(body, session)
      ├ ≤20字节碎片拦截(too-short-skip) / simhash 当日去重(duplicate-referenced)
      └ → corpus_store.upsert_corpus() → raw_corpus(kind=session_dialog, id=dialog.YYYY-MM-DD.hNNNN, next_dialog_seq 取号)
  → dialog_evidence_store.ingest_transcript() → _tmp_dialog_fragment
  → dialog_evidence_store.aggregate_session() → core_raw_dialog_session(快照真相) + core_dialogue_log(逐条) 
  → memory_judge.judge_and_store(candidates) 走判定管道
```

### 2.2 判定链路（五问 + 三级 repro）

```
memory_judge.judge_and_store(candidates, context)   [Jw-G21 钩子: 执行完成/修复验证/会话收尾触发]
   ├ 批量上限 JUDGE_BATCH_LIMIT=8, 超限截断记 judge_log.jsonl
   ├ _judge_call 调度: JUDGE_USE_LLM=0→纯桩; 否则 LLM 优先(CodeChatLLM, 五问system prompt), 整体失败→切桩
   │   ├ _bloodline(): 本地预计算 Q4 证据(2-gram重叠, ov≥6→conflict_suspect), 数值不交LLM猜
   │   ├ _stub_one(): 规则桩(Q1 summary≥8 / Q2 detail≥30 / Q3 白名单 / Q4 血缘 / Q5 分池)——LLM畸形条单条降桩
   │   └ LLM 可选输出 attitude → memory_attitude.record_attitude() (附属JSONL, 红线: 机器不消费)
   ├ verdict=store → memory_hook.add() 直接入库(免人确认, 但 judge_log 全快照留审计)
   ├ verdict=hold  → store_pending.jsonl (+ hold_reason=evidence_gap)
   ├ verdict=discard → 仅记日志
   └ 任何异常 → fallback-hold (judge_failure 标记, 不丢不强discard, E4 计积压)

review_holds(context)  [hold 转正契约: 同实体链再次命中 Jw 触发时]
   ├ _PendingLock 跨进程锁(lockfile+pid+过期判定), 抢不到跳过本轮
   └ 第六问"证据是否补齐": 血缘已现→store; 反向证据→discard; 不足→维持hold追加reviews
```

三级 repro（memory_organize.judge_repro，2026-11-25 核查定案）：
```
behavior∈{B00,Fp,Fq,Fr} → derived
有 affect_tags          → flash
domain 以 005 开头       → flash
其余                     → situational
（Ht 不参与规则化判定; 上游显式传值 repro= → manual/migration 溯源 + gate_log repro_override 留痕）
derived → dfcp_tag.derived_only=true, 业务读 detail 须过 read_card_detail(purpose∈dedupe/audit/governance)
```

### 2.3 检索链路

```
query(自然语言 [+query_tag 编码先验 domain/behavior/relation/memory_class])
   ▼
memory_query.ask(query, query_tag, top_k, include_corpus, include_fiction, cite_source)
   ├ load_memories()  读源分派: DF_MEMORY_SOURCE(默认sqlite: core_memory_card WHERE status='active'; jsonl应急回切)
   ├ entity_id 以图谱末值修正 (主记录字段滞后, 不修正则同entity去重失效)
   ├ _parse_time_window() 时间表达式(上周/最近N天/5月末…); overlay=event_ts_backfill.jsonl
   ├ embed_semantic.warmup() 批量预热 + _embed(query)
   ├ ma.alias_lookup(query) 别名二级召回 (别名词并入kw池)
   ├ topic_layer 路由: cue 命中 top-2 主题 → topic_boost(+TOPIC_ROUTE_BOOST=0.10) [开关关时零行为]
   ├ 逐卡打分:
   │   match_eff = max(code,kw)×W_CODE_MATCH(0.30) + sem×0.30 + kw×0.30 + topic_boost
   │     ├ _code_match_score(): 编码命中层数 + L3缺省折减 + memory_class维(0.05, null不扣分)
   │     └ _keyword_score(): 2-gram连续链过滤碎片 + 停用词 + 框架词分流(fw_only, sem≥0.45/code/alias 命中即屏蔽)
   │   bias_eff  = conf(CONF_SCORE L0=1.0~L4=0)×0.25 + cited×0.25 + rel_act×0.20
   │   total = (match+bias) × time_score(窗内1.0/窗外0/未知TIME_UNKNOWN_SCORE=0.3)
   ├ 两段式排序: 段1 合格池(match≥GATE_HARD_PASS=0.22, cap=20) → 段2 池内 match+min(bias,BIAS_CAP=0.30)
   │   池审计 → pool_audit.jsonl (两次写入, 带ts)
   ├ 治理后置过滤: _governance_families()(corpus_registry mtime缓存) + _card_family()
   │   滤 sim_ok=False 族 / fiction(非include_fiction) / superseded → gov_blocked 落审计
   ├ 同 entity 互斥去重 → top_k
   ├ 调用回写: trace_cited(sqlite主写) + cited_log.jsonl(双轨), source=e8 不喂肥 recency
   ├ 反问门(双门分层):
   │   match≥0.22 → hit (+ma.try_pair_on_hit 配对别名)
   │   match≤0.12 → uncertain (+ma.record_miss 挂起)
   │   中间带 → _has_session_anchor 二次校验(code/recent/chain) → marginal_hit / marginal_ask
   └ ask.last_verdict / last_pool_audit / last_time_window 挂函数属性
取件最后一公里: fetch_by_ref(source_ref)→corpus_store.get_corpus (superseded 显式拦) / fetch_full(inst_id) / cards_from_dialog(dialog_key)反向追溯
CLI: memory_hook.py query → 打印 verdict/时间窗/候选
```

### 2.4 维护/治理链路

```
eval/core 夜间管线 (scripts/nightly_run.py 编排):
  E4 e4_maintenance_patrol (巡检晨报)
    ├ ma.effective_alias_map() L227 / ma.count_active_pending() L239  ← 设施③a: alias_pending 真积压(active)与终态(terminal)分列
    ├ cr.verify_hashes() L319 (corpus_registry 版本锚漂移)
    └ mo 各护栏计数 (quarantine/replica/gate_log)
  E5 e5_consolidation (整理)
    ├ mcb.propose_batch() → class_backfill_pending.jsonl → confirm_and_write() 写 dfcp_tag
    ├ mbb.propose_batch() → behavior_backfill_pending.jsonl (同纪律)
    └ confidence_dynamics_scan() 并入 ops
  E7 e7_nightly_governance (夜间治理/审批官)
    ├ topic_layer._cue_discrimination 复检 / core_hold_pool 消费 / 转正挂载
    └ refresh_topics / archive_idle_topics (规约6 锚生命周期; manual_seed 豁免)
  E9 e9_dialog_governance (对话归档主通道)
    ├ gate._llm_archive 高价值判定 (旁路) / 夜间扫描主通道
    └ dialog_evidence_store.collect_orphan_sessions(72h) 收拢悬空碎片
  E0 e0_topic_gate_guard: 探针写卡验证 mount_topic 自动触发 + hold 池落行
维护修复动作:
  mo.demote(inst_ids, superseded_by, reason, calibration_source) 外部校准降级
    ├ tier→precedent + status→superseded(同叠防混叠) + 备份 + jsonl 重写
    ├ _sync_demote_to_db() UPDATE core_memory_card (#14 根因修复)
    └ maintenance_ledger.append_case() 自动采集运维案例
  trace_archive.do_archive(): trace_* 30天热窗 → trace_archive_YYYY_MM.db (行数守恒对账, audit_maintenance留痕)
  schema_migrate.run_migrations(): 迁移版本表推进 (0001 枚举清洗/0002 僵尸表冻结)
  trace_archive_replay_test: 影子库回放验证归档不影响 cited 计数
```

---

## 三、逐模块详解（按链路分组）

### 3.1 memory_organize.py（写入·原子存储层）
- 职责：三层架构之第一层「原子存储层」。入库唯一真相写入口；唯一 inst_id/full_hash 权威；相似/冲突判定不在本层。
- 关键函数：
  - `atomic_store(summary, detail, source_ref, tags, asset_class="runtime", origin_ids=None, event_ts=None, origin="business") -> (status, msg)` — status ∈ stored|replica|quarantined。护栏链：校验→detail质量警告→摘要收敛→full_hash 副本→P0 语义等价→repro 三级+溯源→jsonl+DB 双写。
  - `judge_repro(content, tags) -> str` — 三级可复现性规则判定（B00/Fp/Fq/Fr→derived；affect/005域→flash；余→situational）。Ht 强判会误伤 77 条，明文不做规则化。
  - `load_memories() -> list` — 读 **memories.jsonl**（注意：审计快照，非真相源）。`load_active_pool() -> list` — DB status/tier 双 active 查重池（#30 R1）。`read_card_detail(entry, purpose) -> str|None` — derived_only 泄露防线（R4）。`demote(inst_ids, superseded_by, reason, calibration_source=None) -> dict` — 三态 applied/skipped/conflicts，conflict 整批回滚，批量缺 calibration_source 硬拒。`change_code()`, `append_jsonl(path, obj, fsync=False)`, `_write_card_to_db(entry, max_retries=3)`（BEGIN IMMEDIATE+指数退避）, `_sync_demote_to_db()`, `supersede_chain_length()`（链上限8跳检测）。
  - 开关：`SUMMARY_EQUIV_MODE`（DF_SUMMARY_EQUIV_DEDUPE，off/dry-run/on），`SUMMARY_EQUIV_THRESHOLD=0.95` 带 `assert ≥0.95` 静态断言。
- 依赖：schema, constants, embed_semantic(P0), maintenance_ledger(demote 挂点)。
- 被调用：全仓最核心（见总览表）。
- 备注：⚠ 坑：`MEM_DIR = <root>/data/memory`，`_DB_PATH` 在 memory_query 等各模块重复定义——测试隔离须 monkeypatch 模块属性或设 MEM_DIR，改 os.environ 无效（读入即固化）。2026-11-24 定案：jsonl 禁作统计源（更新只写 DB 不回写 jsonl）。

### 3.2 memory_hook.py（写入·管道编排入口）
- 职责：会话接入唯一入口；管道顺序 atomic_store → classify_and_link → mount_topic → distill_all。
- 关键函数：`add(...)` — 单条入库+联动，返回 report{store,link,topic,distill}；`_resolve_payload_and_candidates(file_raw, stdin_raw, has_file_flag)` — #31 接线：--file(候选) 与 stdin(PreCompact payload) 两独立来源必须都读；`_parse_transcript(path)` — transcript JSONL→【用户/模型】对话文本；`_session_dialog_from_hook(payload)` — 优先 transcript，降级 dialog_last_segment.md 旧通路；`main()` — CLI add/query/session/stat。
- 依赖：memory_organize, memory_relations, memory_distill, topic_layer, memory_gate, memory_judge, dialog_evidence_store, memory_query。
- 被调用：CodeChat hook（settings.json PreCompact/Stop）、主 AI subprocess。
- 备注：add() 里有一段「先取 inst_id 再精确定位」的冗余赋值（L48-50 被 L51-54 覆盖），读代码别被第一段误导。退出码纪律：judge_failures>0 → RC3；未捕获异常 → RC4 + [memory_hook_abnormal]。

### 3.3 memory_gate.py（写入·对话门）
- 职责：h 通道对话原文落 raw_corpus + 深检/归档启发判定（LLM 升级位留接口，当前本地信号词）。
- 关键函数：`store_dialog(body, session, source_tag) -> dict` — ≤20 字节拦截 too-short-skip；当日 simhash(64bit, hamming≤6) 去重 duplicate-referenced；`next_dialog_seq()` 取号写 raw_corpus。`_llm_probe(topic)`（深检信号词判定，失败保守 true）、`_llm_archive(body)`（高价值信号密度）。
- 依赖：corpus_store。
- 被调用：memory_hook、eval E9。
- 备注：gate_log.jsonl / gate_miss_case.jsonl 落 data/memory/。simhash 用内置 `hash()`（进程间不稳定，仅当日去重够用）。

### 3.4 dialog_evidence_store.py（写入·对话证据库）
- 职责：#31 三表——core_dialogue_log(逐条检索)、core_raw_dialog_session(快照真相源，运行时记忆流水线禁读)、_tmp_dialog_fragment(碎片兜底)。
- 关键函数：`append_turn_fragment(session_id, msg_obj)`（幂等去重）、`ingest_transcript(session_id, transcript_path, **meta)`、`aggregate_session(session_id, ref_archive_fragment, terminate_reason, ...)`（快照 upsert + 逐条 content_hash 去重 + 碎片标 consumed）、`collect_orphan_sessions(max_age_hours=72)`、`load_session/load_turns`、`init_tables()`。
- 依赖：零（纯 sqlite，WAL）。
- 被调用：memory_hook、eval E9/e31。
- 备注：红线：绝不 _parse_transcript 扁平化，messages 原样 JSON。hook 事件→terminate_reason 映射表 `_HOOK_EVENT_TO_REASON` 为未来 Stop/SessionEnd 预留。

### 3.5 memory_judge.py（判定·五问判定卡）
- 职责：入库时机从「LLM 自觉」变「拓扑强制」；五问门槛链 store/hold/discard。
- 关键函数：`judge_and_store(candidates, context=None) -> report`；`_judge_call(candidates, context)`（LLM/桩调度，JUDGE_USE_LLM）；`_judge_call_llm`（CodeChatLLM + extract_json_obj，单条畸形降桩）；`_stub_one(c)`（桩五问，Q1 early-return discard——P2-7 修复）；`_bloodline(c)`（Q4 本地预计算）；`review_holds(context)`（第六问转正契约 + 跨进程锁）；`_atomic_write_jsonl`、`_PendingLock`（唯一 class）。
- 依赖：memory_organize、memory_relations(grams)、cgraph.json_utils/llm_codechat、memory_hook(add，注意**与 memory_hook 互相 import**——运行期函数内 import 规避环)、memory_attitude、schema。
- 被调用：memory_hook session、主 AI。
- 备注：JUDGE_BATCH_LIMIT=8 / HOLD_MAX_AGE_DAYS=14。attitude 仅 LLM 通路产出，桩天然零副作用。

### 3.6 memory_relations.py（判定·认知关联层四档）
- 职责：摘要语义+DFCP 加权相似、四档判定、entity 图谱维护、冲突队列入口。
- 关键函数：`classify_and_link(inst_id, top_k=5) -> dict`（报告 relation/entity_id/best_match）；`_combined_sim()`（sem×0.7+dfcp×0.3，同域 dfcp 回声 sem——只压不抬）；`_dfcp_proximity()`（同域回声/子域0.5/跨域同行为0.3/跨域0.15）；`_cascade_sem()`（三级级联，CASCADE_DROP=0.15）；`_is_placeholder_card()`（复用 quality_gate + 短文本/「详情层：」模板独立判）；`grams(text)`（2-gram，关键词层专用）；`_embed()/_cosine()`（bge 512 维）；`load_graph()/rebuild_graph()`；`_backfill_entity()`（entity_id 经 code_changelog 追加回填，图谱末值为事实源）；`_conflict_pair_pending()`（入队去重，2026-11-12 债③修复）；`_cross_pool_warn()`（#30 P2 跨池等价告警，纯旁路）。
- 依赖：constants, embed_semantic, topic_layer(conflict_gate), memory_organize, memory_quality_gate。
- 被调用：memory_hook、memory_query/memory_distill/memory_judge(引 _embed/grams/load_graph)。
- 备注：EMBEDDING_BASE="bge-small-zh-v1.5-onnx-int8"——换基座须全量重标 + rebuild_graph。`_dfcp_proximity` 用 `.get("behavior")` 防 15 张旧卡缺 behavior 抛 KeyError（2026-11-23 补丁）。

### 3.7 memory_distill.py（判定·经验聚类层）
- 职责：第三层——entity_id+behavior 分桶归纳经验，derived_from 溯源（新增非替代）。
- 关键函数：`distill_all(min_cluster=2, sim_threshold=0.5) -> report`（排除 conflict_pending/fragile/无entity/缺behavior；桶内相干性复核；增量并入 + DERIVED_FROM_MAX=50 上限分裂）；`load_experiences()`（DB core_experience 优先，jsonl 兜底，列优先 meta 补缺）；`_upsert_experience_db()`；`_synthesize_summary()`（Week1 公共2-gram拼主题，真归纳留Week2 LLM）。
- 依赖：constants, memory_organize, memory_relations。
- 被调用：memory_hook.add(auto_distill)。
- 备注：缺 behavior 卡显式告警不静默吞（2026-11-25 加固）。经验 tier 恒 merged，固化门禁在上层。

### 3.8 memory_query.py（检索·读出口）
- 职责：三层架构之读出口；四维加权+两段式排序+反问门+治理过滤+取件原语。
- 关键函数：见 2.3 流程图；另 `entity_evolution(entity_key, as_of)`（B 认知视角双过滤：event_ts AND ts 都 ≤ as_of）、`load_entity_timeline()`（entity_timeline.jsonl）、`_parse_source_ref()`（sqlite:corpus_store.db#树地址 解析）、`cards_from_dialog(dialog_key)`（三种形态弱关联反查）。
- 依赖：schema, constants, memory_organize(MEM_DIR/append_jsonl), memory_relations(_embed/_cosine/grams), embed_semantic, memory_alias, topic_layer, corpus_registry, corpus_store(fetch)。
- 被调用：memory_hook query CLI、cgraph 推演、eval。
- 备注：⚠ 读源开关 `DF_MEMORY_SOURCE` 是**进程启动时**固化的模块级常量 `_MEMORY_SOURCE`，运行中改环境变量无效。`_recency_cache`/`_GOV_CACHE` 均模块级缓存，测试须注意失效。ask() 属性挂载 last_verdict/last_pool_audit/last_time_window 是下游消费契约。

### 3.9 embed_semantic.py（检索·语义基座）
- 职责：bge-small-zh-v1.5 int8 ONNX，512 维 CLS 池化 L2 归一。
- 关键函数：`embed(texts) -> (n,512)`、`embed_cached(text)`、`warmup(texts)`（批量 128，159条 8s→<1s）、`cosine(a,b)`。
- 依赖：onnxruntime、tokenizers；模型在 `calibration/embed_bge_small_zh/`。
- 被调用：memory_relations/query/organize(P0)/alias。
- 备注：seq 截 64 token（62+2），长 detail 传参处已由调用方切片（512/256 字）。`_cache` 进程级字典，按 text 键。

### 3.10 memory_alias.py（检索·二级备用标签）
- 职责：失败→反问→换词命中→六条件配对沉淀别名（append-only 叠加层）。
- 关键函数：`record_miss(query, match_eff, verdict)`；`try_pair_on_hit(query, hit_entry, top_match)`（C1 TTL24h/C2 hard_pass/C3 话题cos≥0.15/C4 实词/C5 弱交集≥1 gram/C6 非保护——ALL 判定严禁放松）；`alias_lookup(query)`；`effective_alias_map()`（L3 30天零命中自动失效，物理行保留）；`mark_alias_hit()`（命中续命）；`reactivate_alias()`（人工复活）；`count_active_pending() -> {total_rows, active, terminal, by_status}`（设施③a 2026-11-25：E4 真积压口径，实测 231 条中 active 仅 22）。
- 依赖：constants, memory_organize, memory_relations。
- 被调用：memory_query.ask（record_miss/try_pair_on_hit/alias_lookup）、eval E4。
- 备注：pending 队列 append-only 含终态（rejected_weak_overlap 209 条），len() 计数会虚报积压——统计必须用 count_active_pending。try_pair_on_hit 重写 pending 文件（唯一非 append 处，有意的状态推进）。

### 3.11 term_alias.py（检索·术语翻译通道，阶段一）
- 职责：query 用户词→体系标准术语；与卡片级 alias 正交。
- 关键函数：`register_term_alias(term, canonical, origin, note)`（宽泛词黑名单拒绝；canonical<4 字拒；canonical 必须在库内 summary 中真实存在）；`expand_query(query) -> {expanded, applied}`（限 1 次替换防语义漂移）；`term_alias_stats()`。
- 依赖：memory_organize。
- 被调用：**未发现生产调用方**（阶段二「一级精确锚失败→翻译重跑」未接入 memory_query.ask）。
- 备注：登记通路可用（CLI），读侧通路处于半接线状态；memory/entity_resolve.py 才是 core_term_alias 的 DB 侧读取者。

### 3.12 topic_layer.py（治理·Topic 锚层，默认关）
- 职责：L4 粗粒度降噪索引；只管候选池范围与冲突比对进入，不碰底层事实（规约5）。
- 关键函数：`classify_topic(summary, detail)`（anchor/hold/new 三判；cue 仅 detail 命中=引用性提及疑似→hold）；`mount_topic(inst_id, summary, detail, method)`（32 位全 id 强校验 + 卡表存在性校验防幽灵挂载；hold 双写 jsonl+core_hold_pool）；`topic_route_retrieve(query, subset_fetch, fallback_fetch, ...)`（fetch 注入解耦，多路并集，兜底永不断流）；`conflict_gate(inst_a, inst_b)`（单向拦截不同 topic）；`seed_topic(...)`（cue 判别力回测 v2.2：disc=n_in/(n_in+n_other+n_dispute)≥0.5 且 total_obs≥3；manual_seed 三枷锁）；`mount_batch()`；`refresh_topics()/archive_idle_topics()`（E7 挂载）；`browse_topic()/stats()`；`topic_enabled()`（TOPIC_LAYER_ON=1）。
- 依赖：memory_organize(read_card_detail/load_memories)。
- 被调用：memory_hook、memory_relations、memory_query、eval E0/E7/topic_layer_regression、scripts/seed_topics。
- 备注：注意本模块建的是**旧表 topic_abstraction/topic_links**（非 v3_schema 的 core_topic_abstraction/core_topic_links），两套表并存待迁移对账。cue 区分度统计遍历全库，慢——仅建锚/复检时用。

### 3.13 memory_quality_gate.py（写入·质量门）
- 职责：占位卡复合检测（缺口3）+ 语料入库范式（缺口2，stable/ephemeral 分支）。
- 关键函数：`detect_placeholder(summary, detail, source_ref) -> {is_placeholder, similarity, hint}`（相似≥0.60 AND 无新增实体(剔胶水gram后>30%算新增) AND 无谓词/指针，三条件同时成立；极简规约豁免：完整命题/量词型）；`classify_source(content_type)`（stable 需 detail≥120字+evidence；ephemeral fragile=True）；`ingest_corpus_evidence(corpus_id, extract_fn)`（registry 契约核验 sim_ok/distill_ok → 提取 → 占位拦截 → items 交调用方走 atomic_store，不旁路护栏）。
- 依赖：corpus_registry。
- 被调用：memory_relations（占位检测）、E5 语料建卡。
- 备注：判 placeholder 不 dismiss，分流 summary_only_suspects 保留告警通道。

### 3.14 corpus_store.py（存储·语料本体层）
- 职责：raw_corpus（树地址+复合主键(id,version)+版本链 supersede）、corpus_tags、iterations、dialog_seq、session_dialog_archive_fragment；人格锚（写已迁 core_persona_anchor）。
- 关键函数：`upsert_corpus(id_, kind, body, *, tree_vol, tree_ch, title, source_ref) -> hash`（同 id 再写=新 version，旧版 superseded，iterations 记 create/revise+why）；`get_corpus(id_, version=None)`（默认 current）；`list_corpus()/list_dialog()`；`add_tags()`/`query_by_tag()`；`next_dialog_seq()`（全局单调序列原子取号，业务禁 SELECT MAX+1）；`add_anchor()`；`get_iterations()`。
- 依赖：零。
- 被调用：memory_gate、memory_query(fetch)、corpus_revision、eval、scripts。
- 备注：_conn() 显式 WAL+wal_autocheckpoint=1000。旧 persona_anchor 表冻结（P2-1 deprecated），写入唯一真相在 core_*。

### 3.15 v3_schema.py（存储·全量建表）
- 职责：主库 schema 唯一定义源（core_* 22张 / audit_* 8张 / trace_* 3张，含 FTS5 两张、治理队列 10 张）。
- 关键函数：`build()`（幂等 executescript；旧表并存不 DROP，迁移对账后再冻结）。
- 依赖：零。被调用：一次性/运维执行，运行时不 import。
- 备注：表清单详见第四章。core_memory_card 24 列（含 superseded_by/status/memory_class 冗余列）。

### 3.16 schema.py + constants.py（规约源）
- schema.py：DOMAIN_TREE（5域+000，域-子域[-场景]三级）/ BEHAVIOR_TREE（25 骨架码 G00-G26）/ RELATION_TREE（P/C/F/H/D/Q/X/S 七类边白名单）/ CONFIDENCE_LEVELS / ASSET_CLASS_ENUM{corpus,runtime} / REPRO_TIERS / MEMORY_CLASS_ENUM 五类+null 合法 / `validate_memory_tags(tags)`（护栏①全枚举校验）。**枚举唯一权威源**，改枚举须四处同步（本枚举/judge Q3/design §7.6/E4 口径）。
- constants.py：检索权重（W_CODE_MATCH=0.30/W_CONFIDENCE=0.25/W_CITED_STATS=0.25/W_RELATION_ACTIVATE=0.20/W_MEMORY_CLASS=0.05）、四档分域阈值（DOMAIN_SIM_THRESHOLDS，bge 基座 2026-11-11 重标）、反问门双阈值（GATE_HARD_PASS=0.22 实测 F1 最优 / GATE_HARD_ASK=0.12）、BIAS_CAP=0.30、QUALIFIED_POOL_CAP=20、TOPIC_ROUTE_BOOST=0.10、停用词/框架词表、TIME_UNKNOWN_SCORE=0.3。每个常量标 [先验-可校准]/[实测标定]/[设计冻结] 来源等级。

### 3.17 schema_migrate.py（治理·版本迁移）
- 职责：schema_version 元表 + MIGRATIONS 有序注册表 + 事务内执行+verify 断言。
- 迁移：0001 枚举规范化（observation→NULL 快照留 audit_schema_migrate；浮点 confidence→L 档映射）；0002 persona_anchor 僵尸表冻结标记。
- 备注：纪律——新迁移只追加末尾，禁改已发布项。CLI：--status / --dry-run。

### 3.18 corpus_registry.py（治理·语料契约）
- 职责：语料资产登记（sim_ok/distill_ok/readonly/expiry/fragile + content_hash 版本锚）。
- 关键函数：`register(...)`、`load_registry()`、`verify_hashes() -> drifted`（db 类语料走 kind_anchor 行级聚合 hash——2026-11-16 根修：整库 hash 会因 session_dialog 每晚写入假漂移）、`refresh_hash(corpus_id)`（prev_hashes 留痕）、`eligible_for_sim()`。
- 被调用：memory_query（治理过滤映射，mtime 缓存）、memory_quality_gate、eval E4。
- 备注：registry 缺失时 memory_query `_governance_families` 打 stderr 告警返回 {}（P2-5，不再静默）。

### 3.19 corpus_revision.py（治理·轻量版本记忆）
- 职责：讨论产出修订想法→core_revision_pending 挂血缘提案→人闸采纳才 upsert_corpus 升版。
- 关键函数：`propose_revision(target_id, change_note, *, source_type, source_id, proposed_body, target_version)`（缺省自动取当前版号）；`adopt_revision(rpid, body, resolved_by)`（基线漂移校验：当前版号≠提案版号抛 ValueError）；`reject_revision()`；`list_pending()/get_pending()/pending_summary()`。
- 依赖：corpus_store。
- 被调用：主 AI/人工（未发现 eval 自动消费方）。
- 备注：红线——pending 阶段绝不改 raw_corpus 正文。

### 3.20 memory_class_backfill.py / memory_behavior_backfill.py（治理·存量回填提案器）
- 二者同纪律铁律：**提案器永不直写主库**——propose_batch 写 pending 队列（class_backfill_pending.jsonl / behavior_backfill_pending.jsonl），confirm_and_write 经人/judge 确认才写 dfcp_tag（code_changelog 留审计）。
- class：behavior 先验（Dl/Bc→scar_case 等）+特征词双通道，无据→null 提案（合法）。behavior：开头 [类型标签] 优先（定稿/定案/终裁→Ht；复盘/闭环→Jw），都无→交人工。behavior 的 confirm 前会 schema.validate_behavior 校验。
- 被调用：eval E5（每晚报案）、scripts/oneoff。
- ⚠ 坑：二者 `_load()` 直读 **memories.jsonl**（审计快照）而非 DB——与「DB 唯一真相」纪律存在张力；confirm_and_write 重写 jsonl 后并不回写 DB（demote 路径有 _sync_demote_to_db，回填路径没有）。改这两个文件前先核 DB/jsonl 是否漂移。
- 数据现状（2026-11-25 诊断）：class_backfill_pending 167 条中 159 为过期提案（卡已回填），真待处理 6 条。

### 3.21 confidence_dynamics.py（治理·置信度动态）
- 职责：L3臆测证实→L1（UPGRADE）；L0/L1 证伪→L2（DOWNGRADE）。叠加层 append-only（confidence_dynamics.jsonl），不改原子层 confidence 字段。「同类型」口径=编码三元组。
- 关键函数：`mark_verified(inst_id, evidence, new_summary, source)`、`mark_falsified(inst_id, evidence, retrofix_note)`（retrofix_pending 找 relation_graph P/D 边下游——⚠ `_find_dependents` 读的键名是 `e.get("from")/e.get("rel")/e.get("to")`，与 relation_graph.jsonl 实际字段名 inst_id/entity_id/relation **不匹配**，疑似旧格式残留，下游追溯当前恒空，用前须核对）、`effective_confidence(inst_id)`、`confidence_dynamics_scan()`（E5 兜底统计）。
- 依赖：memory_query（读源复用，防双读漂移，jsonl 兜底）。
- 备注：L0 实测证伪是最高可靠，DOWNGRADE 把 L0 也降 L2 是照语料原文（origin_corpus3）实现的。

### 3.22 trace_archive.py + trace_archive_replay_test.py（治理·流水归档）
- 职责：trace_cited/trace_call/trace_embedding 30 天热窗剪切式月度归档至 trace_archive_YYYY_MM.db；对账行数守恒（显式 RuntimeError 替代 assert——python -O 下 assert 消失的教训 P2-2）；global_check 全库对账。
- replay_test：复制影子库→灌跨 85d~3d 伪造数据→归档→断言合并视角 cited 计数与基线一致（HLTM 权重读取口径）。
- 被调用：定时任务/手动；replay_test 独立。
- ⚠ 坑：`DB = "data/memory/corpus_store.db"` 是**相对 cwd 的路径**，必须从仓库根运行；replay_test 靠运行时改 `ta.DB` 重定向。

### 3.23 render_persona.py（衍生物·身份 Prompt 渲染）
- 职责：读 core_persona_anchor + core_persona_evolution + thinking/skill 范式两表 → prompts/prompt_persona_v{N}.md（排序：active 优先，evidence_pass DESC，>90d 衰减排尾标 [衰减待激活]）；write_with_diff 写新版+归档旧版+diff_log。
- 被调用：手动一次性；产物 v1 已存在（2026-11-15 渲染）。
- 备注：纯衍生物——真相在库，禁手改渲染文件；同样用相对 cwd 路径。

### 3.24 entity_resolve.py（#49 阶段2·fragment_assembler 层1）
- 职责：core_term_alias 别名归并 + core_entity_timeline 实体锚查找 + 消歧告警（禁强合并，多义两义都返回）。
- 关键函数：`resolve_alias(term, con=None)`（多 canonical→ambiguous 告警；term 自身是 canonical 反查回认）；`resolve_entity(term, con=None)`（无实体锚→降级纯关键词提示）；`_ensure_rows(con)`（外部连接 row_factory 防御——#49 修过 tuple indices 报错）。
- 被调用：范式层（sk_fragment_assembler op 2/4 步）；**未发现生产链路调用方**，当前是「写好待接」状态。
- 备注：只读连接（mode=ro）。

### 3.25 memory_attitude.py（#11·态度附属存储）
- 职责：LLM 判定带出的 approve/disapprove/neutral 态度元标签 → memory_attitude.jsonl 附属存储。
- 关键函数：`record_attitude(attitude_type, target_memory_id, comment, source_dialog_id, context)`（非法类型/缺 target 不落库）；`list_attitudes()/summarize_attitudes()`。
- 红线：机器绝不据此调整可信度/存活；推演/F20/检索打分默认不消费。
- 被调用：memory_judge（LLM 通路）、eval attitude_regression。

### 3.26 maintenance_ledger.py（治理·维护案例账本）
- 职责：运维案例自动采集（demote 等落账即记）→ 同型 ≥3 案例成范式候选（paradigm_candidates）→ freeze_paradigm 人闸固化（derived_paradigm 回填）。
- 被调用：memory_organize.demote、晨报/抽象器。
- TYPE_TAXONOMY 8 类开放枚举（calibration/engine_fix/data_enrichment/corpus_archaeology/dedup_merge/adjudication/threshold_recal/quarantine_recover）。

### 3.27 paradigm_review.py（治理·范式评审视图）
- 职责：只读分组视图——promotable（candidate 且 evidence_pass≥3 且全 pass）/ calibration（calibration_pending）/ decay（active >90d 未触发）/ active_ok；render_one 含证据流水尾部 10 条。
- 被调用：人工终审 CLI（无生产消费方）。

---

## 四、数据库表清单（corpus_store.db，v3_schema.py 定义）

三前缀纪律：core_* 根资产（绝不丢）/ audit_* 审计事件 / trace_* 流水埋点（30 天热窗+月度归档）。

### core_* 根资产（22 张）
| 表 | 存什么 | 主写方 / 审计流 |
|---|---|---|
| core_memory_card | 认知原子卡 24 列（inst_id PK, full_hash UNIQUE, dfcp_tag JSON, superseded_by 版本链, status active/superseded/merged, memory_class 冗余列） | memory_organize._write_card_to_db 唯一写入口；demote UPDATE tier/status/superseded_by |
| fts_memory_card (FTS5) | summary/detail 全文索引 | 建表随 build，未见主动维护触发器 |
| core_thinking_paradigm / core_skill_paradigm | 思维/技能范式（trigger_when/procedure/evidence_count/pass, status candidate/active/superseded） | E7 治理 + paradigm_review 读；render_persona 消费 |
| core_persona_anchor | 出厂人格锚（append-only；旧 persona_anchor 已冻结 deprecated） | corpus_store.add_anchor 唯一写 |
| core_persona_evolution | 经历演化账（milestone/pattern/scar/identity_ruling） | render_persona 读；写入方在外部 |
| core_entity_timeline | 他人实体血统（entity+event PK） | entity_resolve.resolve_entity 读 |
| core_relation | 关系边（me_inst, other_inst, relation 四档, judge_meta, status） | memory_query.load_graph 读 |
| core_topic_abstraction / core_topic_links | 主题锚/挂载（v3 版） | ⚠ topic_layer 实际写旧表 topic_abstraction/topic_links——**两套并存**，core_* 版待迁移对账 |
| core_experience | 经验层（xid PK, cluster_key, derived_from JSON） | memory_distill._upsert_experience_db 双写 |
| core_raw_corpus / core_corpus_tags | 语料登记 v3 版（自 raw_corpus/corpus_tags 迁入） | 写路径仍在 corpus_store 的 raw_corpus——同上两套并存 |
| core_dialogue_log + fts_dialogue | 逐条消息（log_id, session_id, turn, content, content_hash 幂等） | dialog_evidence_store.aggregate_session |
| core_raw_dialog_session | 整会话快照（messages=transcript 原始 JSON，**权威真相源，运行时记忆流水线禁读**） | aggregate_session upsert；E9 巡检 |
| _tmp_dialog_fragment | 会话碎片临时表（兜底+去重锚，consumed=1 保留） | append_turn_fragment/ingest_transcript |

### 治理队列组（core_*，统一生命周期字段 verdict/verdict_ts/resolved_by）
core_hold_pool（judge/topic hold 待归池——topic_layer mount_topic hold 双写、E7 消费）、core_conflict_queue（冲突评审）、core_quarantine（隔离捞回）、core_alias_pending（别名挂起 DB 版）、core_term_alias（术语别名链，entity_resolve 读，8 行）、core_governor_pending、core_class_backfill_pending（E5 回填提案）、core_watchlist、core_revision_pending（corpus_revision 写）。

### audit_* 审计（8 张）
audit_judge / audit_distill / audit_confidence / audit_topic_log / audit_iteration / audit_code_change / audit_maintenance（trace_archive 归档留痕）/ audit_pool + audit_paradigm_evidence（范式实证账，paradigm_review 消费）。另有 schema_migrate 自建的 audit_schema_migrate。⚠ 这些表大多由 v3_schema 建出，但当前 memory/ 代码主要写 jsonl 审计流（judge_log/gate_log/topic_layer_log/distill_log），audit_* 表写入方在外部管线——碰表前查 db_table_registry.md。

### trace_* 流水（3 张）
trace_cited（检索调用回写：memory_query 双轨主写，source=human/e8 区分演习）、trace_call、trace_embedding——30 天热窗，trace_archive 月度剪切归档。

### corpus_store.py 自建（旧体系并存）
raw_corpus（复合主键 id+version 版本链——memory_gate 对话、语料、novel/patent 全在这）、corpus_tags、iterations（改稿账）、dialog_seq（全局序列）、session_dialog_archive_fragment（≤20 字节碎片归档）。

### data/memory/ 下的 jsonl 审计流
memories.jsonl（审计快照，**禁作统计源**）、code_changelog.jsonl（dfcp_tag/entity_id 变更）、relation_graph.jsonl（本体图谱）、conflict_queue.jsonl、summary_only_suspects.jsonl、entity_collision_skip.jsonl、quarantine.jsonl、replica_intercepts.jsonl、equiv_intercepts.jsonl、experiences.jsonl、store_pending.jsonl、judge_log.jsonl、gate_log.jsonl、gate_miss_case.jsonl、alias_pending.jsonl、alias_tags.jsonl、term_alias.jsonl、cited_log.jsonl（应急回切读源+审计）、pool_audit.jsonl、topic_hold_pool.jsonl、topic_layer_log.jsonl、event_ts_backfill.jsonl、confidence_dynamics.jsonl、memory_attitude.jsonl、class_backfill_pending.jsonl、behavior_backfill_pending.jsonl、maintenance_cases.jsonl、paradigms.jsonl、entity_timeline.jsonl、dual_write_errors.jsonl、corpus_registry.jsonl。

---

## 五、给未来 AI 的避坑要点

1. **MEM_DIR / _DB_PATH / DB_PATH 是模块级常量，import 时固化**。`DF_MEMORY_SOURCE`、`TOPIC_LAYER_ON`、`DF_SUMMARY_EQUIV_DEDUPE`、`JUDGE_USE_LLM` 全是进程启动时读环境变量固化的模块常量——测试隔离必须 monkeypatch 模块属性（如 `memory_organize.MEM_DIR`），运行中改 os.environ 无效；e30 系列回归测试就是这么做的。

2. **jsonl ≠ 真相**。core_memory_card（DB）是唯一真相；memories.jsonl 是审计快照，更新路径（status/superseded_by/detail 同步）只写 DB 不回写 jsonl。但 memory_class_backfill / memory_behavior_backfill / confidence_dynamics(兜底) 仍直读 memories.jsonl——回填 confirm 写 jsonl 后**不回写 DB**，读这几个模块前必须先核 DB/jsonl 漂移。统计一律查 DB 不查切片。

3. **alias_pending / class_backfill_pending 的 len() 是假积压**。队列 append-only 含终态（209 条 rejected_weak_overlap、159 条过期提案）；真积压必须走 `memory_alias.count_active_pending()`（设施③a，2026-11-25）或按 status 过滤。E4 报表已切换，新代码别再引入裸 len()。

4. **topic 层与 v3 schema 是两套表**。topic_layer.py 写旧表 topic_abstraction/topic_links；v3_schema 建了 core_topic_abstraction/core_topic_links 但没人写。corpus_store 的 raw_corpus 与 v3_schema 的 core_raw_corpus 同理。读 topic 数据时先确认你查的是哪套表（E7 的 LIKE 兼容是历史双格式遗留）。

5. **依赖方向有反向/循环，import 时机敏感**。memory_judge ↔ memory_hook 互相调用（judge 的 store 分支调 hook.add；hook session 调 judge_and_store），靠**函数体内延迟 import** 规避环；memory_relations → memory_organize、memory_query → memory_relations 均有运行期 import。改这些文件顶部 import 时先查环。

6. **confidence_dynamics._find_dependents 的键名对不上 relation_graph.jsonl 实际字段**（读 `from/rel/to`，实际是 `inst_id/relation/...`），retrofix 下游追溯恒空——使用 mark_falsified 的追溯功能前先修或核对，别信返回的 dependents 计数语义。

7. **阈值改动必须整链同步**。SUMMARY_EQUIV_THRESHOLD 有 `assert ≥0.95` 静态断言（改低 import 即炸）；换 embedding 基座须整套重标（DOMAIN_SIM_THRESHOLDS 六域三档 + CASCADE_DROP + GATE 系列 + rebuild_graph + E8 回归集）。MEMORY_CLASS_ENUM 扩枚举须四处同步（schema/judge Q3/design §7.6/E4 口径）。**一次性导入脚本禁止自造枚举值**（2026-11-16 曾因 entity_history 类名拒收 10 条）。

8. **相对路径模块须从仓库根运行**：trace_archive.py / render_persona.py / trace_archive_replay_test.py 的 DB 常量是 `"data/memory/corpus_store.db"` 相对 cwd 字符串（不带 os.path 换算）——从其他目录调用会静默新建空库或报错。其余模块均已用 `__file__` 推导绝对路径。

9. **短 id 纪律**：topic 挂载/队列入口强制 32 位 hex 全 inst_id + 卡表存在性校验（防抄错一位的伪全 id 幽灵挂载）；写任何挂载队列前照此办理。

10. **退出码契约**：memory_hook/gate 的业务跳过（empty-skip/too-short-skip/duplicate-referenced）= RC0；judge_failures>0 = RC3；未捕获异常 = RC4 + stderr `[memory_hook_abnormal]`。写 wrapper/巡检脚本时按此判定，勿把 3/4 当成功。