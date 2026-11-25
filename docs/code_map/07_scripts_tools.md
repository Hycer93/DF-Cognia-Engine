# 07 scripts + tools 代码地图

> **生成日期**：2026-11-25  
> **文件数**：scripts/ 27 个 .py + tools/can-tools/ 2 个 .py = 29 个  
> **总行数**：~5,500 行（估算）  
> **定性**：scripts 以夜间自治管线为核心，配套一批一次性数据迁移与测试验证脚本；tools/ 下的 can-tools 明确不属于本认知引擎系统。

---

## 一、scripts 分类清单表

| 文件名 | 行数 | 分类 | 作用 | 运行方式 | 是否废弃 |
|--------|------|------|------|----------|----------|
| `nightly_run.py` | 556 | 常驻运维 | **夜间主驱动**，串联 E0/E2/E4/E5/E6/E7/E9 等步骤，分级超时+连续3步熔断 | cron/schtasks → scheduled_run.py | 否 |
| `_watch_nightly.py` | ~35 | 常驻运维 | 夜跑监听器，守护 nightly 进程状态 | 手动/守护 | 否 |
| `scheduled_run.py` | ~60 | 常驻运维 | Windows schtasks 触发入口，支持 --at 倒计时模式，设置 TOPIC_LAYER_ON 开关 | Windows 任务计划程序 | 否 |
| `collab_pipeline.py` | 505 | 常驻运维 | 多 Agent 协同推演（P-collab），并行执行多个 Agent 子流程 | 手动/管线调用 | 否 |
| `parallel_pipeline.py` | ~93 | 常驻运维 | 多进程调度器，分发子任务到各进程池 | 管线工具 | 否 |
| `f40_cross_cluster_eval.py` | 299 | 常驻运维 | F40 跨簇命中率考核，评估记忆聚类准确性 | 回归测试 | 否 |
| `seed_topics.py` | ~63 | 常驻运维 | Topic层建锚脚本，初始化高置信簇（星型拓扑/脑区映射/G22决策/非农复盘）并挂载 | 手动 | 否 |
| `smoke_test.py` | ~85 | 常驻运维 | 冒烟测试，验证核心管线可达性 | 回归测试 | 否 |
| `build_graph.py` | 62 | 一次性历史 | 旧 JSON → mind_graph.json 格式转译 | 一次性 | ✅ 是 |
| `clean_corpus.py` | 50 | 一次性历史 | 语料清洗工具 | 一次性 | ✅ 是 |
| `deepen_retest.py` | ~30 | 一次性历史 | 加深重测工具 | 一次性 | ✅ 是 |
| `collect_trajectories.py` | ~60 | 一次性历史 | 收集轨迹数据 | 一次性 | ✅ 是 |
| `p25_add_connect_timeout.py` | ~80 | 一次性历史 | P25 连接超时参数修复注入 | 一次性 | ✅ 是 |
| `probe_memory_design_20261119.py` | ~70 | 一次性历史 | 2026-11-19 内存设计探测验证 | 一次性 | ✅ 是 |
| `run_p_skeleton_ab_20261119.py` | ~120 | 一次性历史 | 2026-11-19 A/B 对比骨架跑批 | 一次性 | ✅ 是 |
| `anchor_debate.py` | ~150 | 一次性历史 | 锚点辩论实验脚本 | 一次性 | ✅ 是 |
| `debate_collect.py` | ~280 | 一次性历史 | 辩论数据收集 | 一次性 | ✅ 是 |
| `debate_live.py` | ~70 | 一次性历史 | 实时辩论记录 | 一次性 | ✅ 是 |
| `save_share2.py` | ~40 | 一次性历史 | 数据保存/分享操作 v2 | 一次性 | ✅ 是 |
| `save_share3.py` | ~45 | 一次性历史 | 数据保存/分享操作 v3 | 一次性 | ✅ 是 |
| `router_llm_test.py` | 24 | 一次性历史 | 路由 LLM 化后快速验证（3道题看复杂度判定+加深候选） | 手动调试 | ✅ 是 |
| `migrate_memory.py` | ~63 | 一次性历史 | 旧库迁移，**硬编码 C:\opt\astra 路径！** | 一次性 | ✅ 是 |
| `migrate_novel_to_store.py` | 369 | 一次性历史 | 小说数据迁入 store | 一次性 | ✅ 是 |
| `migrate_patents_to_store.py` | 116 | 一次性历史 | 41 件专利入库 | 一次性 | ✅ 是 |
| `oneoff/fix_quarantine_20261123.py` | — | oneoff治理 | 隔离区修复（2026-11-23 治理补丁） | 一次性 | ✅ 是 |
| `oneoff/p3_governor_verdict_20261123.py` | — | oneoff治理 | P3 Governor 判决逻辑修复 | 一次性 | ✅ 是 |
| `oneoff/backfill_suggested_class_20261123.py` | — | oneoff治理 | suggested_class 回填补漏 | 一次性 | ✅ 是 |

