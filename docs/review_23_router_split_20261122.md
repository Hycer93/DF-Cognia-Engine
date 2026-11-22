# #23 Router 分流强化 — 实施记录

> 生成于 2026-11-22 | 状态：**核心改造完成（待环境恢复后跑 LLM 质量回归）**
> 关联计划：`docs/cgraph_refactor_plan_20261122.md`
> 目标（#22 后修正）：**削减 LLM 调用、降 12.6x 耗时，让三档真实分档**，不治意图稀释

---

## 一、问题定位（实测）

| 现象 | 根因（文件:行号） |
|---|---|
| classify 判不出 simple | `router.py:_classify_llm` prompt 深度标准偏宽，LLM 保守抬到 medium；关键词兜底 `simple_max_chars=40` 太窄且任务词命中率高 |
| medium 与 complex 路径全同 14 节点 | **两档 reachable 域其实不同（medium 19 / complex 27 节点），但 `_greedy_path` 硬编码 `range(12)` 且完全不感知档位** → 贪心只挑最重边，最重边全落在两域交集内 → 走出同一条 14 节点路径 |

实测证据（`data/mind_graph.json`，27 节点 84 边）：
- medium(0.35/depth4) reachable = 19 节点；complex(0.2/depth8) = 27 节点。
- 但两者 `_greedy_path` 结果都是 `[Aa,Bb,Cf,Cg,Ch,Di,Dj,Dk,Dl,En,Eo,Fp,Fq,Jw]` 14 节点。
- 边权 median 0.43，0.20~0.35 之间仅 33 条边，dense 图上阈值差异不足以产生不同 reachable 路径命中。

## 二、改动清单

### 1. `_greedy_path` 增加 max_steps 并按档位裁剪（核心修复）

`cgraph/router.py`：
- `_greedy_path(start,end,domain,max_steps=12)`：原 `range(12)` 改为 `range(max_steps)`。
- `route()`：`max_steps = self.medium_steps if medium else self.complex_steps`。
- `__init__` 读 config：`medium_steps`（默认 5）、`complex_steps`（默认 12）。

**效果（实测）**：

| 档位 | 改造前 | 改造后 | 省 LLM 调用 |
|---|---|---|---|
| simple | 3 节点 / ~4 次 | 3 节点 / ~4 次 | — |
| **medium** | **14 节点 / ~16 次** | **8 节点 / ~9 次** | **省 ~7 次** |
| complex | 14 节点 / ~16 次 | 14 节点 / ~16 次 | — |

medium 路径：`[Aa,Bb,Cf,Cg,Ch,Di,Eo,Jw]`（8 节点）。

### 2. classify 判准 simple

`router.py:_classify_llm` prompt 重写：
- 新增**「先按 simple 硬判据筛一遍，命中就判 simple，不要保守上抬」**。
- simple 定义扩展：闲聊/问候/单点事实/情绪/翻译/名词解释/单句改写；**不含分析/对比/论证/决策/方案/溯源类结构诉求 → 一律 simple**。
- 给出正反示例（『你好』『X是什么』→simple；『分析风险』→medium+）。

`_classify_keywords` 兜底收紧：**极短(<20字)且无任务词才 simple**（避免把短问题误降）。

### 3. 分流观测日志

`cgraph/engine.py`：
- 新增 `self.router_log = logs/router_log.jsonl`。
- `run()` 路由后调 `_log_route()`：记录 `{ts,q,complexity,path_len,path,domain_len,reason}`，失败不阻断。
- 用途：验证线上三档真实分档，为后续质量回归提供样本。

### 4. 配置

`config/config.yaml` + `config.example.yaml` 的 `router:` 增加 `medium_steps:5` / `complex_steps:12`，可调。

## 三、验证

- 三档路径长度实测不同：simple 3 / medium 8 / complex 14。✅
- 端到端 mock LLM 走通：medium 走 8 节点，`router_log` 正确记录 `path_len:8, domain_len:19`。✅
- **回归安全**：medium 域(19节点)、complex 域(27节点)**均仍含 Dl/Eo**，Dl 审计类用例走「domain 弱触发」路径不受影响；且 simple 判严反而修复了原「长审计题误判 simple 漏触 Dl」的已知 bug。✅
- 语法检查 router.py / engine.py 通过。✅

## 四、限制与待办（环境恢复后）

- [ ] **LLM 真实分类回归**：当前后端降速（单次 60-90s），未跑真实 LLM 的 classify 准确率。需环境恢复后用 `data/probe/dl_route_regression.jsonl`（11 条）+ 新增 simple 正例回归，确认：①simple 召回率提升 ②Dl 召回不退化。
- [ ] **质量盲评**：medium 裁到 8 节点后，答题质量是否掉分（复用 `eval/audit/score.py`）。
- [ ] `medium_steps=5` 是初值，需按 skill 数/结果质量调参（当前 5 步 → 8 节点；可试 4 或 6）。
- [ ] simple 一旦召回，`_walk_chain` 是否只跑 Aa/Eo（Jw 出主链）需确认 LLM 实际调用数降到 ~4。

## 五、回滚

- 改回即恢复：`_greedy_path` 默认 `max_steps=12` 且 `route()` 不传 max_steps（或 config 设 `medium_steps:12`）。
- 日志为 append，不影响功能，可直接停用 `_log_route` 调用。