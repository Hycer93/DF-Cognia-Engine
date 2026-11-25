# 临时脚本盘点与处置建议（2026-11-23）

> 盘点范围：memory/、scripts/、eval/、cgraph/ 全部 .py（忽略 __pycache__、单测）
> 方法：文件名日期戳 + docstring 一次性标注 + 硬编码检测 + import 依赖反查
> **本清单为建议，最终处置待用户拍板；删除需用户明确授权。**

---

## 统计

| 类 | 数量 | 定性 |
|---|---|---|
| A 范式化候选 | ~8 | 有复用价值，需参数化改造 |
| B 应保留 | ~30 | 核心模块/调度/回归 |
| C 一次性已完成 | ~10 | 使命完成，可归档/删 |
| D 测试/探针 | ~45 | 回归/实验，多数保留 |

---

## A 类 — 范式化候选（改造方向）

| 脚本 | 现状 | 改造方向 |
|---|---|---|
| `eval/audit/backfill_jsonl_from_db.py` | 已有 --dry-run/--apply | 升级为 `sync-db-jsonl` 标准 CLI，纳入运维工具箱 |
| `scripts/clean_corpus.py` | DEEPEN 残留清洗，路径硬编码 | 参数化输入路径 → 通用清洗命令 |
| `scripts/migrate_patents_to_store.py` | 专利入库，路径硬编码 | 与 novel 版合并为 `ingest-corpus --type` |
| `scripts/migrate_novel_to_store.py` | 小说入库，同上 | 同上，合并 |
| `scripts/save_share2.py` / `save_share3.py` | 分享稿保存，高度重复 | 合并为 `save-share --src` |
| `scripts/p25_add_connect_timeout.py` | AST codemod 通用模式 | 保留为 codemod 样例工具 |
| `memory/memory_class_backfill.py` | 已被 nightly_run 引用，提案器 | 泛化为 backfill framework（当前虽被引用，已是 B 半级） |

---

## B 类 — 应保留（核心/调度/回归）

**核心模块**（全局被 import）：v3_schema / schema / constants / term_alias / corpus_store / corpus_revision / corpus_registry / topic_layer / memory_relations / memory_gate / memory_judge / memory_attitude / memory_quality_gate / memory_organize / memory_distill / memory_alias / embed_semantic / confidence_dynamics / trace_archive / memory_hook / dialog_evidence_store

**cgraph 内核**：graph / engine / router / executor / atomic_skill / skill_* / json_utils / llm / trajectory

**调度/管线**（外部 cron 触发）：nightly_run.py / scheduled_run.py / _watch_nightly.py / collab_pipeline.py / parallel_pipeline.py / build_graph.py

**特殊保留**：`memory/schema_migrate.py` —— 虽是 migration runner，但 `schema_migrate_regression.py` import 了 `run_migrations()`，属回归依赖，保留。

---

## C 类 — 一次性已完成（处置建议：**分两档，非全删**）

### C1 今天治理脚本（建议**归档保留**，不删）
| 脚本 | 理由 |
|---|---|
| `memory/fix_quarantine_20261123.py` | 隔离区 43 条处置的**执行凭证**，审计溯源需要 |
| `memory/backfill_suggested_class_20261123.py` | P2 回填 7 条的执行凭证 + 报告生成逻辑 |
| `memory/p3_governor_verdict_20261123.py` | P3 6 卡裁决的执行凭证 + C 卡根因记录 |

> **为什么保留**：这三个是今天治理闭环的**可复现证据链**，删了以后审计问"43条怎么处理的"没法现场演示。建议移到 `scripts/oneoff/20261123_governance/` 归档，不留在 memory/ 主目录干扰。

### C2 历史迁移脚本（建议**可删**，使命已完成且幂等）
| 脚本 | 理由 |
|---|---|
| `memory/migrate_raw_corpus_pk.py` | 单主键→复合主键迁移，已跑完，幂等跳过 |
| `memory/v3_migrate_batch1.py` | v3 第一批 jsonl→DB 迁移，已跑完 |
| `memory/seed_paradigm_migrate.py` | 种子范式迁移 v1，已跑完 |
| `memory/seed_paradigm_batch2.py` | 种子范式第二批，已跑完 |
| `scripts/oneoff/transcript_backfill_history.py` | 文件自述 oneoff，硬编码历史日期 |
| `eval/experiment/ab_exp_rollback.py` | 特定快照+题目的单次 A/B 实验 |

> **删前确认**：`grep -rn "import migrate_raw_corpus_pk\|from v3_migrate_batch1" .` 确无引用后再删。

---

## D 类 — 测试/探针（多数保留）

