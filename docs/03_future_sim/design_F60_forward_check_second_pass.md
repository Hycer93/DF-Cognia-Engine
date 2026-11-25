# F60 正演校验 第二环设计（forward_check second pass）

> 状态：**设计定稿（待编码）**。本文只写文档，不含代码实现。
> 前置：F30 真实库验证已完成（`exec_f30_realdb_validation_20261124.md`，0 代码 bug），具备作为 F60 正演算子 F 的成熟度。
> 公式锚点：`formula_future_sim_v2_1.md` §7。
> 命名纪律：第一环输出字段 `forward_check_static` 保持不动；第二环新增字段 `forward_check_deep`，互不覆盖。

---

## 0. 一句话定义与架构决策

**F60 = 正演校验两环：第一环「静态词面粗筛」（已实装）+ 第二环「深度正演精验」（本文设计）。**

对 F40 逆映射给出的粗解候选 ΔX，代入正向演化函数 F **真跑一遍**，得到新终态 S_T' = F(S₀+ΔX, T)，再用 `‖S_T' − G‖² + λ·Ω(ΔX)` 度量「这个调整能否把现状推向目标、代价多大」，防止 F40 逆求解失真。

架构决策（三条硬契约）：

| 决策 | 内容 | 理由 |
|---|---|---|
| **D1 两环串联不并联** | 第一环 FAIL/NO_CONSENSUS → 直接终结，**不跑**第二环；第一环 PASS/PASS_WITH_GAPS → 才触发第二环 | 低算力粗筛 → 高算力精验，避免对明显跑偏的候选浪费正演算力 |
| **D2 F 算子复用 F30** | 第二环的 F 就是 `f30_forward(...)`，不再造第二个正演器 | F30 已过真实库验证；单一实现避免两套正演逻辑漂移 |
| **D3 只试算不执行** | 第二环全程只读 DB、不改 S₀、不回流；结论仅「候选 + 校验」，最终执行留人裁 | ΔX 是粗解语义（`derive_dx` note 原文："未经正演校验不得直接执行"） |

**与 `minloop.verify` 关系的澄清（关键）**：`review_8_forward_check_20261122.md` 说"深度正演复用 minloop.verify"，此为**不精确表述**。实际二者不同：
- `minloop.verify(path, branches, truth_seq)`（`future_sim_minloop.py:278`）是**考题专用评测器**——它验证「F40 逆向路径」的正确性（三指标：正向可达/与 F10 相交/步数偏差），输入是**真值路径**，属评测性质。
- F60 第二环要的是「**代入 ΔX 正演能否到达 G**」，输入是 **ΔX 候选**（非真值路径），属决策校验性质。
- **可复用的只有它的「正向可达」子检查**（`future_sim_minloop.py:281-294`：路径反转后每步边是否库内真实存在 + topic 同簇），作为第二环「图结构分量」的一个原子。但**不能整锅复用 verify**，它没有"ΔX→新终态→对 G"的链路。

---

## 1. 与公式体系对齐

`formula_future_sim_v2_1.md` §7（L120-127）F60 定义：

```
ΔX* = argmin_{x∈X} ( ‖F(S₀ + x, T) − G‖² + λ·Ω(x) )
```

变量（继承 §5 L99-106）：

| 符号 | 定义 | F60 第二环落点 |
|---|---|---|
| `G` | 预设目标终态 | 双模式：考题→goal 卡实体；开放→目标关键词集（见 §3.2） |
| `X` | 可行调整空间，受 p_n 约束 | ΔX 候选动作集（`candidate_actions_dedup`），受"只从共识/薄弱点派生"约束 |
| `Ω(x)` | 调整代价正则（改动现状阻力/成本） | 动作数 + 动作权重反比 + 跨类惩罚（见 §3.4） |
| `λ` | 逼近-代价权衡，λ 越大越倾向小改动 | 待标定区间，默认见常量表 §6 |
| `‖·‖` | 加权欧氏 / 马氏距离 | 图结构分量 + 语义分量加权欧氏（见 §3.3） |
| `F` | 正向演化函数（§4 F30 同源） | `f30_forward()` |
| `T` | 推演终点时刻 | max_steps 上限 |

**与 F40 对称性**（§5 粗解公式）：

```
F40: ΔX = argmin_x ( ‖F⁻¹(G) − (S₀ + x)‖² + λ·Ω(x) )   ← 有逆算子 F⁻¹，低算力粗解
F60: ΔX* = argmin_x ( ‖F(S₀ + x, T) − G‖² + λ·Ω(x) )   ← 无逆算子，代入正演精验
```

F40 用 `F⁻¹(G)` 快速给方向（低算力），F60 用 `F(S₀+x,T)` 真跑校验（高算力、防失真）。**倒推定位 → 正演验证双通道**，严格镜像对称，只差 F 侧有无逆算子。

---

## 2. 第一环现状（已实装，本文不改）

`scripts/collab_pipeline.py:407` `forward_check(dx, goal_keywords)`：

- **本质**：零 LLM 静态词面近似。`F(S₀+ΔX,T)−G` 的图语义粗近似 = 共识假设 + 共同薄弱点**词面覆盖目标主题的程度**（divergent 单票项不计入——未共证不构成方向证据）。
- **n-gram**：`_bigrams_seg`（`:306`）2~4 字滑窗宽松匹配。
- **四枚举**：`NO_CONSENSUS` / `FAIL` / `PASS` / `PASS_WITH_GAPS`。
- **回归**：`eval/audit/collab_forward_check_regression.py` 15/15 全绿，纯内存零 DB。
- **输出字段**：报告 `forward_check_static`。

**本文不触碰第一环任何逻辑**。第二环只消费第一环 verdict 做是否触发的门控。

---

## 3. 第二环深度正演设计（核心）

### 3.1 F 算子：复用 f30_forward

| 项 | 内容 |
|---|---|
| 调用签名 | `f30_forward(branches, profile, max_steps, prune_w, enable_llm_epilog=False, con, t_card=None, ...)`（`eval/experiment/f30_forward.py:245`） |
| 输入构造 | 把 ΔX 候选转成「起点分支」喂给 F30：起点 = 当前锚终态 S₀；`t_card` = G 的目标卡（考题模式）或 None（开放模式） |
| LLM 叙述 | 第二环**强制 `enable_llm_epilog=False`**（校验必须零 LLM 污染，LLM 不得参与任何判定） |
| 输出 | F30 `export()`：`branches[].{state, stop_reason, S_T, path, n_steps}` |

**ΔX → F30 输入的桥接**（这是第二环唯一需要新建的接线逻辑）：
- ΔX 的 `candidate_actions_dedup` 是「动作文本 + 权重」，不是图节点。F30 的 branches 需要「起点 inst_id + 方向」。
- 桥接规则：**ΔX 候选动作按权重重排成 F30 的分支意图**——每条高权重动作映射为该动作指向的目标 topic/卡作为优先方向，`prune_w` 收紧以强制沿该方向走。若动作无法映射到图节点（纯文本动作），退化：直接以 S₀ 为起点、用动作关键词构造 `t_card` 目标做正演。
- **诚实声明**：本桥接是第二环最大的设计风险点——动作文本到图意图的映射天然有损。设计上**不假装无损**：映射失败的候选在输出中标 `bridge_unsupported=true`，不参与 argmin 竞争，只记明细供人裁。

### 3.2 G 目标终态：双模式

| 模式 | 触发条件 | G 定义 | 距离度量适配 |
|---|---|---|---|
| **考题/真值模式** | 传入了 `goal_prefix` 且库内能定位到 goal 卡 | G = goal 卡（真实 inst_id） | 图距离（S_T'→G 的 BFS hop）+ 语义 Jaccard 双分量 |
| **开放模式** | 无 goal 卡，只有目标关键词集 | G = 目标关键词集合 `goal_keywords` | 退化纯语义分量：`1 − goal_coverage`（与第一环同源但用于度量距离而非粗筛） |

**G 存在的双模式是 F60 的固有约束**：真实业务多数是开放模式（无实体 goal 卡），考题模式只在 eval 里有。设计必须两种都支撑，不能只做考题模式假装通用。

### 3.3 距离 ‖F(S₀+x,T) − G‖²

加权欧氏，两分量：

```
‖S_T' − G‖² = w_graph · d_graph² + w_sem · d_sem²
```

- **d_graph（图结构分量）**：S_T' 到 G 的最短图距离，归一化到 [0,1]。
  - 考题模式：BFS(双边 core_relation + topic 同簇) 求 S_T'→G 的 hop，`d_graph = hop / max_steps`（截断 1.0；无路径=1.0）。
  - 开放模式：无 G 实体，`d_graph = 0`（该项失效，权重并入 w_sem）。
  - **复用** `minloop.verify` 的"正向可达"逻辑（边存在性 + topic 同簇判定），但不复用其 verdict。
- **d_sem（语义分量）**：S_T'.summary 与 G 的语义距离 = `1 − bigram_jaccard(S_T'.summary, G_text)`。
  - 考题模式：G_text = goal 卡 summary。
  - 开放模式：G_text = 关键词拼接；d_sem = `1 − coverage`（直接用第一环的 `_bigrams_seg` 覆盖判定，复用不重造）。

### 3.4 代价 Ω(x)

调整代价正则 = 改动现状的阻力：

```
Ω(ΔX) = α_n · (|actions| / MAX_ACTIONS)
      + α_w · (1 − avg_weight_normalized)
      + α_x · cross_class_penalty
```

| 项 | 含义 | 取值 |
|---|---|---|
| `|actions|` | 候选动作数（`candidate_actions_dedup` 长度） | 越多改动越大 |
| `avg_weight_normalized` | 动作平均权重归一化 | 权重越高=越有共识支撑=阻力越小 |
| `cross_class_penalty` | 动作跨越 memory_class 的比例 | 跨类改动代价更高（车载场景跨域改动风险大） |
| `α_n/α_w/α_x` | 权重，待标定 | 见常量表 §6 |

### 3.5 λ 权衡与决策阈值

- **λ 语义**：λ 越大越倾向小改动（§5 原义）。第二环用 `fitted = ‖S_T'−G‖² + λ·Ω(ΔX)` 排序候选。
- **λ 约束**：λ 必须 > 0 且不能太大（否则永远选"啥都不改"的零动作）。设计给标定区间，实测阶段做 λ 消融（对齐 F40 门槛②的消融纪律）。
- **硬决策线**：`‖S_T'−G‖²` 单独超过 `DIST_FAIL` 阈值 → 直接 FAIL（不管 Ω 多小，方向错了就是错了）。避免 `λ·Ω` 把"方向错误但改动小"的候选抬成赢家。

### 3.6 判定融合（第一环 × 第二环）

第二环输出独立 verdict `PASS_DEEP / GAP_DEEP / FAIL_DEEP / UNSUPPORTED`，与第一环融合：

| 第一环 | 第二环 | 融合结论 |
|---|---|---|
| NO_CONSENSUS | （不跑） | `NO_CONSENSUS` |
| FAIL | （不跑） | `FAIL` |
| PASS | PASS_DEEP | `PASS`（双环一致，最高置信） |
| PASS | FAIL_DEEP | `FAIL`（**逆求解失真被第二环抓住**——这是第二环的核心价值） |
| PASS_WITH_GAPS | GAP_DEEP | `PASS_WITH_GAPS`（一致） |
| PASS_WITH_GAPS | PASS_DEEP | `PASS_WITH_GAPS`（保留第一环缺口标注，不升 PASS） |
| 任意 PASS 系 | UNSUPPORTED（桥接失败） | 保留第一环 verdict + 标 `deep_unsupported`（诚实降级，不假装验过） |

---

## 4. 数据 Schema

第二环输出（挂 `report["forward_check_deep"]`）：

```json
{
  "triggered": true,
  "skip_reason": null,
  "goal_mode": "exam | open",
  "lambda": 0.5,
  "candidates": [
    {
      "action": str,
      "bridge_unsupported": false,
      "f30_state": "frozen|reached_T|...",
      "stop_reason": str,
      "S_T_prime": {"inst_id":..., "summary":..., "memory_class":...},
      "d_graph": 0.0, "d_sem": 0.42, "dist_sq": 0.18,
      "omega": 0.31, "fitted": 0.335,
      "deep_verdict": "PASS_DEEP|GAP_DEEP|FAIL_DEEP|UNSUPPORTED"
    }
  ],
  "argmin_action": str|null,
  "fused_verdict": "PASS|PASS_WITH_GAPS|FAIL|NO_CONSENSUS",
  "dist_weights": {"w_graph": 0.5, "w_sem": 0.5},
  "note": "深度正演仅试算不执行; argmin 为候选非最终动作"
}
```

`skip_reason` 枚举：`no_consensus` / `static_fail` / `f30_unavailable`（F30 打不开或异常）/ `no_candidates`。

---

## 5. 模块调用契约

### 5.1 依赖 F30（正演算子）
- `f30_forward(...)` 只读调用，`enable_llm_epilog=False` 强制。
- F30 异常 → `skip_reason="f30_unavailable"`，第二环整体降级，**不阻断**主链路（第一环结论保留）。

### 5.2 依赖 F40（ΔX 输入）
- 输入即 `derive_dx(report)` 返回的 `dx` dict（`collab_pipeline.py:393` 结构）。
- 只读 `dx["dx_candidates"]` + `dx["vote_stats"]` + `dx["single_branch_mode"]`，不改 `dx`。

### 5.3 依赖 F50（上游）
- `dx` 已由 F50 `aggregate` 的 `branches[].S_native` 派生，第二环不重碰 `S_native`，只消费 `dx`。

### 5.4 输入契约
- `report`（含 `forward_check_static`）、`dx`、`goal_keywords`、`goal_prefix`（可选）、`con`（只读连接）。
- 第二环**不修改** `report["forward_check_static"]`，只新增 `report["forward_check_deep"]`。

### 5.5 落点
- 新函数 `forward_check_deep(dx, report, goal_keywords, con, goal_prefix=None, profile="baseline", lambd=0.5, max_steps=12)` 放 `scripts/collab_pipeline.py`（与第一环同文件，便于复用 `_bigrams_seg`/`derive_dx`）。
- `run_f60`（`:430`）和 `run_collab`（`:466`）在写完 `forward_check_static` 后，**仅当第一环 verdict ∈ {PASS, PASS_WITH_GAPS}** 才调第二环。

---

## 6. 常量总表

| 常量 | 建议值 | 含义 | 标定方式 |
|---|---|---|---|
| `F60_LAMBDA` | 0.5（区间 0.2~1.0） | 逼近-代价权衡 | λ 消融，选 fused 排序稳定点 |
| `F60_W_GRAPH` | 0.5 | 图结构分量权重 | 考题模式调，开放模式自动置 0 |
| `F60_W_SEM` | 0.5 | 语义分量权重 | 同上 |
| `F60_ALPHA_N` | 0.4 | 动作数惩罚权重 | Ω 分量调参 |
| `F60_ALPHA_W` | 0.4 | 权重反比惩罚权重 | 同上 |
| `F60_ALPHA_X` | 0.2 | 跨类惩罚权重 | 同上 |
| `F60_MAX_ACTIONS` | 8 | Ω 归一化基准动作数 | 与 K 分支数对齐 |
| `F60_DIST_FAIL` | 0.6 | dist_sq 硬 FAIL 线 | 防 λ·Ω 抬升方向错误候选 |
| `F60_PRUNE_W` | 0.4（比 F30 默认 0.3 收紧） | 第二环正演裁剪阈值 | 强制沿 ΔX 方向 |

---

## 7. 边界与红线

### 7.1 继承红线（沿用 F 系 + F30）
- **零 LLM 参与数值**：第二环 `enable_llm_epilog=False` 强制，LLM 不得进入任何判定。
- **只读沙箱**：`con` 必为 `mode=ro`，禁止任何写操作。
- **不回流**：不修改 `S₀`/`dx`/`report`（除新增 `forward_check_deep` 字段）。
- **可复现**：同权并列按 `inst_id` 字典序定序（沿用 F30 §2 次级键纪律）。

### 7.2 F60 第二环新增红线
- **不假装验过**：桥接失败（动作→图意图映射不了）必须标 `bridge_unsupported=true`，**不得**用第一环结论冒充第二环 PASS。
- **两环不并联**：第一环 FAIL 时**禁止**调第二环（省算力 + 避免"第一环明明 FAIL 第二环却 PASS"的误导）。
- **单向门控**：第二环只降级不升级——第一环 PASS_WITH_GAPS 经第二环 PASS_DEEP 仍是 PASS_WITH_GAPS，不能洗白缺口。
- **不可直接执行**：`argmin_action` 是候选，不是最终执行动作，落盘 note 强制标注。

### 7.3 不做的事
- **不做通用无 goal 的结构距离**：开放模式 d_graph 直接置 0，不硬凑图距离。
- **不重造 n-gram**：语义分量复用 `_bigrams_seg`。
- **不整锅复用 minloop.verify**：只用其"正向可达"原子，不复用其考题评测 verdict。
- **不做多轮正演迭代**：第二环单次代入正演，不做"正演→反推→再正演"的多轮闭环（超本期 scope）。
- **不改第一环任何代码**。

---

## 8. 实现清单（编码阶段，本文不写码）

| # | 项 | 落点 |
|---|---|---|
| 1 | `forward_check_deep(...)` 主函数 | `scripts/collab_pipeline.py` |
| 2 | `_dx_to_f30_branches(dx, S0, ...)` 桥接器（含 bridge_unsupported 检测） | 同上 |
| 3 | `_goal_distance(S_T', G, mode, ...)` 距离度量（d_graph + d_sem） | 同上 |
| 4 | `_omega(dx, ...)` 代价正则 | 同上 |
| 5 | `run_f60` / `run_collab` 接线（第一环门控） | `:430` / `:466` |
| 6 | 回归 `eval/audit/collab_f60_deep_regression.py` | 新建 |
| 7 | 常量迁入（如果项目有统一常量文件） | 视项目结构 |

---

## 9. 与现有验证结果的衔接

- **F30 成熟度**：`exec_f30_realdb_validation_20261124.md` 确认 F30 真库 0 bug，可作为可信 F 算子。但注意该报告发现②：**mc=NULL 盲区**（mc=None 卡经 topic 弱边跨异簇会漏拦）——F60 第二环的 `_goal_distance` 用 d_graph 时若 G 或 S_T' 是 mc=NULL 卡，图距离判定会有同类盲区。设计上：**距离度量不依赖 mc 判跨类**，只用图 hop + 语义，规避此盲区。
- **第一环回归**：`collab_forward_check_regression.py` 15/15 保持不动，第二环新增独立回归，两者互不干扰。
- **minloop.verify**：作为考题评测的"正向可达"原子被引用，但第二环 verdict 独立计算，不混用。

---

## 10. 版本与血缘

| 版本 | 日期 | 内容 |
|---|---|---|
| v0（设计） | 2026-11-24 | 两环架构定稿；澄清 minloop.verify 非通用正演器；双模式 G；Ω/λ 常量表；7 条红线 |

**血缘**：
- 上游：`formula_future_sim_v2_1.md` §7（F60 公式）、§5（变量表）
- 兄弟：`design_F30_fwd_evolution.md`（F 算子）、`design_f40_threshold_234.md`（消融纪律）
- 盘点：`review_8_forward_check_20261122.md`（第一环现状）
- 算子成熟度证据：`exec_f30_realdb_validation_20261124.md`

---

## 11. 实现注意事项（预埋，给编码阶段）

1. **桥接器是最大风险**：`_dx_to_f30_branches` 动作文本→图意图映射有损，必须先实现 `bridge_unsupported` 检测并**优先跑**覆盖测试，确认多少候选能桥接。若桥接率过低（<50%），第二环价值存疑，需回炉讨论 F 算子输入形态。
2. **F30 的 t_card 语义**：F30 `reached_T` 依赖 `t_card` 命中。考题模式传 goal 卡；开放模式 t_card=None 则 F30 只能 frozen，此时距离全靠 d_sem——要确认开放模式 F30 是否有意义（可能就是"跑到自然收敛再比语义"）。
3. **λ 消融必做**：不标定 λ 直接上线=`fitted` 排序不可信。参考 F40 门槛②消融网格纪律。
4. **DIST_FAIL 硬线先于 λ**：判定顺序必须是「先看 dist_sq 是否 > DIST_FAIL（硬 FAIL），再看 fused 排序」，否则 λ 会把方向错的候选抬上来。
5. **不污染第一环**：新增字段 `forward_check_deep`，`forward_check_static` 字节级不动（对齐"已实装不改"纪律）。
6. **回归零 DB**：第二环回归用假 con/假 F30（mock f30_forward 返回固定 S_T'），纯内存四枚举 + bridge_unsupported + 门控（第一环 FAIL 不触发）覆盖。
7. **开放模式优先验证**：真实业务多为开放模式，编码后先用无 goal 卡场景验证，别只在考题模式 self-satisfy。