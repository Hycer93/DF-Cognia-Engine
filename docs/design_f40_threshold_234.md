# F40 晋升门槛②③④ 冻结设计文档（2026-11-22 定稿）

> 状态：**口径冻结**（2026-11-22 用户裁决）。本文档为门槛②③④的唯一契约来源。
> 对齐范式：同 `docs/design_f40_hitrate.md`（门槛①）——先冻结口径，再编码回归。
> 范围红线：全部新增代码落 `eval/*`；`core/ cgraph/ memory/` 生产模块**只读不改**。
> 机器判定仅辅助，**晋升必须人工复核签字**。

---

## 0. 三门槛一句话定义

| 门槛 | 定义（README 四门槛原文） | 本档量化口径 |
|---|---|---|
| ② 消融收敛 | Ω、β、裁剪阈值全套消融收敛，参数不再频繁改动 | β×λ_debt 9 点网格，相邻点命中率变化 ≤5% 且 top-3 Jaccard ≥0.7；机器达标 + 人工签字 |
| ③ 边覆盖率扰动 | 图谱边覆盖率对结果的扰动被量化、有补偿机制 | debt_ratio 作覆盖率代理；沙箱扰动 debt_ratio，判定翻转=0 且 top-3 Jaccard ≥0.6 |
| ④ 失败溯源+降级 | 配套独立失败溯源与降级分支（边稀疏时返回低置信推演，不随机崩坏） | 四枚举归因（已有）+ `degraded` 标记 + `low_confidence_candidates` 低置信 top-k |

---

## 1. 门槛② 消融收敛（D1 定案）

### 1.1 消融网格（固定，不寻优）

```
BETA        ∈ [0.6, 1.0, 1.6]          # 复用 f20_weight.BETA_PROFILES 三档
LAMBDA_DEBT ∈ [0.25, 0.5, 0.75]        # 0.5 为代码默认值(f20_weight.LAMBDA_DEBT)
→ 3 × 3 = 9 个网格点
```

- **评测集**：复用门槛①真跨簇题库 `data/experiment/f40_cross_cluster_question_bank.jsonl`。
  **不新增测试用例**，**不做复杂肘部寻优**。
- **λ_debt 覆盖方式**：不得改生产常量 `f20_weight.LAMBDA_DEBT`（只读红线）。
  在审计脚本内通过 `monkeypatch`（`setattr(f20_weight, "LAMBDA_DEBT", v)` 于进程内临时覆盖，
  运行结束 `finally` 还原）注入网格值；`factor_density` 读模块全局，进程内覆盖即生效。
  若某点注入失败，该点记 `error` 并跳过，不影响其他点。

### 1.2 每组输出指标

| 指标 | 来源 | 说明 |
|---|---|---|
| `cross_hit_rate` | 复算门槛①口径（`bfs` + `qualifies`） | 库里该 β/λ 下真跨簇可达率 |
| `scenarios` | 复用 `future_sim_minloop` 逻辑跑 5 场景或守卫 5 场景 | 每场景 PASS/FAIL |
| `top3` | 场景输出的 F10 分支 top-3 短 id | 用于 Jaccard |

> 说明：β 影响 F20 权重 → 影响 F10 分支排序 top-3；λ_debt 影响 D 因子 → 同样影响排序。
> 命中率部分因门槛①的 `bfs` 不读 F20 权重（纯图可达），β/λ 不改可达性；
> 故 `cross_hit_rate` 主要作"库态一致性"参照，**收敛判据以 top-3 Jaccard 为主、命中率变化为辅**。

### 1.3 机器收敛条件（全部同时满足）

- **C2-1**：相邻网格点之间，`cross_hit_rate` 变化 ≤ **5%**（绝对值）。
- **C2-2**：对应五场景输出 top-3 **Jaccard ≥ 0.7**（逐场景取最低值判定）。

相邻定义：β 相邻（0.6↔1.0↔1.6）且 λ 相邻（0.25↔0.5↔0.75），即格内 12 条正交相邻边。

### 1.4 机器达标 ≠ 晋升通过（强制人工字段）

产物 `ablation_summary.json` 必须含：

```json
{
  "machine_converged": true,
  "grid": [{"beta":0.6,"lambda_debt":0.25,"cross_hit_rate":0.83,"top3":["..."]}, ...],
  "adjacent_checks": [{"from":"b0.6_l0.25","to":"b1.0_l0.25","hit_delta":0.02,"jaccard_min":0.75,"pass":true}, ...],
  "human_accept": false,
  "reviewer": "",
  "review_ts": "",
  "note": "机器收敛仅辅助；human_accept=true 且 reviewer/review_ts 非空，方视为消融收敛验收完成"
}
```

`human_accept` / `reviewer` / `review_ts`（iso-time）默认空，**由人填**。脚本只输出骨架。

### 1.5 产物目录

