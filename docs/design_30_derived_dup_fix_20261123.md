# #30 记忆入库-派生重复卡根治立项书

> 文档路径：`docs/design_30_derived_dup_fix_20261123.md`
> 立项编号：#30 ｜ 优先级：P1 ｜ 风险等级：高（修改 atomic_store 入库核心路径，多调用方）
> 前置条件：#29 存量空壳消重已闭环（commit e42c5b5）；仅解决新增写入链路，不处理历史存量。
> 本立项仅产出方案文档；审阅通过后，才正式启动七段契约流程，不直接开始编码。
> 实证附录：`docs/attach_30_dup_samples_20261123.md`（8 对双-active 近重复样本人工定性）

---

## 一、问题定义

### 1.1 现象（两层）

**第一层：derived 派生空壳重复（#30 主目标）**

LLM 派生/复述已有事实时，容易批量生成 `repro=derived` 记忆卡：
- summary 措辞轻微改写，full_hash 变化，精确哈希查重放行；
- 派生逻辑强制清空 detail、source_ref，卡片变空壳；
- corpus 池与 runtime 池互相隔离，跨池不做相似度比对；
- 最终产出多张 active、summary 近似、hash 不同的同事件多版本卡，计数膨胀、E4 统计失真、检索可能命中重复变体。

历史实例：2026-11-16 一次性产出 8 张此类空壳卡，已由 #29 做存量降级。**但写入根源未修复，未来仍会复现。**

**第二层：situational 卡形成的 summary 近似双-active 多版本（#30 不解决，登记 #31）**

实测发现（详见实证附录）：库中除 derived 空壳外，还存在 **8 对** `repro=situational`、**detail/source_ref 完整（非空壳）** 的 summary 近似双-active 卡。这证明：

> **问题范畴大于"derived 派生缺陷"——derived 空壳只是其中一类极端表现。**

**⚠️ 段二补充定性（2026-11-23）**：经 `source_ref` 复核，这 8 对中 **PAIR2-5（5 张）全部 `source_ref=memory_gate;` 开头 = 路径B（`commit_green_cards`）产物**；PAIR1/6 为 `rel-test/sim-test` 自检脚本产物；PAIR7/8 为专利评估多卡。**均非路径A（atomic_store）产物**。这一发现直接决定：P0（路径A闸门）不能拿这些卡验召回，灰度正样本改用 #29 的 8 空壳 vs 正本（详见 `risk_30_phase2_20261123.md` §3）。

**边界裁定（用户 2026-11-23）**：
- #30 **只解决未来新写入链路**；
- 这 8 对存量 situational 多版本 **不并入 #30**，登记为**候选独立立项 #31**，后续单独评估是否做存量消重；
- 严禁 #30 从"写链路修复"膨胀为"大规模存量治理"，风险不可控。

### 1.2 根因：三道闸门串联失效（非并列独立 bug）

因果顺序（必须按此阅读，不是三个互不相关问题）：

1. **第一道：full_hash 精确查重过松放行**
   `full_hash = summary+detail+source` 拼接 SHA256，仅逐字完全一致才命中；summary 措辞微调，hash 即不同，第一道闸门直接放行。

2. **第二道：derived 派生逻辑销毁关键比对输入**
   进入 derived 分支后，`memory_organize.py` 原逻辑强制 `detail=None`、`source_ref=None`。即便上游传入完整详情，到这一步直接清空。后续用于 embedding 语义查重的输入消失，语义相似度闸门失去素材，形同虚设。

3. **第三道：corpus/runtime 池隔离，跨池完全不比对**
   冲突/相似度候选池逻辑仅在 runtime↔runtime 内部生效；corpus 卡直接跳过比对，跨池之间不会触发任何重复/冲突告警。

**串联逻辑**：第一道拦不住微改复述 → 第二道删掉语义比对素材 → 第三道跨池看不见对方 → 同事件多版本空壳顺利入库。

### 1.3 影响范围

- **存储**：active 卡数量膨胀，E4 多 active 同 summary 指标失真；
- **检索**：可能返回多条语义等价变体，干扰结果；
- **治理**：需定期人工存量消重，运维成本上升；
- **不破坏正确性**：卡片 schema 合法，不致数据损坏，属质量与治理类缺陷，非阻断性故障。

---

## 二、改造总体原则

