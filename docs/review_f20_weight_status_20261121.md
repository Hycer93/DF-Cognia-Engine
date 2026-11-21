# F20 权重层现状盘点（2026-11-21）

> 定位：主线任务 #5「F20 权重层」启动前的现状盘点。**只做盘点，不改代码、不改常量。**
> 打底文档：`docs/design_f20_weight.md`（2026-11-16 定稿）、`docs/design_retrieval_weight_calibration.md`（P2-4）。
> 盘点结论含一个**当前红警**：F20 夜间守卫已连续告警（`flipped=true, rank_shifted=true`），
> 本文给出根因定位与处理建议。

---

## 0. 一句话结论

F20 代码本身**无逻辑缺陷**，当前守卫告警**不是 F20 权重漂移**，而是**上游图谱数据漂移**
（goal 卡 `9e23b6ee` 的 relation 边全数消失、smoke 场景簇债务比例变化）导致 BFS 可达性退化。
F20 在 smoke 场景的排序变化是**正确的响应**，不是 bug。真正待办：
(a) 图数据退化需人工查因；(b) F20 守卫缺"数据漂移 vs 权重漂移"的区分归因；
(c) F20 无单元测试、无消融脚本落库、δ_step 魔法数无标定说明。

---

## 1. 现有代码位置

### 1.1 核心实现（唯一主文件）

| 文件 | 行数 | 角色 |
|---|---|---|
| `eval/experiment/f20_weight.py` | 200 | **F20 主实现**：5 因子 + 复合公式 + β 档位表 + 失败归因四枚举 |

关键符号：

| 位置 | 符号 | 说明 |
|---|---|---|
| L34-39 | `BETA_PROFILES` / `BASELINE_PROFILE` | β 三档 `{conservative:0.6, baseline:1.0, exploratory:1.6}`，写死不可运行时改 |
| L41 | `CONF_TIER` | `{L0:1.0, L1:0.7, L2:0.4}` |
| L42 | `EDGE_W` | `{rel:1.0, topic:0.5}` |
| L43 | `FRESH_HALFLIFE_DAYS = 90` | 新鲜度半衰期（对齐范式层 decay_period_days） |
| L44 | `LAMBDA_DEBT = 0.5` | 债务软惩罚系数（A2 消融默认） |
| L47 | `D_FLOOR = 0.3` | D 硬下限，债务=1.0 时仍 >0（红线，写死） |
| L63-67 | `factor_conf(con, iid)` | C(conf)：查 `core_memory_card.confidence`，缺失回落 0.7 |
| L70-86 | `factor_fresh(con, iid, now)` | F(fresh)：`0.5^(age_days/90)`，age 用**连续秒/86400**（非 `.days` 整数截断） |
| L89-91 | `factor_edge(etype)` | R(edge)：`rel*` → 1.0，其余 → 0.5 |
| L94-120 | `factor_density(con, iid)` | D(density)：`conn_score × (1 - min(1,debt_ratio)×λ)`，`max(D_FLOOR, ·)` |
| L123-139 | `score_branch(con, branch, step, profile, now)` | **主入口**，返回 `(w, factors)` |
| L144-171 | `attribute_failure(...)` | 失败归因四枚举 |
| L174-192 | `main()` | 自检：memory_ops 债务红线 + 因子样例 |

### 1.2 夜间守卫

| 文件 | 角色 |
|---|---|
| `eval/experiment/f20_nightly_guard.py` | 五场景基线判定翻转 + top-k 位次漂移检测；吞错恒退 0 |
| `scripts/nightly_run.py` L76-80 / L317-338 | 守卫挂主链尾部（timeout 30min）+ 晨报摘要读取 |

### 1.3 接线处（唯一消费端）

`eval/experiment/future_sim_minloop.py`：
- L351-366：F20 接线块。对 F10 全部候选分支调 `f20.score_branch()`，按 `(-w, to)` 确定性排序取 top-K，
  原均匀权重保留在 `w_uniform` 字段（纪律④：叠加层，删此块即回滚）。
- L367-378：F40 逆向回溯 + 失败归因写入 `snapshot["f20_meta"]`。

### 1.4 消费边界（重要）

`grep` 全仓：`f20_weight` / `score_branch` **只在 `eval/experiment/` 内被引用**，
`cgraph/`、`memory/`、`scripts/`（除 nightly 调 guard 外）**零引用**。
→ F20 是纯 experiment 层，**未接入生产检索/路由链路**，风险面可控。

