# F30 推演层·正向演化 设计文档（2026-11-24 定稿）

> 定位：F 系六层链路唯一未编码环节（F00✅/F10✅/F20✅/F40✅/F50✅，F30 空缺）。
> 公式体系出处：`docs/formula_future_sim_v2_1.md` §4。
> 保密：技术秘密（r108 裁决），引擎开源时本文档与 32684974/76f6b86b 卡需过滤。
> 层级：experiment 层——只读库，不写卡/不挂簇/不改范式。

---

## 0. 一句话定义与架构决策

**F30 = 分支正向演化层**。把 F20 赋权后的 K 条分支，沿库内真实边从锚态 S_0 确定性推进到终态 S_T^(k)，产出 F50 聚合所需的原生终态清单。

**架构决策（2026-11-24 用户拍板，方案 C 混合模式）**：
- **内层核心推演 = 纯确定性图循环**：状态转移 `S_t → S_{t+1}` 完全由图谱边 + F20 `score_branch` 打分决定，**LLM 绝不参与任何一步中间状态转移**。
- **外层附属展示 = LLM 终态叙述**：仅在分支 `state == reached_T` 后触发，输入该分支完整轨迹，输出可读情景文本；文本仅作 trace 附属字段，**不参与打分、不改变分支状态、不作为下一步输入**。
- **LLM 叙述可开关**：`enable_f30_llm_epilog` 默认 False；关闭时仅输出结构化分支数据（完整可用，只是无自然语言情景描述）。

**硬性契约（写死，不可配置绕过）**：
1. `S_t → S_{t+1}` 状态转移全部由图谱 + F20 完成，LLM 零参与。
2. LLM 只在 `reached_T` 分支触发；输出是附属字段，不回流推演。
3. 关闭 LLM 后推演结果完全等价（只是少一段文本），可跑全量回归。
4. F30 不生成任何库外虚拟节点；所有跳转必须落在 `core_memory_card` 已有实体上。

---

## 1. 与公式体系的对齐

公式（formula_future_sim_v2_1.md §4）：
```
单步: S_{t+1} = F(S_t, A_t, I_t)
多步: S_T^(k) = Φ_k(S_0; θ_k)
```
- `F`（演化算子）= 本设计的**加权图推进器**：在 S_t 邻域沿真实边选「F20 最高权」的下一跳。
- `A_t`（动作）= 本步选中的转移边 `{from, to, rel}`；F30 是纯推演（无真实世界动作），A_t 即「推演路径上的选择」。
- `I_t`（环境输入）= 库内属性（置信/边类型/簇密度/时间戳），由 F20 五因子吸收。
- `Φ_k`（分支专属演化函数）= 每分支独立推进器实例，θ_k 承接 F10 的平行程度因子（好坏未来光谱）。
- **允许提前收敛终止**：命中终止条件即 `frozen`，不必强推到 max_steps。
- **允许分支汇聚**：K 条分支走到同一卡是合法且有价值的信号（多情景共同关键点），记录汇聚节点，不人为排斥。

**F30 与 F40 的对称性**：
| | F40 逆向 | F30 正向 |
|---|---|---|
| 起点 | 目标 G | 锚态 S_0 |
| 方向 | 逆推回锚 | 正推往未来 |
| 搜索 | 等权 BFS（找最短可达） | 加权贪心（每步选最高权边） |
| 边规则 | `_edges_from()`（高敏过滤+大簇裁剪） | **复用同一 `_edges_from()`** |
| 输出 | 路径 + 失败归因 | 终态 S_T^(k) + 轨迹 |

---

## 2. 内层核心推演循环（确定性）

### 2.1 分支状态机

沿用五态（用户契约）：
| 状态 | 含义 | 触发 |
|---|---|---|
| `active` | 推进中 | 初始 |
| `discarded` | 已淘汰 | 邻域无合法边 / 权重低于剪枝阈值 |
| `frozen` | 冻结（提前收敛） | 命中终止条件（见 2.4） |
| `reached_T` | 抵达终态 | 命中 T 判定（见 2.4） |
| `dead` | 异常终止 | 读取异常 / 循环死锁保护 |