---

## 二、常驻运维脚本详解

### 2.1 nightly_run.py（核心中的核心）

**位置**：`D:\uidq0280\Downloads\Agent\cognitive_engine_v3\scripts\nightly_run.py`  
**大小**：35,272 字节 / ~556 行  
**作用**：整个认知引擎的"夜间自治管线"——无人值守地串联所有质量步骤。

#### STEPS 步骤链

nightly_run 按序执行以下步骤（编号即对应质量检查阶段）：

| Step | 阶段 | 功能 | 超时策略 |
|------|------|------|----------|
| E0 | 初始校验 | 基线可用性检查，确保运行时环境正常 | 短超时 |
| E2 | 基础巡检 | 记忆完整性与一致性检查 | 中超时 |
| E4 | 维护巡逻 | 自动检测 pending 队列、别名冲突、未配对记忆 | 中超时 |
| E5 | 深化评估 | 对可疑条目执行深度学习判断 | 长超时 |
| E6 | 聚类评估 | 跨簇相似度与聚类质量评分 | 长超时 |
| E7 | 夜间质检 | 范式审批官机制，审查当日产出 | 长超时 |
| E9 | 最终报告 | 汇总全部步骤结果，生成质量报告 | 固定耗时 |

#### 超时与熔断

- **分级超时**：每个 Step 有独立超时上限，按步骤重要性分配长短。
- **连续熔断**：**连续 3 步失败即终止全程**，防止错误累积污染后续数据。
- **日志输出**：每步写入 `logs/nightly_console.log`，供故障回溯。

#### 调用链路

```
Windows schtasks → scheduled_run.py [--at HH:MM] → subprocess → nightly_run.py → [E0, E2, E4, E5, E6, E7, E9]
                                              ↕ TOPIC_LAYER_ON=1
                                             ↕ NO_MEMORY=1
```

`scheduled_run.py` 在启动 nightly_run 前设置两个关键环境变量：
- `TOPIC_LAYER_ON=1`：启用主题锚层全局开关
- `NO_MEMORY=1`：跳过部分内存密集型操作

### 2.2 collab_pipeline.py（多Agent协同）

**大小**：505 行 / 26,794 字节  
**作用**：P-collab（Productivity Collaboration）多 Agent 协同推演管线。在单条管线内启动多个 Agent 并行推理、互相参考输出结果。  
**运行方式**：作为 nightly_run 的一个子步骤或独立调用。

### 2.3 parallel_pipeline.py（多进程调度）

**大小**：~93 行  
**作用**：通用多进程调度器，将任务分发给进程池中的 Worker。为 pipeline 类脚本提供并发能力。

### 2.4 seed_topics.py（Topic 层建锚）

**大小**：~63 行  
**作用**：存量高置信锚建锚脚本，用于 Topic 层上线第一步。内置四个预定义高置信簇：

| topic_id | 抽象描述 | cue_anchors |
|----------|---------|-------------|
| topic_star_topology | 星型拓扑专利簇进化 | P10, P12, P22, 星型拓扑, 星域联邦 |
| topic_brain_region | 脑区映射体系 | 脑区, 海马体, 前额叶, 杏仁核 |
| topic_g22_decision | G22 决策整合骨架 | G22, 光谱系数, 决策整合 |
| topic_feinong_review | 非农数据复盘 | 非农, 金价 |

支持 `--dry-run` 干跑模式。通过 `topic_layer` 模块写数据库并挂载记忆条目。

---

## 三、一次性历史脚本详解

### 3.1 数据迁移类

| 脚本 | 行数 | 日期 | 说明 |
|------|------|------|------|
| `migrate_memory.py` | ~63 | 2026-11-02 | **⚠️ 硬编码 C:\opt\astra 路径！** 从旧 Astra 存储目录迁移记忆数据。**已废弃** |
| `migrate_novel_to_store.py` | 369 | 2026-11-12 | 小说数据迁入 memory store，内含大量文本块 |
| `migrate_patents_to_store.py` | 116 | 2026-11-12 | 41 件专利数据入库 |

### 3.2 格式转换/清理类

| 脚本 | 行数 | 说明 |
|------|------|------|
| `build_graph.py` | 62 | 旧版 JSON 格式转为 mind_graph.json（2026-11-13 起 mind_graph 已有新格式） |
| `clean_corpus.py` | 50 | 语料清洗管道 |
| `deepen_retest.py` | ~30 | 加深重测验证 |

### 3.3 实验/调研类