---

## 2. 当前实现逻辑

### 2.1 复合权重公式

```
w(branch) = (1 + δ_step)^β · C(conf) · F(fresh) · R(edge) · D(density)
```

| 因子 | 实际代码 | 备注 |
|---|---|---|
| `δ_step` | `0.6 if step>0 else 0.0`，再 `*= 0.85**max(0, step-1)` | **魔法数**：0.6 / 0.85 无标定文档、无消融依据（见 §3 缺陷 D3） |
| `β` | 查 `BETA_PROFILES[profile]` | 人闸静态参数 |
| `C` | `CONF_TIER.get(confidence, 0.7)` | 缺失回落 0.7 |
| `F` | `0.5^(age_days/90)` | 无 event_ts/ts 按 1 年前处理 |
| `R` | `rel*`→1.0 / 其余→0.5 | 前缀匹配 |
| `D` | `conn_score × (1 - min(1,debt_ratio)×0.5)`，`max(0.3, ·)` | debt = (hold留置 + 簇内无 relation 边孤立) / 簇成员数 |

### 2.2 数据源依赖（全部只读 `data/memory/corpus_store.db`）

| 因子 | 依赖表 | 依赖字段 |
|---|---|---|
| C | `core_memory_card` | `confidence` |
| F | `core_memory_card` | `event_ts`, `ts` |
| D | `topic_links`(双向聚合) + `core_hold_pool` + `core_relation` | `topic_id`, `inst_id`, `verdict`, `status` |
| 归因 | `core_memory_card` / `core_relation` / `topic_links` | 度、簇、敏感簇判定 |

### 2.3 消费链

```
F00(锚定冻结) → F10(发散候选) → [F20 复合权重排序取 top-K] → F40(BFS 逆向回溯)
                                      ↑ 仅排序，不改 F40 主干
```

F20 **只影响 F10 候选分支的展示排序与 top-K 选择**；F40 BFS 路径由独立 `_edges_from` 决定，
不读 F20 权重。这点在 A3 消融中有明确记录（"δ_step 仅作用于候选分支排序，不改 F40 BFS 主干"）。

---

## 3. 现存缺陷

### D1【红警·最高优先】守卫当前告警，根因是图数据漂移不是权重漂移

`logs/f20/guard_latest.json`（2026-11-21 21:34）：`flipped=true, rank_shifted=true`。

实测定位：

| 场景 | 现象 | 根因 |
|---|---|---|
| explore1 | PASS→FAIL（`step_exhausted`） | goal 卡 `9e23b6ee` 在 `core_relation` 中**零边**（全库搜不到）；1119 路径的 `f7a20eb7 ↔ 04353a35` 边**现已完全不存在**（任意 status 都没有），两卡 topic 也无交集 → BFS 断链 |
| smoke / explore2 | top-3 由 `f4198bf7` → `58707552` | `58707552` 的 D=0.419 > `f4198bf7` 的 D=0.300（后者所在簇债务更高），F20 排序**正确响应**了债务变化 |

**判定**：F20 代码无缺陷；`f20_weight.py` 最近一次改动（`726a712`）只是补 `timeout=5`，不改逻辑。
问题在图谱数据：goal 卡边丢失 + 簇债务比例变化。这是**上游数据治理问题**，需人工排查那次写库/治理动作。
（DB 未被 git 跟踪，无法用 git 回溯；依赖 `logs/f20/nightly_*/` 历史快照佐证。）

### D2 守卫归因盲区：分不清"数据漂移"与"权重漂移"

守卫只报 `flipped/rank_shifted` 布尔量，不区分是 F20 权重异常还是库数据变动导致。
本次告警若按字面理解会误判为"F20 权重层故障"，实际是数据问题。**建议**：告警时附
`anchor_state_features` 差分（n_cards / n_with_graph_neighbor / memory_classes）+ goal 边数快照，
让晨报能一眼分辨。

### D3 `δ_step` 魔法数无标定、无消融落库

`delta_step = 0.6`（首层）与斜率 `0.85` 硬编码在 `score_branch`，设计文档 §6 A3 只做"观察长链存活"，
**未产出参数扫描结论**，也无单独常量名（无法单因素消融）。

### D4 无单元测试