### 2.2 单分支推进算法（贪心加权）

```
输入: S_0 起点（F10 分支的 to 卡）, profile(baseline), max_steps=12, prune_w
state = active; path = [S_0]; visited = {S_0}; step = 0
while state == active and step < max_steps:
    neighbors = _edges_from(con, cur)   # 复用 F40 边枚举: 高敏过滤+大簇裁剪
    # 过滤: 已在 visited（防环）/ 跨 memory_class 强冲突(&Ω(x) 同 F40)
    cands = []
    for (other, etype, w_edge) in neighbors:
        if other in visited: continue
        if 高敏簇: continue
        if topic弱边跨类: continue        # Ω(x) 同 F40 L259
        w, factors = f20.score_branch(con, {"to": other, "rel": etype}, step=step+1, profile)
        cands.append({to, rel, w, factors, depth=step+1})
    if not cands:
        state = discarded; break          # 邻域枯竭
    best = max(cands, key = (w, to))      # 次级键 to 确定序（同 F20 位次稳定）
    if best.w < prune_w:
        state = discarded; break          # 权重过低
    path.append(best); visited.add(best.to); cur = best.to; step += 1
    state = _check_terminal(best, step)   # 见 2.4
```

**关键约束**：
- 边枚举**完全复用 `f20`/minloop 的 `_edges_from()`**，保证 F30/F40 边规则一字不差（高敏簇过滤、大簇横穿裁剪、relation 边永不裁）。
- 每步候选打分直接调 F20 `score_branch()`，`factors` 全量写入 trace（审计可查每个选择的因子分解）。
- 贪心 + 次级键 `to`：同权并列时按 inst_id 字典序，保证**跨运行可复现**（对齐 F20 §9 位次稳定纪律）。

**真实库验证补注（2026-11-24，见 `exec_f30_realdb_validation_20261124.md`）**：
1. **β profile 对 F30 内层路径无区分力**：β 作用于 `(1+δ_step)^β`，同一跳内所有候选 δ_step 相同 → β 只统一缩放该步所有 w、不改相对排序，贪心路径对 β 不敏感（三档路径实测 100% 相同）。β 实际只在 F20 跨步 top-K 初筛有效。属数学必然非 bug。
2. **起点卡不受 prune_w 约束**：path[0] 是 F10 分支自带起点（`branch["weight"]`），不经 F30 prune_w 过滤；step1+ 才约束。起点即终点（无合格邻居）会立即 frozen，属合理退化。
3. **mc=NULL 是 Ω(x) 盲区**：`_mc_of` 返回 None 的卡（#34 回填遗留），topic 弱边跨类判据对它失效（none 侧不触发拒绝）。当前实测放行的均是同簇合法边，但若未来 mc=NULL 卡跨异簇会有漏拦风险。随 #34 尾清理。
4. **弱边簇内横向游走**（增强候选非 bug）：连续 topic 弱边在簇内兄弟卡间轮转（实测分支 33% 弱边、连续 3 跳），合法但推演价值低。后续可考虑「连续弱边惩罚/弱边后优先 relation 边」，本期不改。

### 2.3 K 条分支并发管理

```
for k in K_branches:                      # F20 排序后的 top-K
    inst = FwdEvolver(k, profile, max_steps, prune_w)
    inst.run()                            # 各自独立推进
results[k] = inst.export()                # 各自导出 S_T^(k)
# 汇聚检测（跨分支，不改变单分支走向）
converged_nodes = 出现在 ≥2 条 path 中的卡 → 记 convergence_points
```

- K 条分支**互不阻塞**，各自用独立 visited（防各自成环），但**汇聚点共享统计**。
- 汇聚点不触发淘汰/改道——仅作 F50 聚合参考信号（多情景共同关键点）。

### 2.4 终止条件（命中任一即冻结/reached_T）

| 条件 | 状态 | 说明 |
|---|---|---|
| 命中 F10 预定义 T 卡（若指定） | reached_T | 优先 |
| 到达「无更高权未访问邻居」局部极大点 | frozen | 情境自然收敛 |
| step ≥ max_steps | frozen | 步数上限 |
| 邻域无合法边 / 权重 < prune_w | discarded | 枯竭/低质 |
| 读取异常 | dead | 记 error |

