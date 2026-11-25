# 文档总谱目录（README）

> 更新：2026-11-25　|　结构：总（本文件）→ 分（9 个主题目录）
> 说明：docs/ 下 112 份零散文档 + 7 份代码地图，已按"总分结构 + 主题分类"重组。所有文件仅移动位置，内容未改。
> **改动任何文档前先读本目录 §二「归类约定」和 §四「合并候选」**，避免又造散档。

---

## 一、目录总览（先看这里定位）

| 目录 | 文件数 | 主题 | 何时来查 |
|---|---|---|---|
| `code_map/` | 7 | **代码地图**（类图/流程图，逐文件读源码产出） | 改任何代码模块前。先看 `code_map/00_INDEX.md` |
| `00_architecture/` | 10 | **架构宪法**：系统总设计、认知内核、DFCP 树、规划 | 理解系统"为什么这么设计"、大方向 |
| `01_judge_repro/` | 9 | **入库判定**：五问判定 + 三级 repro + 相关标定/门禁 | 改记忆怎么判、判得准不准 |
| `02_memory_storage/` | 8 | **记忆存储**：存储结构、情感账、对话证据、话题层、拼接范式 | 改记忆怎么存、存什么 |
| `03_future_sim/` | 6 | **未来推演**：F20 权重、F30 演化、F40 门槛、F60 校验、公式体系 | 改前向推演/未来模拟 |
| `04_cgraph_skill/` | 4 | **元核与技能**：骨架技能、路由器、元核重构 | 改调度/技能体系 |
| `05_data_governance/` | 39 | **数据治理**（按问题分子目录，见下） | 处理存量数据问题（重复/回填/漂移/隔离） |
| `08_patrol_review/` | 25 | **巡检与审查**：review_*.md 系列 + 巡检存档 + 回归手册 | 查历史审查结论、回归方法、人机差距 |
| `10_patent/` | 2 | **专利**：现有技术检索、修订日志 | 专利相关 |
| `99_misc/` | 9 | **杂项**：数据库台账、待办总表、语料笔记、案例 | 查表结构、看待办、翻素材 |
| `artifacts/` | 2 | **产物 JSON**：消融/稳定性实验输出 | 被实验/审查引用时 |

---

## 二、归类约定（新增文档前必读）

**原则：按"主题/功能"归目录，不按"文件前缀"。** 前缀（design/review/dryrun/exec…）只表示文档体裁，主题看放在哪个目录。

| 要写的新文档类型 | 放哪 | 命名建议 |
|---|---|---|
| 系统级设计/架构 | `00_architecture/` | `design_<主题>.md` |
| 判定/检索/门禁设计 | `01_judge_repro/` | `design_<主题>.md` |
| 存储/情感/话题/拼接设计 | `02_memory_storage/` | `design_<主题>.md` |
| 前向推演/F 系列设计 | `03_future_sim/` | `design_<主题>.md` |
| cgraph/技能/路由设计 | `04_cgraph_skill/` | `design_<主题>.md` |
| 存量数据治理（重复卡/回填/漂移/隔离） | `05_data_governance/<子类>/` | `dryrun_*`/`exec_*`/`design_*` |
| 审查/巡检/回归 | `08_patrol_review/` | `review_*`/`audit_*` |
| 专利 | `10_patent/` | — |
| 表台账/待办/语料/案例 | `99_misc/` | — |

**一次性过程档（dryrun/exec/recon/impl/test/risk）**：完成即归入对应主题目录，**不要堆在 docs 根**。治理类统一进 `05_data_governance/` 的四个子目录：
- `dup_cards/` — 重复卡根治（#30/#31/#43 等）
- `backfill/` — 回填（memory_class #34 / event_ts #37 / detail #44）
- `drift_demote/` — DB↔jsonl 漂移 / demote 语义 / 并发 / raw 叶子边
- `quarantine/` — 隔离区/候审/watchlist 裁决

---

## 三、按主题查文档（同一主题的文档在哪）

