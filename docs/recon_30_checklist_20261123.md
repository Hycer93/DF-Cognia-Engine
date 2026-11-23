# #30 段一侦察 · 检查清单

> 生成：2026-11-23 ｜ 阶段：七段「段一 侦察」
> 目的：代码实地勘察，确认立项书方案在代码层面可落地；发现偏差则回写修订 design_30。
> 前置：立项书 `docs/design_30_derived_dup_fix_20261123.md` + 实证附件 `docs/attach_30_dup_samples_20261123.md` 已审阅通过。
> 边界：#30 只管新写入链路；**侦察阶段不触碰存量 8 对 situational 样本**；#31 维持候选。

---

## 〇、预备发现（立项前已实地勘察，段一需复核确认）

**⚠️ 重大偏差预警：立项书 §四风险清单假设"atomic_store 是唯一写入入口"，实测不成立。**

生产写入 `core_memory_card` 的路径有 **两条**：

| # | 路径 | 入口 | 去重机制 | 是否走 atomic_store |
|---|---|---|---|---|
| A | 记忆入库主路径 | `memory_hook.add()` → `atomic_store()` | full_hash 精确 | ✅ 是 |
| B | **绿档落库旁路** | `executor.py:192` → `green_card_writer.commit_green_cards()` → 直接 INSERT | full_hash 精确 + **bigram-Jaccard>0.6 同日幂等** | ❌ **否，完全绕过** |

**实证**：本附件 8 对重复中的 8 张 observation 卡 source 全是 `memory_gate`，即路径 B 产物——**立项书 P0「atomic_store 内部加查重」对路径 B 完全无效**。

**段一必须回答**：P0/P1 改造落点应如何覆盖路径 B？这决定立项书是否需要重大修订。

---

## 一、写入调用点全量勘察

- [ ] **1.1** 摸清 `atomic_store` 定义签名（`memory_organize.py:80`）：入参 summary/detail/source_ref/tags/asset_class/origin_ids/event_ts，返回 (status,msg)。
- [ ] **1.2** 遍历 `atomic_store` 全部真实生产调用点，逐个确认入参形态：
  - `memory_hook.py:40`（主通路，auto_link/auto_distill 联动）
  - `memory_relations.py:449/453`（自检，非生产）
  - `memory_organize.py:411+`（`__main__` 自检，非生产）
- [ ] **1.3** **勘察路径 B**：`green_card_writer.commit_green_cards()`（`cgraph/green_card_writer.py`）——入参 cards 结构、直接 INSERT 的字段、其内置去重（full_hash + bigram-Jaccard）逻辑、`executor.py:192` 调用上下文。
- [ ] **1.4** 确认 E9 夜间治理是否写入 card：实测 E9 只写 `store_pending.jsonl`（提案），**不直接写 card**；确认转正链路最终走哪条（hold 转正 → atomic_store？需确认）。
- [ ] **1.5** 确认 backfill 脚本性质：`backfill_jsonl_from_db.py` 只 DB→jsonl 导出，**不新建 card**，与 P0 无关（避免误判为调用方）。
- [ ] **1.6** 登记全部 eval/audit 下的 INSERT（回归脚本自建测试数据），确认它们不影响生产路径判断。

**产出**：一张"真实写入路径清单"，标注每条路径是否需覆盖 P0/P1。

---

## 二、复现验证（确认当前原始行为）

- [ ] **2.1** 本地复现 #29 那 8 张 derived 空壳的入库路径：确认是走 `atomic_store` 且 `judge_repro` 判 derived → detail/source_ref 被清空（`memory_organize.py:148-149`）。
- [ ] **2.2** 复现路径 B 产重复：模拟 `commit_green_cards` 同一 user_input 多个 evidence_step（如 node_Bd/Di/Dk），确认 bigram-Jaccard 0.6 阈值为何没拦住（实测 PAIR 2-5 相似 0.92+ 却都入库——**0.6 阈值下为何漏拦？是否因剥前缀后 gist 差异大、或跨 span 比对逻辑有洞**）。
- [ ] **2.3** 确认 derived 路径下 detail 被清空的精确代码行，为 P1 改动定位。
- [ ] **2.4** 复现一条"真实相近但不同事实"（用附件 PAIR 6 非农对）走当前逻辑，确认现状是**都入库**（无任何拦截）——作为 P0 上线后的对照基线。