`data/experiment/ablation_f40/`
- `<run_ts>/ablation_summary.json`：汇总 + 人工字段
- `<run_ts>/grid_b<beta>_l<lambda>.json`：每组参数快照 + 指标（审计溯源）

### 1.6 实现落点

`eval/audit/f20_ablation_convergence.py`（S2 新建）
- 单元用例：① 网格遍历完整性（9 点全覆盖）② 机器收敛布尔输出存在 ③ 产物目录与文件生成。

---

## 2. 门槛③ 边覆盖率稳定性（D2 定案）

### 2.1 覆盖率代理：debt_ratio

**放弃「理论应有边」**（难形式化、主观假设多）。采用 `debt_ratio` 作覆盖率代理，口径与
`f20_weight.factor_density` 一致：

```
debt_ratio = (簇内 hold 留置数 + 簇内孤立节点数) / 簇内成员数
D(density) = (1 - isolated/len) × (1 - min(1,debt_ratio) × λ_debt)   , 下限 D_FLOOR=0.3
```

### 2.2 沙箱约束（硬红线）

- **全程临时 SQLite**，`tempfile.mkdtemp()` 内复制 `corpus_store.db` 副本；
  `PRAGMA`/写入全部作用于副本，**绝不碰真实 `corpus_store.db`**。
- 结束时删除临时目录。真实库 diff 必须零改动（S3 断言）。

### 2.3 扰动方式

- 克隆 nightly 守卫 5 场景（`f20_nightly_guard.SCENARIOS`：smoke/explore1-4）。
- 人为修改**目标簇**内节点状态制造 debt_ratio 扰动：
  - 提升债务：向副本 `core_hold_pool` 插入若干目标簇成员的 hold 记录（`verdict IS NULL`）；
  - 或在副本中删目标簇内部分 `core_relation` active 边制造孤立节点。
- 对每场景：原始快照 vs 扰动快照，各自跑 F10+F20 排序取 top-3。

### 2.4 稳定性 PASS 条件（全部满足）

- **C3-1**：判定翻转数量 = **0**（各场景 PASS/FAIL 与原始一致）。
- **C3-2**：top-3 分支集合 **Jaccard ≥ 0.6**（逐场景取最低值）。

### 2.5 产物

`data/experiment/coverage_stability/<run_ts>/`
- `stability_summary.json`：每场景原始/扰动 top-3、flip、jaccard、PASS/FAIL
- 复用 `f20_nightly_guard` 的位次漂移检测骨架（不改原文件，import 其 `SCENARIOS` 常量）。

### 2.6 实现落点

`eval/audit/f20_coverage_stability.py`（S3 新建）

---

## 3. 门槛④ 失败溯源 + 降级分支（D4 定案：二者都实现）

### 3.1 触发条件（严格限定）

| 归因 | 是否触发降级 |
|---|---|
| `step_exhausted`（步数耗尽） | ✅ 触发 |
| `goal_isolated`（目标真孤立） | ✅ 触发 |
| `sensitive_blocked`（高敏拦截） | ❌ 不触发 |
| `edge_coverage_gap`（边覆盖缺口） | ❌ 不触发 |

> 语义：只有"搜索没走完/目标确实孤立"才降级出低置信候选；高敏拦截属红线不可降级召回；
> 边覆盖缺口属"补债可解"，不降级以免掩盖图谱债务。

### 3.2 降级返回结构

在不改变 `attribute_failure()` 原返回值（`(code, note)`）的前提下，新增：

```python
def degrade_path_candidates(con, goal_prefix, anchor_ids, max_steps, top_k=3):
    """门槛④降级分支：仅 step_exhausted / goal_isolated 触发。
    返回 dict：
      {
        "degraded": bool,                 # 是否进入降级
        "failure_reason": str|None,       # 复用 attribute_failure 四枚举 code
        "low_confidence_candidates": [    # 低置信候选 top-k
            {"branch": "<inst_id>", "w": float, "factors": {...}}, ...
        ],
        "note": str
      }
    非触发归因（sensitive_blocked/edge_coverage_gap）返回 degraded=False、候选空。
    """
```

### 3.3 契约约束

- **降级产出不自动晋升为正常可达路径**，仅用于审计 / 人工分析素材。
- **原有 `max_steps` 硬上限推演主逻辑完全保留，业务行为不变**。
  `degrade_path_candidates` 为**新增函数**，不被生产主链自动调用；
  原 `attribute_failure` 签名与返回不变。
- 低置信候选来源：在 `max_steps` 边界内已探索到的 top-k 分支按 F20 权重排序
  （`score_branch`），**不突破步数上限、不造边**。

### 3.4 实现落点

`eval/experiment/f20_weight.py`（S4 新增函数，**唯一允许触碰的既有业务文件，且只增不改**）
- 铁律：只在文件末尾新增 `degrade_path_candidates()`，**不改任何现有函数/常量/公式**。