1. **不推翻三层模型语义**：`repro=derived` 定义仍为「派生，不占用独立事实层」。
2. **最小侵入**：只为查重/治理目的保留 detail、source_ref，默认检索链路不把派生 detail 作为主事实源。
3. **阈值保守优先，且以实测为准**：语义查重宁漏勿错，不误伤真实新增相近主题；阈值铁律见 §3.1-P0。
4. **兼容全部调用方**：atomic_store 调用入口不止 memory_hook，还包括 judge_and_store、backfill 回填、E9 夜间提案、人工导入——全部路径回归覆盖。
5. **存量不动**：只治理新写入卡片；历史存量交由 #29（已闭环）及 #31（候选）。

---

## 三、改造项 P0-P3（有依赖顺序，必须按次序实施）

**依赖关系**：P0 语义查重依赖 P1 保留 detail 素材；P2 告警依赖相似度能力；P3 监控用于事后观测整套改造效果。

### P0：入库前 summary 语义等价查重（最高优先级，入口拦截）

- **位置**：`atomic_store` 内部，full-hash 精确查重完成之后、正式写库之前。
- **⚠️ 查重池口径（段二 R1 前置阻塞）**：比对池必须为 **DB `status='active' AND tier='active'`**（当前 280 条），**不得复用 `atomic_store` 现有 `load_memories()`（读 jsonl 全量 320 条，含 2 deleted + 37 precedent）**。现状 full_hash（L114）与未来 P0 若口径不一，会误拦已删除/已降级卡。段三须统一为 DB 池（新增 `load_active_pool()`）。
- **逻辑**：取待入库卡片 summary 做 embedding，与库内 **active 池** summary 做相似度比对（全量矩阵乘，非采样）。
- **阈值定义**：`equivalent` =「同一事实，仅措辞改写」，不是主题相似。

**⚠️ 阈值铁律（基于实测，2026-11-23 修订）**：
- **实测数据**：#29 的 8 张 derived 空壳 vs 正本 summary 相似度**全部 ≥0.953，均值 0.989**；本附件 PAIR 2（真实业务真重复）最低 **0.952**。
- **关键反例**：本附件 PAIR 6（非农复盘对）相似度 **0.905，但归因完全不同 = 两个不同事实**。
- **结论**：历史真重复下限 ≈ **0.95**；0.905 已是不同事实。
- **P0 起步阈值 = 0.95**。灰度过程再评估是否微调，**严禁直接低于 0.95 上线**。
- ~~旧参考 0.84-0.88~~ ← **作废（该区间会误伤 0.905 这类不同事实）**。

- **命中等价处理**：
  1. 找到已存在等价正本；
  2. **拒绝新建卡**，将本次写入转为对正本的补充元信息（不新建 card）；
  3. 日志 `skip_equivalent_derived`：记录待写入 summary 摘要、匹配到的正本 inst_id、相似度分数。

**✅ 灰度强制双判据（必须全部满足，2026-11-23 段二修订）**：
- **召回**：正样本集 = **#29 的 8 张 derived 空壳 vs 各自正本**（实测 summary embedding 相似度 0.953~1.000，**8/8 全部命中**）。~~旧判据 PAIR 2~5~~ ← **作废**：段二实测确认 PAIR2-5 全部 `source_ref=memory_gate`，是**路径B产物**，P0 作用于路径A，拿它们验 P0 逻辑不成立（详见 `risk_30_phase2_20261123.md` §3.2）。
- **精确率**：**关键反例 = 本附件 PAIR 6 非农复盘对（0.905），必须不被拦截**，固化为回归用例；同时从**路径A真实写入**（memory_hook / judge_and_store / 回填）抽样近 30 天真实业务卡作反例集，正常新增不被误拦。
- **⚠️ 阈值不可分实测（R3）**：PAIR4(0.945)/PAIR5(0.920) 为真重复但 0.95 漏放；PAIR5(0.920) 与反例 PAIR6(0.905) 间距仅 0.015。**单一 summary 阈值不可分**，接受「宁漏勿错」（最坏退化为现状漏放，绝不误拦不同事实）。

> 注：PAIR 1/6 为 11-08 测试数据（source=rel-test/sim-test），**非生产**，仅作阈值下限旁证，不进生产精确率统计。

### P1：derived 分支保留 detail/source_ref，但不暴露给检索（关键语义妥协）

**备选二选一评估**：
- **方案 A**（直接 derived 卡完整保留 detail，检索可见）：改变原 derived「派生层不占用事实」语义，风险高；
- **方案 B（采纳）**：detail/source_ref **物理保存入库**，新增标记 `derived_only: true`；检索/事实推理链路过滤带该标记的 detail 字段，仅查重、治理脚本可读取。

**修改位置**：`memory_organize.py` 原 L148-149，不再无条件置空 detail 与 source_ref。

