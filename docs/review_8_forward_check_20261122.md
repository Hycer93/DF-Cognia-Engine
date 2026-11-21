# #8 forward_check 现状盘点（2026-11-22）

> 低优项盘点，非编码任务。结论先行：**forward_check 已实装（第一环静态版），台账"待启动"标记滞后**。

## 一、结论摘要

| 维度 | 事实 |
|---|---|
| 是否实现 | ✅ 已实装（零 LLM 静态正演第一环） |
| 代码位置 | `scripts/collab_pipeline.py` L407-L427 `forward_check(dx, goal_keywords)` |
| 真实状态 | 线上运行中（`run_collab` 在线 / `run_f60` 离线均内联调用） |
| 测试覆盖 | ❌ 零回归测试 |
| 已知缺口 | 第二环（深度正演复用 `minloop.verify`）仅 docstring 提及，未实现 |

## 二、代码位置与调用链

| 文件 | 行号 | 角色 |
|---|---|---|
| `scripts/collab_pipeline.py` | L407-L427 | `forward_check(dx, goal_keywords)` 定义 |
| `scripts/collab_pipeline.py` | L306-L309 | `_bigrams_seg()` 中文 n-gram 分词 |
| `scripts/collab_pipeline.py` | L312+ | `derive_dx()` 上游 ΔX 派生 |
| `scripts/collab_pipeline.py` | L430-L439 | `run_f60()` 离线入口 |
| `scripts/collab_pipeline.py` | L471 | `run_collab()` 编排入口内联调用 |

调用路径：
```
run_collab() → derive_dx() → forward_check(dx, gk)   [在线 L467-471]
run_f60()    → derive_dx() → forward_check(dx, gk)   [离线 L435]
```

## 三、当前实现逻辑

`forward_check()` 为零 LLM 静态正演校验第一环（docstring 原话"F60 正演校验第一环(零 LLM 静态版)"）：

1. **输入**：共识候选 `dx` dict + 目标关键词 `goal_keywords`
2. **语料拼接**：共识假设/共享薄弱点/单路模式差异假设拼成 corpus
3. **n-gram 匹配**：`_bigrams_seg()` 2~4 字滑窗宽松匹配，判断关键词是否覆盖
4. **判定**：
   - `NO_CONSENSUS` — 无共识簇且非单路模式
   - `FAIL` — 关键词覆盖率 0
   - `PASS` — 全部命中
   - `PASS_WITH_GAPS` — 部分命中

结果挂到 collab 报告 `forward_check_static` 字段。

## 四、现存缺陷 / 未完成点

- **L403 注释**："ΔX 为候选(F40 粗解语义), 未经正演校验不得直接执行" — 正演仅为初筛，深度正演（复用 `minloop.verify`）未见实现。
- **L408 docstring** 明写"深度正演复用 minloop.verify"，暗示第二环未落地。
- 全文件无 `TODO`/`FIXME`/`NotImplemented` 显式占位。
- 功能边界明确限定"第一环"（关键词覆盖），无语义级/图结构级正演。

## 五、外部依赖

同文件内：`derive_dx` / `_bigrams_seg` / `_task_topic_words` / `_TOPIC_LEX`；标准库 `io/json/os/argparse/time`。**零第三方依赖**。

## 六、回归测试状态

✅ **已补（2026-11-22）**：`eval/audit/collab_forward_check_regression.py`，覆盖四枚举
（PASS / PASS_WITH_GAPS / FAIL / NO_CONSENSUS）+ 单路模式 divergent 计入 + 空关键词边界，
纯内存用例 15/15 全绿，零 DB 读写。原缺口已闭合。

## 七、待决策点

1. **台账状态修正**：`待启动` → `已实装（第一环静态版）`。
2. **第二环规划**：是否实施"深度正演复用 minloop.verify"？scope / 时机？
3. **测试缺口**：`forward_check()` 是唯一有核心逻辑但零测试覆盖的公开函数，是否补白盒测试？
4. **命名隔离**：`forward_check_static` 后缀已为第一环预留，引入第二环需注意命名。

## 八、备选实施路线

| 方案 | 描述 | 成本 |
|---|---|---|
| A. 维持现状 | 承认第一环已有，仅更新台账 + 补测试 | 低 |
| B. 补第二环 | 实现"深度正演复用 minloop.verify" | 中高 |
| C. 重构拆分 | 将 forward_check 从 pipeline 大文件拆出独立模块 | 中 |
| D. 降级归档 | 若静态关键词覆盖已够用，关闭 #8 并移除 docstring "深度正演"字样 | 低 |

## 九、建议（待用户裁决）

推荐 **A（维持现状）**：第一环已满足粗筛需求，当前真实缺口是零测试覆盖。补一组 `forward_check` 四枚枚举（PASS/PASS_WITH_GAPS/FAIL/NO_CONSENSUS）白盒回归，成本最低、收益明确。第二环（B）作为独立需求立项，不混在低优 #8 里。

## 十、执行结果（2026-11-22，采纳方案 A）

- ✅ 回归脚本 `eval/audit/collab_forward_check_regression.py` 落地，15/15 全绿（四枚枚举 + 单路 divergent + 空关键词边界），纯内存零 DB。
- ✅ 业务逻辑零改动：`forward_check_static` 输出语义不变；docstring "深度正演复用 minloop.verify" 备注保留作为未来迭代提示。
- ✅ 台账 #8 更新：`已实装第一环静态版；待补充回归；第二环深度正演待立项`。
- ⏸ 第二环（深度正演）暂不启动，作为独立后续需求，需要时单独开任务。
