# 数据库表台账（db_table_registry）

> **读者**：未来会话的我 / 任何要碰这张库的 agent。
> **目的**：低成本判断「这张表能不能碰、真相源是谁、删了会怎样」。
> **不是**字段字典（字段看 `PRAGMA table_info` 即可）。
> **快照时间**：2026-11-24 22:30（数据会变，定性结论需按需复核）
> **主库**：`data/memory/corpus_store.db`（36MB，唯一真相）

---

## 0. 一句话结论（先看这个）

`v3_schema.py`（2026-11-15 三前缀重设计：core_根资产/audit_审计/trace_流水）**只落地了一半**：
- ✅ `core_memory_card` 迁完并用上（195 处引用，绝对主力）
- ❌ `core_raw_corpus`/`core_topic_links`/`core_topic_abstraction`/`core_corpus_tags`/`audit_iteration` 只建空壳，**代码零引用**，数据还比旧表少（迁移半途停）
- ✅ 旧的**无前缀表**（`raw_corpus`/`topic_links`/`topic_abstraction`/`corpus_tags`/`iterations`）**才是当前活的**——17~18 个文件在读它们

**致命坑**：看到 `core_xxx` 别以为它就新就活。实际 `core_raw_corpus` 是死的，`raw_corpus` 才是活的。**判断唯一依据 = 代码引用数 + 最新写入时间，不是表名。**

---

## 1. 三类定性

### A 类 · 活表（照常用，36 张）

代码读写、当前仍在更新（最新写入 = 2026-11-24）。**核心表的「谁用 / 干什么」**（引用数来自 2026-11-24 扫描）：