- **eval/audit/**：历史回归测试集（e30/e31/e7/f40/topic 等），保留作回归网。
- **eval/core/**：核心链路冒烟/巡检（e0~e12），保留。
- **cgraph/test_skill_*.py**：skill 单测，保留。
- **eval/experiment/**：实验探针，多数带实验编号，可择旧清理（低优先）。
- **今日新增**：`eval/audit/test_memory_vs_human_20261123.py`（记忆对照测试）—— 建议**去除日期戳**改名为 `test_memory_vs_human.py`，纳入常驻回归。

---

## 待用户拍板的处置清单

| 动作 | 文件 | 我的建议 |
|---|---|---|
| 🔵 归档（移 scripts/oneoff/20261123_governance/） | fix_quarantine / backfill_suggested_class / p3_governor_verdict | 保留凭证，离开主目录 |
| 🔴 删除（确认无引用后） | migrate_raw_corpus_pk / v3_migrate_batch1 / seed_paradigm_migrate / seed_paradigm_batch2 / transcript_backfill_history / ab_exp_rollback | 历史迁移已跑完 |
| 🟢 改名去日期戳 | test_memory_vs_human_20261123 → test_memory_vs_human | 纳入常驻回归 |
| 🟡 范式化（后续） | backfill_jsonl_from_db / clean_corpus / save_share2+3 / migrate_patents+novel | 改造通用 CLI |

---

## 我不建议做的

- **不建议一次删太多**：C2 的迁移脚本虽跑完，但保留成本极低，删前务必 `grep import` 确认零引用。
- **不建议删今天的治理脚本**：它们是审计闭环的证据，归档即可。
- **不建议现在范式化**：A 类改造会引入工作量，当前优先主线（P0 观测），范式化排后。

---

## ✅ 实际处置结果（2026-11-24 09:30 执行完毕）

> 用户 2026-11-24 拍板：今日 3 份治理脚本不删（归档）；C2 历史迁移 6 个全删；测试探针改名入回归；A 类范式化全部后置（当前主线 P0 观测，不改造）。

### 一、归档移动（3 个，保留审计凭证）

| 源 | 目标 |
|---|---|
| `memory/fix_quarantine_20261123.py` | `scripts/oneoff/20261123_governance/fix_quarantine_20261123.py` |
| `memory/backfill_suggested_class_20261123.py` | `scripts/oneoff/20261123_governance/backfill_suggested_class_20261123.py` |
| `memory/p3_governor_verdict_20261123.py` | `scripts/oneoff/20261123_governance/p3_governor_verdict_20261123.py` |

新建目录 `scripts/oneoff/20261123_governance/`。这 3 个是隔离区 43 条 / P2 回填 7 条 / P3 6 卡裁决的可复现执行凭证，归档不删。

### 二、删除（6 个历史迁移脚本，已确认零 import 引用）

| 已删文件 | 原因 |
|---|---|
| `memory/migrate_raw_corpus_pk.py` | raw_corpus 复合主键迁移，已跑完幂等 |
| `memory/v3_migrate_batch1.py` | v3 第一批 jsonl→DB 迁移，已跑完（仅注释提及，无真 import）|
| `memory/seed_paradigm_migrate.py` | 种子范式迁移 v1，已跑完 |
| `memory/seed_paradigm_batch2.py` | 种子范式第二批，已跑完 |
| `scripts/oneoff/transcript_backfill_history.py` | 文件自述 oneoff，硬编码历史日期 |
| `eval/experiment/ab_exp_rollback.py` | 单次 A/B 回滚实验 |

删除前终检：6 个文件全部存在、`grep '^import|^from' <name>` 反查均 **0 处真 import**。

### 三、改名（去日期戳，纳入常驻回归）

| 原 | 新 |
|---|---|
| `eval/audit/test_memory_vs_human_20261123.py` | `eval/audit/test_memory_vs_human.py` |

改名后冒烟通过（D1 召回 5/5，六维全部正常输出）。路径推算依赖 `__file__` 向上 2 级，改名不改变目录深度，运行正常。

### 四、A 类范式化 —— 全部后置，本次不动

`backfill_jsonl_from_db` / `clean_corpus` / `save_share2+3` / `migrate_patents+novel` / `p25_add_connect_timeout` 等全部**保持原样**。
理由：主线是 P0-dry-run 观测等待真实样本，范式化 CLI 属低优先级技术债务，后续迭代。

### 五、执行后状态核对

- ✅ 归档目录：`scripts/oneoff/20261123_governance/` 含 3 个治理脚本
- ✅ 6 个历史迁移脚本：已从文件系统移除
- ✅ 对照测试：`eval/audit/test_memory_vs_human.py` 常驻，可重复跑
- ✅ memory/ 主目录不再有当日临时脚本干扰