### 3.5 回归用例（S4 补入总回归 S5）

- 模拟 step_exhausted：构造目标有边但 `max_steps` 极小 → 断言 `degraded=True` 且
  `low_confidence_candidates` 非空、`failure_reason=="step_exhausted"`。
- 构造 goal_isolated：目标度=0 → `degraded=True`、`failure_reason=="goal_isolated"`。
- 构造 sensitive_blocked：目标挂 `topic_user_relation` → `degraded=False`、候选空。
- 构造 edge_coverage_gap → `degraded=False`、候选空。

---

## 4. 总回归（S5）

`eval/audit/f40_promotion_gate_regression.py`（新建）集成：
1. **②机器收敛单元**：9 点网格遍历完整性、`machine_converged` 布尔输出、产物目录生成。
2. **③沙箱稳定性**：临时库扰动、flip=0 / Jaccard≥0.6 判定、真实库零改动断言。
3. **④降级分支**：四类归因 × degraded 触发矩阵（见 3.5）。

- **不实现 `promote_check()` 统一门面**（D5 默认：本期不做）。
- 所有产物写 `logs/` 或 `data/experiment/` 审计目录，**不改业务库**。
- 退出码：全绿 0；任一断言失败非 0。

---

## 5. 边界与红线（重申）

1. 新增代码全部落 `eval/*`；`core/ cgraph/ memory/` 只读。**唯一例外**：
   `eval/experiment/f20_weight.py` 属 experiment 层，仅允许**末尾新增函数**，不改既有逻辑。
2. 门槛②机器收敛只是辅助，**无人工签字不算验收完成**。
3. 门槛③扰动测试全部运行于**临时沙箱**，严禁写改真实业务数据库。
4. 晋升门禁只是检测工具，**不会自动切换线上参数**。
5. 不新增测试用例题库（复用门槛①题库）；不做肘部寻优。

---

## 6. 常量总表（引用即契约）

| 常量 | 值 | 归属 |
|---|---|---|
| `BETA_GRID` | [0.6, 1.0, 1.6] | 本档 §1.1 |
| `LAMBDA_DEBT_GRID` | [0.25, 0.5, 0.75] | 本档 §1.1 |
| `C2_HIT_DELTA_MAX` | 0.05 | 本档 §1.3 |
| `C2_JACCARD_MIN` | 0.7 | 本档 §1.3 |
| `C3_JACCARD_MIN` | 0.6 | 本档 §2.4 |
| `C3_FLIP_MAX` | 0 | 本档 §2.4 |
| `DEGRADE_TRIGGERS` | {step_exhausted, goal_isolated} | 本档 §3.1 |
| `D_FLOOR` | 0.3（既有，只读） | `f20_weight.py` |
| `LAMBDA_DEBT` | 0.5（既有默认，进程内覆盖不写回） | `f20_weight.py` |
| `PASS_RATE` | 0.8（门槛①，只读） | `scripts/f40_cross_cluster_eval.py` |

---

## 7. 实施分解（S1-S5）

| 步 | 产物 | 类型 |
|---|---|---|
| S1 | `docs/design_f40_threshold_234.md` + 台账 #10→进行中 | 文档 ✅（本步） |
| S2 | `eval/audit/f20_ablation_convergence.py` | ② |
| S3 | `eval/audit/f20_coverage_stability.py` | ③ |
| S4 | `eval/experiment/f20_weight.py` 末尾新增 `degrade_path_candidates()` | ④ |
| S5 | `eval/audit/f40_promotion_gate_regression.py` | 总回归 |

### commit 分段纪律

- **commit1**：本设计文档 + 台账更新
- **commit2**：S2-S4 业务代码实现
- **commit3**：S5 总回归脚本

---

## 8. 证据附录（口径对齐的代码事实）

- `f20_weight.BETA_PROFILES = {"conservative":0.6,"baseline":1.0,"exploratory":1.6}`（line 34）
- `f20_weight.LAMBDA_DEBT = 0.5`（line 48）、`D_FLOOR = 0.3`（line 51）
- `factor_density()` 读模块级 `LAMBDA_DEBT`（line 98-124）→ 进程内 `setattr` 可临时覆盖
- `attribute_failure()` 四枚举判据顺序 goal_isolated > sensitive_blocked > edge_coverage_gap > step_exhausted（line 148-176）
- 门槛① 常量 `PASS_RATE,MIN_DENOM,MAX_PER_ROUND,MIN_PER_ROUND=0.8,3,6,3`（`scripts/f40_cross_cluster_eval.py:31`）
- 守卫 5 场景 `SCENARIOS`（`f20_nightly_guard.py:36-51`）：smoke/explore1-4
- 门槛①题库 `data/experiment/f40_cross_cluster_question_bank.jsonl`