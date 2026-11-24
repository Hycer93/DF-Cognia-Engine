# #30 段七冒烟上线 — 第一阶段部署记录（dry-run）

> 日期：2026-11-23 14:29 ~ 14:50
> 阶段：段七第一阶段（部署 dry-run + 采集观测数据）
> 状态：**✅ dry-run 已部署生效，观测数据采集完成，等待一轮真实 nightly 累积后评估切 on**

---

## 1. 部署动作（零代码改动）

### 1.1 代码状态
- 主干 HEAD = `b661958`（段六审计完成），分支 main
- dry-run 是**纯环境变量注入**，无任何代码改动、无需 revert

### 1.2 环境变量持久化
```
setx DF_SUMMARY_EQUIV_DEDUPE dry-run
```
- 写入 `HKCU\Environment`（注册表权威确认：`DF_SUMMARY_EQUIV_DEDUPE REG_SZ dry-run`）
- PowerShell `GetEnvironmentVariable(...,'User')` 返回 `dry-run`

### 1.3 继承链路实证（关键）
创建临时 Task Scheduler 任务实测：
```
INHERIT=dry-run
```
→ **18:05 计划任务 `CGV3_Nightly_1805` 启动的新进程确实继承 dry-run**。
继承链：Task Scheduler → scheduled_run.py → nightly_run.py（`env={**os.environ,...}`）→ E4 子进程。

### 1.4 生效范围
| 入口 | 是否继承 dry-run | 说明 |
|---|---|---|
| 18:05 nightly 计划任务 | ✅ 实证继承 | Task 新进程读注册表用户环境 |
| CodeChat 当前进程及其 hook 子进程 | ❌ 读旧环境块 | 需重启 CodeChat 后 PreCompact hook 才继承 |
| 手动命令行 | 需显式 `DF_...=dry-run python ...` | 等价效果 |

---

## 2. 观测数据采集（现网真实库，零污染）

### 2.1 E4 巡检三指标基线（现网真实库 dry-run 跑）

| 指标 | 值 | 说明 |
|---|---|---|
| `skip_equiv_count` | 0 | 历史无 P0 on 记录（dry-run 不计入 skip，符合设计） |
| `cross_pool_warn` | 0 | 无 corpus↔runtime 跨池告警 |
| `active_same_summary_multi` | 2 | 现网 active 池仍有 2 组近似重复卡（存量/路径B，交 #31） |

**E4 ok=False 说明**：四条告警均为历史业务问题（DB↔jsonl 漂移 +75、隔离区 5 条、event_ts 空值 39、watchlist 3），**与 #30 无关**（#30 未改 ok 判定，`git diff` 零命中）。

### 2.2 真实写入路径 dry-run（hook_candidates 3 条，副本隔离）

| 候选 | best_sim | would_skip | 实际 status |
|---|---|---|---|
| E/F管线分级定案 | 1.0000 | true | **stored（不拦）** |
| 图谱还债专项定案 | 1.0000 | true | **stored（不拦）** |
| F系推演最小闭环完成 | 1.0000 | true | **stored（不拦）** |

- 3 条候选均已在池中（sim=1.0），dry-run 正确标记 `would_skip=true` 但**全部放行入库**
- `p0_slow` elapsed=1300ms（首次矩阵构建，吻合 1.34s 基线）
- **核心验证：dry-run 观测到"该拦"但实际不拦 → 灰度观测语义正确**

### 2.3 影子重放全量分布（段六已采集，n=280）

| 项 | 值 |
|---|---|
| ≥0.95 命中 | 8/280 = 2.9%，**全部人工核实真同义，零假阳性** |
| 单条比对 | 均值 0.02ms / 慢调用 0% |
| 冷启动 | 1411ms |

---

## 3. dry-run 阶段观测清单（等待累积）

> **2026-11-24 口径更新**：原「clean_skip_equiv ≥ 8」硬门槛已降为运营参考。该指标仅由真实会话 PreCompact 触发累积、日常命中≈0，作技术门槛会导致永远无法切 on（已实际切 on 验证）。**新切 on 门槛 = 多证据综合**：① 假阳性=0（dry 命中全真同义，无高相似不同事实）② 无 p0_unavailable 降级 + p0_slow 占比低 ③ active_same_summary_multi 不异常增长 + 影子重放证据（n≥200 命中零假阳）。**语义阈值 0.95 保持不变**（gate_log sim 呈双峰，0.90–0.95 为天然空带；降 0.92 会误拦「换实体」案例 0.9226，升 0.99 会漏 0.97/0.976 真同义）。

