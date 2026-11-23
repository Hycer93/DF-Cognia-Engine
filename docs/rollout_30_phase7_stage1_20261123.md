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

| 观测项 | 数据源 | 当前值 | 切 on 门槛 |
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
1. **让 18:05 nightly 真实跑一轮**（或后续多轮），累积真实 gate_log 的 `p0_equiv_dry` 分布
2. 抽样人工复核该轮 dry-run 命中样本 → 确认全部同义
3. 确认 `active_same_summary_multi` 在新写入下是否稳定（不新增更多近似组）
4. 满足条件后：`setx DF_SUMMARY_EQUIV_DEDUPE on`

### 回退预案
任何异常：`setx DF_SUMMARY_EQUIV_DEDUPE off`（或 dry-run），秒级生效，无需改码/revert。

---

## 5. 关键部署提醒

- **CodeChat 重启提醒**：本会话 hook 进程仍读旧环境块，若本轮 PreCompact 触发写入，需在重启 CodeChat 后才自动 dry-run；当前手动观测已用显式 env 覆盖验证。
- **nightly 稳定性**：历史 nightly 因 E1(65min)/E12(60min) 长时 + E4 偶发 FAIL 熔断，全链不稳定；核心观测数据（E4 三指标 + gate_log）已单独跑通采集，不依赖全链完成。
- **边界**：本阶段只验证路径A（atomic_store）dry-run；路径B（green_card_writer）situational 重复仍存在，交 #31。