# #11 回执态度元标签子通路 — 复盘与后续衔接（2026-11-22）

> 工单：**#11（P1，方案A）** ｜ 提交：`06305fc`（已推远端）
> 关联：`docs/review_20261121_system_status.md` §五（立项）、`docs/review_memory_versioning_20261122.md`（#12/#13 同族设计铁律）
> 性质：**复盘 + 后续任务规划**。本轮不写代码，仅固化文档与台账。

---

## 一、实现要点回顾（已落地）

**核心哲学**：态度回执是**纯附属观察层**，人审可读，机器推理链路默认不消费。

### 新增模块 `memory/memory_attitude.py`
- 三枚举 `approve / disapprove / neutral`，输入自动归一（大小写/空格），非法输入直接丢弃不落库。
- 存储：独立 JSONL `data/memory/memory_attitude.jsonl`；**不改 SQLite 主库 schema、不新增数据库表**，与 `judge_log.jsonl` / `gate_log.jsonl` 审计范式统一。
- API：`record_attitude`（写入）/ `list_attitudes`（筛选查询）/ `summarize_attitudes`（单条记忆统计汇总）。

### 接入 `memory/memory_judge.py`（Jw 判定钩子）
- `_JUDGE_SYSTEM_PROMPT` 追加**可选** `attitude` 对象，**不改原有 verdict 枚举**（store/hold/discard）。
- 仅 `_judge_call_llm`（LLM 通路）解析提取，存入内部键 `_attitude`。
- 规则桩 stub 通路**完全不产生**态度记录 → 降级时能力自动关闭，旧行为 100% 兼容。
- `judge_and_store` 主循环捕获 `_attitude` → `record_attitude` 落附属存储；无论候选最终 store/hold/discard，态度独立落库。
- **强约束**：态度不生成新记忆卡、不改 `core_memory_card` 任何字段（confidence/superseded_by 等全不动）。

### 回归 `eval/audit/attitude_regression.py`
- 沙箱隔离（复制 db + patch 路径常量），不碰真实库，**22 项全通过**。
- 原有 judge 8/8、pending 18/18、dialog 27/27 回归全绿，无破坏性变更，真实库零污染（320 卡不变）。

---

## 二、⚠️ 复盘事实修正（与代码实际核对，务必以此为准）

复盘初稿在 3 处技术前提与当前代码不符，落地 P2 前必须修正：

| # | 初稿说法 | 代码实际（证据） | 修正结论 |
|---|---|---|---|
| 1 | "Hebbian 反馈信号单一，只有 verify_ratio" | `cgraph/hebbian.py:34-42` `WalkFeedback` **已有两个字段**：`verify_ratio` + `utility`；`cgraph/engine.py:60,86` `run(user_input, utility=...)` 已接线并传入 `WalkFeedback` | **不是缺两路，是第二路 `utility` 存在但一直无人喂值**（所有 `engine.run()` 调用点都用默认 `utility=0.0`）。#11 的用户回执是**第三路可映射信号**，可映射到 `utility` 或新增独立权重项 |
| 2 | "读取某一轮 **trail_id** 对应的用户回执" | 全库**无 `trail_id` 概念**；Hebbian 用 `WalkFeedback.path`（骨架路径）+ `edge_log` 累积轨迹；态度记录字段是 `target_memory_id` / `source_dialog_id` / `ts`，**无 trail_id** | P2-A 的"按 trail_id 关联"前提不成立。改用 **`target_memory_id`（被评价记忆）+ 时间窗** 关联到相应路径反馈；若需精确到"轮"，须先引入 trail_id 概念（属新增设计，不在 P2 轻量范围） |
| 3 | "红线写入 **ARCHITECTURE.md**" | 项目**无 `ARCHITECTURE.md`**；现有架构文档为 `docs/design_cognitive_os.md`（自称"架构宪法 v2"）、`docs/design_v3_master.md` | 红线写入 **现有 `docs/design_cognitive_os.md`**，不新建空壳文件 |

> 注：`utility` 已有接线是**利好**——P2-B 融合时不必新增 Hebbian 输入通道，只需把"态度→utility/reward"的映射值真正喂进 `engine.run(utility=...)` 或 `HebbianUpdater.apply` 的融合项即可，改动面比初稿预估更小。

---

## 三、评审风险点缓解现状

评审指出：Hebbian 反馈信号单一，高度依赖 LLM 内部自检 `verify_ratio`，存在**自循环漂移风险**（自己判自己对，再据此加强路径）。