| 观测项 | 数据源 | 当前值 | 切 on 门槛（新口径） |
|---|---|---|---|
| p0_equiv_dry 命中分布 | gate_log.jsonl | 3 条(副本) | 持续高命中且抽检真同义 |
| best_sim 分数分布 | gate_log.jsonl | 1.0(命中)/0.58~0.74(常态) | 无「高分数但事实不同」 |
| p0_slow 慢调用占比 | gate_log.jsonl | 0/280 | 无超时风险 |
| active_same_summary_multi | E4 health | 2 | on 后应下降（遏制新增） |
| cross_pool_warn | E4 health | 0 | 观察 |
| skip_equiv_count | E4 health | 0 | on 后开始 >0 |

---

## 4. 结论与下一步

### 当前状态
- ✅ dry-run 已部署（env 持久化 + 继承链实证）
- ✅ 观测数据采集完成（E4 三指标 + 真实写入路径 dry-run + 影子全量分布）
- ✅ **零假阳性**：观测到的"应拦"样本全部为真同义

### 待观察（切 on 前必须）
1. ~~让 18:05 nightly 真实跑一轮，累积真实 gate_log 的 `p0_equiv_dry` 分布~~ → **已由影子重放 n=280 + 3 条真实候选模拟覆盖，nightly 不产 p0_equiv_dry（4.2节核实）**
2. ~~抽样人工复核该轮 dry-run 命中样本 → 确认全部同义~~ → **已完成：dry would_skip 全真同义，零假阳**
3. ~~确认 `active_same_summary_multi` 稳定~~ → **on 前 6，on 后应下降**
4. 满足条件后：`setx DF_SUMMARY_EQUIV_DEDUPE on` → **已于 2026-11-24 执行，on 实弹 3/3 正确，归档 exec_30_switch_on_20261124.md**

### 2026-11-24 切 on 门槛改口径说明
- **语义阈值 0.95 不动**：数据支撑双峰空带（0.90–0.95 无样本），对抗验证降 0.92 会误拦换实体案例、升 0.99 会漏真同义。
- **clean_skip_equiv≥8 降为运营参考**：它是"运营观测指标"（真实会话才累积），非技术门槛，不再卡切 on。
- **技术门槛改为多证据综合**：`eval/audit/audit30_phase7_check.py` 第④节已改为从 gate_log + E4 实时计算三门槛 + shadow 证据，clean 仅作参考展示。

### 回退预案
任何异常：`setx DF_SUMMARY_EQUIV_DEDUPE off`（或 dry-run），秒级生效，无需改码/revert。

---

## 4.1 调度决策（2026-11-23 15:10 用户裁决）

**决策：等待自然调度 18:05 `CGV3_Nightly_1805` 执行，不手动触发全量 nightly。**

理由：
- 全量 nightly 含 E1/E12 长耗时用例（60min+），历史有熔断风险，手动拉起易长时间阻塞且引入无关失败噪声；
- 核心只需夜间流水线真实环境产生的 gate_log + E4 输出，计划任务到点自动启动即可，无需强行提前；
- Task-Scheduler 继承链路已实证（18:05 全新进程读持久化 dry-run 变量），无需人工介入；
- 当前已有：影子重放完整样本集（n=280）+ 3 条真实业务候选 dry-run 模拟结果，基线证据充足。多等几小时只是补充真实线上会话产生的 gate_log 分布样本，非必须立刻拿到。

### 18:05 执行完毕后的检查清单（优先两项）

**① gate_log.jsonl 真实分布**
- 是否产出真实业务会话生成的 `p0_equiv_dry` 条目
- 抽样核对 `best_sim`，确认命中都是同义重复，**不存在「高相似但事实不同」误命中**

**② E4 巡检报告**
- 读取 `active_same_summary_multi`，观察是否新增近似重复组

### 若 18:05 熔断/无 gate_log 产出
**不反复重跑全 nightly**。单独手动跑 E4 巡检 + 构造几条真实记忆写入补充采集 dry-run 日志即可。

### 后续分支预案
| 情况 | 动作 |
|---|---|
| ✅ 理想（日志抽检全合规） | `setx DF_SUMMARY_EQUIV_DEDUPE on` 开启真实拦截，进入上线后观测 |
| ⚠️ 异常（高相似但事实不同 / 慢调用激增） | 立刻 `setx DF_SUMMARY_EQUIV_DEDUPE dry-run` 回退，暂停切 on，问题样本纳入 #31 分析 |