**产出**：复现记录（命令 + 当前行为快照），特别是 2.2 的"路径B为何漏拦"结论。

---

## 三、日志/埋点可复用字段勘察

- [ ] **3.1** 梳理 `gate_log.jsonl` 现有字段（op/query_hint/simhash/deep_retrieve 等），确认 P0 `skip_equivalent_derived`、P2 `warn_corpus_runtime_equivalent` 新增字段落位方案。
- [ ] **3.2** 确认 `green_card_writer` 的审计行 `audit_topic_log op=memory_commit:green` 结构，P0 若要覆盖路径 B，拦截/半重复是否复用该审计行还是新开。
- [ ] **3.3** 梳理 E4 `_memory_health()` 现有四维（retrieve/conflict/dedup/archive）结构，确认 P3 三项指标（skip_equivalent_derived / warn_corpus_runtime_equivalent / active_same_summary_multi）挂哪个维度。
- [ ] **3.4** 确认 `active_same_summary_multi` 的"summary 近似"判据用什么（embedding≥0.95？还是复用现有 simhash/gist 比对），避免 E4 巡检引入重 embedding 开销。

**产出**：埋点落位表（新增日志 → 写入位置 → E4 读取位置）。

---

## 四、方案可落地性核对（对照立项书逐条）

- [ ] **4.1** **P0**：在 atomic_store 内 full_hash 之后、写库之前插入 summary embedding 查重（阈值 0.95）——勘察插入点、embedding 复用 `embed_semantic.embed_cached` 是否现成、对性能影响。
- [ ] **4.2** **P0 覆盖路径 B（关键）**：若路径 B 也必须挡重复，改造方案是什么？——是在 `commit_green_cards` 内置 0.6 Jaccard 升级为 embedding 0.95，还是抽公共查重函数两端共用？**此条侦察结论直接决定立项书是否需修订 §四风险清单与 §三 P0 范围。**
- [ ] **4.3** **P1**：`memory_organize.py:148-149` 改为保留 detail/source_ref + 加 `derived_only=True` 标记——确认 card schema 是否需加列（DB 表结构 ALTER）、读取侧过滤点在哪。
- [ ] **4.4** **P2**：`memory_relations.py` 跨池告警——确认 corpus 卡跳过的具体行（L199/L215），新增跨池扫描的挂载点。
- [ ] **4.5** **P3**：E4 三指标——确认聚合所需数据源齐全（若 P0/P2 未落地则指标为空，需标注依赖）。

**产出**：逐条"可落地/需修订/有阻塞"三态结论。

---

## 五、偏差登记与回写

- [ ] **5.1** 汇总段一所有与立项书不符的发现，列偏差清单（已知首条：路径 B 绕过 atomic_store）。
- [ ] **5.2** 对每条偏差给出处置建议：改立项书 / 改方案 / 登新任务 / 接受现状。
- [ ] **5.3** 若偏差触及 P0 范围（如必须覆盖路径 B），**段一结束前回写修订 `design_30_derived_dup_fix_20261123.md`**，并同步给用户确认，再进段二。

---

## 六、段一退出标准（Definition of Done）

全部满足才可进段二风险评估：

1. 真实写入路径清单完成，**路径 A/B 是否都要覆盖 P0 有明确结论**；
2. 8 张 derived 空壳 + PAIR 2-5 的复现行为确认，**路径 B 漏拦根因查清**；
3. 埋点落位表完成；
4. P0-P3 逐条"可落地/需修订/阻塞"三态结论完成；
5. 偏差清单 + 处置建议完成，**触及 P0 范围的偏差已回写立项书并经用户确认**。

---

## 七、段一不做（守边界）

- ❌ 不修改任何代码（侦察只读）；
- ❌ 不触碰存量 8 对 situational 样本（属 #31）；
- ❌ 不做 P0-P3 实现（段一仅勘察+复现，实现留给段三）；
- ❌ 不评估 #31、不动历史数据。