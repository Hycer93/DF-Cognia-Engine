# #30 段一侦察报告

> 生成：2026-11-23 ｜ 阶段：七段「段一 侦察」（只读，零代码改动）
> 输入：`design_30_derived_dup_fix_20261123.md` + `attach_30_dup_samples_20261123.md` + `recon_30_checklist_20261123.md`
> 边界裁定（用户 2026-11-23）：**#30 只处理路径 A（atomic_store）；路径 B 本次零改动，仅取证移交 #31。**

---

## 一、真实写入路径全量清单（核心产出）

**实测生产写入 `core_memory_card` 共两条路径：**

| 路径 | 入口 | 去重机制 | 走 atomic_store | #30 范围 |
|---|---|---|---|---|
| **A** 记忆入库主路 | `memory_hook.add()` → `atomic_store()` (`memory_organize.py:80`) | full_hash 精确 | ✅ | **#30 处理** |
| **B** 绿档落库旁路 | `executor.py:192` → `green_card_writer.commit_green_cards()` (`green_card_writer.py:31`) | full_hash + bigram-Jaccard>0.6（同日） | ❌ 直接 INSERT | **#30 不处理，移交 #31** |

**其余候选点勘察结论**：
- `memory_relations.py:449/453`、`memory_organize.py:411+`：`__main__` 自检，非生产。
- `eval/audit/*.py` 多处 `INSERT INTO core_memory_card`：回归脚本自建测试数据，非生产。
- **E9 对话治理**：只写 `store_pending.jsonl`（提案），**不直接写 card**；转正链路最终走 A 或 B，E9 自身不是写入方。
- **backfill 脚本**：只做 DB→jsonl 导出，不新建 card，与 P0 无关。

✅ 结论：生产写入就 A/B 两条；立项书 §四"5 类调用方"实际应表述为"A 主路 + B 旁路"。

---

## 二、路径 A 复现（P0/P1 可落地性验证）

**复现命令**（隔离环境，临时 jsonl，不写 DB）：

| 用例 | 输入 behavior | 结果 | 结论 |
|---|---|---|---|
| 正常入库 | Ht（无 repro） | `repro=situational`，detail/source **保留** | 正常路径不受影响 |
| derived 入库 | **B00** | `repro=derived`，`detail=None`、`source_ref=None`（**被强制清空**） | **复现 #29 空壳成因** |

✅ 确认：`judge_repro`（`memory_organize.py:68`）behavior∈{B00,Fp,Fq,Fr} → derived → `memory_organize.py:148-149` 清空 detail/source。

**P1 改动点确认**：`memory_organize.py:148-149` 的 `detail if layers["detail"] else None` / `source_ref if layers["source"]...` 即清空根源，改为保留 + `derived_only=True` 可落地。

**P0 插入点确认**：`atomic_store` 内 full_hash 检查（`L113`）之后、`append_jsonl`（`L158`）之前，可插 summary embedding 查重；`embed_semantic.embed_cached` 现成可用。

---

## 三、路径 B 取证（移交 #31，根因已定位）

### 3.1 现象
本附件 8 对重复中的 8 张 observation 卡，source 全 `memory_gate`，即路径 B 产物，0.92+ 语义相似却全部入库。

### 3.2 根因（实测定位，两条）

**根因一：`existing` 是循环前的一次性快照，批内互不查重**

`green_card_writer.py:48-55` 在 `for x in cards` 循环**之前**一次性 `SELECT` 当天 existing 列表；循环内新 INSERT 的卡**不进入** existing。→ **同一次 `commit_green_cards` 调用内的多张卡，互相之间永不比对**。

实测印证：同 ts 成对出现的卡（13:25:29 两张、13:27:23 两张）都入库，正是同批次产物。

**根因二：Jaccard 逐对比对，但同批卡内容差异大，且快照缺批内项**

- 同批内两张卡实为不同资产（"dumpsys 回读" vs "vhal_test 部署"，Jaccard 仅 0.012）——**同批不同资产，本就该都入库，不属漏拦**。
- 真正的重复（PAIR2-5）跨批次产生：第一张入库后，**后续批次**的 existing 快照才包含它；但实测其 gist 对 Jaccard（0.65~0.87）**多数 >0.6**，理论上后续批次应拦住——

**⚠️ 矛盾点已解（决定性证据）**：PAIR2/3/4/5 的 gist Jaccard 实测 0.65~0.87（>0.6 本应拦），却都入库了。查 git 史定位根因：