终态 S_T^(k) = 该分支停止时的当前卡 + 完整轨迹。

**实现注记（`_check_terminal` 判定顺序）**：
1. 若指定 T 卡且 `best.to == T` → `reached_T`
2. 否则若 `step >= max_steps` → `frozen`
3. 否则检查「是否还有未访问的合法高分邻居」——若无（局部极大）→ `frozen`（注意：下一步循环 `cands` 为空会先落 `discarded`，故**局部极大判定要在 cands 计算之后**，用"cands 非空但都被 visited/低权排除"与"已到边界"区分；实现上简化为：每步成功推进后**预探测**下一跳是否存在合法邻居，无则当步末置 frozen）
4. `discarded` 仅在 cands 为空或 best.w < prune_w 时置。

---

## 3. LLM 终态叙述（外层，可开关）

### 3.1 触发与隔离

- **触发条件**：`enable_f30_llm_epilog=True` **且** 单分支 `state in (reached_T, frozen)`。
- **输入**：该分支完整 path（卡 summary 序列 + 边类型 + 因子）。
- **输出**：一段自然语言「未来情景描述」文本，写入 `trace[k].llm_epilog`。
- **隔离硬约束**：
  1. LLM 输出**绝不写回** `path`/`state`/`w`/`factors`——只在 `export()` 末尾追加附属字段。
  2. `discarded`/`dead` 分支**不触发** LLM（无终态可叙述）。
  3. 关闭开关时该字段为 `None`，结构化结果完全等价。

### 3.2 风险隔离

| 风险 | 隔离手段 |
|---|---|
| LLM 幻觉污染推演 | LLM 零参与状态转移，只读轨迹生成文本 |
| 不可复现 | 关闭 LLM 后推演 100% 确定性可复现 |
| 成本失控 | 默认关闭；仅 reached_T/frozen 分支调用；可整批关 |
| LLM 文本被误当结论 | 字段命名 `llm_epilog` 且注释明确「展示用，不参与聚合」 |

---

## 4. 数据 Schema（区分核心 vs 附属）

```jsonc
{
  "f30_meta": {
    "ts": "...", "profile": "baseline", "max_steps": 12, "prune_w": 0.3,
    "enable_llm_epilog": false, "n_branches": 3
  },
  "branches": [
    {
      // ---- 推演核心结构化字段（确定性，可复现，进 F50） ----
      "k": 0,
      "state": "reached_T",              // active/discarded/frozen/reached_T/dead
      "S_T": {                            // 原生终态（禁平均，整支保留）
        "inst_id": "...", "summary": "...",
        "memory_class": "...", "confidence": "..."
      },
      "path": [                           // 完整轨迹（每步含 F20 因子分解审计）
        {"from": "...", "to": "...", "rel": "rel:augment",
         "w": 0.512, "step": 1,
         "factors": {"C_conf": 1.0, "F_fresh": 0.7, "F_fresh_source": "neutral",
                     "D_density": 0.85, "R_edge": 1.0, "beta": 1.0, "delta_step": 0.6}},
        ...
      ],
      "stop_reason": "reached_T|local_max|step_limit|exhausted|low_weight|error",
      "f20_weight": 0.512,                // 该分支最终权重（F20 top-K 时的 w）
      // ---- LLM 附属输出字段（非确定性，不进 F50，可 None） ----
      "llm_epilog": null                  // 或 "未来情景自然语言描述…"
    }
  ],
  "convergence_points": ["inst_id_shared_by_2plus_branches"],
  "n_reached_T": 1, "n_discarded": 1, "n_frozen": 1
}
```

**schema 契约**：F50 只消费 `S_T` + `path` + `f20_weight`；`llm_epilog` 被 F50 显式忽略。

---

## 5. 模块调用契约

### 5.1 依赖 F20（打分）
```python
w, factors = f20_weight.score_branch(con, {"to": other, "rel": etype}, step=depth, profile=profile)
# 返回 (浮点权重, 因子字典)；factors 含 F_fresh_source（2026-11-24 新增来源标记）
```

