# #44 detail 质量护栏 —— 执行归档报告

- 日期：2026-11-24 13:15
- 性质：**已执行**（代码改动 + 新增回归），零 DB 数据变更
- 决策：只警告不拒收（记 gate_log，不阻断入库）+ green_card_writer/atomic_store 双管

---

## 一、背景与选型

**问题**：detail 字段历史污染严重（active 279 张中：空 detail 29、非空<30 字 20、绿档模板占位 19）。detail 口径查重 96 对全是模板撞车噪音。

**关键约束发现**：<30 字卡里**多数是合法短详情**（如「双暮暮对峙完整现场，含挂单去留分析。」23字），硬性字数门槛会误伤。真垃圾只是 `content`/`content2`/`d1`/`d3`/`复盘详情2` 等少数。

**用户决策**：
1. 阈值策略 = **只警告不拒收**（最安全，不阻断入库）
2. 处置动作 = **双管**：green_card_writer 修模板源头 + atomic_store 加检测兜底

---

## 二、代码改动

### 改动 1：`memory/memory_organize.py` 新增检测函数（护栏②）
```python
_DETAIL_GREEN_TEMPLATE = "绿档自动立卡"

def _detail_quality_issue(detail) -> str:
    """返回质量问题标签（''=无明显问题）。仅检测，不修改、不阻断。
    empty / green_template / ascii_placeholder / numeric_only / '' 合法
    """
    if not detail or not str(detail).strip():
        return "empty"
    d = str(detail).strip()
    if _DETAIL_GREEN_TEMPLATE in d:
        return "green_template"
    if d.isdigit():
        return "numeric_only"
    if d.isascii() and len(d) < 10:      # 纯 ASCII 短 → 疑似占位
        return "ascii_placeholder"
    return ""
```

### 改动 2：`atomic_store()` 编码校验后插入 warning（不拒收）
```python
# #44 detail 质量护栏（护栏②）：只警告不拒收——质量问题记 gate_log，仍照常入库
_dq = _detail_quality_issue(detail)
if _dq:
    _log_p0("warn_detail_quality", {
        "issue": _dq, "detail_preview": (detail or "")[:80],
        "summary_preview": summary[:60], "origin": origin,
    })
```
> 复用现有 `_log_p0` → `gate_log.jsonl`，格式与既有审计同构。

### 改动 3：`cgraph/green_card_writer.py` 模板去硬编码
新增 `_green_detail(x)`，以 `proposed_action` 实义内容为主拼接，替换原两处固定前缀 `"绿档自动立卡(memory_gate 0f2f4458双层写入规约); ..."`：
- L77（`_sync_committed_to_jsonl` jsonl 同步）
- L161（`commit_green_cards` DB INSERT）

```python
def _green_detail(x: dict) -> str:
    action = (x.get("proposed_action") or "").strip()
    step = (x.get("evidence_step") or "").strip()
    gist = (x.get("gist") or "").strip()
    parts = []
    if action: parts.append(action)
    if gist: parts.append(f"要点: {gist}")
    if step: parts.append(f"证据步: {step}")
    return ("；".join(parts) if parts else step or gist or "绿档立卡(无实义内容)")[:400]
```

---

## 三、判定规则（黑名单模式，不用字数硬门槛）

| 标签 | 条件 | 举例 |
|---|---|---|
| empty | 空/None/纯空白 | "" |
| green_template | 含"绿档自动立卡"前缀 | 旧绿档占位 |
| numeric_only | 纯数字 | "123" |
| ascii_placeholder | 纯 ASCII 且 <10 字 | "d1"/"content" |
| （放行） | 其他（含合法中文短详情） | "双暮暮对峙完整现场…" |

**不误伤验证**：`双暮暮对峙完整现场，含挂单去留分析。`(23字中文)、`详情层：经济学·宏观指标·多标的情报扫描` 均判为合法。

---

## 四、回归结果（全 PASS）

| 套件 | 结果 |
|---|---|
| e7_gate_regression | 13 PASS / 0 FAIL |
| e30_p0_regression | 17 PASS / 0 FAIL |
| e30_p2_p3_regression | 13 PASS / 0 FAIL |
| e46_demote_status_regression | 10 PASS / 0 FAIL |
| **e44_detail_guard_regression（新增）** | **16 PASS / 0 FAIL** |
| **合计** | **69 PASS / 0 FAIL** |

新增 16 用例覆盖：检测函数 7 类准确性、atomic_store 脏 detail 仍 stored 且记 warning、合法 detail 不误报、绿档 detail 去模板化 + 空字段兜底。

---

## 五、影响面

- **零 DB 数据变更**（本次只改代码 + 加回归，不回填历史 29 空 + 20 短 + 19 模板）
- atomic_store 遇脏 detail：仍 `stored`，仅在 gate_log 增一条 `warn_detail_quality`
- 绿档新卡 detail 不再写固定模板，含实义内容（仍需 #39 类回填历史空壳，另议）
- 历史遗留脏 detail（29 空/20 短/19 模板）**不自动清理**，可后续按需回填

---

## 六、约束确认
- ✅ 零物理删除、零数据变更
- ✅ 只警告不拒收（不阻断入库主链）
- ✅ 全套回归 69 PASS / 0 FAIL
- ✅ 黑名单精准识别，不误伤合法短详情