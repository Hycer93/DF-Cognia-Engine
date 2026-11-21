# P2-7 Q1 continue 修复 — 影响评估报告（A/B）

> 2026-11-21 P2-7 评估交付。**本文件为评估结论；按用户裁决，代码修复须评估通过后另行落地，
> 本报告不修改 `memory_judge.py`。**
> 评估脚本：`eval/experiment/p27_q1_continue_ab_eval.py`（只读，产物 `logs/p27_q1_continue_ab_eval.json`）。

---

## 1. 问题定义（缺陷机理）

`memory_judge._stub_one` 的 Q1 分支：

```python
if not summary or len(summary) < 8:
    verdict = "discard"; reasons.append("Q1fail: ...")
# ↓ 没有 continue / return, 继续往下执行 Q2/Q3/Q4
...
_gram_set, suspect, best_overlap = _bloodline(c)
if best_overlap == 0:
    verdict = "hold"; ...        # ← 覆盖了上面的 discard
```

即：**Q1 判定 discard 后未中断，孤独条目（零血缘）会被 Q4 覆写为 hold**。
这是历史存量行为（P1-1 桩转正时按"字节等价"红线原样保留，已在 docstring 与 commit 记录）。

修复 = Q1 分支补 `continue`（或 `return discard`），使 discard 不被 Q4 覆盖。
**这是语义行为变更**（改判定结果、改 judge_log 历史快照分布），故须前置 A/B 评估。

---

## 2. 评估方法

对两个来源做"现实现 vs Q1-continue 模拟"对照：

| 来源 | 说明 | 可评估性 |
|---|---|---|
| `store_pending.jsonl` | 现存 hold 池全部条目（13 条） | 完整候选，可完整 A/B |
| `judge_log.jsonl` | 历史判定日志 187 条 | 仅含 summary+verdict，**Q1 判定只依赖 summary**，故可做 Q1 维度精确回放（命中即 discard，否则不变） |

Q1 命中条件唯一：`summary 为空 或 len(summary.strip()) < 8`。

---

## 3. 评估结果

### 3.1 store_pending（现存 hold 池）

- 共 **13 条**，全部 `summary` 非空且 ≥8 字符。
- **verdict 变化 0 条**：无一条会因 Q1 continue 变 discard。

### 3.2 judge_log（历史 hold 记录 Q1 回放）

- 187 条中 verdict=hold 且含 summary 的 **9 条**可做 Q1 回放（其余 11 条为 batch/异常等无 verdict 记录，跳过）。
- **Q1 命中→discard 1 条**（该条 summary 短于 8 字符，本应 discard 却被 Q4 覆写成 hold）。

### 3.3 汇总

| 指标 | 值 |
|---|---|
| 总评估 | 22 条 |
| 判定变化 | **1 条** |
| 变化率 | **4.55%** |
| 变化方向 | 全部 hold → discard（符合预期，无误伤 store/无 hold→更宽松） |

---

## 4. 业务影响判断

1. **对现存 hold 池（store_pending）零影响**：13 条全部 summary 合规，修复后仍 hold。
   → 不会把任何"正在等待补证据"的条目错误清理出去。
2. **对历史判定仅 1 条修正**：一条短 summary 的孤条，本应 discard，现实现误判为 hold。
   修复后该条变 discard 是**正确化**（Q1"无新认知"本就该 discard，不应被 Q4 血缘逻辑救回）。
3. **无方向性风险**：全部变化为 hold→discard（更严格），不存在"把本该 discard 的放行成 store"
   或"把 hold 变宽松"的反向漂移。
4. **成本**：discard 意味着该条不入 hold 池、不进二次复审 → 减少 1 条无效复审负担，
   无额外副作用。

**结论：影响面极小且方向正确（仅 1 条历史误判被修正，现存池零影响）。评估通过。**

---

## 5. 落地建议（评估通过后的执行清单）

| 步骤 | 内容 |
|---|---|
| S1 | `_stub_one` Q1 分支改 `verdict="discard"; ...; return {discard dict}`（或 `continue` 等价） |
| S2 | 更新 docstring：删除"Q1 discard 未 continue"的历史说明，改为"Q1 命中直接 discard" |
| S3 | 跑 `memory_judge_stub_regression.py`：**期望矩阵会变**（孤条短 summary 用例从 hold→discard），须更新基线并 `--baseline` 重建 |
| S4 | 补一条显式回归：短 summary + 孤条 → 断言 discard（防回退） |
| S5 | 更新 P2-7 台账为完成 + commit hash |

**注意**：S3 会改基线，须在同一 commit 内同步"代码 + 基线 + 回归断言"，
不可拆开（否则夜间 `--compare` 误报）。

---

## 6. 备注

- 本评估只覆盖"现有数据"的即时影响。Q1 continue 修复的**语义正确性**独立于数据：
  无论有无受影响条目，Q1 discard 不应被 Q4 覆盖这一逻辑本身是对的（Q1 是"无新认知"的一票否决）。
- 因此即便影响为 0，修复仍建议落地（消除逻辑缺陷）；本次评估的价值是**确认落地的实际冲击可控**。
- 用户裁决回顾：「P2-7 Q1 continue 修复严禁提前动手，等前面全部做完，再专门做影响评估；
  评估通过才编码」。**本报告即该评估，结论通过；编码落地按 §5 S1~S5 执行。**