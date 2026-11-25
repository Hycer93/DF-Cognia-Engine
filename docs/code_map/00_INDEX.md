# 00 cognitive_engine_v3 代码地图 · 总索引

> 生成日期：2026-11-25
> 覆盖范围：全仓 179 个 .py 文件 / 约 36,700 行 / 134 个 class（docs/、data/、logs/ 除外）
> 目的：让未来 AI（含自己）**一次读懂系统全貌、避免反复踩坑**。每份子文档均来自逐文件读源码，非抽样。
> 阅读建议：先读本索引 §1-§3 建立全局观，再按需跳子文档 §4。

---

## 一、全景速览

| 模块 | 文件数 | 行数 | class | 定性 |
|---|---|---|---|---|
| `memory/` | 30 | 8,037 | 1 | 记忆数据层：写入/判定/检索/治理四链路。**几乎全函数式**，SQLite `corpus_store.db` 是唯一真相 |
| `cgraph/` | 38 | 8,207 | 113 | 元核运行时（调度大脑）：图走链 + 原子技能。class 集中于此 |
| `eval/core/` | 13 | 3,289 | — | E 系列夜间巡检（E4/E5/E7/E9…），由 `scripts/nightly_run.py` 调度 |
| `eval/audit/` | 57 | 9,309 | 19 | 回归测试族(36) + 巡检/校准/治理/一次性脚本 |
| `eval/experiment/` | 15 | 3,474 | — | AB 实验/回滚/shadow，**内含 3 个生产组件**（勿误删） |
| `scripts/` | 27 | 3,586 | 1 | 夜间主驱动 + 迁移 + 一次性历史脚本 |
| `tools/` | 2 | 482 | 0 | CAN 车机信号工具，**与认知引擎无关** |

**一句话架构**：`memory/` 是数据与规则的地基（SQLite + jsonl 审计流）；`cgraph/` 是跑在图上的调度大脑（骨架链 x 原子链双管线）；`eval/` 是夜间质检官（只读巡检 + 提案治理）；`scripts/` 是总开关。

---

## 二、三大主数据流（跨模块主线，必须先懂这三条）

### 2.1 写入链路：对话 → 记忆卡（memory/ 主导）
```
用户对话
  → memory_gate（闸门：过短/simhash重复 → 丢弃）
  → memory_judge（LLM 五问判定是否值得记忆；失败进 store_pending hold）
  → memory_relations（四档同源判定：equivalent/augment/conflict/new，纯 embedding）
  → memory_organize.atomic_store（落库 core_memory_card【唯一真相】+ 审计导出 memories.jsonl）
  → [cgraph] green_card_writer.commit_green_cards（BEGIN IMMEDIATE 事务批量落绿卡）
```
详见 `01_memory.md §2.1`。

### 2.2 判定链路：五问 memory_class × 三级 repro（正交两维）
```
memory_judge 五问 → memory_class ∈ {skill_refine, scar_case, strategy_evolve, relation_milestone, key_pact}
judge_repro      → repro       ∈ {derived(可复现), situational(条件), flash(不可复现)}
```
- 两维**正交**：class 决定"怎么用"，repro 决定"存多省/怎么复现预算"。
- repro 来自规则查表，但上游显式传值可 `or` 短路绕过 → **设施②已留痕**（`gate_log op=repro_override`）。
- 详见 `01_memory.md §2.2` + `docs/design_repro_vs_judge_flow_20261125.md`。

### 2.3 检索链路：查询 → 召回排序
```
memory_query 入口 → load_graph（core_relation）→ 打分/排序 → trace_cited 回写
```
详见 `01_memory.md §2.3`。

### 2.4 维护/治理链路：夜间巡检 → 提案（eval/ 主导）
```
scripts/nightly_run.py 串联 STEPS
  → E0(硬FAIL守卫) → E2(标定) → E4/E6/E10(只读巡检) → E5/E7/E9(提案治理) → E-hold(消费闭环)
  → 产物：晨报 + 各类 *_pending 队列（供人裁决）
```
详见 `06_eval_core_experiment.md §1、§5`。

---

## 三、全局关键事实（跨模块，最容易记错）