`f20_weight.py` 只有 `main()` 自检（红线 + 样例打印），**无断言式回归**。
`grep` 确认 `eval/audit/` 下无任何 f20 相关回归脚本。
现存 `eval/audit/retrieval_weight_doc_sync_regression.py` 只校验 P2-4 检索常量与文档一致，**不覆盖 F20**。

### D5 消融脚本未落库、不可复现

`logs/f20_ablation_report.md` 是 2026-11-16 一次性产物（A1-A4），
**无对应可复现脚本**（`eval/experiment/` 下无 `f20_ablation*.py`）。重跑消融须手写循环。

### D6 F 因子无 event_ts 卡的处理偏保守且影响排序

`factor_fresh` 对无 ts/event_ts 的卡按"1 年前"处理（F≈0.06）。smoke 场景的 `58707552`（ev=None）、
`04353a35`（ev=None）都吃这个折扣。这本身是设计选择，但与"新鲜度对齐范式层 90d"的口径存在张力——
无 event_ts 不等于陈旧。口径需裁决。

### D7 D 因子每次打分全表扫描 `core_relation`

`factor_density` 每次调用执行 `SELECT ... FROM core_relation WHERE status='active'`（全 183 行 ×2 次）
+ `topic_links` 双向聚合。当前库小（183 边）无感，但**随图谱增长是 O(N) 每卡**，
与 P2-6 记录的 Q4 O(N) 问题同源。属未来性能债，本轮不阻塞。

---

## 4. 外部依赖

| 依赖 | 位置 | 影响 |
|---|---|---|
| `data/memory/corpus_store.db` | 只读 | 全部因子数据源；**未被 git 跟踪**，数据漂移不可 git 回溯 |
| `core_memory_card` / `core_relation` / `topic_links` / `core_hold_pool` | 同上 | 表结构或语义变更会直接改变因子值 |
| `future_sim_minloop.py` | 接线端 | 改其 F20 块会影响五场景基线 |
| `scripts/nightly_run.py` | 调度 | 守卫挂载点，改 STEPS 位置影响晨报 |
| `memory/constants.py` | **不依赖** | F20 与检索权重是两套独立体系，互不影响 |

**关键澄清**：`design_retrieval_weight_calibration.md`（P2-4）讲的是 **`memory/memory_query.py` 检索打分权重**
（`W_CODE_MATCH`/`GATE_HARD_PASS`/`BIAS_CAP` 等），与 F20（`f20_weight.py` 分支赋权）**是两套系统**。
P2-4 文档不能直接当 F20 的标定依据，只能借鉴其"单因素对照 + 人闸改值"方法论。

---

## 5. 回归点

| 回归项 | 脚本/位置 | 现状 |
|---|---|---|
| 五场景基线判定 + top-k 位次 | `eval/experiment/f20_nightly_guard.py` → `logs/f20/guard_latest.json` | ⚠️ **当前 failed**（D1） |
| memory_ops 债务红线（D≥0.3） | `f20_weight.py main()` | ✅ PASS（历史 λ 0/0.5/1.0 均守住） |
| β 三档判定不翻转 | `logs/f20_ablation_report.md`（A1 补跑） | ✅ 三档五场景判定一致（历史快照） |
| 位次确定性（同权并列） | minloop `sort(key=(-w, to))` | ✅ 已修（次级键 inst_id 定序） |
| F factor 连续 age | `factor_fresh` | ✅ 已修（弃 `.days` 截断） |
| 断言式单测 | **无** | ❌ 缺（D4） |

**基线真值（`design_f20_weight.md` §10）**：`/logs/f20/nightly_20261119/` 之前五场景全绿；`20261121` 起红警。

---

## 6. 待决策点

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 当前守卫红警如何处置 | A. 先查图数据退化（goal 卡 `9e23b6ee` 边丢失、`f7a20eb7↔04353a35` 边消失）再重定基线；B. 直接重定五场景基线接受现状 | **A**：中断链是数据问题，直接重定会掩盖真实退化 |
| Q2 | 守卫告警是否补数据漂移归因 | 是/否 | 是（D2），否则下次仍误判 |
| Q3 | `δ_step` 的 0.6/0.85 是否标定 | 抽常量+扫描 / 保持现先验 | 先抽常量（零行为变更），扫描排后续批次 |
| Q4 | F 因子对无 event_ts 卡口径 | 现"按1年前" / 改"按 ts 或中性 0.5" / 按写入时间 | "按 ts 或中性 0.5"更合理，但**属语义变更需 A/B 评估**（同 P2-7 纪律） |
| Q5 | F20 是否上生产链路 | 仅实验层 / 接入检索或路由 | **仅实验层**（现状）；上生产须先补单测+标定，且需单独裁决 |
| Q6 | 是否需要 D 因子预聚合快照 | 现每次全表扫 / 缓存 | 库小可暂缓，与 P2-6 的 preaggregation 方案合并考虑 |

