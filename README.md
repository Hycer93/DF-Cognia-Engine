# DF-Cognia-Engine

带权思维图认知引擎：路由自决走链 + 赫布边权微调（旁支层面，效果待质量盲评验证）+ 记忆驱动的经验归档。

本仓是 [DF-Cognia-Protocol](https://github.com/Hycer93/DF-Cognia-Protocol) 的引擎参考实现——协议定义认知骨架与调度契约，本引擎负责执行。

## 核心思想

旧认知引擎（7500 行流水线架构）的重构。核心转变：

**骨架从"每轮强制流水线" → "带权有向思维图"**，路由器自决走链，赫布规则对旁支做边权微调（主干路径由结构权重锁定，#21 消融实验证实边权学习不改变主链，详见 `docs/review_21_verify_ratio_ablation_result_20261122.md`）。

## 架构（契约 v2，2026-11-13）

```
用户输入
  ├─ ComplexityRouter  路由：简单任务只走保底三条，复杂任务沿高权重边贪心选主路径
  │     保底：Aa(发散·多维考虑) / Eo(验证·意图回归) / Jw(总结·轮末进化) —— 认知论，永远强制
  │
  ├─ ChainExecutor     走链（四条调度契约）：
  │     ① 节点产出 = 内部工作结果，不天然具备对外输出资格
  │     ② 加深走 JSON 受控字段 deepen，正文禁止路由暗号
  │     ③ Jw 复盘 = 主链完成后的支线任务，产出只写记忆不进回答管线
  │     ④ 专门「汇总节点」面向原始问题作答，是唯一出口
  │
  │  节点输出协议: {"analysis": "本节点工作结果", "deepen": "节点码|null"}
  │
  ├─ HebbianUpdater    fire together, wire together：走通+ε / 走偏-ε，图 JSON 持久化
  ├─ TrajectoryAssembler  走链全程拼 DPO 训练样本（chosen=思维轨迹 / rejected=裸直答）
  ├─ MemoryStore       ChromaDB 三集合（闪念/知识/对话）+ judge 管道入库
  └─ LLMClient         OpenAI 兼容双后端：Ollama 本地 / DeepSeek 云端，配置切换
```

快尺度（改图）与慢尺度（蒸馏）分离：`logs/edge_log.jsonl` 累积带成败标注的轨迹，
`HebbianUpdater.path_win_rates()` 可随时统计哪些思维路径真正有效——未来 DPO/LoRA 的现成教材。

## 快速开始

```bash
# 0. 依赖
pip install pyyaml chromadb   # Python 3.11+

# 1. 从旧工程转译骨架 → 思维图（自动修复旧 JSON 的截断损坏）
python scripts/build_graph.py

# 2. 迁移旧记忆库（可选）
python scripts/migrate_memory.py --dry-run   # 先看条数
python scripts/migrate_memory.py

# 3. 配置
cp config/config.example.yaml config/config.yaml
#    - 本地 Ollama：直接可用
#    - DeepSeek：set DEEPSEEK_API_KEY=sk-xxx （禁止写进任何文件）

# 4. 离线自测（不依赖 LLM/网络）
python scripts/smoke_test.py

# 5. 单轮对话
python -m cgraph.engine "从多角度分析：老板说'你很有潜力'是夸我还是捧杀我"

# 6. A/B 验尸：同一问题，裸模型 vs 图路由
python eval/ab_test.py "问题"
python eval/ab_test.py --file eval/questions.txt
python eval/score.py logs/ab_result_xxx.jsonl   # LLM 裁判盲评 + 裁决
```

## 与旧版的对比清单

| | v2（旧） | v3（本工程） |
|---|---|---|
| 骨架形态 | 7 阶段固定流水线，每轮全跑 | 有向图节点，路由自决走链 |
| 深度控制 | 无（全开/全关布尔开关） | 复杂度路由 + JSON 受控字段 deepen 中途加深 |
| 出口 | 链尾节点输出直接给用户 | 专门汇总节点（复盘走支线，与回答管线物理隔离） |
| 保底 | 工序校验（动作合规） | 三条认知论保底 + 意图回归比率（结果对齐） |
| 进化 | 关键词规则检测 | 赫布边权更新 + 轨迹日志（主链不变、旁支微调；蒸馏预留） |
| 安全 | 硬编码 API key、shell_exec 无防护 | key 走环境变量，无 shell 工具 |
| 路径 | C:\opt\... 5 处硬编码 | 全部收进 config.yaml |

## 文件

```
cgraph/
  graph.py       思维图（节点/边权，赫布更新，持久化）
  router.py      复杂度路由 + 保底强制 + 贪心主路径
  executor.py    走链执行（契约 v2）+ 结构化加深 + 意图回归自检
  hebbian.py     边权更新 + 轨迹日志 + 路径胜率统计
  trajectory.py  DPO 训练样本组装器
  llm.py         双后端适配（OpenAI 兼容）
  memory.py      ChromaDB 记忆层
  engine.py      入口（run 一轮全流程）
scripts/
  build_graph.py        旧 cognition_mapping.json → data/mind_graph.json
  migrate_memory.py     旧库三集合 → 新库
  nightly_run.py        夜间管线（探针 + 晨报）
  smoke_test.py         离线自测（MockLLM 全链路）
eval/            # 2026-11-15 管线分级重构（四层）
  core/          常驻自维护底盘——nightly 主链只串这些：
                 E1 探针回归 / E2 闸门标定 / E4 维护巡逻(只读报警) /
                 E5 软层自动整理 / E6 思维图巡逻 / E7 夜间治理 / E9 对话治理(归档主通道,只提案)
  drill/         定期演习——E8 影子对话（人工喂标签+外部DS，按需手动触发）
  audit/         一次性/专项审计——E3 SFT体检、打分对账、topic层回归等
  experiment/    实验原型——F系推演冒烟、A/B、deepen 实验
docs/            设计文档（affect vector / 记忆 v0.2 / 冲突级联 / 思维检查表）
memory/          记忆模块：judge 管道 / corpus_store / 置信度动态
data/ logs/      运行数据（gitignore，不入仓）
```

## 管线分级（2026-11-15 用户裁决）

**core 启动次序**：内核最先拉起 E4/E5/E6/E7（纯本地记忆内务）；
E1/E2 依赖 cgraph + DS 评分链，启动次序后置。

**主链原则**：nightly 主链 = 无人值守、纯本地、稳定闭环。
依赖人工输入（标签）或外部资源的步骤（E8）一律不进主链。
E3（一次性审计）已摘出主链；若改造为"新入库 SFT 样本准入预检"再迁移进 core，需单写晋升规约。

**F 系晋升门槛**（experiment → core，四条同时满足）：
1. 跨簇探索场景命中率 ≥80%（每轮 3~6 道拓扑独立题、隔离剔除口径）——阈值已定案 2026-11-20，详见 docs/design_f40_hitrate.md
2. Ω、β、裁剪阈值全套消融收敛，参数不再频繁改动
3. 图谱边覆盖率对结果的扰动被量化、有补偿机制——结果不再随库债务剧烈浮动
4. 配套独立失败溯源与降级分支（边稀疏时返回低置信推演，不随机崩坏）

**E8 独立演习**：`NO_MEMORY=1 python eval/drill/e8_shadow_dialog.py`
（按周/按需手动调度；晨报仍会汇报最近一次演习的落盘结果）


## 会话记忆门控（2026-11-15 用户定稿）

**读取侧两级**：轻量基线(静态摘要快照)无条件加载；深度检索门 `memory_gate.py probe`
只管向量检索，输出 `{deep_retrieve, query_hint[], reason}`，`/force-mem-on` 直通。
**归档侧主通道**：E9 夜间扫描(时序可靠)为主，会话收尾仅快速旁路。优先级：
同源hash跳过 / 旁路false+夜间true覆盖 / 旁路true+夜间false保留打风险标送E7。
**即时归档合法触发**：仅用户显式指令(`/force-archive`、"这条入库")或脚本硬事件；
模型自主标记=已废除的自觉依赖(明写风险清单)。
**raw_corpus 治理**：段落 simhash 入库比对去重；热区90天全扫/冷区跳常规仅溯源；不删除。
**transcript 兜底**：E9 只读扫 CodeChat applog 提取当日用户提交原文，simhash 去重后
复本隔离入 raw_corpus（三规约：只读/复本隔离/来源指针 source_ref 指路径+时间窗）。
**miss_case**：深度检索空结果记 hint+主题(gate_miss_case.jsonl)，卡片ID人工补，E9晨报列示。
PreCompact 钩子: `python memory/memory_gate.py dialog --file <段文件> --session <标识>`。

## 关键设计文档

- `docs/thinking_checklist_v1.md` — 思维检查表（骨架行为问题全集）
- `docs/design_affect_vector.md` — 情感向量（自主性策略参数，非拟人）
- `docs/conflict_cascade.md` — 记忆冲突三级级联判定
- `CLAUDE.md` — 项目协作规则（骨架运行时注入层，AI CLI 协作开发用）

## License

Apache-2.0（见 [LICENSE](LICENSE)）
