# cognitive_engine_v3

旧版 [cognitive_engine](../cognitive_engine)（7500 行流水线架构）的重构。
核心转变：**骨架从"每轮强制流水线"→"带权有向思维图"，路由自决走链，赫布规则让路径随使用进化。**

## 架构

```
用户输入
  ├─ ComplexityRouter  路由：简单任务只走保底三条，复杂任务沿高权重边贪心选主路径
  │     保底：Aa(发散·多维考虑) / Eo(验证·意图回归) / Jw(总结·轮末进化) —— 认知论，永远强制
  ├─ ChainExecutor     走链：每个骨架节点转阶段性 Prompt；模型可输出 [[DEEPEN:xx]] 自决加深
  ├─ HebbianUpdater    fire together, wire together：走通+ε / 走偏-ε，图 JSON 持久化
  ├─ MemoryStore       ChromaDB 三集合（闪念/知识/对话），兼容旧库迁移
  └─ LLMClient         OpenAI 兼容双后端：Ollama 本地 / DeepSeek 云端，配置切换
```

快尺度（改图）与慢尺度（蒸馏）分离：`logs/edge_log.jsonl` 累积带成败标注的轨迹，
`HebbianUpdater.path_win_rates()` 可随时统计哪些思维路径真正有效——未来 DPO/LoRA 的现成教材。

## 快速开始

```bash
# 0. 依赖
pip install pyyaml chromadb networkx   # networkx 可选（图分析/可视化）

# 1. 从旧工程转译 27 骨架 → 思维图（自动修复旧 JSON 的截断损坏）
python scripts/build_graph.py

# 2. 迁移旧记忆库（可选）
python scripts/migrate_memory.py --dry-run   # 先看条数
python scripts/migrate_memory.py

# 3. 配置
cp config/config.example.yaml config/config.yaml
#    - 本地 Ollama：直接可用
#    - DeepSeek：set DEEPSEEK_API_KEY=sk-xxx （禁止写进任何文件）

# 4. 单轮对话
python -m cgraph.engine "从多角度分析：老板说'你很有潜力'是夸我还是捧杀我"

# 5. A/B 验尸：同一问题，裸模型 vs 图路由
python eval/ab_test.py "问题"
python eval/ab_test.py --file eval/questions.txt
python eval/score.py logs/ab_result_xxx.jsonl   # LLM 裁判盲评 + 裁决
```

## 与旧版的对比清单

| | v2（旧） | v3（本工程） |
|---|---|---|
| 骨架形态 | 7 阶段固定流水线，每轮全跑 | 有向图节点，路由自决走链 |
| 深度控制 | 无（全开/全关布尔开关） | 复杂度路由 + 模型 [[DEEPEN]] 中途加深 |
| 保底 | 工序校验（动作合规） | 三条认知论保底 + 意图回归比率（结果对齐） |
| 进化 | 关键词规则检测 | 赫布边权更新 + 轨迹日志（蒸馏预留） |
| 安全 | 硬编码 API key、shell_exec 无防护 | key 走环境变量，无 shell 工具 |
| 路径 | C:\opt\... 5 处硬编码 | 全部收进 config.yaml |

## 文件

```
cgraph/
  graph.py      思维图（27节点/44边，边权，赫布更新，持久化）
  router.py     复杂度路由 + 保底强制 + 贪心主路径
  executor.py   走链执行 + 自决加深 + 意图回归自检
  hebbian.py    边权更新 + 轨迹日志 + 路径胜率统计
  llm.py        双后端适配（OpenAI 兼容）
  memory.py     ChromaDB 记忆层
  engine.py     入口（run 一轮全流程）
scripts/
  build_graph.py       旧 cognition_mapping.json → data/mind_graph.json
  migrate_memory.py    旧库三集合 → 新库
eval/
  ab_test.py    A/B 双路对比埋点
  score.py      LLM 裁判盲评 + 验尸裁决
```