### 重复卡根治（#30 主问题）
`05_data_governance/dup_cards/`：design_30_derived_dup_fix(立项) → recon_30_*(侦察) → risk_30(风控) → impl_30_phase3(实现) → test_30(测试) → audit_30_phase6(审计) → rollout_30(灰度) → exec_30_switch_on(切on) + dryrun_31/43(存量) + duplicate_cards_audit(盘点) + attach_30(样本) + dryrun_40(写入入口) + p0_switch_on_eval + exec_31_35_36

### memory_class 回填（#34）
`05_data_governance/backfill/`：dryrun_34_memory_class / dryrun_34_backfill_high_medium / exec_34_memory_class / exec_34_batch2

### DB↔jsonl 漂移 / demote（问题A/B、#41/#46）
`05_data_governance/drift_demote/`：diag_db_jsonl_drift → disposal_20261124 → dryrun_41_unified_update → dryrun_46/exec_46_demote + green_card_gate_concurrency_fix

### 巡检与审查
`08_patrol_review/`：review_7~review_29 系列（编号=议题号，非时间序）+ review_20261121_system_status(总纲) + audit_memory_chain + patrol_p25_baselining + regression_playbook(回归手册) + memory_vs_human_gap + script_inventory

### 判定体系
`01_judge_repro/design_repro_vs_judge_flow_20261125.md`（**主文档，最新**）+ design_memory_judge_llm_fallback + recurrence_gate + paradigm_layer

### 未来推演 F 系列
`03_future_sim/`：formula_future_sim_v2_1(公式总纲) + design_f20/F30/F40/F60

---

## 四、合并候选（同一问题多份过程档，未来可合并，本次仅归类未合并）

> 用户要求"能合并的合并"。以下为高频重复的过程档族——保留全部原始档（审计价值），但标注**合并指向**。若要瘦身，可合并为一份带附录的单档。

| 合并候选 | 涉及文件 | 建议合并产物 |
|---|---|---|
| **#30 重复卡全周期** | `05_data_governance/dup_cards/` 16 份（design_30→recon→risk→impl→test→audit→rollout→exec→dryrun→p0） | 一份 `#30根治全记录.md`（设计正稿 + 附录收纳过程档） |
| **#34 回填** | `backfill/dryrun_34_*` + `exec_34_*`（4份） | `#34回填全记录.md` |
| **event_ts 回填** | `backfill/dryrun_37_*` + `exec_37_*`（3份） | 并入 `#37回填.md` |
| **demote/漂移** | `drift_demote/dryrun_46_*` + `exec_46_*` + dryrun_41 + diag_drift + disposal | `DB漂移与demote治理.md` |
| **review 系列** | `08_patrol_review/review_*.md` 20 份 | 可按议题聚合成 `审查结论总辑.md`（但 review 是独立议题，建议保留） |

**本次未真合并的理由**：这些是带日期的过程审计档，合并会丢失时间线追溯性，且代码注释引用了其中若干文件名（如 `eval/audit/f20_ablation_convergence.py` 引用 `design_f40_threshold_234.md`）。**建议保留分档，用上方"合并指向"做逻辑聚合**。如需物理合并请显式确认。

---

## 五、注意：代码引用约束（移动/改名会断链）

**27 个 .py 文件在注释里引用了 docs 具体文件名**（纯注释，非运行时读取，不影响运行但会误导读者）。移动/改名前需同改这些注释。高频被引用：
- `design_skeleton_skills_v01.md` → cgraph/skill_*.py（6处）
- `design_f40_threshold_234.md` → eval/audit/f20_*.py、eval/experiment/f20_weight.py
- `design_skill_router.md` → cgraph/router_atomic.py
- `design_repro_vs_judge_flow_20261125.md`、`design_affect_vector.md` 等

查全部引用：`grep -rn "docs/[a-z0-9_]*\.md" --include="*.py" .`

---

## 六、维护约定

1. 新文档按 §二约定归档，**不堆 docs 根**。
2. 改文档名/路径 → 同步改 §五 列出的代码注释引用。
3. 本 README 的目录表、主题索引随增删同步更新。
4. 代码地图（`code_map/`）随代码改动同步更新（见 `code_map/00_INDEX.md §六`）。