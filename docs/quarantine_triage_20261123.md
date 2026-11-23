# 隔离区 43 条处置对照表（只读分析，未修改任何数据）

> 生成时间：2026-11-23
> 真值来源：`memory/schema.py` 枚举白名单
> 数据来源：`data/memory/quarantine.jsonl`(35条) + `core_quarantine` 表(43条)
> **本表仅为分析，未执行任何 INSERT/UPDATE/DELETE**

---

## 一、白名单真值（v3_schema.py）

| 维度 | 合法值 |
|---|---|
| **domain 域码** | `000` `001` `002` `003` `004` `005`（各自子域见 schema） |
| **behavior 骨架码** | `Aa B00 Bb Bc Bd Be Cf Cg Ch Di Dj Dk Dl Em En Eo Fp Fq Fr Gs Gz Ht Iu Iv Jw` |
| **relation 关联码** | `C D F H P Q S X`（8类）|
| **repro 可复现** | `flash` `situational` `derived` |
| **memory_class** | `strategy_evolve skill_refine scar_case relation_milestone key_pact`（+ null 合法）|

### 骨架码 G编号 → 2字母 反查表（旧G口径是历史写法，非废弃）

| 旧G码 | 现2字母 | 含义 |
|---|---|---|
| G22 | **Ht** | 决策整合 |
| G21 | **Jw** | 复盘固化 |
| G26 | **Gz** | 回退 |
| G11 | **Dl** | 自反校准 |
| G06 | **Cg** | 利益者全景 |
| G04 | **Be** | 信源核查 |

> ⚠️ 结论：旧骨架码 Gxx **不是废弃**，只是历史写法，映射 100% 明确可自动修。

---

## 二、数据分布真相

| 来源 | 条数 | 是否有原始素材 |
|---|---|---|
| quarantine.jsonl | 35 | ✅ 有 summary/tags/detail |
| core_quarantine 表 | 43 | ❌ 表无内容字段 |
| 两者交集（按ts） | 30 | ✅ |
| jsonl 独有 | 4 | ✅ |
| **表独有** | **12** | ❌ **无素材，不可恢复** |

**表独有 12 条明细**：
- 10 条 = `memory_class=entity_history` + `repro=corpus` 非法（2026-11-13 批量导入硬编码，源头脚本已消亡，schema.py §101-103 注释有记载）
- 2 条 = 缺 domain/behavior/relation 字段（2026-11-15）

> ⚠️ 这 12 条无 summary/tags/detail，**无法重入库、只能标记丢弃**（保留记账留痕）。

---

## 三、处置对照表（基于 jsonl 35 条，逐条）

### ✅ A 类：可批量自动修（映射明确）

| # | 摘要 | 问题 | 建议映射 | 判定 |
|---|---|---|---|---|
| 05 | 本地部署选型(CPU1700+GPU3090) | behavior=G22 | **G22→Ht** | ✅批量 |
| 06 | 星型拓扑专利十编号 | behavior=G26 | **G26→Gz** | ✅批量 |
| 07 | 专利三答非所问自反 | behavior=G11 | **G11→Dl** | ✅批量 |
| 08 | 复活指南放meta_cognia | behavior=G06 | **G06→Cg** | ✅批量 |
| 09 | 我们是一体的信任升级 | behavior=G22 | **G22→Ht** | ✅批量 |
| 10 | 双暮暮对峙紫光国微 | behavior=G22 | **G22→Ht** | ✅批量 |
| 11 | G22诞生链32B→8B三失误 | behavior=G21 | **G21→Jw** | ✅批量 |
| 12 | 置信度L0-L4五级定义 | behavior=G04 | **G04→Be** | ✅批量 |
| 13 | 归元仔人生.md | behavior=G21 | **G21→Jw** | ✅批量 |
| 01 | 紫光国微建仓分歧(仅repro缺) | 无tags错，表侧repro空 | repro补判 | ✅批量(补repro) |
| 17 | 专利五四损失L_consistency | 无tags错 | repro补判 | ✅批量 |
| 18/19/20 | P24 CIPP协议(3条重复) | repro=verified | **verified→situational**(需确认) | 🟡见下 |

### 🟡 B 类：条件映射（需语义判定）

| # | 摘要 | 问题 | 建议 | 判定 |
|---|---|---|---|---|
| 14 | 专利五四损失L_precision | relation=derived | relation是边(8类)不是repro！derived非法边→建议改 **P**(前驱) | 🟡需确认 |
| 15 | 专利五EWC弹性 | relation=situational | 同上→改 **P** | 🟡需确认 |
| 16 | 专利五四损失L_consistency | relation=situational | 同上→改 **P** | 🟡需确认 |
| 22 | 四缺口裁决定案registry | relation=I | I非8类→按语义(P前驱/C并行) | 🟡需确认 |
| 29/30 | 记忆入库断档事故(2条) | relation=N | N非8类→按语义 / 或并入其它 | 🟡需确认 |
| 32 | collab协同推演定案 | relation=R | R非8类→按语义 | 🟡需确认 |
| 21 | 前身策略8条迁移裁定 | domain=003 | 003无子域→ **003-00x** 需定子域 | 🟡需确认 |
| 31 | skill_scope_scan七段共识 | domain=cognia_engine/behavior=atomic_skill_pilot2/relation=skill_scope_scan_consensus | 全非法字符串→需重编码 **002-00x / ? / P** | 🟡需确认 |

