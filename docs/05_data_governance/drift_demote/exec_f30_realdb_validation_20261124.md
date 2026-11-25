# F30 正向演化 真实库场景验证报告（2026-11-24）

> 目的：mock 单元测试只证"逻辑符合纸面设计"，本报告在**真实记忆图谱**（真值考题簇 topic_memory_ops + 专利簇）上跑 F30，抽样审阅推演轨迹，暴露真实脏数据下的隐藏问题。
> 考题：`anchor_query="记忆入库断档事故"` → `goal_prefix="a65a500c"`，锚卡 87f66cc1/f1577a38/58707552。
> 层级：experiment 只读观测。

---

## 一、参数实验矩阵（baseline/conservative/exploratory × prune_w × max_steps）

| 实验 | profile | prune_w | max_steps | #0 | #1 | #2 |
|---|---|---|---|---|---|---|
| baseline | baseline | 0.3 | 12 | frozen/local_max(8) | frozen/step_limit(12) | frozen/local_max(3) |
| conservative | conservative | 0.3 | 12 | 同 baseline 完全相同 | 同 | 同 |
| exploratory | exploratory | 0.3 | 12 | 同 baseline 完全相同 | 同 | 同 |
| prune 0.5 | baseline | 0.5 | 12 | frozen/local_max(4) | frozen/local_max(1) | frozen/local_max(3) |
| prune 0.7 | baseline | 0.7 | 12 | frozen/local_max(3) | frozen/local_max(0) | frozen/local_max(3) |
| prune 0.9 | baseline | 0.9 | 12 | frozen/local_max(3) | frozen/local_max(0) | frozen/local_max(3) |
| steps 3 | baseline | 0.3 | 3 | step_limit(3) | step_limit(3) | step_limit(3) |
| steps 6 | baseline | 0.3 | 6 | step_limit(6) | step_limit(6) | local_max(3) |
| steps 20 | baseline | 0.3 | 20 | local_max(8) | local_max(12) | local_max(3) |

**核心观察**：三 profile 路径 100% 相同（见发现①）；max_steps=20 时 #1 在 12 步自然 local_max（真实收敛点=12）；prune_w≥0.7 时 #1 起点秒冻结（n_steps=0）。

---

## 二、抽样审阅轨迹

### 分支#1（最长链，12 步，边类型序列）
```
r:new r:new r:new r:back t:weak t:weak t:weak r:new r:new t:weak r:new r:back
```
- 弱边占比 4/12 = 33%（step5/6/7 连续三个 topic 弱边 + step10 一个）。
- step4 出现 `rel:back`（反向边），step5-7 连续 topic 弱边簇内游走。

### 分支#0（8 步）
```
r:new r:new r:new r:new r:new r:new r:back r:back
```
- 全程 relation 边，无弱边，链路质量高。

### 分支#2（3 步）
```
r:new r:conflict r:back
```
- 含一条 `rel:conflict`（矛盾边），符合发散预期。

---

## 三、发现清单

### 发现①（非 bug，机制说明）：β profile 对 F30 内层路径无影响
- **现象**：conservative/baseline/exploratory 三档路径完全相同。
- **根因**：β 作用在 `(1+δ_step)^β`，同一跳内所有候选的 δ_step 相同（step 相同、DELTA_STEP_BASE/SLOPE 相同），β 只是**统一缩放**该步所有候选的 w，**不改变相对排序**。贪心只依赖相对排序 → 路径对 β 不敏感。
- **影响**：β 在 F30 内层几乎失效，仅在 F20 跨步 top-K 初筛时有用。**这是数学必然，非 bug**，但设计文档 §2 应补注记：F30 内层的 β 敏感性来自「不同候选可能 step 不同」的场景（如降级候选），纯同步推进时 β 无区分力。
- **处置**：设计文档补说明，代码不改（行为正确）。

### 发现②（数据缺陷，#34 遗留）：fd5772c6 memory_class=NULL
- **现象**：分支#1 step10 `fd5772c6(None)→e464a2d1(skill_refine)` topic 弱边，mc 一侧为 None。
- **核查**：两卡同 domain(002-003) + 共同 topic 1 个 → **同簇合法边**，Ω(x) 因 same_topic=True 放行，**逻辑正确**。
- **但**：fd5772c6 的 memory_class 为空（#34 回填遗留），导致 Ω(x) 跨类判据对它失效（mc=None 时不触发拒绝）。若未来出现"mc=None 卡经 topic 弱边跨到异簇"的真跨类跳，会漏拦。
- **处置**：不改 F30 逻辑（当前放行正确），但记账：mc=NULL 卡是 Ω(x) 的盲区，随 #34 尾 B 组清理。

### 发现③（行为观察，非 bug）：弱边簇内横向游走
- **现象**：分支#1 step5/6/7 连续三个 topic:weak_ts 边，在簇内兄弟卡间轮转。
- **分析**：合法（同簇、非跨类、权重过 prune_w），但推演价值低——只是簇内平铺兄弟卡轮转，不产生语义跃迁。根因：topic 弱边在同一簇内彼此权重相近，贪心会连续选弱边。
- **处置**：非 bug，不改。但记录为**后续 F30 增强候选**：可考虑「连续弱边惩罚」或「弱边后强制优先 relation 边」，避免簇内空转。当前不改，纳入设计演进备忘。

### 发现④（已验证正确）：高敏簇隔离 / 悬空边跳过
- ✅ 所有路径节点均未触及 `topic_user_relation`（高敏隔离生效）。
- ✅ 悬空边 `24c2bfef` 已被 `exists_fn` 跳过，所有终态卡 summary 均真实（悬空修复生效）。

### 发现⑤（设计待澄清）：起点卡不受 prune_w 约束
- **现象**：分支#1 起点 w=0.295 < prune_w=0.3，但未被剪（path[0] 保留）。
- **核查**：path[0] 是 F10 分支自带起点（`branch["weight"]`），不经过 F30 的 prune_w 过滤；step1+ 才受约束。**逻辑正确**（起点是既定输入非 F30 选择）。
- **但**：prune_w≥0.7 时 #1 n_steps=0（起点自保留但无合格邻居→立即 frozen）。这是合理的"起点即终点"退化，非 bug。设计文档可补一句说明。

---

## 四、结论

| 项 | 结论 |
|---|---|
| 发现① β 内层无影响 | 数学必然，补设计说明，代码不改 |
| 发现② mc=NULL 盲区 | 数据缺陷(#34遗留)，F30 逻辑正确，记账随 #34 清 |
| 发现③ 弱边簇内游走 | 非 bug，记 F30 增强候选，不改 |
| 发现④ 高敏/悬空 | ✅ 验证正确 |
| 发现⑤ 起点不剪 | ✅ 逻辑正确，补设计说明 |

**总体判定**：F30 在真实库上**行为符合设计预期，未发现代码级 bug**。5 项发现中 3 项是「逻辑正确但需澄清/记账」，0 项需改 F30 代码。唯一可考虑的增强（弱边簇内游走）属设计演进，非本期范围。

**与原计划「修复发现的 F30 bug」的偏差说明**：真实库验证未暴露代码 bug（单元回归已抓掉 3 个真实 bug），故本阶段无代码修改。发现①②③⑤均属「机制说明/数据缺陷/增强候选」，已记账。

---

## 五、残留待办

- #47 DB 悬空边：本报告发现②的 mc=NULL 盲区同源，继续观察扰动，暂不清理。
- F30 增强候选（弱边簇内游走抑制）：纳入设计演进备忘，非本期。
- 设计文档 §2 补说：β 内层无区分力 / 起点不受 prune_w / mc=NULL 盲区。