---

## 7. 实施备选方案

### 方案 A（推荐·最小闭环）"先止血，再补测试，标定排后"

1. **A-1 查图数据退化**：排查 `9e23b6ee`、`f7a20eb7↔04353a35` 边丢失原因（哪次治理/写库动作），
   评估能否补回边；补不回的按"数据债"记入台账，不遮掩。
2. **A-2 守卫补数据漂移归因**（D2）：`guard_latest.json` 增 `delta_vs_last`（anchor_state_features 差分 +
   goal 边数），晨报能分辨"数据变"vs"权重变"。
3. **A-3 补断言式单测**（D4）：新建 `eval/audit/f20_weight_regression.py`，覆盖
   - β 三档对同一分支的单调性（β↑ → w 单调不减，对 1+δ>1 的真）
   - D 红线（memory_ops 五卡 D≥0.3）
   - F 因子连续 age 无跳变（同 ts 微差 → F 差 < 阈值）
   - 档位表不可运行时改（`BETA_PROFILES` 内容断言）
   - 归因四枚举各命中一次（构造样例）
4. **A-4 收口 D1**：A-1 结论落地后，判定"补回边重跑"或"确认数据债后重定基线"，再更新 §10 基线真值。
5. **A-5 `δ_step` 抽常量**（Q3，零行为变更）：提 `DELTA_STEP_BASE=0.6`、`DELTA_STEP_SLOPE=0.85`，
   供后续标定，不改值。

**优点**：风险最低，先解决红警再谈优化；单测补上后后续改值有护栏。
**缺点**：不立即解决 D6/D7 语义与性能问题（排后续批次）。

### 方案 B "先标定再收口"

在方案 A 基础上，追加 `eval/experiment/f20_ablation.py` 可复现消融脚本，串行跑
A1(β)/A2(λ_debt)/A3(δ_step)/A4(半衰期)，产出 `logs/f20_ablation_<date>.md`，
按结果改常量（走人闸 + 更新文档 + 回归基线）。

**优点**：把 D3/D5 的魔法数变成有据可依的标定值。
**缺点**：周期长；且当前图谱数据在漂移（D1 未决），**在脏数据上标定会得到不可靠结论**——
必须在 A-1/A-4 数据稳定之后再做，否则白跑。

### 方案 C "上生产接线"

不建议本轮做。F20 现零生产引用；上生产前必须具备：断言式单测 + 数据稳定 + 标定完成 + 独立裁决。
提前上会放大 D1 类数据漂移对线上排序的影响。

---

## 8. 建议执行顺序

```
第一步  方案 A-1 查图数据退化（人工，1 次）→ 出结论
第二步  按 A-1 结论决定：补回边重跑守卫 / 或确认数据债重定基线（A-4）
第三步  A-2 守卫归因 + A-3 断言式单测 + A-5 δ_step 抽常量（可并行，均低风险）
第四步  数据稳定后，视需要启动方案 B 标定（非必须，视 #5 目标范围）
```

**不建议**：在 D1 未结案前动任何权重常数值或重定基线。

---

## 9. 附：本次盘点实测证据

- `logs/f20/guard_latest.json` 2026-11-21：`flipped=true, rank_shifted=true`
- explore1 判定链：1116/1117/1118/1119 全 PASS → 1121 FAIL（`step_exhausted`）
- `core_relation` 中 `9e23b6ee`（goal）零边；`f7a20eb7↔04353a35` 边任意 status 均不存在
- 实测 BFS：anchor `2e9d4192` 可达集仅 4 节点，`89cb3db1` 不可达
- F20 打分对照：`58707552`(D=0.419) vs `f4198bf7`(D=0.300) → 排序变化为正确响应
- 全仓 grep：F20 无生产接线，仅 `eval/experiment/` 引用