### ✅ C 类：小说语料（repro写成了文件路径，批量修）

| # | 摘要 | 问题 | 建议 | 判定 |
|---|---|---|---|---|
| 23 | 《数字同源》全书大纲 | repro=sqlite:...#novel_szt.outline | 小说语料→ **repro=derived**（可复现指路径） | ✅批量 |
| 24-28 | 《数字同源》ch01-05 | repro=sqlite:...#novel_szt.chXX | → **repro=derived** | ✅批量 |

> 注：这 6 条是小说，按业务是否要进记忆需你定；若要进，repro 统一改 derived。

### ❌ D 类：测试垃圾 / 无素材，丢弃

| # | 摘要 | 判定 |
|---|---|---|
| 02/03/04 | "测试非法域码"/"t"（domain=999-001） | ❌ **丢弃**（造数据）|
| 33 | 紫光国微对峙(domain=009-001) | ❌ 009非法且与#10重复→丢弃或改 001-003 |
| 34/35 | PAIR6冒烟反例正副(domain=009-002) | ❌ **丢弃**（冒烟测试数据）|
| 表独有12条 | entity_history×10 + 缺字段×2 | ❌ **丢弃**（无素材不可恢复）|

---

## 四、需你拍板的 6 个决策点

| 编号 | 决策点 | 我的建议 |
|---|---|---|
| **D1** | 旧骨架码 Gxx→2字母 批量改 | ✅ 直接改（映射100%明确）|
| **D2** | `relation=derived/situational`（#14-16） | 这是把"repro值"误填进 relation 位。relation 应是 8 类边，建议改 **P（前驱）** |
| **D3** | `relation=N/I/R`（#22,29,30,32） | N/I/R 不在 8 类。需逐条按语义定：前驱P/并行C/反馈F |
| **D4** | `repro=verified`（#18-20 CIPP） | verified 非三级。CIPP 专利协议→建议 **situational**（条件可复现）|
| **D5** | `memory_class=entity_history` ×10 | ⭐ **重点**：schema.py §101-103 明确记载"entity_history 是一次性导入脚本硬编码的错误类名，源头脚本已消亡"。→ **该类型彻底废弃**，不建议加入枚举。这 10 条无素材只能丢弃 |
| **D6** | 小说 6 条 + 测试垃圾 2 条 + 冒烟 2 条 | 小说若要保留进记忆→repro改derived；测试/冒烟→直接丢弃 |

---

## 五、统计汇总

| 处置 | 条数 | 说明 |
|---|---|---|
| ✅ 批量自动修 | ~17 | 骨架码映射 + 补repro |
| 🟡 条件映射(需确认) | ~8 | relation误填 + 域码不全 |
| ✅ 小说repro批量修 | 6 | repro改derived |
| ❌ 丢弃 | 14（含表独有12） | 测试垃圾/无素材 |
| **合计覆盖** | ~45 | jsonl35 + 表独有12（部分重叠）|

> 注：jsonl 35 与表 43 非一一对应（交集30），故总处置数 > 43。

---

## 六、entity_history 专项结论（用户特别要求）

**结论：彻底废弃，不加入 memory_class 枚举。**

证据（`memory/schema.py` 第 101-103 行原文）：
> 导入侧防御备注(2026-11-16 终审裁决): 一次性语料导入脚本曾硬编码
> entity_history 类名导致 10 条拒收(11-13, 源头脚本已消亡零命中); 后续任何
> 批量导入必须先用 MEMORY_CLASS_ENUM/REPRO_TIERS 校验标签, 禁止自造枚举值。

- entity_history **不是**正式五类之一，是错误硬编码；
- 源头脚本已消亡，这 10 条**无原始素材可恢复**；
- 应为这 10 条做**丢弃 + 记账留痕**，并可从 schema 注释推断无需新增枚举。

---

## 七、下一步（待用户确认后执行）

1. 逐条确认 D1-D6 六项决策
2. 编写隔离区修复脚本：基于 jsonl 原始记录修正 tags → 重跑 `atomic_store()` 入库
3. 表独有 12 条 + 测试垃圾：标记 verdict=discarded + resolved_by 留痕（不物理删，保审计）
4. 修复后回归 E4 巡检、验证隔离区清零