1. **真相源唯一**：`data/memory/corpus_store.db`（`core_memory_card` 表）是记忆卡唯一真相。`memories.jsonl` 已退化为**审计导出流，禁作统计源**（2026-11-15 起）。
2. **两套表并存**：`topic_layer.py` 写旧表 `topic_abstraction`/`topic_links`；`v3_schema` 建了 `core_topic_*` 但**没人写**。`raw_corpus` 与 `core_raw_corpus` 同理。碰表前先确认查的是哪套。
3. **双管线并存**：cgraph 骨架链（ComplexityRouter 边权贪心）与原子链（RouterAtomic 邻接表 LLM 逐跳）**互不替代**，仅 executor 收尾（memory_gate/落库）共用。
4. **模块级常量陷阱**：`MEM_DIR`/`DB_PATH`/`JUDGE_USE_LLM` 等在 import 时固化。测试隔离必须 **monkeypatch 模块属性**，运行中改 `os.environ` 无效。
5. **pending 队列是 append-only**：`alias_pending`(231,真22)/`class_backfill_pending`(167,真6) 的裸 `len()` 是**假积压**（含终态）。真积压走 `memory_alias.count_active_pending()`（设施③a，2026-11-25）。
6. **DB 路径同源硬约束**：cgraph 侧一切 DB 写必须 `from cgraph.atomic_skill import AUDIT_DB`（绝对路径）。相对路径硬编码 CWD 漂移会静默落错库。
7. **技能菜单/终止永不交 LLM**：next_options/terminate 全取自邻接表 `data/atomic_skills_adjacency.json` + Python 条件函数。改技能必须同步改邻接表并过 `adjacency_validator`。
8. **eval/experiment 里有生产组件**：`f20_weight`/`f30_forward`（被 minloop 调用）、`adjacency_validator`（被 cgraph 加载链调用）——别当实验脚本误删。
9. **tools/can-tools 与本系统无关**：车机 CAN 信号查询/导入工具。
10. **回归脚本只碰临时库**：`tempfile.mkdtemp()` + 复制 DB 后操作；一次性修复脚本必须 `dry-run → --apply` 两步。

---

## 四、子文档导航

| 文档 | 覆盖内容 | 何时读 |
|---|---|---|
| `01_memory.md` (422行) | memory/ 30 文件、四链路流程图、逐模块详解、DB 表清单(22 core+8 audit+3 trace+jsonl)、10 条避坑 | 改记忆读写/判定/repro/pending 时 |
| `02_cgraph_core.md` (318行) | cgraph 核心 13 文件 17 类、Mermaid 类图、双管线调度流程、8 条避坑 | 改调度/router/executor/落绿卡时 |
| `03_cgraph_skills.md` (267行) | 13 个 skill_*、技能基类契约、协作流程、15 条避坑 | 改/加技能、改邻接表时 |
| `05_eval_audit.md` (258行) | eval/audit 57 文件全清单(回归36/巡检6/一次性9/专项7/工具3)、命名规律、6 条避坑 | 加回归/改测试/找脚本时 |
| `06_eval_core_experiment.md` (217行) | eval/core 13 + eval/experiment 15、E 编号体系、夜跑 STEPS、13 条避坑 | 改夜间巡检/实验开关/回滚时 |
| `07_scripts_tools.md` (227行) | scripts 27(常驻8/一次性16/oneoff3) + tools 2、危险脚本红标、6 条避坑 | 跑运维脚本/迁移前 |

---

## 五、常见任务 → 该看哪份

| 我要… | 先看 |
|---|---|
| 改记忆卡的写入/落库逻辑 | 01_memory §2.1 + §三(organize 小节) |
| 改 repro 判定/五问判定 | 01_memory §2.2 + design_repro_vs_judge_flow |
| 查某张表存什么/谁在写 | 01_memory §四 + docs/db_table_registry.md |
| 改系统怎么路由/调度 | 02_cgraph_core §三 + §四 |
| 加一个新技能 | 03_cgraph_skills §一(基类契约) + §二(邻接表) |
| 加一个回归测试 | 05_eval_audit §二 + e30 命名规律 |
| 改夜间巡检步骤/阈值 | 06_eval_core_experiment §1 + calibration/nightly_calib.toml |
| 跑脚本/迁移数据 | 07_scripts_tools §六(危险脚本红标) |

---

## 六、维护约定

- 新增/删除 .py 文件、改核心函数签名、改表结构 → **同步更新对应子文档**。
- 本索引的 §三「全局关键事实」是全系统最易错点的浓缩，新增坑点优先补这里。
- 子文档的「给未来 AI 的避坑要点」是逐模块坑，改动相关模块后回填。