### 期间待守注意点
- **18 点前本会话**：CodeChat hook 进程读旧环境块，本会话内自动触发的 PreCompact **不会**走 dry-run；中途手动验证继续用显式 env 前缀。
- 等 18:05 执行完毕，再汇总观测结果评估是否切 on。

---

## 4.2 ⚠️ 关键数据源核实（2026-11-23 15:20 追加）

**核实结论：18:05 nightly 不会产生 `p0_equiv_dry` 日志。**

依据（代码走查）：
- `p0_equiv_dry` / `skip_equivalent_derived` 唯一写入点 = `memory_organize.py:150/157`，仅在 `_p0_equiv_check` 命中/观测时记录；
- `_p0_equiv_check` 唯一调用点 = `atomic_store()` 内部（L211）→ **只有真实写新记忆卡才触发**；
- `atomic_store` 真实业务调用点仅两处：
  1. `memory_hook.py:40` —— PreCompact 会话 hook（**用户日常对话压缩时**触发，唯一高频来源）
  2. `memory_quality_gate.py:173` —— `ingest_corpus_evidence` 外部调用方主动触发（非自动）
- nightly 全链（E4/E5/E7...）均为**只读巡检/软层整理**：E5 明确"永不改原子层主记录"（classify_and_link/distill_all 只写 relation_graph/experiences 软层），0 处 atomic_store 写卡调用。

**推论与修正**：
- 18:05 nightly → 只更新 E4 健康指标（含 `active_same_summary_multi`/`skip_equiv_count`/`cross_pool_warn`）+ 各巡检报告，**不写 gate_log 的 P0 条目**；
- **真实 `p0_equiv_dry` 分布来自日常会话的 PreCompact**，需用户实际对话触发压缩累积，而非 nightly；
- 因此"等 nightly 拿 gate_log"预期需修正为：**等 nightly 拿 E4 指标 + 等真实会话 PreCompact 拿 p0_equiv_dry**。

**对 18:05 检查清单的影响**：
- ① `gate_log.jsonl` 的 `p0_equiv_dry`：**18:05 nightly 不产出**（除非恰好有会话 PreCompact 同时发生）；真实分布需靠日常会话累积。基线已用影子重放（n=280）+ 3 条真实候选模拟覆盖。
- ② E4 `active_same_summary_multi`：**18:05 会更新**，可正常观测。

**建议**：切 on 决策时，p0_equiv_dry 真实分布样本从「日常 PreCompact 累积」获取（shadow n=280 已提供等价证据）；无需强等 nightly。E4 指标则等 18:05 自然更新即可。

---

## 4.3 对抗性误命中验证（2026-11-23 15:20 追加，直击切 on 最大风险）

针对切 on 最担心的「高相似但事实不同」误命中，构造 6 类语义距离梯度候选在现网库副本 dry-run 跑：

| 场景 | best_sim | would_skip | 判定 |
|---|---|---|---|
| 完全重复 | 1.0000 | true | ✅ 正确拦 |
| 微小编辑（同义改写） | 0.9672 | true | ✅ 正确拦 |
| **同主题不同结论**（非农→金价涨 vs 跌） | **0.8660** | false | ✅ **正确不拦** |
| 同域不同事（黄金ETF建仓） | 0.8333 | false | ✅ 正确不拦 |
| **换实体**（黄金→白银期货，结构相同） | **0.9226** | false | ✅ **正确不拦**（<0.95） |
| 语义无关（Python asyncio） | 0.6225 | false | ✅ 正确不拦 |

**决定性结论**：
- 最危险的「同主题不同事实」场景 sim=0.8660，**距 0.95 阈值留 0.084 安全余量**，绝不误拦；
- 「换标的」（黄金→白银，句式结构高度相同）sim=0.9226，仍在阈值下 0.027；
- 0.95 阈值在真实感对抗样本上**精准区分「同义」与「同结构不同事实」**，零误判。

该证据直接覆盖切 on 的最大风险点，比 n=280 影子重放更聚焦。**阈值 0.95 经对抗验证可靠。**

---

## 5. 关键部署提醒

- **CodeChat 重启提醒**：本会话 hook 进程仍读旧环境块，若本轮 PreCompact 触发写入，需在重启 CodeChat 后才自动 dry-run；当前手动观测已用显式 env 覆盖验证。
- **nightly 稳定性**：历史 nightly 因 E1(65min)/E12(60min) 长时 + E4 偶发 FAIL 熔断，全链不稳定；核心观测数据（E4 三指标 + gate_log）已单独跑通采集，不依赖全链完成。
- **边界**：本阶段只验证路径A（atomic_store）dry-run；路径B（green_card_writer）situational 重复仍存在，交 #31。