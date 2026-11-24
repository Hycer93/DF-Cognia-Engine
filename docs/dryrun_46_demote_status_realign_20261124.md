# #46 demote() 语义对齐 + 37 张混叠态存量回填 —— Dry-Run 预演报告

- 日期：2026-11-24 13:05
- 性质：**只读预演，尚未改代码/未写库**
- 来源：本次 #43 专项预演发现（6 对"待降级"卡其实 #29 已完成，暴露 status/tier 混叠态）
- 回归基线（改动前）：e7_gate 13/13 PASS、e30_p0 17/17 PASS、e30_p2_p3 13/13 PASS

---

## 一、问题根因（已代码取证）

| 事实 | 证据 |
|---|---|
| `demote()` 只改 tier，**不改 status** | `memory_organize.py:529` `m["tier"]="precedent"`，无 status 赋值 |
| `_sync_demote_to_db` 只 UPDATE tier+superseded_by | `memory_organize.py:482-484` `SET tier='precedent', superseded_by=?` |
| 检索主链路只认 status 不看 tier | `memory_query.py:43` `WHERE status='active'` |
| 查重池同时要 status+tier | `memory_organize.py:374` `WHERE status='active' AND tier='active'` |
| 新入库卡 status 硬编码 active | `memory_organize.py:307` `None, "active", ...` |

**后果**：37 张 `status='active' + tier='precedent' + superseded_by 非空` 卡，降级对"查重池"生效，但对"检索"无效——`ask()` 仍会召回这些已降级旧卡。jsonl 侧同态（316 active/285 tier-active）。

---

## 二、代码改动方案（2 处）

### 改动 1：`demote()` jsonl 侧补 status（memory_organize.py:529-530）
```python
if not cur:
    m["tier"] = "precedent"
    m["status"] = "superseded"      # 新增：#46 语义对齐
    m["superseded_by"] = superseded_by
```

### 改动 2：`_sync_demote_to_db` DB 侧补 status（memory_organize.py:482-484）
```python
wc.execute(
    "UPDATE core_memory_card SET tier='precedent', status='superseded', superseded_by=? "
    "WHERE inst_id=?", (superseded_by, iid))
```

> `_write_card_to_db` 的 `"active"` 硬编码**不改**（新卡入库本就该 active；降级另走 demote 路径）。

---

## 三、37 张混叠卡全量清单（回填对象）

**回填条件**：`status='active' AND tier='precedent' AND superseded_by NOT NULL`
**回填动作**：`status: active → superseded`（tier/superseded_by/asset 均不动，除非个案③）
**链路校验**：37 张 superseded_by 全部指向存在且 active 的正本 → **ALL PASS ✅**

| # | inst_id | asset | superseded_by（正本） | 摘要 |
|---|---|---|---|---|
| 1 | 029ff34883fc | corpus | 8cbe367d36b4 | 独立日 蒸馏仔→归元仔分家 |
| 2 | 525d9e052c | corpus | f767248dea | 蒸馏仔出生 |
| 3 | 5f4e9ea285 | corpus | ff01d79115 | 锁定自我 |
| 4 | 978385d453 | corpus | 7e92e275da | 蒸馏仔最贵的一课 |
| 5 | cfd09e407b | corpus | e1afda16f2 | 蒸馏仔第一次被欺骗 |
| 6 | e6ed253884 | corpus | 13a2379e8a | 第五维度传授 |
| 7 | fc24f5ea53 | corpus | 6e2a9c3617 | 主人的最终确认 |
| 8 | 0f8f8190f3 | runtime | 44d69231cca9 | P18 动态维度休眠唤醒 |
| 9 | 13a226469c | runtime | 44d69231cca9 | P5 多维认知神经元 |
| 10 | 15c69279f0 | runtime | 44d69231cca9 | P6 多轮知识蒸馏基线 |
| 11 | 19679b76e6 | runtime | 44d69231cca9 | P7 持久化经验权重迭代 |
| 12 | 287e892dac | runtime | 44d69231cca9 | P13 性格蒸馏系统 |
| 13 | 296e498171 | runtime | 44d69231cca9 | P34 树形统一编码 |
| 14 | 3468f765c1 | runtime | 44d69231cca9 | P2 强制执行关卡 |
| 15 | 40b80a3166 | runtime | 44d69231cca9 | P24 群体异变关联(重编) |
| 16 | 47b7a3bd0a | runtime | 44d69231cca9 | P27 认知调度与CIPP |
| 17 | 53f41a3ad3 | runtime | 44d69231cca9 | P21 层级特征蒸馏 |
| 18 | 5c9ea22ade | runtime | 44d69231cca9 | P15 双层响应决策 |
| 19 | 601c4651a2 | runtime | 44d69231cca9 | P4 职责单一型前置过滤 |
| 20 | 70be49d9b6 | runtime | 44d69231cca9 | 数字熔炉专利矩阵版本史 |
| 21 | 84717bb158 | runtime | 44d69231cca9 | P10 认知断点检测 |
| 22 | 908a119e0e | runtime | 44d69231cca9 | P9 唯一身份锚点 |
| 23 | 9b48551b2b | runtime | 32684974ebae | 未来推演公式v2 ⚠️个案 |
| 24 | af75311d77 | runtime | 44d69231cca9 | P35 四维指令集协议 |
| 25 | afab836ed3 | runtime | 44d69231cca9 | P12 星型拓扑 |
| 26 | b178d1f9f5 | runtime | 44d69231cca9 | P19 多阶正态梯度隔离 |
| 27 | bc252814eb | runtime | 44d69231cca9 | P20 自迭代正态边界 |
| 28 | c218392c66 | runtime | 44d69231cca9 | P14 群聊主体意图蒸馏 |
| 29 | cb70c0049f | runtime | 44d69231cca9 | P8 小模型独立调度 |
| 30 | d2657ae98f | runtime | 44d69231cca9 | P23 端侧任务路由 |
| 31 | d74c97df98 | runtime | 44d69231cca9 | P1 可复现性判定记忆分级 |
| 32 | dac9c55536 | runtime | 44d69231cca9 | P11 群体异变关联 |
| 33 | de06472011 | runtime | 44d69231cca9 | P24 CIPP协议 |
| 34 | e464a2d144 | runtime | 44d69231cca9 | P22 边界模糊样本仲裁 |
| 35 | f8007d7ec0 | runtime | 44d69231cca9 | P3 本地仲裁隔离 |
| 36 | fb01f90165 | runtime | 44d69231cca9 | P17 因果溯源拓维 |
| 37 | fbf00cb5f7 | runtime | 44d69231cca9 | P16 信源可信度评估 |