- `bigram-Jaccard>0.6` 补强逻辑由 commit **`19b46d9`（2026-11-17 13:32:08）** 才引入（提交说明："语义幂等破功: 真实LLM同资产措辞漂移使full_hash去重失效 → commit侧加bigram-Jaccard(>0.6跳过)"）。
- 而漏拦的卡入库时刻：**13:25:29 / 13:27:23 / 13:29:26，全部早于 13:32:08**。
- **结论：这批卡是 Jaccard 补强上线前的产物，当时只有 full_hash 精确去重，措辞漂移必然漏拦。Jaccard 上线后（13:32 起）同类卡应已被拦。** 这解释了"矛盾"，且证明路径 B 的 Jaccard 补强**对上线后的同类重复是有效防线**——B 路径的现网风险比预想小，但仍有批内快照缺陷（§3.2 根因一）与跨天不拦等问题。

**路径 B 现网真实风险（修正后）**：
- 根因一（批内快照不查重）：**仍存在**——同一次 commit_green_cards 内多卡互不比。
- 根因二（0.87 应拦未拦）：**已消除**——系 Jaccard 上线前的历史遗留，非当前逻辑缺陷。

### 3.3 路径 B 完整问题清单（移交 #31 前置素材）
1. existing 快照机制：批内不查重（已确认）。
2. 跨批 Jaccard 去重疑似失效（0.87 应拦未拦，待深挖）。
3. 去重仅限"当天 + source_ref LIKE memory_gate%"，跨天同事件不拦（PAIR 跨 11-17/11-19）。
4. 无 embedding 语义查重，仅 bigram 字面，措辞差异大即 Jaccard 低而漏拦（PAIR7/8 Jaccard 0.46/0.22）。
5. 阈值 0.6 与 #30 P0 的 0.95 无统一口径。

✅ 这些全部**只记录、不修复**，作为 #31 立项输入。

---

## 四、埋点/E4 指标落位（仅路径 A 生效）

| 新增日志/指标 | 写入位置（路径 A） | E4 读取位置 |
|---|---|---|
| `skip_equivalent_derived` | `atomic_store` P0 拦截分支 → gate_log.jsonl | `health.dedup` |
| `warn_corpus_runtime_equivalent` | `memory_relations.py` 跨池扫描 → gate_log.jsonl | `health.conflict` |
| `active_same_summary_multi` | E4 直接扫 memories.jsonl（加 derived_only 过滤后） | `health.dedup` |

**边界**：路径 B 不埋点、不进 E4 指标（#30 不触碰 B）。`active_same_summary_multi` 统计时会同时统计到 B 产物——这是**观测事实**，不构成对 B 的修复。

**判据确认**：`active_same_summary_multi` 用 embedding≥0.95 判近似（与 P0 同口径，避免引入第二套阈值）；若性能顾虑，复用小批量采样。

---

## 五、P0-P3 可落地性逐条结论

| 项 | 结论 | 说明 |
|---|---|---|
| P0 summary 语义查重（路径A） | ✅ 可落地 | 插入点明确，embed_cached 现成，阈值 0.95 实测支撑 |
| P0 覆盖路径B | ❌ **不做（决策）** | 移交 #31；立项书 §四已登记该边界风险 |
| P1 derived 保留 detail + derived_only | ✅ 可落地 | 改 L148-149；需确认 DB 是否 ALTER 加列（见 §六偏差1） |
| P2 corpus↔runtime 跨池告警 | ✅ 可落地 | L199/L215 挂载点明确 |
| P3 E4 三指标 | ✅ 可落地（依赖 P0/P2 先落地） | 数据源齐全 |

---

## 六、偏差登记（对照立项书）

| # | 偏差 | 处置 |
|---|---|---|
| 1 | **路径 B 绕过 atomic_store** —— 立项书假设"唯一入口"，实为双路径 | ✅ 已决策：#30 只做 A，B 移交 #31（立项书 §四/§七已修订） |
| 2 | **P1 `derived_only` 需 DB 加列**——立项书未明确 schema 变更 | 段二确认：`core_memory_card` 需 ALTER ADD `derived_only`，或复用现有字段（如 weight/dfcp_tag 携带）避免改表。**倾向不改表**，用 `dfcp_tag.derived_only` 或独立标记列二选一，段二定。 |
| 3 | **E9/backfill 非写入方**——立项书 §四"5类调用方"表述偏宽 | 修正为"路径A主路 + 路径B旁路"，E9/backfill 不在写入链（已在本报告 §一澄清） |

**偏差 1 已回写立项书并经用户确认。偏差 2/3 留待段二风险评估细化。**

---

## 七、段一结论

1. **立项书方案在路径 A 上完全可落地**，P0/P1/P2/P3 均有明确插入点。
2. **路径 B 是独立写路径，根因已取证**（existing 批内快照缺陷 + 跨批 Jaccard 疑似失效），全部移交 #31。
3. **#30 边界清晰**：只改 A，B 零改动；E4 指标虽会观测到 B 产物但不构成对 B 的修复。
4. 偏差 1 已裁决，偏差 2（derived_only schema）需段二定方案。

**下一步**：进入段二风险评估，复用立项书 §四风险清单为基底，结合本报告 §三（B 漏拦根因）、§六（偏差 2 schema）更新。