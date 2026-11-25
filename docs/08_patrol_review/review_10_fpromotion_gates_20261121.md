# #10 门槛②③④ 现状盘点（2026-11-21）

> 定位：主线任务 #10 启动前的现状盘点。**只做盘点，不改代码。**
> 前序：#7 pending 队列可靠写回已结案（A-1/A-2/A-4/A-5 落地 `6d5a25a`，A-3/A-6 后置），主线交接至 #10。
> 搜索口径：「门槛 / gate / threshold / barrier / 准入 / 消融 / Ω」全仓。

---

## 0. 一句话结论

**门槛②③④ 目前是「设计已定、执行件大面积缺位」状态。**
- 门槛①（跨簇命中率）**已定案并已编码**（`scripts/f40_cross_cluster_eval.py`，2026-11-20）。
- 门槛②（消融收敛）：**零执行件**。Ω/β/裁剪阈值的参数散落在 `future_sim_minloop.CONFIG` 与 `f20_weight` 常量，但「收敛判定标准」和「验收脚本」都不存在。
- 门槛③（边覆盖率扰动）：**半个执行件**。`factor_density()` 有软降权，但「边覆盖率本身的量化」与「结果稳定性校验」没有。
- 门槛④（失败溯源+降级分支）：**归因一半、降级为零**。`attribute_failure()` 四类归因已编码有回归，但「边稀疏时返回低置信推演」的降级分支未实现。

⇒ #10 的真实工作量不是「写四道门槛」，而是**补三样东西**：门槛②的收敛验收、门槛③的覆盖率扰动量化、门槛④的降级分支。

---

## 1. 代码位置

### 1.1 门槛定义出处（文档层）

| 文件 | 行号 | 内容 |
|---|---|---|
| `README.md` | 117-121 | 「F 系晋升门槛」（experiment→core，四条同时满足）1) 跨簇命中率≥80% 2) Ω、β、裁剪阈值全套消融收敛 3) 图谱边覆盖率扰动被量化、有补偿 4) 独立失败溯源与降级分支 |
| `docs/design_v3_master.md` | 57 | F 系晋升四门槛总述（截断） |
| `docs/review_20261121_system_status.md` | 211 | `| #10 | 门槛②③④ | ⏸ 待启动 | — |`（仅一行，无展开） |

### 1.2 门槛①（已完成，作为参照基线）

| 要素 | 位置 |
|---|---|
| 设计文档 | `docs/design_f40_hitrate.md` v1.0（2026-11-20 定稿） |
| 执行脚本 | `scripts/f40_cross_cluster_eval.py`（13772 B） |
| 常量 | `PASS_RATE, MIN_DENOM, MAX_PER_ROUND, MIN_PER_ROUND = 0.8, 3, 6, 3`（line 31） |
| 隔离红线 | `SENSITIVE = {"topic_user_relation"}`（line 29）；`MAX_HOPS = 12` |
| 题库 | `data/experiment/f40_cross_cluster_question_bank.jsonl` |
| 产物 | `data/experiment/f40_hitrate_runs/` |
| 核心函数 | `cmd_run()` line 188 / `qualifies()` line 93（真跨簇校验）/ `same_question()` line 104 / `pick_round()` line 164 |

### 1.3 门槛②③④ 相关散落执行件

| 门槛 | 相关代码 | 状态 |
|---|---|---|
| ② 消融收敛 | `eval/experiment/future_sim_minloop.py` `CONFIG` 块（line 34-57）：K/beta/branch_prune_w/topic_edge_w/relation_edge_w/max_steps/confidence_tier/sensitive_topics/prune_cluster_size | 参数集中可消融，但无「收敛判定」 |
| ② β 档位 | `eval/experiment/f20_weight.py`：`BETA_PROFILES`(line 34, conservative 0.6/baseline 1.0/exploratory 1.6)、`DELTA_STEP_BASE=0.6`、`DELTA_STEP_SLOPE=0.85` | 三档已固化 |
| ② 裁剪阈值 | `cgraph/atomic_skill.py` `REF_INLINE_THRESHOLD = 8000`（line 124）；`future_sim_minloop.branch_prune_w=0.3` | 已固化但分散 |
| ③ 边覆盖率补偿 | `eval/experiment/f20_weight.py` `factor_density()` line 98：`D = cluster_connectivity × (1 - debt_ratio × LAMBDA_DEBT)`；`LAMBDA_DEBT=0.5`(line 48)、`D_FLOOR=0.3`(line 51) | 软降权有，覆盖率量化无 |
| ④ 失败归因 | `eval/experiment/f20_weight.py` `attribute_failure()` line 148：四类枚举 `edge_coverage_gap/sensitive_blocked/goal_isolated/step_exhausted`（设计见 `docs/design_f20_weight.md` §5） | 归因有、降级分支无 |

---

## 2. 当前实现逻辑

### 2.1 门槛②「消融收敛」——目前只有"可消融"，没有"收敛"

