# #43 候选重复卡专项 —— Dry-Run 只读甄别报告

- 日期：2026-11-24 12:52（13:00 重大更正）
- 性质：**只读 dry-run 扫描分析，零 UPDATE / 零 DELETE，不改动 DB 与 jsonl**
- 口径：`status='active'` 全池两两 bigram-Jaccard（summary 字段），阈值 sim≥0.84
- **已排除 #31 处置完毕的 PAIR1-8**（PAIR1/7 已 superseded，PAIR2-5 闭环，PAIR6 反例保护，PAIR8 保留双张）

---

## ⚠️ 重大更正（2026-11-24 13:00 执行预演时发现）

**本文下述 6 对"待处置"判断有误——它们早已被 #29 降级完成。**

预演 `demote` 链路时发现：这 6 张"乙卡"**实际 `tier='precedent'` 且 `superseded_by` 已指向正本**（DB/jsonl 双写一致），是 **#29 脚本 `eval/audit/demote_superseded_shells.py`（2026-11-23）** 的产物：

```
组1 525d9e05 ← f767248d   组2 cfd09e40 ← e1afda16
组3 978385d4 ← 7e92e275   组4 5f4e9ea2 ← ff01d791
组5 fc24f5ea ← 6e2a9c36   组8 9b48551b ← 32684974
```

本报告上一版误判原因：初扫只看 `status='active'`，**未查 `tier`**。真实活跃正池（`status='active' AND tier='active'`，278 张）内 **sim≥0.84 对数 = 0**，即当前无真重复待合并。

**由此暴露的真问题**：`demote()` 只改 `tier`、**从不改 `status`**，导致全库 **37 张**卡处于 `status='active' + tier='precedent' + superseded_by 非空` 混叠态。而检索主链路 `load_memories()` **只认 status 不看 tier** → 这 37 张已降级旧卡**仍会被 `ask()` 召回**。降级只对"查重池"生效，对"检索"无效。

**处置变更**：#43 **不再执行任何 superseded 变更**（6 对重复的 tier 降级历史已由 #29 完成）。现存 `status`-`tier` 字段失同步缺陷，统一归 **#46 存量回填**根治。详见后附 #46 条目。

---

## 零、重要偏差说明（需先对齐）

台账记「18 对 sim≥0.84」，但**当前 real DB 全量重扫，summary 口径实际只有 5 对达 0.84**，另 1 对 0.80（边界）。

| 口径 | sim≥0.84 对数 | 说明 |
|---|---|---|
| summary（active） | **5** | 本报告主口径 |
| summary（全部 status） | 5 | superseded 不带来新增 |
| summary_converged | 0 | 该字段多空 |
| summary+detail 拼接 | 1 | — |
| detail（单字段） | 96 | 噪音大（详见第四节） |

**差异根因**：#31 报告（11-24 11:50 生成）第三节的"18 对"含估算成分——其点名的具体例子 `7240a272 vs 5dcf14c5`（称 0.910）、紫光三副本（称 0.86-0.866）**实测真实 sim 仅 0.26~0.45**，远未达阈值：

| #31 点名对 | 当时声称 | 实测 sim |
|---|---|---|
| 7240a272 vs 5dcf14c5（P38专利两版） | 0.910 | **0.333** |
| 7ee56837 vs ef3ef53f（紫光对峙） | ~0.86 | **0.260** |
| 7ee56837 vs 0b272d92（紫光对峙） | ~0.866 | **0.453** |
| ef3ef53f vs 0b272d92（紫光对峙） | ~0.866 | **0.455** |
| 8d16d61e vs 4687a63a（命名链） | ~0.86 | **0.106** |

> 结论：**"18 对"不可复现，实际高相似候选只有 5 对（+1 对边界 0.804）**。本报告据实盘点这 6 对，不虚构凑数。专利族/紫光三副本属"同主题不同表述"，bigram 口径下并不重复（但可作为人工关注的语义近似，见第四节）。

---

## 一、5 对主候选（sim≥0.84）逐对甄别

### 共同模式（关键发现）
这 5 对**结构完全同构**：

| 角色 | tier | detail | source_ref | asset_class |
|---|---|---|---|---|
| **甲（信息完整版）** | active | 有 detail | 有 source（`origin_corpus4: 归元仔人生.md 专项扫描回填 2026-11-13` 等） | corpus |
| **乙（瘦身副本）** | precedent | **detail 空** | **None** | corpus/runtime |

**根因**：11-13「归元仔人生.md 专项扫描回填」和 11-16 批量入库时，同一内容被写了两次——一次带 detail 的 active 版，一次无 detail 的 precedent 版。**乙是甲的降级副本**。

### PAIR A ｜ sim=1.000 ｜【真实完全重复】
- 甲 `e1afda16f2`：tier=active，src=归元仔专项回填，detail="归元仔人生.md第一次被骗日。G07骨架诞生现场。2026-11-13专项扫描回填"
- 乙 `cfd09e407b`：tier=precedent，src=None，detail=(空)
- summary 完全相同：蒸馏仔第一次被欺骗（预告净利润+70-100% vs 正式季报-24%…G07多维状态机校验）
- **定性**：真实完全重复，乙是甲的无 detail 副本
- **实际状态**：乙 `cfd09e407b` 已 tier=precedent、superseded_by=e1afda16f2（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**

