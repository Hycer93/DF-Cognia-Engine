# DF-Cognia-Engine v3 总设计（design_v3_master.md）

> 定稿：2026-11-15。**本文档为当前唯一权威总纲**；`docs/` 下旧设计文档
> （design_memory_v02 / design_cognitive_os / design_affect_vector 等）自此降级为
> 演进史料——其中的裁决推导过程保留价值，**结论以本文为准**。
> 接口契约见 `memory/API.md`（AI 接入版）；变更纪律：管线/模块变更随提交同步本文。

---

## 1. 系统总图（三模块 + 一实验层）

```
┌─ 思维(范式) ────────────────────────────────────────────┐
│ core_thinking_paradigm(13) / core_skill_paradigm(3)     │
│ E7 夜间提案晋升 → paradigm_review 人闸终审               │
│ render_persona.py 渲染 → prompts/prompt_persona_v{N}.md │
└─────────────────────────────────────────────────────────┘
┌─ 人格 ──────────────────────────────────────────────────┐
│ core_persona_anchor(10) 身份基线 + core_persona_evolution│
│ (行为模式演化, 出生→现在快照, 辅助性格基线)              │
└─────────────────────────────────────────────────────────┘
┌─ 记忆(三层 + 门控) ─────────────────────────────────────┐
│ raw_corpus 原文层(1330段, 11-02~11-15)                  │
│ → core_memory_card 提炼卡(245) [judge管道+人闸]          │
│ → topic_abstraction 主题锚(11 active 含7 manual_seed)   │
│ 门控: memory_gate(读两级/存判重/miss_case)               │
└─────────────────────────────────────────────────────────┘
┌─ 推演(F系·experiment层) ────────────────────────────────┐
│ F00 冻结快照 → F10 发散 → F40 逆向BFS+正演校验           │
│ (v2.1 最小闭环已四场景验证; 晋升需过四门槛)              │
└─────────────────────────────────────────────────────────┘
```

双库架构（2026-11-15 #31 切换完成）：**主库** `corpus_store.db`（影子账本，
card/paradigm/persona/topic 40+ 表，FTS5 全文索引）+ **线边库** `memory.db`
（存量，逐步归档）。trace 流水月度归档（T1，热窗 30 天）。

---

## 2. 管线账本（编号 = 时间戳历史，退役缺号不回收）

| 编号 | 层 | 职责 | 状态 |
|---|---|---|---|
| E1 | core | 探针触发 | 活跃 |
| E2 | core | 门校准 | 活跃 |
| E4 | core | 维护巡逻 | 活跃 |
| E5 | core | 软层整理 | 活跃 |
| E6 | core | 思维图巡逻 | 活跃 |
| E7 | core | 夜间治理（hold 复审/范式提案/锚词扩充，只提案） | 活跃 |
| E9 | core | 对话治理（归档主通道 + transcript 兜底，只提案） | 活跃 |
| T1 | core | trace 月度归档 | 活跃 |
| E3 | audit | — | 已退役（缺号记录） |
| E8 | drill | 演习（独立调用） | 摘出主链 |
| F00/F10/F40 | experiment | 未来推演（K=3 发散/逆 BFS/正演三指标） | 实验层 |

调度：schtasks 每日 18:00（Governor）/ 18:05（主链 `scripts/nightly_run.py` 八步
E2→E1→E4→E5→E6→E7→E9→T1）。F 系晋升四门槛：跨簇命中率 / 消融收敛 /
库扰动补偿 / 失败溯源降级。

---

## 3. 记忆门控架构（2026-11-15 用户五条修正定稿）

### 读侧两级
- 轻量基线（静态摘要快照）**无条件加载**，无布尔总闸
- 深度检索门 `memory_gate.py probe`：`{deep_retrieve, query_hint[], reason}`；
  失败/超时保守按 true（读侧假阴性代价不对称）

### 存侧主通道 = 夜间 E9（时序可靠）
- 会话收尾信号不可靠 → 仅快速旁路；夜间固定时序为主力
- **transcript 兜底**：只读扫 CodeChat applog 提取当日用户提交 → simhash 当日
  去重 → 复本隔离入 raw_corpus（三规约：只读 / 复本隔离 / 来源指针
  source_ref=路径+时间窗）。助手正文平台不落盘，由 PreCompact hook 互补