- **Ω(x) 可行域**（`future_sim_minloop.py` line 11/212）：①转移必在库中存在（禁凭空造边）②逆向单步禁跨 memory_class 强冲突；topic 弱边禁跨类（line 260）。
- **可消融参数**集中在一个 `CONFIG` dict（改这里做消融实验，日志头记录全套参数）。
- **β** 三档在 `f20_weight.BETA_PROFILES`；衰减 `(1+δ_step)^β`，`δ_step` 按 `DELTA_STEP_BASE × SLOPE^(step-1)`。
- **裁剪**：branch_prune_w=0.3（低权裁剪）、topic_edge_w=0.5（topic 弱边降权）、relation_edge_w=1.0、prune_cluster_size=25（大簇横穿裁剪）。
- **缺口**：「参数不再频繁改动」这个验收条件没有任何量化——没有"最近 N 轮消融报告参数漂移 < X%"之类的判定器，也没有一份固定的消融结果台账。

### 2.2 门槛③「边覆盖率扰动」——有补偿，缺量化

- 现有实现：`factor_density(iid)` = `cluster_connectivity × (1 - debt_ratio × LAMBDA_DEBT)`，硬下限 `D_FLOOR=0.3`。即"库越欠债，该卡密度因子越低"。
- **缺口1**：`debt_ratio` 的口径标注为"E7 P2 同源"，但 F20 内部没有独立的 `edge_coverage_ratio` 计算（覆盖率 = 实有边 / 应有边？定义未落）。
- **缺口2**："结果不再随库债务剧烈浮动"需要**稳定性校验**：同一组固定 query，在两次库债务变化前后，top-k 位次漂移是否被限制在阈值内。目前无此脚本。
- 可复用素材：`eval/experiment/f20_nightly_guard.py` 已有「5 场景快照 + 位次漂移检测」框架（smoke/explore1-4，判定翻转 + top-k 漂移），可作为门槛③稳定性校验的骨架。

### 2.3 门槛④「失败溯源 + 降级分支」——归因有、降级无

- `attribute_failure(con, goal_prefix, anchor_ids, max_steps)` 返回四类归因之一：`edge_coverage_gap` / `sensitive_blocked` / `goal_isolated` / `step_exhausted`。
- 已有回归 `f20_weight_regression.case_failure_attribution()`（覆盖四类可命中）。
- **缺口**：「边稀疏时返回**低置信推演**，不随机崩坏」的降级分支**未编码**。当前步数耗尽只归因、不产出低置信路径。

---

## 3. 现存缺陷（带证据）

| 编号 | 缺陷 | 证据 |
|---|---|---|
| G1 | 门槛②无收敛判定器、无消融结果台账 | 全仓无「收敛」判定脚本；`grep 消融` 仅命中参数注释与文档，无验收件 |
| G2 | 门槛②参数三处散落（CONFIG / BETA_PROFILES / REF_INLINE_THRESHOLD） | `future_sim_minloop.py:34`、`f20_weight.py:34`、`atomic_skill.py:124` |
| G3 | 门槛③无 `edge_coverage_ratio` 独立计算 | `factor_density` 只吃 `debt_ratio`，来源"E7 P2 同源"未引用具体模块 |
| G4 | 门槛③无结果稳定性校验脚本 | 无"债务前后 top-k 漂移"回归；`f20_nightly_guard` 是告警非门禁 |
| G5 | 门槛④降级分支未实现 | `attribute_failure()` 只归类不产出低置信路径；grep 无 "low_confidence"/"降级推演" 实现 |
| G6 | 门槛②③④与 F 系晋升「四条同时满足」无统一裁决入口 | 无 `promote_check()` 汇总四门槛并出 PASS/FAIL 的门面 |
| G7 | `#10` 台账仅一行「待启动」，无分解 | `review_20261121_system_status.md:211` |

> 注：全仓无传统 TODO/FIXME 代码标记；`memory_gate.py:78` 的 `except OSError` 已于 P2-5 修（告警不静默），不属 #10 范围。

---

## 4. 外部依赖

| 依赖 | 用途 | 位置 |
|---|---|---|
| `data/memory/corpus_store.db` | 图/卡/边/topic_links 全量 | 唯一主库（33MB） |
| BGE-small-zh-v1.5 | 语义相似度（门槛③稳定性比对可能用到） | embedding 基座 |
| `memory/memory_relations.py` | `load_graph()` / 路径验证 | 门槛①③共用 |
| `eval/experiment/f20_weight.py` | β/因子/归因 | 门槛②④ |
| `eval/experiment/future_sim_minloop.py` | Ω(x) 可行域 + CONFIG 消融块 | 门槛② |
| `eval/experiment/f20_nightly_guard.py` | 5 场景快照 + 漂移检测框架 | 门槛③骨架 |
| `data/experiment/f40_cross_cluster_question_bank.jsonl` | 门槛①题库 | 门槛③可借题库做稳定性 query |
| `eval/core/e7_nightly_governance.py` | 债务/hold 治理 | 门槛③ debt_ratio 口径源 |

---

## 5. 回归点

### 5.1 已有回归