**⚠️ 存储载体（段二收敛①，采纳不改表）**：`derived_only` 写入 **`dfcp_tag` JSON 内嵌键**（`dfcp_tag.derived_only = true`），与现有 `memory_class` 同级。
- **不改 DB schema**（`core_memory_card` 无新列，零 ALTER）；
- DB 实测 `dfcp_tag` 为 JSON 文本列，追加未知键不影响 `schema.validate_memory_tags`（只校验 5 维，不拒未知键）；
- E4/治理脚本可直接 `json.loads(dfcp_tag).get("derived_only")` 读取；
- 备选：**仅当**未来需按该标记做高频列过滤/索引时，才评估 ALTER 加独立列（当前无此需求，否决）。

**行为**：
- derived 卡完整接收上游 detail/source_ref 并存入 DB；
- `dfcp_tag.derived_only = true`；
- 业务检索、事实读取接口过滤：带 `derived_only` 标记的 detail 不对外输出、不参与事实推理；
- 仅入库查重逻辑、E4 审计、治理脚本可读取该 detail 用于 embedding 相似度计算。

**⚠️ 读取封装（段二 R4 强制项）**：当前检索/事实推理链路**无统一 detail 读取封装**，直接置 `derived_only` 会导致派生 detail 泄露到业务层。P1 落地前**必须**先建 `read_card_detail(entry, purpose)` 统一入口：
- `purpose in {"search","reasoning"}` 且 `dfcp_tag.derived_only` → 返回 `None`；
- `purpose in {"dedupe","audit","governance"}` → 返回完整 detail；
- 业务侧禁止直接 `entry["detail"]`，一律走该函数。

**收益**：既保留 embedding 查重所需原始素材，又不破坏三层模型「derived 派生不占用事实层」语义。

### P2：corpus ↔ runtime 跨池疑似重复告警（不阻断，仅告警）

- **位置**：`memory_relations.py` 冲突判定模块。
- **现状**：corpus 卡直接跳过 runtime 冲突候选池，完全不比对。
- **改动**：
  - 不改核心冲突判定逻辑，不阻断入库，不拒绝写入；
  - 新增跨池 embedding 相似度扫描：新写入卡，跨另一池做 summary 相似度扫描；
  - 达到阈值 → 输出 **仅 E4 晨报可见**的告警日志 `warn_corpus_runtime_equivalent`（两边 inst_id、相似度）。
- **定位**：仅给人看的治理提示，机器不自动降级/拒绝，避免跨池误判带来业务风险。

### P3：E4 巡检新增监控指标（兜底观测）

E4 记忆健康度块新增指标，观测 P0-P2 运行效果：
- `skip_equivalent_derived` 计数：P0 语义查重拦截数；
- `warn_corpus_runtime_equivalent` 计数：跨池疑似等价告警数；
- `active_same_summary_multi`：active 状态下 summary 近似的多卡组数。

**作用**：线上通过晨报观测复现率；指标反弹说明阈值漂移或出现新派生模式。

---

## 四、风险清单（七段段二风险评估依据）

### 4.0.1 atomic_store 多调用方影响（高风险）
调用入口（全部需回归覆盖）：
- memory_hook（主通路）
- judge_and_store（judge LLM 判定后写入）
- backfill 回填脚本
- E9 夜间会话提案写入
- 人工导入/测试脚本

**风险**：某通路绕过 P0 语义查重，继续生成重复空壳。
**缓解**：所有写入统一经过 atomic_store，禁止绕开直接写 DB；回归覆盖 5 类调用路径；E4 指标兜底监控，`active_same_summary_multi` 异常反弹立即告警。

### 4.0.2 P0 语义查重阈值误拦真实新增（高风险）
**风险**：阈值过低，把相近但不同的新卡判为等价而拒绝写入，丢失业务信息。
**缓解**：
- 灰度强制双判据（#29 8 空壳召回 + PAIR6 反例不误拦 + 路径A 30 天真实样本精确率）；
- 开关 `ENABLE_SUMMARY_EQUIV_DEDUPE`（off/dry-run/on 三态），误拦可快速关闭 P0，不影响整套记忆系统；
- 每次拦截完整打日志（inst_id、similarity、elapsed_ms），便于事后审计。

### 4.0.3 P1 derived_only 标记漏过滤（中风险）
**风险**：业务检索接口忘记过滤 `derived_only`，派生 detail 泄露到事实推理，违背三层模型语义。
**缓解**：强制统一读取封装 `read_card_detail(entry, purpose)`（不在每处业务手写 if）；回归用例构造 derived_only 卡，断言 search/reasoning 读 None、dedupe/audit 可读完整 detail。