- 历史回填已完成：2026-11-02~14 共 1078 段入库（oneoff 脚本）

### 治理
- 段落 simhash 入库比对去重（`duplicate-referenced`=引用非错误）；热区 90 天
- 优先级三规约：同源 hash 跳过 / 旁路 false+夜间 true 覆盖 / 旁路 true+夜间
  false 保留打风险标送 E7
- **人闸终审**：E7/E9 晨报提案放行/否决 = 人类专属；机器零直写主库
- miss_case：probe 空结果记 hint+主题，卡片 ID 人工补，晨报列示

### 即时入库仅两类合法触发
用户显式"入库/记下来"或 `/force-archive`；模型自主标记已废除。

---

## 4. 主题锚层（topic_layer）

- `classify_topic`：锚词命中 + **E7 引用性提及过滤**（仅 detail 命中而无
  summary 命中 → hold 池待夜间复审，防复盘污染）
- 自动建锚规约 ≥5 卡；**manual_seed 特例**（manual_seed=1, inspect_freq='high'
  夜间优先检查）不成为先例
- hold 池治理：释放带 released_by 溯源；锚词扩充经用户裁决
- 图谱还债专项（11-15）：11 簇 182 卡挂载 + 三条卡死裁决；F40 可信度上限 =
  边覆盖率

---

## 5. 思维与人格

### 范式层（design_paradigm_layer.md 11-15 定稿，仍有效）
- thinking(13) / skill(3) 两表；status ∈ {candidate, active, …}
- E7 产出 propose_promote / propose_recalibrate → `paradigm_review.py` 人闸
  终审预览（实证明细/通过率/failure_mode 一屏）
- 排序键：evidence_pass DESC → evidence_count DESC → 90 天衰减降权

### 人格渲染（render_persona.py，#31 切换后新增）
- 纯衍生物层：只读主库 persona 基线 + 范式两表 → 渲染
  `prompts/prompt_persona_v{N}.md`（身份 SystemPrompt 随经历可演化）
- 版本机制：写 v{N+1} 草稿不覆盖定稿；archive 归档 + diff_log 变更日志
- 现状：v1 已产出（prompts/prompt_persona_v1.md）
- core_persona_evolution（0 条）为预留：行为模式演化快照，待首条经验积累

---

## 6. 历史文档定位

| 文档 | 定稿日 | 现状 |
|---|---|---|
| design_memory_v02.md | 11-06 | 史料（门控/E9/transcript 均在本文 §3） |
| design_cognitive_os.md | 11-04 | 史料（管线账本在本文 §2） |
| design_affect_vector.md | 11-04 | 史料（情感向量未并入主链，独立演进） |
| design_topic_layer.md | 11-13 | 史料（E7 过滤/manual_seed 在本文 §4） |
| design_paradigm_layer.md | 11-15 | **仍有效**（范式层细节唯一来源） |
| formula_future_sim_v2_1.md | 11-15 | 仍有效（F 系公式唯一来源） |
| memory/API.md | 11-15 | 仍有效（AI 接入契约唯一来源） |

---

## 7. 待办指针

- **人格层接通**（§5 现状：渲染器已建但产物无消费方，管道断在最后一米）：
  ① core_persona_anchor 基线 10 条人工复审（11-02 早期设定 vs 现系统形态）
  ② 注入点选型（静态贴 CLAUDE.md / 会话入口动态加载）
  ③ persona_evolution 攒首条行为模式记录 → E7 提案 → 人闸 → v2 演化闭环
  前置条件：今晚调度验证通过后接（人格变更影响会话行为，一次接对）
- F20 权重层 → F50/F60/F30 按序（见 CodeChat 记忆 future-sim-implementation-todo）
- judge stored 计数口径失真（含静默隔离）——管道改进攒件
- E9 提案人闸终裁晨报首轮实测（今晚 18:05 调度后）
- 耦合剥离低垂项：E9 TRANSCRIPT 路径抽成 CLI 参数（demo 化）