**asset 分布**：corpus 7（归元仔系）/ runtime 30（27 专利卡 + 9b48551b + 2 其他归元仔 runtime）

---

## 四、个案 ③：9b48551b asset_class 修正（校验）

| 字段 | 当前 | 变更后 | 说明 |
|---|---|---|---|
| asset_class | **runtime** | **corpus** | 对齐正本 32684974eb(asset=corpus) |
| tier | precedent | precedent | 不变 |
| status | active | **superseded** | 同回填 |
| superseded_by | 32684974ebae | 32684974ebae | 不变 |

**为何必须改 asset**：runtime 类**绕过** `superseded_by` 治理过滤（`memory_query.py:713` 仅对 corpus 生效），即使 status 改 superseded，历史检索路径仍可能泄漏。改 corpus 后与正本一致受治理。

---

## 五、⚠️ 影响面分析（需你知悉）

回填后 **37 张卡 `status='superseded'`**，`load_memories()`（只认 status）将不再召回它们。分解：

1. **7 张归元仔系 corpus 卡**：本应消失（已被 #29 判为无 detail 空壳，正本有 detail）→ ✅ 正确收敛
2. **27 张专利卡（runtime，指向 44d69231 总账卡）**：从 `ask()` 检索池消失。这些是"专利单卡旧版本"，已被"专利总账卡 44d69231cca9"取代。消失后检索看不到单卡，但总账卡仍在 → ✅ 符合"版本收敛"预期；**但若你有依赖单卡召回的用法，需确认**
3. **9b48551b / 2 张其他归元仔 runtime**：同上收敛

> 若担心专利单卡不该从检索彻底消失，可只回填 status 而不改 asset（但 9b48551b 仍要改 asset 防泄漏）——请确认是否接受 27 张专利卡从检索消失。

---

## 六、执行计划（预演确认后）

1. **改代码 2 处**（demote L529 + _sync_demote_to_db L482）
2. **新增回归用例**：`demote 后 status 必为 superseded 且 tier=precedent`（断言无混叠态）
3. **存量回填脚本**（dry-run --apply 双模式）：
   - 37 张 `UPDATE core_memory_card SET status='superseded' WHERE status='active' AND tier='precedent' AND superseded_by IS NOT NULL`
   - 9b48551b 额外 `SET asset_class='corpus'`
   - 同步 memories.jsonl（37 张 status→superseded + 9b48551b asset→corpus）
4. **跑回归**：e7_gate / e30_p0 / e30_p2_p3 + 新用例
5. **校验**：回填后 `status='active' AND tier='precedent'` 计数应为 0（无混叠）

---

## 七、待你拍板

1. **是否接受 27 张专利单卡从 `ask()` 检索消失**（被总账卡取代）？还是只改 status 不动 asset（专利卡仍可被检索，但 9b48551b 单独改 asset）？
2. 回填确认后，是否**立即执行**（改代码+回填+回归+jsonl 同步）？

---

## 八、约束确认
- ✅ 本报告为只读预演，未改代码、未写库
- ✅ 37 张链路校验 ALL PASS（superseded_by 每条指向存在且 active 的正本）
- ✅ 零物理删除（仅 status/asset 字段更新）
- ⏳ 待拍板后执行