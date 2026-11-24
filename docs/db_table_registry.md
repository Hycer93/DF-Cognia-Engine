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

代码读写、当前仍在更新（最新写入 = 2026-11-24）。按职能分组：

| 职能 | 表 | 说明 |
|---|---|---|
| **认知主存储** | `core_memory_card` (325) | 认知原子卡，195 处引用，绝对核心 |
| | `raw_corpus` (1690) | 原文语料。**注意无 core_ 前缀**（core_raw_corpus 是死壳） |
| | `core_relation` (183) | 卡↔卡关系边 |
| | `topic_links` (197) / `topic_abstraction` (12) | 主题簇（无前缀=活） |
| | `corpus_tags` (159) | 语料标签（无前缀=活） |
| **别名/实体** | `core_term_alias` (8) | 别名链 alias→canonical（#49 阶段2 启用读取 `memory/entity_resolve.py`） |
| | `core_entity_timeline` (7) | 实体锚（一义一锚，同体不同阶段用 summary 记演化） |
| **范式** | `core_skill_paradigm` (7) / `core_thinking_paradigm` (13) | 技能/思维范式库 |
| | `audit_paradigm_evidence` (2) | 范式证据账 |
| **治理队列** | `core_hold_pool` / `core_conflict_queue` / `core_alias_pending` / `core_governor_pending` / `core_quarantine` / `core_class_backfill_pending` | 待审队列（`verdict IS NULL` 为待处理） |
| | `core_watchlist` (3) | 仅夜间报告 COUNT，无业务读写——**准活** |
| **对话** | `core_dialogue_log` (12) / `core_raw_dialog_session` (3) / `session_dialog_archive_fragment` (259) / `dialog_seq` (1) / `_tmp_dialog_fragment` (12) | 对话流水 |
| | `core_raw_dialog_session` | 对话原始会话 |
| **审计/流水** | `trace_cited` (45472) / `trace_call` / `trace_embedding` / `audit_topic_log` (5813) / `audit_pool` (3815) / `audit_schema_migrate` (82) / `audit_maintenance` (23) | 埋点/审计事件（trace_* 30天窗口归档） |
| **待处理/版本** | `core_revision_pending` (1) / `core_persona_evolution` (0) | 修订/人格演化 |
| **系统** | `schema_version` (2) / `iterations` (3364) / `persona_anchor` (10) / `core_persona_anchor` (10) | 见下方特殊说明 |
| **FTS 索引** | `fts_memory_card*` / `fts_dialogue*` | 全文检索影子表（随主表重建，勿手改） |

**特殊说明**：
- `iterations` (3364)：活，12 处引用。**`audit_iteration`(2863) 是它的死镜像**，别用。
- `persona_anchor` vs `core_persona_anchor`：**都在用**（11 / 9 处引用），不是死的。`schema_version` 记录 2026-11-21 曾"冻结保留 core_persona_anchor，定义从 SCHEMA 移除但不 DROP"——两表并存属历史遗留，当前两表仍被读，**改动前必查谁在读**。

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

| 死表 | 行数 | 真正的真相源 | 最后写入 |
|---|---|---|---|
| `audit_code_change` | 1732 | `code_changelog.jsonl` (352KB) | 2026-11-16 |
| `audit_distill` | 251 | `distill_log.jsonl` (65KB) | 2026-11-16 |
| `audit_judge` | 172 | `judge_log.jsonl` (233KB) | 2026-11-15 |
| `audit_confidence` | 7 | `confidence_dynamics.jsonl` (3KB) | 2026-11-11 |
| `core_experience` | 18 | `experiences.jsonl` (26KB) | 2026-11-16 |

> 处置：标注**只读归档**，查历史用表或 jsonl 都行；**别再写入**（写了也无人读）。若要清理，先确认 jsonl 完整再 DROP。

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