| 脚本 | 覆盖 | 与本任务关系 |
|---|---|---|
| `eval/audit/f20_weight_regression.py` | β 单调/D_FLOOR/F 连续/常量契约/四枚举归因/公式恒正（20 项） | 门槛②③④的**因子级**回归已有，可作底座 |
| `eval/core/e2_gate_calibration.py` | 门控阈值标定扫描（GATE_HARD_PASS/ASK） | 门槛①外无关（属 Gate 阈值体系，不属 F 系四门槛） |
| `eval/audit/e7_gate_regression.py` | E7 自治三路分流 8 case | debt/hold 治理侧，门槛③债务口径相关 |

### 5.2 缺失回归

| 缺失 | 说明 |
|---|---|
| 门槛② 收敛验收 | 无（本任务核心新建项之一） |
| 门槛③ 覆盖率扰动/稳定性 | 无（本任务核心新建项之二） |
| 门槛④ 降级分支 | 无（因降级未编码，本任务核心新建项之三） |
| F 系四门槛统一裁决 `promote_check` | 无 |

---

## 6. 待决策点

| 编号 | 问题 | 可选 |
|---|---|---|
| D1 | 门槛②「收敛」如何量化？ | (a) 参数漂移 <X% 最近 N 轮 (b) 消融曲线肘部判定 (c) 人工签字 + 日志冻结 |
| D2 | 门槛③「边覆盖率」定义？ | (a) 实有边/应有边（需定义应有） (b) 债务比 debt_ratio 直接作代理 (c) 多簇覆盖率加权 |
| D3 | 门槛③「扰动被量化」的稳定阈值？ | top-k 漂移 ≤K 位 / Jaccard ≥Y / 判定零翻转 |
| D4 | 门槛④降级分支形态？ | (a) 返回低置信 top-k（带 confidence 标签） (b) 返回显式 `degraded` 状态 + 归因 (c) 二者都 |
| D5 | 统一裁决入口是否本期做 `promote_check()`？ | 本期做 / 后置 |
| D6 | 是否引入新回归脚本 vs 扩展 `f20_weight_regression`？ | 新建 `f40_promotion_gate_regression.py`（推荐，边界清晰）/ 扩展旧脚本 |

---

## 7. 备选实施路线

### 路线 A（推荐）：骨架优先，最小可验收
按「已有素材拼装」顺序，不重写引擎：
1. **门槛②**：新增 `eval/audit/f20_ablation_convergence.py` —— 读 `future_sim_minloop.CONFIG` + `f20_weight` 常量，对固定题库跑 N 档参数（β 三档 × LAMBDA_DEBT 三档），输出参数-命中和曲线 + 收敛判定（D1 选定口径）。产出 `data/experiment/ablation_runs/`。
2. **门槛③**：新增 `eval/audit/f20_coverage_stability.py` —— 复用 `f20_nightly_guard` 的 5 场景快照骨架，前后对比 top-k 漂移，加 `edge_coverage_ratio` 计算（D2 口径）。产出稳定性 PASS/FAIL。
3. **门槛④**：在 `f20_weight.py` 增加 `degrade_path()` 返回低置信 top-k + `degraded=True` + 归因（D4），补回归到新建脚本。
4. **汇总**：可选 `scripts/f40_promotion_check.py`（D5 决定是否本期）。
- 优点：复用现有 80% 素材，改动集中在 eval/audit，不碰生产检索路径，风险低。
- 缺点：门槛②收敛判定需你先定口径（D1）。

### 路线 B：先定口径再编码
先出三份「口径标定文档」（收敛定义 / 覆盖率定义 / 降级契约），冻结后再一次性编码 + 回归。
- 优点：避免口径返工；与门槛①（先定 v1.0 定稿再编码）风格一致。
- 缺点：多一轮对齐，主线推进慢。

### 路线 C：仅补门槛④降级分支（最小闭环切片）
只做门槛④降级分支 + 回归，②③后置。
- 优点：最快见效，④是唯一「设计好但完全没编码」的分支。
- 缺点：②③仍空，不构成完整晋升门禁。

---

## 8. 建议下一步（待用户裁决 D1-D6 后启动）

推荐 **路线 A**，因现有素材（F20 因子回归、5 场景快照、归因函数）已覆盖 80%，门槛②③④都可「拼装」而非「重写」。
需你先裁决 **D1（收敛口径）/ D2（覆盖率口径）/ D4（降级形态）** 三项，D5/D6 可默认（后置 promote_check、新建独立回归脚本）。

---

## 9. 证据附录（本盘点实测命令）

- `grep -rn "门楘\|gate\|threshold"` 全仓 → 分类见 §1/§3
- `sed -n '112,126p' README.md` → 四门槛原文
- `ls scripts/f40_cross_cluster_eval.py` → 存在 13772B
- `grep -n "^def\|^DELTA_STEP\|^LAMBDA_DEBT\|^D_FLOOR" eval/experiment/f20_weight.py` → 因子函数与常量确认
- `grep -rn "Ω\|omega\|消融"` → 仅命中参数注释/文档，无收敛验收件（印证 G1）