### 5.2 复用 F40（边枚举）
```python
from future_sim_minloop import _edges_from, _mc_of, CONFIG
neighbors = _edges_from(con, cur, in_topic_jump=...)  # 返回 [(other, etype, w_edge, raw_rel)]
# 高敏簇过滤 / 大簇横穿裁剪 / 双向边 全在此，F30 不重复实现
```

### 5.3 输出对接 F50
```python
# F30 产出 branches[k].S_T + path  →  F50 aggregate(snaps)
# F50 的 S_native 结构（collab_pipeline.py L187）可直接承接 F30 的 S_T：
#   S_native.summary ← S_T.summary
#   其余 hypotheses/gaps/final_deliverable 由 F50 侧补充或留空
# F30 不做聚合、不做 softmax、不决定 baseline 分支——全交 F50
```

### 5.4 与 F00/F10 的输入契约
```python
# 输入 = F10 输出的 K 条分支（future_sim_minloop.py f10_diverge L144）
# 每条 {from, to, rel, delta, weight, to_memory_class, to_summary, f20_factors}
# F30 起点 = 分支的 to 卡；θ_k = 分支的 delta（平行程度，好坏未来光谱，当前仅记录）
```

---

## 6. 单元测试策略（mock 关闭 LLM，核心逻辑零大模型依赖）

**核心原则**：全部推演逻辑不需 LLM 即可跑回归。LLM 只在 epilog 开关开启的集成测试里 mock。

| 用例 | 断言 | 依赖 |
|---|---|---|
| T30-1 单分支推进可达 | 给定小图，3 步内正确到 T，path 每跳边在库存在 | 假 con / 临时库 |
| T30-2 贪心选最高权 | 邻域两候选，选中 F20 分高者 | mock score_branch |
| T30-3 同权并列确定序 | 两候选同权，按 inst_id 字典序（可复现） | mock |
| T30-4 防环 | 图有环，visited 不重复访问 | 假 con |
| T30-5 高敏簇过滤 | 邻域含 topic_user_relation 成员，被排除 | 复用 _edges_from |
| T30-6 跨类 topic 边拒绝 | topic 弱边跨 memory_class 强冲突 → 不走 | 复用 Ω(x) |
| T30-7 低权剪枝 discarded | 邻居全 < prune_w → discarded | mock |
| T30-8 step 上限 frozen | 超 max_steps → frozen | 假 con |
| T30-9 K 分支独立性 | 3 分支各自 path 互不影响 | mock |
| T30-10 汇聚点检测 | 两分支经同卡 → convergence_points 含该卡 | mock |
| T30-11 LLM 关闭等价 | enable=False 与 (True+清理 epilog) 结构化结果完全相等 | mock LLM |
| T30-12 LLM 不回流 | 开 LLM 后 path/state/w 与关闭时字节级一致（仅多 llm_epilog） | mock LLM |
| T30-13 discarded 不触发 LLM | discarded 分支 llm_epilog 恒 None | mock LLM 调用计数=0 |

**回归落点**：`eval/audit/f30_regression.py`，退出码 0=全过。夜间守卫可选（experiment 层不阻塞 core）。

---

## 7. 边界与红线

### 7.1 继承红线（F 系通用）
- 只读库，产物仅日志/晨报/snapshot json，不送内生目标提案池。
- 快照冻结后全程不复检（F00 口径）。
- 高敏簇（topic_user_relation）推演默认隔离。
- relation 显式边永不裁剪。
- 不构造库外虚拟节点。

### 7.2 F30 新增红线
- **LLM 零参与状态转移**——`S_t→S_{t+1}` 全图确定性。
- **LLM 输出不回流**——只追加 `llm_epilog`，不改 path/state/w。
- **分支汇聚不排斥**——汇聚点只记录给 F50，不改道。
- **完成判定不做人工圆场**——`discarded`/`frozen` 明确标 stop_reason，不美化。
- **穷尽 N 步不出结论 = 老实报 frozen**，不编造终态。