### 4.1 性能风险（中风险）
**风险**：每次入库新增 embedding 计算，高频写入增加耗时。
**缓解**：只对 summary 做 embedding，不处理完整长 detail；耗时指标写入 gate_log，观测入库延迟变化。
**段二实测**：全量比对（280 条池）单次入库增量 ≈ **36ms**（embed 8.5ms + 矩阵乘 27.5ms），噪声级；红线 >300ms 触发降级为采样/跳过。无需采样。

### 4.2 查重池口径分裂（高风险，段二新增 R1，前置阻塞）
**风险**：`atomic_store` 现有 `load_memories()` 读 **jsonl 全量 320 条**（status 全 None，含 2 张 deleted + 37 张 precedent）；而正确口径应为 **DB `status='active' AND tier='active'` = 280 条**。P0 若复用 jsonl 池 → 误比对已删除/已降级卡（实测 6/37 张 precedent 卡在旧池有 >0.95 近邻，会造成误拦）。
**缓解**：P0 比对池改用 DB active 口径（新增 `load_active_pool()`）；段三方案须先将 full_hash 与 P0 查重池口径统一为 DB。

### 4.3 灰度正样本路径错配（高风险，段二新增 R2）
**风险**：立项书原灰度正样本 PAIR2-5 实测 `source_ref=memory_gate`，全部为**路径B产物**（`commit_green_cards` 写入），P0 只作用于路径A，拿路径B产物验路径A闸门逻辑不成立。
**缓解**：正样本改用 **#29 8 空壳 vs 各自正本**（实测 8/8 召回，最低 0.953）；PAIR2-5 移出 P0 判据，转 #31。PAIR6 保留作阈值下限旁证（非生产反例）。

### 4.4 阈值不可分（中风险，段二新增 R3）
**风险**：真重复 PAIR4(0.945)/PAIR5(0.920) 在 0.95 阈值下漏放；而 PAIR5(0.920) 与不同事实反例 PAIR6(0.905) 间距仅 0.015，**不存在单阈值同时拦住 PAIR5 又放过 PAIR6**。
**缓解**：坚持「宁漏勿错」——0.95 只拦高度同源（如 derived 空壳≥0.953），0.90~0.95 边界带宁可漏放（退化为现状），绝不误拦不同事实。PAIR4/5 类漏放记入 #31 存量治理候选。

### 4.5 P1 derived_only detail 泄露检索（中风险，对应 P1 R4）
**风险**：检索/事实推理链路漏过滤 `derived_only`，派生 detail 泄露到业务层，违背三层模型语义。
**缓解**：强制统一读取封装 `read_card_detail(entry, purpose)`（见 §三 P1）；回归用例构造 derived_only 卡，断言 search/reasoning 读到 None、dedupe/audit 可读完整 detail。

### 4.6 旁路路径 B 不受 #30 防护（已知边界风险，归属 #31）
**⚠️ 风险**：旁路路径 B（`green_card_writer.commit_green_cards`，经 `executor.py:192` 调用）绕过 `atomic_store`，直接 INSERT `core_memory_card`，**不受 #30 的 P0/P1 防护**，依旧可以生成相似 situational 重复卡。
- 实证：本附件 8 对重复中的 8 张 observation 卡（source=`memory_gate`）均为路径 B 产物，0.92+ 语义相似却全部入库。**段二复核：PAIR2-5 的 5 张卡 `source_ref` 全部以 `memory_gate;` 开头，确认路径B产物。**
- **归属裁定（用户 2026-11-23）**：该问题**不归属于 #30**，留待**候选 #31** 立项评估处理。
- **#30 处置**：严格只处理路径 A；路径 B 本次零代码修改，仅由段一/段二侦察做只读取证，产出移交 #31。
- **回归要求**：本项风险不作为 #30 回归用例（B 路径行为在 #30 前后不变）；但需在侦察报告显式记录，避免后续误以为 #30 已解决全部重复问题。

---

## 五、回归测试集（七段段四测试基础）