现在系统具备**三路潜在独立信号**的数据/接口基础：
- 原有①：`verify_ratio`，来自 Jw 复盘自检（已接线，当前唯一实际生效信号）。
- 已有②：`utility`，外部效用反馈（hebbian 有字段、engine 有入参，**当前无人喂值**）。
- 新增③：**用户主观回执**（#11 已采集，`memory_attitude.jsonl`）。

⚠️ **现状态纠正**：#11 **只完成「采集存储」**，尚未接入 `HebbianUpdater` 权重更新逻辑——`hebbian.py` 当前**完全不读取** `memory_attitude.jsonl`。这不构成 bug（红线即要求默认不消费），但意味着"缓解自循环漂移"的**效果尚未兑现**，须 P2-A/B 落地后才成立。

#### 加权融合参考（P2 开发参考，非本轮实现）
```python
# positive=1.0  neutral=0.5  negative=0.0   无用户回执时 w2=0 → 完全回退旧逻辑
reward_self = verify_ratio                       # 原有自检信号
reward_user = map_attitude_score(attitude_records)  # 用户回执映射(第三路)
final_reward = w1 * reward_self + w2 * reward_user  # 显式加权，非覆盖
```
约束：无回执完全兼容旧行为；开关可控（可关用户反馈通路做 A/B 消融）；Hebbian **不直接读原始 JSONL**，经封装的小工具函数读汇总分数，保持职责边界。

---

## 四、与「调制型真赫布」的前置关系

既定落地路径：
- **Stage0**：现有计数器版本（基线，当前线上）✅ 当前状态
- **Stage1**：补齐两路反馈源（#11 完成采集，**还差接入 Hebbian 加权**）+ 节点激活度输出契约改造
- **Stage2**：离线仿真消融实验，对比计数器 VS modulated-hebbian
- **Stage3**：双模式开关灰度上线

✅ #11 已完成用户反馈信号的**采集底座**。
🟡 缺口：
1. 把回执信号接入 Hebbian 的 reward 计算（P2-A/B）。
2. 改造 Executor 所有原子节点输出 `activation` 激活分数（0~1），为赫布共现乘积准备输入（P2-C）。

> 这两个缺口补齐前，**不适合直接上真调制赫布在线运行**，否则反馈通路仍残缺（Stage1 未完成）。

---

## 五、现存小局限（已标注）

1. **回执来源单一**：当前仅来自 `memory_judge` LLM 解析用户自然语言评价（"我同意/不同意这条记忆"）；**尚无上层 UI/交互层的点赞/点踩 API**。
   - 扩展点：可对外暴露薄 API，允许上层直接调 `memory_attitude.record_attitude()`，绕过 LLM 解析直写回执。**存储层已就绪，仅缺上层调用入口**。
2. **回执绑定依赖 `context.entity_hint`** 提供 `target_memory_id`；无法定位目标记忆 → 态度丢弃不落库（避免无归属的无效反馈）。
3. **JSONL 存储适合中等规模**；未来回执量级巨大才评估迁移 SQLite 附属表。现阶段 JSONL 够用、改动最小。

---

## 六、后续 P2 任务清单（承接 #11，编码必须遵守约束）

| 编号 | 任务 | 关键约束 |
|---|---|---|
| **P2-A** | 工具函数：读取某组用户回执（按 `target_memory_id`+时间窗），映射为 0-1 reward 分数；带开关可关用户反馈 | **不按 trail_id**（不存在）；封装独立工具，Hebbian 不直读 JSONL |
| **P2-B** | 改造 `HebbianUpdater.apply()`，实现 `verify_ratio` + 用户回执 reward 加权融合；保留旧计数器逻辑作可切换基线 | 显式加权非覆盖；无回执回退旧逻辑；开关可 A/B 消融 |
| **P2-C** | Executor 节点契约升级：每个原子节点输出 `activation: float 0~1`，为调制赫布离线仿真提供输入 | 只加输出字段，不改现有路由/打分逻辑 |
| **可选** | 暴露上层薄 API，支持 UI 层直接传入点赞/点踩，不经 LLM 解析 | 复用 `record_attitude`，存储层已就绪 |
| **实验** | 离线消融脚本：对比"仅自检 / 自检+用户回执"两种模式下边权演化差异 | 纯 eval/experiment，不改生产 |

---

## 七、边界红线汇总（详见 `docs/design_cognitive_os.md` §六-B）

1. `memory_attitude.jsonl` 是**纯人审审计附属存储**。
2. 默认推演、路由、记忆打分**不消费**态度数据。
3. 即使后续接入 Hebbian，也是**显式加权融合**，不是直接覆盖原有自检信号。
4. 规则桩 stub 模式下，整个态度回执子通路**自动关闭**，不产生任何记录，保证向后兼容。