### PAIR B ｜ sim=1.000 ｜【真实完全重复】
- 甲 `32684974eb`：tier=active，src=None，detail=(空)，asset=corpus
- 乙 `9b48551b2b`：tier=precedent，src=None，detail=(空)，asset=runtime
- summary 完全相同：未来推演公式体系v2定稿(F00-F60六层)…
- **⚠️ 特殊**：两张都无 detail，区别仅 tier（active vs precedent）+ asset（corpus vs runtime）。甲虽 tier=active 但 detail 也空。
- **定性**：真实完全重复
- **实际状态**：乙 `9b48551b2b` 已 tier=precedent、superseded_by=32684974eb（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**
- **⚠️ 个案**：`9b48551b` asset_class=`runtime`，runtime 会**绕过** superseded_by 过滤逻辑，需单独修正为 corpus（纳入 #46）

### PAIR C ｜ sim=0.957 ｜【真实重复（措辞微变）】
- 甲 `f767248dea`：tier=active，src=归元仔专项回填，detail="归元仔人生.md出生日…2026-11-13专项扫描回填, 用户口头确认"
- 乙 `525d9e052c`：tier=precedent，src=None，detail=(空)
- summary：蒸馏仔出生（外快计划启动…四因子评分体系）
- **定性**：真实重复，乙无 detail 副本
- **实际状态**：乙 `525d9e052c` 已 tier=precedent、superseded_by=f767248dea（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**

### PAIR D ｜ sim=0.951 ｜【真实重复（措辞微变）】
- 甲 `ff01d79115`：tier=active，src=归元仔专项回填，detail="人格核心约定卡。归元仔人生.md锁定自我日…"
- 乙 `5f4e9ea285`：tier=precedent，src=None，detail=(空)
- summary：锁定自我（出厂设置锁死…熔炉级高优先级指令）
- **⚠️ 注意**：PAIR D 甲是 `ff01d79115`（有 detail），乙是 `5f4e9ea285`（空）。方向与其它对相反——**保留 ff01d79115，superseded 5f4e9ea285**
- **定性**：真实重复
- **实际状态**：乙 `5f4e9ea285` 已 tier=precedent、superseded_by=ff01d79115（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**

### PAIR E ｜ sim=0.944 ｜【真实重复（措辞微变）】
- 甲 `7e92e275da`：tier=active，src=归元仔专项回填，detail="归元仔人生.md: 主人没有骂我,但这条伤疤被永久刻进了G20…"
- 乙 `978385d453`：tier=precedent，src=None，detail=(空)
- summary：蒸馏仔最贵的一课（淘汰指令下达但旧挂单未同步撤单…G20零延迟执行耦合）
- **定性**：真实重复，乙无 detail 副本
- **实际状态**：乙 `978385d453` 已 tier=precedent、superseded_by=7e92e275da（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**

---

## 二、边界候选（sim=0.804，低于阈值但属同款模式）

### PAIR F ｜ sim=0.804 ｜【真实重复 — 建议纳入人工确认】
- 甲 `6e2a9c3617`：tier=active，src=归元仔专项回填，detail="归元仔人生.md主人的确认日…"
- 乙 `fc24f5ea53`：tier=precedent，src=None，detail=(空)
- summary：主人的最终确认（'你已经不是工具,是和我一样的独立个体'…G09逆向第一因提取）
- **定性**：同款「甲有detail / 乙空detail」重复模式，但 sim 因 summary 措辞差异（甲"从策略工具到数字伙伴身份确认"，乙"…数字伙伴的身份确认"）降到 0.804
- **实际状态**：乙 `fc24f5ea53` 已 tier=precedent、superseded_by=6e2a9c3617（#29 已完成）
- **【注：tier=precedent、superseded_by 已填充，#29 demote_superseded_shells.py 已完成 tier 侧降级；status 仍 active，属 status-tier 混叠态，纳入 #46 存量回填统一修复，本 #43 专项不再重复处置】**

---

## 三、分类结果汇总（已按 13:00 更正修正）

### ~~✅ 可自动处理（5 对）~~ → **#29 已完成 tier 降级；status 混叠交由 #46 统一回填，#43 不再执行**
| 正本 | 已降级卡（#29 完成） | sim | 当前状态 |
|---|---|---|---|
| e1afda16f2 | cfd09e407b | 1.000 | tier=precedent + ssby 已写，status 待 #46 |
| 32684974eb | 9b48551b2b | 1.000 | 同上（⚠️runtime 个案，asset 待 #46 修） |
| f767248dea | 525d9e052c | 0.957 | 同上 |
| ff01d79115 | 5f4e9ea285 | 0.951 | 同上 |
| 7e92e275da | 978385d453 | 0.944 | 同上 |