### 功能用例
1. 复现 #29 的 8 张历史空壳输入（vs 各自正本）→ 全部被 P0 拦截（实测 8/8，sim 0.953~1.000），不新建卡，日志正确记录 skip；
2. ~~PAIR 2~5 真重复对 → 被拦截~~ ← **P0 用例作废（段二 R2）**：PAIR2-5 是路径B产物，P0 不作用于路径B；转 #31 回归。仅保留「PAIR2/3 若走路径A 应拦」作参考，不纳入 #30 门禁；
3. **本附件 PAIR 6 非农复盘对（0.905）→ 不被拦截**（关键反例，阈值下限旁证）；
4. 真实相近但不同事实 → 不被拦截，正常新建；
5. derived 卡 → detail/source_ref 物理入库、`dfcp_tag.derived_only=true`；业务检索读不到 detail；审计脚本可读；
6. corpus/runtime 产生等价 summary → 触发 E4 告警，不阻断写入；
7. 多调用通路全覆盖：hook / judge_and_store / backfill / E9 提案 / 人工导入，均走 P0-P1；
8. 开关 `ENABLE_SUMMARY_EQUIV_DEDUPE` 关闭（off）→ P0 完全关闭，行为退回改造前；dry-run → 只记不拦；
9. **查重池口径（R1）**：断言 P0 比对池 = DB status=active∧tier=active，**不含** deleted/precedent 卡（构造 deleted 卡与准新卡同 summary，验证不被其误拦）。

### 观测用例
- E4 新增三项指标正常输出；
- gate_log 包含拦截、告警完整审计字段。

### 非回归（必验证）
- 正常 non-derived 卡逻辑完全不变；
- 历史存量卡不受本次代码改动影响（除 P0 未来写入查重外不动存量）；
- 记忆检索、冲突判定、Hebbian 权重更新行为不变。

---

## 六、上线与灰度策略

1. 开发完成 → 跑全部回归用例；
2. **灰度阶段**：`ENABLE_SUMMARY_EQUIV_DEDUPE=dry-run`，**只做日志统计不真正拒绝写入**，统计 1 周拦截数、命中分布（0.95~0.97 / 0.97~0.99 / >0.99）、误拦数；
3. **灰度达标**（#29 8 空壳召回 8/8 + 人工抽 30 条命中确认无误拦），才切 `=on` 开启真实拦截；
4. 一旦异常，切回 `dry-run`/`off` 快速回退 P0，不需版本回滚。
5. **性能红线**：入库延迟 >300ms 自动降级 dry-run（记 `p0_slow`）；embed 加载失败整体跳过 P0（记 `p0_unavailable`），不阻断入库。

---

## 七、明确排除在 #30 之外

- **历史存量修复**：由 #29 完成（已闭环）；
- **本附件 8 对 situational 双-active 存量**（detail 完整、非 derived）→ 登记**候选 #31**，本项目不做任何降级/修改；
- **组 8 detail 缺失证据回填**：独立小任务，本项目不做；
- **大规模改写旧的 derived 存量卡**：不改动历史数据；
- **彻底移除 full_hash 精确查重**：full_hash 保留为第一道快速过滤；
- **⚠️ 旁路路径 B（`green_card_writer.commit_green_cards`）**：该路径绕过 `atomic_store`，直接 INSERT `core_memory_card`，**不受 #30 的 P0/P1 防护**。边界裁定（用户 2026-11-23）：
  - **#30 严格只处理路径 A（atomic_store），路径 B 本次不做任何代码修改**；
  - 段一侦察对路径 B 做**只读取证**（复现漏拦、定位 bigram-Jaccard 根因），产出全部移交**候选 #31** 作为前置素材；
  - 现状说明：路径 B 以 full_hash + bigram-Jaccard>0.6 同日幂等去重，但实测 0.92+ 语义相似的 PAIR2-5 仍放行入库（漏拦根因见段一侦察报告）；
  - 该问题不归属 #30，留待候选 #31 立项评估处理。

---

## 八、后续

文档审阅完成 + 实证附件就位后，正式启动七段契约完整流程（侦察-方案-风险-测试-实现-冒烟-回归）。
本立项文档 = 七段「段二：方案」的基础输入。

**台账**：#30 P1｜段一侦察✅、段二风险评估✅（2026-11-23）｜待段三实现方案细化｜根治 derived 派生重复空壳入库链路（仅路径A）；前置 #29 ✅完成。

> **段二风险评估结论摘要**（详见 `docs/risk_30_phase2_20261123.md`）：
> - derived_only 采纳 `dfcp_tag` 内嵌（不改表），前提建 `read_card_detail` 统一读取封装；
> - P0 全量比对 36ms/次，性能无忧；
> - **两处立项书硬伤已修**：① 查重池口径须统一为 DB active（非 jsonl）；② 灰度正样本改用 #29 8空壳 vs 正本（8/8 召回），PAIR2-5 是路径B产物转 #31；
> - 阈值不可分（PAIR5 0.920 vs PAIR6 0.905 间距 0.015），坚持「宁漏勿错」。
**候选**：#31 存量 situational 双-active 多版本治理（本附件 8 对），待评估立项。