| 表 | 行数 | 主要使用代码 | 具体用途 |
|---|---|---|---|
| `core_memory_card` | 325 | `memory/memory_organize.py`(写)、`memory/memory_query.py`(读)、`topic_layer.py`、`future_sim_minloop.py`(F30/F40图)、`e30_p0_regression.py`(验)、54 文件 | **唯一真相认知主存储**。写入加固(INSERT OR IGNORE)、查询投影、demote 降级(UPDATE tier/status)、图推演节点。jsonl 仅审计存档 |
| `raw_corpus` | 1690 | `memory/corpus_store.py`(14处,增删改查主责)、`memory_gate.py`(准入)、`future_sim_minloop.py`(#50 raw边) | 原文语料通用表(小说正文/前身语料/长文本)。版本链 status=current/superseded、tree_vol/tree_ch 树位、body 正文 |
| `core_relation` | 183 | `f20_weight.py`(6)、`future_sim_minloop.py`(6)、`shadow_regress.py` | **卡↔卡关系边**。F20 加权按 me_inst/other_inst 双向度数算关联激活度；F30/F40 图遍历的边来源 |
| `topic_links` | 197 | `topic_layer.py`(13,主责)、`future_sim_minloop.py`(15)、`topic_layer_regression.py`(22) | 卡→主题簇挂载边(inst_id→topic_id+method)。挂载校验：先验 inst_id 存在才挂(core_memory_card 校验) |
| `topic_abstraction` | 12 | `topic_layer.py`(15,主责)、`memory_query.py`(主题检索) | 主题簇抽象(cue_anchors 锚词/mount_count/access_count)。挂载计数、访问计数、归档(status=archived) |
| `corpus_tags` | 159 | `memory/corpus_store.py`(5) | 树级标签字典(卷-章位置键+arc/chars 多入口)。与 raw_corpus JOIN 检索 |
| `core_term_alias` | 8 | `memory/entity_resolve.py`(4,#49阶段2) | 别名链 alias→canonical。`resolve_alias()` 归并+多义告警 |
| `core_entity_timeline` | 7 | `memory/entity_resolve.py`(2)、`test_memory_vs_human.py` | 实体锚(entity+event+event_ts+summary)。`resolve_entity()` 按名/摘要查锚，一义一锚 |
| `core_skill_paradigm` / `core_thinking_paradigm` | 7 / 13 | `memory/paradigm_review.py`(TABLES)、`render_persona.py`、`e7_nightly_governance.py` | 技能/思维范式库。范式评审、人格渲染、夜间治理 |
| `audit_paradigm_evidence` | 2 | 范式证据链路 | 范式证据账(evidence_count/pass) |
| `core_hold_pool` | 16 | `topic_layer.py`(4,hold池)、`e7_nightly_governance.py`、`governance_reconcile.py` | 主题挂载 hold 池(topic_id=null 待夜间裁决)。夜间治理消费 |
| `core_quarantine` | 43 | `fix_quarantine_20261123.py`(捞回)、`governance_reconcile.py` | 隔离区(问题卡)。捞回三步流程 |
| `core_conflict_queue` / `core_alias_pending` / `core_governor_pending` / `core_class_backfill_pending` | 149/89/17/151 | `scripts/nightly_run.py`(队列统计)、`governance_reconcile.py` | 待审队列(verdict IS NULL 待处理)。冲突/别名/治理/分类回填 |
| `core_dialogue_log` / `core_raw_dialog_session` / `session_dialog_archive_fragment` / `dialog_seq` / `_tmp_dialog_fragment` | 12/3/259/1/12 | `e9_dialog_governance.py`、`patrol_dialog_fragments.py` | 对话流水/原始会话/碎片归档/序号 |
| `trace_cited` / `trace_call` / `trace_embedding` | 45472/-/- | 埋点、夜间报告 MAX(ts) | 流水埋点，30天窗口+月度归档(`trace_archive/`) |
| `audit_topic_log` / `audit_pool` / `audit_schema_migrate` / `audit_maintenance` | 5813/3815/82/23 | 审计事件 | 主题日志/池/迁移记录/维护记录 |
| `core_revision_pending` / `core_persona_evolution` | 1/0 | 修订/人格演化 | 修订待核/人格演化(空) |
| `schema_version` | 2 | 迁移记录 | 迁移版本+结果 JSON |
| `iterations` | 3364 | 12 处 | 迭代记录。**`audit_iteration`(2863) 是死镜像，别用** |
| `persona_anchor` / `core_persona_anchor` | 10/10 | 都在用(11/9 处) | 人格锚点。两表并存属历史遗留，改动前必查谁在读 |
| `core_watchlist` | 3 | `scripts/nightly_run.py`(仅 COUNT) | **准活**：仅夜间报告统计待审数，无业务读写 |
| `fts_memory_card*` / `fts_dialogue*` | - | 全文检索 | FTS 影子表，随主表重建，**勿手改** |

### B 类 · 镜像死壳（建表未用，数据不同步）

代码**零读写**，只在 `memory/v3_schema.py` 的 CREATE 定义里出现；数据比旧表少（迁移半途停）；最后写入停在 2026-11-16。

| 死壳表 | 行数 | 对应活表 | 处置建议 |
|---|---|---|---|
| `core_raw_corpus` | 1457 | `raw_corpus` (1690) | 补齐或删壳 |
| `core_topic_links` | 179 | `topic_links` (197) | 同上 |
| `core_topic_abstraction` | 11 | `topic_abstraction` (12) | 同上 |
| `core_corpus_tags` | 147 | `corpus_tags` (159) | 同上 |
| `audit_iteration` | 2863 | `iterations` (3364) | 同上 |

> ⚠️ `audit_topic_log`(5813)/`audit_pool`(3815) **是活的**，别和 `audit_iteration` 混——同 audit_ 前缀不等于同类命运，逐个看引用数。

### C 类 · 迁移死资产（有数据、代码零引用，数据源在 jsonl）

从 jsonl 迁进表，但代码仍读 jsonl，**表本身零引用**。数据是真历史，只是没人通过表访问。jsonl 源都还在。

| 死表 | 行数 | 真正的真相源 | 最后写入 | 代码出现位置 |
|---|---|---|---|---|
| `audit_code_change` | 1732 | `code_changelog.jsonl` (352KB) | 2026-11-16 | 仅 `v3_schema.py`(建表) + `migration_report_v3.md`(迁移记录) |
| `audit_distill` | 251 | `distill_log.jsonl` (65KB) | 2026-11-16 | 仅 `v3_schema.py` + 迁移报告 |
| `audit_judge` | 172 | `judge_log.jsonl` (233KB) | 2026-11-15 | 仅 `v3_schema.py` + 迁移报告 |
| `audit_confidence` | 7 | `confidence_dynamics.jsonl` (3KB) | 2026-11-11 | 仅 `v3_schema.py` + 迁移报告 |
| `core_experience` | 18 | `experiences.jsonl` (26KB) | 2026-11-16 | 仅 `v3_schema.py` + 迁移报告 |

> **"代码出现位置"= 建表定义 + 迁移日志，无任何 SELECT/INSERT/UPDATE**。业务代码一律读 jsonl，不读表。
> 处置：标注**只读归档**；**别再写入**（写了无程序读）。清理前确认 jsonl 完整再 DROP。

---

## 2. 其他 db 文件（别误碰）

| 文件 | 状态 | 说明 |
|---|---|---|
| `data/memory/corpus_store.db` | ✅ 主库 | 唯一真相，36MB |
| `data/memory/_replay_shadow.db` | 🔒 实验影子 | 34MB，54 表，回放实验用。**非真相，勿当主库读** |
| `data/memory/backups/*.db` | 📦 备份 | 时间戳命名，勿删勿用 |
| `data/memory/trace_archive/trace_archive_2026_08.db` | 📦 归档 | trace 月度归档 |
| `data/memory/memory.db` / `store.db` / `topic_store.db` / `dfcp_memory.db` / `data/dfcp_memory.db` | 💀 0 字节空壳 | 废弃残留，可删（不确认前别删） |

---

## 3. 判定方法（数据变了怎么自查）

**唯一可信判据 = 代码引用数 + 最新写入时间**，不是表名、不是前缀。

```python
# 引用数（排除建表文件，才是真在用）
import re,glob
files=[f for f in glob.glob('**/*.py',recursive=True) if '.venv' not in f]
src={f:open(f,encoding='utf-8',errors='ignore').read() for f in files}
pat=re.compile(r'(?<![A-Za-z0-9_])'+TABLE+r'(?![A-Za-z0-9_])')
sum(len(pat.findall(s)) for f,s in src.items() if 'v3_schema' not in f)

# 最新写入时间（对照当前时间，差值大=死表）
sqlite3 ... "SELECT MAX(ts) FROM TABLE"
```

**定性规则**：
- 非 schema 文件引用 ≥2 且近 7 天有写入 → **A 活**
- 仅 v3_schema 引用 + 写入停在迁移日 → **B/C 死**
- 有引用但从不写（如 watchlist 只 COUNT）→ **准活**，改前查上下文

---

## 4. 踩坑红线（血泪）

1. **别信 `core_` 前缀 = 新/活**。`core_raw_corpus` 死、`raw_corpus` 活，反直觉。
2. **改任何表前，先 grep 谁在读**。`persona_anchor` 和 `core_persona_anchor` 两表并存都还在用。
3. **同前缀不同命**：`audit_topic_log` 活、`audit_iteration` 死，必须逐个查引用。
4. **删表不可逆**。B/C 类清理前：①备份 ②确认 jsonl/活表数据完整 ③问用户。台账只定性不擅删。
5. **写 B/C 死表 = 白写**（无人读），别在死表上开发新功能。