| 脚本 | 行数 | 日期 | 说明 |
|------|------|------|------|
| `probe_memory_design_20261119.py` | ~70 | 2026-11-19 | 内存设计方案探测验证 |
| `run_p_skeleton_ab_20261119.py` | ~120 | 2026-11-19 | P-skeleton 架构 A/B 对比 |
| `p25_add_connect_timeout.py` | ~80 | — | P25 连接超时修复注入 |
| `router_llm_test.py` | 24 | — | 路由 LLM 化后快速验证（3道示例题：面试稳了/投钱建议/天气查询） |
| `collect_trajectories.py` | ~60 | — | 收集交互轨迹数据 |

### 3.4 辩论实验类

| 脚本 | 行数 | 说明 |
|------|------|------|
| `anchor_debate.py` | ~150 | 锚点辩论实验主脚本 |
| `debate_collect.py` | ~280 | 辩论数据收集处理 |
| `debate_live.py` | ~70 | 实时辩论记录 |

### 3.5 数据保存类

| 脚本 | 行数 | 说明 |
|------|------|------|
| `save_share2.py` | ~40 | 数据保存/分享 v2 |
| `save_share3.py` | ~45 | 数据保存/分享 v3 |

---

## 四、oneoff 子目录脚本

**路径**：`scripts/oneoff/20261123_governance/`（2026-11-23 专项治理批次）

| 脚本 | 作用 | 性质 |
|------|------|------|
| `backfill_suggested_class_20261123.py` | suggested_class 字段回填补漏 | 一次性修补 |
| `fix_quarantine_20261123.py` | quarantine（隔离区）状态修复 | 一次性修补 |
| `p3_governor_verdict_20261123.py` | P3 Governor 判决逻辑修复 | 一次性修补 |

均为单次运行即生效的修补脚本，无回滚需求（或回滚已在下次全量重构时解决）。

---

## 五、tools/can-tools（备注与认知引擎无关）

**路径**：`tools/can-tools/`

| 脚本 | 行数 | 作用 |
|------|------|------|
| `can_query.py` | 292 | CAN 总线信号查询工具，读取车机 CAN 数据流 |
| `import_can_xlsx.py` | 190 | 从 Excel (.xlsx) 导入 CAN 信号配置 |

**重要说明**：这两个脚本属于**车机 CAN 信号分析工具链**，与认知引擎 v3 的记忆管理/推理管线无任何依赖关系。它们被放置在 tools/ 下可能是历史遗留的共用仓库结构。如需删除可不影响认知引擎正常运行。

---

## 六、给未来 AI 的避坑要点

### 🔴 危险脚本（严禁直接运行）

1. **`migrate_memory.py`**
   - **硬编码 `C:\opt\astra` 路径**，该目录在当前 Windows 机器上大概率不存在。
   - 这是旧库迁移脚本，目标库可能已被新系统替代。
   - **不要跑**，除非确认源路径存在且需要恢复历史数据。

2. **`nightly_run.py`**
   - 这是**无人值守的主管线**，会同时执行 E0-E9 全流程。
   - 每次运行会修改 memory 数据库、写入大量日志和报告。
   - **仅在需要完整夜间质量审计时运行**，或通过 `scheduled_run.py` 间接触发。

### 🟡 需谨慎脚本

3. **`collab_pipeline.py`**（505 行）：多 Agent 协同，每个 Agent 都会产生 LLM 调用，消耗 API Token。
4. **`migrate_novel_to_store.py`**（369 行）：包含大文本块，运行时间长且占用内存。
5. **`migrate_patents_to_store.py`**（116 行）：41 件专利一次性写入，重复运行会导致重复数据。

### 🟢 可安全清理（已废弃）

以下脚本已完成使命，可以安全归档或删除（保留 git history）：

- `build_graph.py`、`clean_corpus.py`、`deepen_retest.py`
- `collect_trajectories.py`、`router_llm_test.py`
- `anchor_debate.py`、`debate_collect.py`、`debate_live.py`
- `save_share2.py`、`save_share3.py`
- `probe_memory_design_20261119.py`、`run_p_skeleton_ab_20261119.py`、`p25_add_connect_timeout.py`
- `oneoff/20261123_governance/` 下全部 3 个脚本
- `tools/can-tools/` 下全部 2 个脚本（与本项目无关）

### 📋 依赖关系速查

```
scheduled_run.py ──subprocess──→ nightly_run.py ──step──→ [E0,E2,E4,E5,E6,E7,E9]
                                   │                              │
                                   ├──── collab_pipeline.py       │ (作为子步骤调用)
                                   ├──── f40_cross_cluster_eval.py│ (测试调用)
                                   └──── smoke_test.py            │ (回归调用)
                                                                        
seed_topics.py ──uses──→ topic_layer + load_memories    (Topic层初始化)
router_llm_test.py ──uses──→ Engine.router.route()     (调试用)
migrate_* ────→ standalone                               (各自独立)
can-tools ────→ standalone (非本项目)                     (互不相关)
```