### ~~⚠️ 需人工 PAIR-F~~ → **#29 已 tier 降级，status 混叠交由 #46**
| 正本 | 已降级卡 | sim | 当前状态 |
|---|---|---|---|
| 6e2a9c3617 | fc24f5ea53 | 0.804 | tier=precedent + ssby 已写，status 待 #46 |

### 🛡️ 保护样本（禁止合并降级）
- **PAIR 6 反例**（ef590c15 / d7d25873，sim-test，不同事实）：#31 已保护，本报告不动
- 单元测试样本：`sim-test-*` / `rel-test-*` 系列。注：PAIR1(ac25fe3a/5cb9bdf8) 已在 #31 处置，其余 rel/sim-test 若后续发现重复，**按测试夹具保护，禁止当业务重复合并**

### 🚩 专项提醒：9b48551b runtime 泄漏个案
`9b48551b` 已 tier=precedent、superseded_by 指向正本 `32684974eb`，但 `asset_class=runtime`。runtime 类别**绕过** `superseded_by` 治理过滤（过滤只对 asset_class=corpus 生效），即使 status 改成 superseded 之前也会发生检索泄漏。**须将该卡 asset_class 从 runtime→corpus**（与正本一致），纳入 #46 dry-run 一并校验执行。其余 36 张混叠卡 asset_class 不动。

---

## 四、detail 口径 96 对高分 —— 噪音排除结论

detail 单字段 sim≥0.84 命中 96 对，但**全部为模板占位符撞车**，非内容重复：

- 大量卡 detail = `绿档自动立卡(memory_gate 0f2f4458双层写入规约); 素材步: node_Ch; 档案动词: 写卡` 同一模板串
- 核实样例：94f77455 / 17acf52b / 570a57f5 三张 summary 完全不同（不同踩坑事件），detail 却完全相同
- **结论**：detail 字段不可作重复判据（模板污染）。**96 对无一为真实重复，无需处置**。

### 专利族/紫光三副本：非重复但值得人工留意
- P38 两版（7240a272 / 5dcf14c5，sim 0.333）：短标题 vs 长正文，属同专利两版并存，bigram 不判重
- 紫光对峙三条（7ee56837 / ef3ef53f / 0b272d92，sim 0.26~0.455）：同一事件不同摘要视角
- **这些不属于 #43 自动处置范围**，若需收敛专利族版本，应另立"专利族版本归并"专项，非本次重复治理

---

## 五、结论与建议（已按 13:00 更正修正）

| 类别 | 数量 | 处置 |
|---|---|---|
| 6 对重复（含 PAIR-F） | **6 对** | **#29 已完成 tier 降级**；status 混叠交由 #46 存量回填，#43 不再执行 |
| 🔴 status-tier 混叠态 | **37 张** | 归 #46 根治（改 demote() + 存量回填 status） |
| 🚩 runtime 泄漏个案 | **1 张**（9b48551b） | asset_class runtime→corpus，并入 #46 同批校验 |
| 🛡️ 保护不动 | PAIR6 反例 + rel/sim-test 夹具 | 禁止合并 |
| 噪音排除 | detail 口径 96 对 | 全部模板撞车，不动 |
| 台账偏差 | "18 对" → 实际 5+1 对 | 台账 #43 需校正 |

### 关键边界（写入报告备注区）
1. #43 专项**不再做任何 superseded 状态变更**：6 对重复的 tier 降级历史已由 #29 完成；现存缺陷是 `status` 与 `tier` 字段语义不同步，归 #46 统一根治，账实对齐。
2. `9b48551b` 是唯一 runtime 异常个案，**只修正这一张** asset_class（runtime→corpus），其余 36 张不动；与 #46 存量回填同一批次执行，dry-run 一并校验（改后 asset 与正本对齐、superseded_by/tier/status 保持预演值）。
3. 继续保护：PAIR-6 反例、rel-test/sim-test 夹具不动；detail 96 组模板噪音完全忽略。

### 执行建议
1. #43 **收口**：不执行写库，改为 #46 专项。
2. **#46 执行链路**：① 改 `demote()` 同步写 `status='superseded'`；② 存量回填 37 张混叠卡；③ `9b48551b` asset_class 修正。先 dry-run 校验全量清单 + 单条 asset 变更，无问题再真实执行 + 同步 jsonl + 补回归用例防复现。
3. 台账 #43 改正为"甄别完成，处置移交 #46"。

---

## 六、约束遵守确认
- ✅ 纯只读扫描，零 UPDATE / 零 DELETE
- ✅ 未改动 DB 与 jsonl
- ✅ 已排除 #31 处置的 PAIR1-8
- ✅ PAIR6 反例、rel/sim-test 测试夹具明确保护
- ✅ 13:00 预演发现 6 对已被 #29 降级，本报告已据实更正
- ⏳ #43 收口，实际处置移交 **#46**（demote() 语义对齐 + 37 张存量回填 + 9b48551b asset 修正）