### 7.3 不做的事
- F30 不做聚合（交 F50）、不做 softmax、不选 baseline 分支。
- F30 不做失败归因四枚举（那是 F40 的 `attribute_failure`）；F30 的 `stop_reason` 是**推进停止原因**，与 F40 的**失败归因**是两套枚举，不混用。
- F30 不用 LLM 生成中间推演假设节点——所有跳转落在 core_memory_card 已有实体。

---

## 8. 实现清单

1. `eval/experiment/f30_forward.py`（新建）：
   - `class FwdEvolver`：单分支状态机（run/export）
   - `f30_forward(branches, profile, max_steps, prune_w, enable_llm_epilog)`：K 分支并发入口
   - `_check_terminal()` / `_detect_convergence()`
   - 复用 `future_sim_minloop._edges_from/_mc_of/CONFIG` + `f20_weight.score_branch`
2. `eval/audit/f30_regression.py`（新建）：T30-1~13 共 13 用例，mock LLM。
3. `future_sim_minloop.py` 接线：main() 在 F20 排序后调 F30 推进，把 S_T^(k) 写入报告与 snapshot（改动最小，叠加层，回滚=不调 f30_forward）。
4. 报告：`logs/f30_forward_<date>.md` + snapshot 增 `f30_meta`/`branches`。
5. 夜间：暂不挂 nightly（等 F30 回归稳定后，参照 F20 守卫模式挂主链尾部 experiment 层）。

---

## 9. 与现有验证结果的衔接

- 现有 minloop 的 `verify()`（L275）是「F40 逆向路径反转后查边存在」——是**弱正演校验**，不是真推进。F30 落地后，`verify()` 的 forward_reachable 可升级为「F30 真实推进是否可达」的强校验（后续增强项，不在本期）。
- 真值集考题沿用：`anchor_query="记忆入库断档事故"` → `goal_prefix="a65a500c"`，验证 F30 推进是否与已知真值链一致。

---

## 10. 版本与血缘

- v1 定稿 2026-11-24：架构决策方案 C（确定性图骨架 + LLM 终态叙述）。
- 上游：`formula_future_sim_v2_1.md` §4、`design_f20_weight.md`、`future_sim_minloop.py`、`collab_pipeline.py`(F50)。
- 下游：F50 聚合消费 S_T；F60 正演校验复用 F30 推进器。

---

## 11. 实现注意事项（踩坑备忘，实现时逐条核对）

1. **边类型格式对齐**（已实证 2026-11-24）：`_edges_from()` 产出 `rel:augment`/`rel:back`/`topic:weak_ts`；F20 `factor_edge()` 用 `startswith("rel")` 判定 → rel 类=1.0 / topic 类=0.5，**对齐无误**。F30 传 `{"to": other, "rel": etype}` 给 `score_branch` 即得正确 R_edge。
2. **`step` 语义**：F20 `score_branch(step=N)` 的 δ_step 按 N 衰减（step=0 不衰减）。F30 第 j 跳应传 `step=j`（从起点算距离），不要从 0 重复。
3. **`_edges_from` 的 `in_topic_jump`**：与 F40 一致——上一步是 topic 弱边入簇时，本节点允许一次簇内出边。F30 需在 trace 里记录上一步 etype 以正确传参。
4. **连接管理**：F20 的 `_get_pool_matrix` 有进程级缓存；F30 多分支并发时复用同一只读连接，避免反复 open。但 F40 的 `_edges_from` 内部按 cur 查库，需保证连接未被关闭。
5. **确定序**：贪心 `max` 用 `key=lambda c: (c["w"], c["to"])`——同权按 inst_id 字典序，**否则 DB 行序漂移会让同权并列分支跨运行抖动**（F20 §9 同源教训）。
6. **汇聚检测口径**：统计 path 中「出现于 ≥2 条分支」的卡，注意去掉各分支起点（起点可能本就相近），只报真正中途汇聚的节点。
7. **LLM epilog mock**：回归里 mock 一个固定返回字符串的 LLM 桩，断言 (a) 开/关结构化结果字节级一致 (b) discarded 分支调用计数=0。切勿在回归里真调模型。
