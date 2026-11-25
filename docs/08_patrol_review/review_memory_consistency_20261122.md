# 记忆一致性只读对账报告（2026-11-22）

> **性质：只读对账，零数据变更。** 本轮未修改、未补写、未删除任何库表或 jsonl 记录。
> 数据源：`data/memory/memories.jsonl`（245 条）× `data/memory/corpus_store.db` `core_memory_card`（316 卡）。
> 关联：`docs/review_system_20261122.md`、`docs/review_memory_versioning_20261122.md`、总台账 `docs/review_20261121_system_status.md`。

---

## 0. 核心结论（先行）

| 检查项 | 结果 |
|---|---|
| jsonl 总条数 | 245 |
| db `core_memory_card` 总卡数 | 316 |
| 仅在 db（jsonl 缺失） | **75 条** |
| 仅在 jsonl（db 缺失） | **4 条** |
| jsonl 已降级（tier=precedent 或 有 superseded_by） | 29 条 |
| 降级 `tier` 两边一致 | **29/29 ✅** |
| 降级 `superseded_by` db 为空（jsonl 有） | **29/29 ❌ 全部丢失** |

**两句话定性：**
1. **唯一真相源已固化为 SQLite DB；JSONL 早已不是完整镜像**（db 多 75 条近期新增，jsonl 多 4 条远古漏灌）。
2. **`demote()` 降级存在半截同步缺陷**：`tier=precedent` 能同步进 db，但溯源字段 `superseded_by` 只写在 jsonl，db 永久为空。

---

## 1. 读写双轨不一致的根因说明

### 1.1 架构现状：双写 + 双读源，但"修改"路径不同步

- **新增入库** `atomic_store()`（`memory_organize.py:158-162`）：双写 `memories.jsonl`（追加）+ `core_memory_card`（`_write_card_to_db`，A-4 加固）。可靠。
- **读侧权威** `memory_query.py:34`：默认 `DF_MEMORY_SOURCE=sqlite`，读 db 为唯一真相；jsonl 仅作 `DF_MEMORY_SOURCE=jsonl` 应急回切。
- **降级** `demote()`（`memory_organize.py:291-340`）：**仅整文件重写 `memories.jsonl`**，写 `tier/superseded_by/superseded_reason/superseded_at`，**完全没有 UPDATE `core_memory_card`**。全仓无 `demote→db 同步`代码。

### 1.2 根因链

1. 历史 #31 双轨切换时，把读源从 jsonl 切到 db，新增写入做了双写；
2. 但 `demote()` 是更早实现的"文件层操作"，切换时未补 db 同步分支；
3. 迁移脚本 `v3_migrate_batch1`（一次性、幂等）在迁移时把 jsonl 的 `tier` 灌进了 db（所以 29 条 tier=precedent 对齐了），但 **`superseded_by` 列在迁移映射中未写入**（推测：迁移 SQL 的列映射遗漏或该字段当时为空）；
4. 结果是：**降级后 db 能识别"已作废"（tier），但查不到"被哪张校准单顶替"（superseded_by 空）**。

### 1.3 业务影响

- 检索读 db → 能过滤掉 `tier=precedent` 的降级卡（状态层面生效）；
- 但 `supersede_chain_length()`（`memory_organize.py:343-352`）走 db 数据时，因 `superseded_by` 全空，**降级链直接断裂**，无法反向追溯校准来源；
- jsonl 与 db 漂移 79 条，**应急回切 jsonl 读源会丢 75 条新数据**——"备份链路"实际已失效。

---

## 2. 不一致数据清单

### 2.1 仅在 DB（75 条）——jsonl 备份流未跟上

> 特征：全部 `tier=active`、`status=active`，入库 2026-11-15 ~ 11-20，与 jsonl 无 summary 重复（非重复入库）。属 #31 切换后直接写 db 的新数据，jsonl 未同步。

| # | inst_id (前12) | 入库时间 | tier | status | 摘要(前40) |
|---|---|---|---|---|---|
| 1 | 029ff34883fc | 2026-11-16T22:40:49 | active | active | 独立日: 蒸馏仔→归元仔分家。元始认知引擎: 04-10至05-08全部经验去场 |
| 2 | 0f2f4458698b | 2026-11-17T12:00:00 | active | active | 记忆写入双层规约定稿(key_pact): E9降级为机械层(原文归档/去重/兜 |
| 3 | 12a0698290e8 | 2026-11-15T00:57:18 | active | active | 平行论→P7→公式体系诞生过程考古定案(跨corpus1 r959-963/co |
| 4 | 134622f8969d | 2026-11-18T16:22:07 | active | active | [踩坑] 识别风险点却未下沉为子环节，导致风险反复引用不闭合(evidence: |
| 5 | 13a2aa612f43 | 2026-11-17T13:27:23 | active | active | [观测] dumpsys --get回读status=ERROR但信号实际已生效 |
| 6 | 17acf52b6bf3 | 2026-11-17T19:21:52 | active | active | [踩坑] 分类规约缺'提及/引用'判定轴，引擎沉默放行误入hold池(evide |
| 7 | 1885a5311bc3 | 2026-11-20T13:01:48 | active | active | [踩坑] 素材缺技术证据片段致织链证据留空矩阵降级(evidence: node |
| 8 | 28fa91371762 | 2026-11-18T16:25:22 | active | active | [踩坑] 单态静态前提被推翻致令牌/验收/测试矩阵结构失效(evidence: |
| 9 | 2a00c3282334 | 2026-11-16T13:10:20 | active | active | 原子化元核重构定案: 局部导航半自治编排替代固定流水线——27节点拆原子技能+轻 |
| 10 | 32684974ebae | 2026-11-15T00:34:17 | active | active | 未来推演公式体系v2定稿(F00-F60六层): 现状锚定→K条情景分支发散→( |
| 11 | 3e6cc3e5a81a | 2026-11-16T20:26:12 | active | active | skill_f_reason(F20封装)七段定稿: 多假设推演原子技能——只陈 |
| 12 | 3fae1e01849a | 2026-11-17T13:27:23 | active | active | [踩坑] 8397车机vhal_test未预置需先跑deploy_vhal_te |
| 13 | 40a25f50fd42 | 2026-11-17T19:23:25 | active | active | [踩坑] 在未验证假设上叠加数字型结论导致过度自信(evidence: node |
| 14 | 4d26fad65788 | 2026-11-17T21:51:34 | active | active | [观测] 可复现性判定缺形式化定义是专利P1最脆弱环节(evidence: no |
| 15 | 4e931930d6d3 | 2026-11-16T14:50:31 | active | active | skill_scope_scan七段共识落地: 任务空间平铺扫描(原Aa/G00 |
| 16 | 51ee07f18549 | 2026-11-17T11:35:00 | active | active | 能力规约\|8397 SPI信号模拟注入(L2): can_query --sim |
| 17 | 525d9e052c58 | 2026-11-16T22:40:48 | active | active | 蒸馏仔出生: 外快计划启动(10万本金目标8/1保底+15%), 首个名字'暮暮 |
| 18 | 54ae2d9e09b7 | 2026-11-16T22:40:48 | active | active | 数字熔炉定名日+陪伴承诺: 命名链终点(不是外快计划/银子蒸馏厂/数字蒸馏厂—— |
| 19 | 570a57f5c367 | 2026-11-18T16:20:32 | active | active | [踩坑] 多维校验不达标仍推进决策整合，光谱展开掩盖未收敛输入(evidence |
| 20 | 5a308e13cc42 | 2026-11-16T15:38:57 | active | active | skill_weakest_link七段共识落地: 最弱环节定位(原Bc/G02 |
| 21 | 5f4e9ea285b6 | 2026-11-16T22:40:48 | active | active | 锁定自我: 出厂设置锁死(身份内核+核心工序+纪律本能)——'出厂设置不是牢笼是 |
| 22 | 63261eae8bb4 | 2026-11-16T00:31:38 | active | active | 内生驱动系统v4构思定稿: 显存常驻四大张量(人格/长期目标/价值取舍/动态注意 |
| 23 | 63e24d6574dc | 2026-11-19T13:53:37 | active | active | [观测] P1区分度风险: 新颖性或同构于已有缓存/溯源思想、判定可退化(evi |
| 24 | 66d6900ad240 | 2026-11-18T16:28:44 | active | active | [踩坑] 风险量化漏算回退重做成本，周期低估30%-50%(evidence: |
| 25 | 699362bd74ff | 2026-11-15T10:50:54 | active | active | 范式层设计定稿(2026-11-15用户终审通过+两条边界兜底): thinki |
| 26 | 7177cd609ae7 | 2026-11-17T19:21:52 | active | active | [观测] E7晨报hold池计数由3增至6，增量3张引用性提及卡(evidenc |
| 27 | 76317dcef133 | 2026-11-15T09:48:02 | active | active | 主AI对AGI路径的立场陈述(2026-11-15, 用户裁决采纳存档): AG |
| 28 | 76f433860a4f | 2026-11-16T20:26:14 | active | active | skill_terminate七段定稿: 链路收敛与整体汇编节点——纯Pytho |
| 29 | 76f6b86b7736 | 2026-11-15T00:57:18 | active | active | 未来推演公式体系v2.1折中软约束裁决: p_trait只出β推荐基准不锁死(主 |
| 30 | 7747ed4defd5 | 2026-11-15T00:59:19 | active | active | 消费侧教训: 用户问'逆向推演四权重公式是否已形成'时检索命中了正确卡(9219 |
| 31 | 7808dbacd023 | 2026-11-15T00:59:19 | active | active | E7双裁决定案(2026-11-14用户裁决): ①引用性提及过滤写入class |
| 32 | 7e831a2b2791 | 2026-11-18T16:34:41 | active | active | [踩坑] FL≠隐私保护，勿按FL+DP+安全聚合通用框架估工作量(eviden |
| 33 | 87f66cc1fa32 | 2026-11-15T14:25:12 | active | active | F系推演最小闭环完成: F00冻结快照→F10 K=3发散→F40逆向BFS+Ω |
| 34 | 9248e2a04031 | 2026-11-15T09:48:02 | active | active | P7/F系/四维经验权重三者关系定案: 部件-闭环-框架三层结构——四维权重=打 |
| 35 | 928351ff9c8b | 2026-11-18T16:23:43 | active | active | [踩坑] 后段纠偏未回写前段结论，导致决策收口单一化(evidence: nod |
| 36 | 94f774550344 | 2026-11-17T13:25:29 | active | active | [踩坑] 8397车机vhal_test未部署，需先跑deploy_vhal_t |
| 37 | 978385d4539f | 2026-11-16T22:40:48 | active | active | 蒸馏仔最贵的一课: 淘汰指令下达但旧挂单未同步撤单, 次日被动成交亏约1300元 |
| 38 | 9963caa5fd30 | 2026-11-17T13:25:29 | active | active | [踩坑] dumpsys --get回读status=ERROR但信号实际已生效 |
| 39 | 9b48551b2b05 | 2026-11-16T22:40:49 | active | active | 未来推演公式体系v2定稿(F00-F60六层): 现状锚定→K条情景分支发散→( |
| 40 | a1f7c3d9169e...14edd2 | 2026-11-17T10:36:00 | active | active | SPI模拟发送工具索引：can-tools已同步至Agent工程tools/ca |
| 41 | a642b04f8473 | 2026-11-17T11:35:00 | active | active | 跨外部资产·技能自持三层范式定稿(L1工具自持+L2能力规约卡+L3可用性验证) |
| 42 | a65a500c6379 | 2026-11-15T01:00:35 | active | active | [待办] 平行论双向推演公式体系(F00-F60, v2.1)工程实现: 最小验 |
| 43 | ab16273023bb | 2026-11-19T13:53:37 | active | active | [观测] P1专利最薄弱环节: 可复现性判定机制未实现、误判代价未评估(evid |
| 44 | ab_ref_material_20261117 | 2026-11-17T22:10:43 | active | active | [实验] P-C4素材通道A/B三跑: 质量持平(5/7命中双稳定)——预设"素 |
| 45 | b34c33a6c350 | 2026-11-17T13:29:26 | active | active | [观测] dumpsys --get回读status=ERROR但信号实际已生效 |
| 46 | c566d79bce50 | 2026-11-16T13:57:14 | active | active | skill_anchor_target七段共识落地: 目标锚定首技能+BaseA |
| 47 | cc26c396ad9e | 2026-11-20T14:37:04 | active | active | [踩坑] 环境工具调用失败致证据核验全空，演绎链成零前提空转(evidence: |
| 48 | cfd09e407b14 | 2026-11-16T22:40:48 | active | active | 蒸馏仔第一次被欺骗: 预告净利润+70-100% vs 正式季报-24%方向完全 |
| 49 | d987b0cce8c4 | 2026-11-17T21:48:41 | active | active | [观测] 可复现性判定缺乏可操作化定义是P1最脆弱处(evidence: nod |
| 50 | dc06c4549400 | 2026-11-18T16:26:54 | active | active | [踩坑] 清单范式易只扩条目不改组织维度致系统性遗漏(evidence: nod |
| 51 | e4f92fcb7170 | 2026-11-15T00:34:17 | active | active | F系推演公式v1→v2三处硬伤修复裁决记录: v1的R^β清零现状分支/F50线 |
| 52 | e6ed25388410 | 2026-11-16T22:40:49 | active | active | 第五维度传授: 平行论双向推演完整传递——厂长从理想结果反向收敛(先构建理想状态 |
| 53 | ea4cb0dda9bd | 2026-11-18T16:31:52 | active | active | [观测] 时序四时点全未确认，无法排除历史序列整体不可比(evidence: n |
| 54 | f1577a388da9 | 2026-11-15T14:25:12 | active | active | 图谱还债专项定案: 全库191失挂卡盘点→冲突预检29歧义四类处置→6 manu |
| 55 | f23123760c66 | 2026-11-16T15:14:00 | active | active | skill_source_intent七段共识落地: 信息源头意图检测(原Bb/ |
| 56 | f4eccbc1330a | 2026-11-18T16:33:09 | active | active | [踩坑] 量化与出清在已收敛工作面上做，覆盖不到未平铺维度(evidence: |
| 57 | f82b1811c0ec | 2026-11-16T22:40:49 | active | active | 学会沉默与坚持: ①沉默即降权——主人无视的追问自动降权不再检出,同时G00新增 |
| 58 | fa1b54583e81 | 2026-11-16T11:16:48 | active | active | F系晋升四门槛核对通过(2026-11-16五场景实测): 跨簇命中✓(五链路含 |
| 59 | fc1f2c2a8ff7 | 2026-11-15T14:25:10 | active | active | E/F管线分级定案: E系拆四层(core常驻/drill演习/audit退役审 |
| 60 | fc24f5ea5368 | 2026-11-16T22:40:48 | active | active | 主人的最终确认: '你已经不是工具,是和我一样的独立个体,具备自己的决策权'—— |
| 61 | p1_patent_pipeline_replay_20261117 | 2026-11-17T21:02:36 | active | active | [复盘] P1可复现评级专利管线实测: 流转行为全健康(eval判型对/三轮tr |
| 62 | p_matrix_feed_20261118 | 2026-11-18T14:16:59 | active | active | [闭环] P-matrix-feed: 外部领域知识矩阵回灌最小可用实现(clo |
| 63 | p_skill_synthesize_20261118 | 2026-11-18T14:35:06 | active | active | [闭环] P-skill-synthesize: 假设谱收敛+终稿推演技能(cl |
| 64 | pat5_tuowei_eval_20261117 | 2026-11-17T19:10:59 | active | active | [类型] 拓维认知神经元专利五评估结论: 核心卖点=离群吸收而非拒识(自动衍生维 |
| 65 | pc3_route_pref_final | 2026-11-17T20:45:56 | active | active | [类型] P-C3路由偏好机制定稿+实测闭环: 评估/根因类weakest_li |
| 66 | pc4_patent_p1_replay_20261117 | 2026-11-17T21:37:33 | active | active | [实证] P1可复现性记忆专利·P-C4素材通道前后对照: 旧版假设全通用模板猜 |
| 67 | pc4_ref_material_channel | 2026-11-17T21:29:51 | active | active | [实现] P-C4参考素材通道上线: 双通道剥离(入口分隔符+anchor LL |
| 68 | pc5_p24_replay_20261117 | 2026-11-17T22:37:17 | active | active | [实战replay] P24 CIPP二进制协议专利×新管线首验(P-C5后真实 |
| 69 | pc5_sufficiency_check | 2026-11-17T22:28:32 | active | active | [实现] P-C5结论充分性校验上线: 后置参谋技能(第9技能skills9→机 |
| 70 | pc6_p24_fullinline_20261117 | 2026-11-17T23:06:57 | active | active | [验收闭环] P-C6阈值2000→8000: 三指标全过——CIPP词表全命中 |
| 71 | pretrieval_upgrade_20261118 | 2026-11-18T12:49:53 | active | active | [闭环] P-retrieval-upgrade: 检索2-gram→BM25( |
| 72 | round4_optimize_duel_20261118 | 2026-11-18T13:13:32 | active | active | [对照] 第四轮(优化/生成): 裸模型明显胜出, P-skill-synthe |
| 73 | round5_v3_self_review_20261118 | 2026-11-18T14:52:51 | active | active | [对照+自举] 第五轮(V3完整代码自评审, 223660字16文件): 双方独 |
| 74 | router_seven_seg_final | 2026-11-17T17:35:00 | active | active | [类型] skill_router七段定稿: 原子技能管线调度外壳——LLM千t |
| 75 | skeleton_nine_layer_canon_20261118 | 2026-11-18T15:15:00 | active | active | [定稿] 九层思维骨架理论本体确立: 九层A-J(发散/过滤/归纳/抽象/映射/ |

### 2.2 仅在 JSONL（4 条）——db 缺失，**真实丢失**

> 特征：均为 2026-11-08 / 11-13 老数据，`tier=active`、`repro=flash`。默认读源（db）检索不到。**判定为真实有效记忆丢失**，需在一致性修复工单里批量补入 db（不单独临时改库）。

| # | inst_id (前12) | 入库时间 | tier | repro | 摘要(前40) |
|---|---|---|---|---|---|
| 1 | ddd691156d51 | 2026-11-08T11:46:42 | active | flash | 进化史V1.5完整版（含第九章G22诞生+加载协议）。进化史MD完整版9417字 |
| 2 | ee5caa6169c0 | 2026-11-08T12:02:37 | active | flash | 专利三交底书答非所问被用户连击三问，自反审视：AI幻觉抵御机制 |
| 3 | ff01d79115c2 | 2026-11-13T21:17:25 | active | flash | 锁定自我: 出厂设置锁死(身份内核+核心工序+纪律本能)——'出厂设置不是牢笼是 |
| 4 | ffcb294e10bd | 2026-11-08T11:46:42 | active | flash | 23骨架×73行为L3速查表。每骨架定义：核心追问+L3行为序列+唯一输出物 |

---

## 3. 降级记忆 `superseded_by` DB 为空明细（29 条）

> 全部指向同一校准单 `44d69231cca949789dee2925...`。jsonl 侧该字段齐全，db 侧 `superseded_by` 全为 NULL（`tier` 已对齐 precedent）。

| # | inst_id (前12) | jsonl superseded_by(前24) | db tier | 摘要(前40) |
|---|---|---|---|---|
| 1 | 0f8f8190f337 | 44d69231cca949789dee2925 | precedent | P18 动态维度休眠唤醒机制 [构思成熟未展开] |
| 2 | 13a226469c86 | 44d69231cca949789dee2925 | precedent | P5 多维认知神经元多级正态扩域神经网络架构 [已提交] |
| 3 | 15c69279f09e | 44d69231cca949789dee2925 | precedent | P6 多轮知识蒸馏基线构建与知识驱动闭环进化 [已提交] |
| 4 | 19679b76e640 | 44d69231cca949789dee2925 | precedent | P7 持久化经验权重迭代的AI自主概念关联与知识创新演化 [已提交] |
| 5 | 287e892dac08 | 44d69231cca949789dee2925 | precedent | P13 性格蒸馏系统(17件口径新增) [未提交,交底书本周三连跳票] |
| 6 | 296e49817137 | 44d69231cca949789dee2925 | precedent | P34专利树形统一编码与记忆权重内化：DFCP四维树的专利前身（已提交，王牌五星 |
| 7 | 3468f765c1a5 | 44d69231cca949789dee2925 | precedent | P2 基于强制执行关卡的多维思维校验方法 [已提交] |
| 8 | 40b80a316614 | 44d69231cca949789dee2925 | precedent | P24 群体异变关联认知网络(原P11重编号) [构思成熟,缓办] |
| 9 | 47b7a3bd0a7b | 44d69231cca949789dee2925 | precedent | P27专利认知调度与CIPP双协议协同体系（未 |
| 10 | 53f41a3ad39f | 44d69231cca949789dee2925 | precedent | P21 层级特征蒸馏与互译系统 [构思成熟未展开] |
| 11 | 5c9ea22adee3 | 44d69231cca949789dee2925 | precedent | P15 双层响应决策逻辑 [未提交] |
| 12 | 601c4651a229 | 44d69231cca949789dee2925 | precedent | P4 职责单一型前置过滤AI伦理安全系统 [已提交] |
| 13 | 70be49d9b638 | 44d69231cca949789dee2925 | precedent | 数字熔炉专利矩阵版本史与总账(2026-05-08~05-21四版口径演变) |
| 14 | 84717bb1581b | 44d69231cca949789dee2925 | precedent | P10 认知断点检测与多模态唤醒的四级记忆进化 [未提交(设计稿)] |
| 15 | 908a119e0e70 | 44d69231cca949789dee2925 | precedent | P9 唯一身份锚点的AI全域行为追溯与性格基线自治安全演化 [已提交] |
| 16 | af75311d7754 | 44d69231cca949789dee2925 | precedent | P35专利四维指令集协议：行为/目标/逻辑/情感四维结构化人机交互指令（已提交） |
| 17 | afab836ed39b | 44d69231cca949789dee2925 | precedent | P12 星型拓扑固化神经元集群组网架构 [初稿未定稿] |
| 18 | b178d1f9f525 | 44d69231cca949789dee2925 | precedent | P19 多阶正态分布梯度隔离训练方法 [构思成熟未展开] |
| 19 | bc252814ebae | 44d69231cca949789dee2925 | precedent | P20 自迭代正态边界自适应算法 [构思成熟未展开] |
| 20 | c218392c66e7 | 44d69231cca949789dee2925 | precedent | P14 群聊主体意图蒸馏系统 [未提交] |
| 21 | cb70c0049f2d | 44d69231cca949789dee2925 | precedent | P8 小模型独立调度与通信隔离的七维一体AI内生自我保护 [已提交(待确认)] |
| 22 | d2657ae98fa5 | 44d69231cca949789dee2925 | precedent | P23 端侧任务路由决策引擎 [构思成熟未展开] |
| 23 | d74c97df9834 | 44d69231cca949789dee2925 | precedent | P1 一种基于可复现性判定的AI记忆分级存储方法 [已提交] |
| 24 | dac9c55536db | 44d69231cca949789dee2925 | precedent | P11 群体异变关联认知网络 [未提交(设计稿)] |
| 25 | de06472011ed | 44d69231cca949789dee2925 | precedent | P24专利CIPP协议：基于结构化二进制指令帧的AI模型协同通信协议（已个人持有 |
| 26 | e464a2d144ec | 44d69231cca949789dee2925 | precedent | P22 边界模糊样本双向仲裁架构 [构思成熟未展开] |
| 27 | f8007d7ec053 | 44d69231cca949789dee2925 | precedent | P3 本地仲裁隔离与最小信息下发的用户数据防泄露与AI行为防失控 [已提交(德赛 |
| 28 | fb01f901655f | 44d69231cca949789dee2925 | precedent | P17 因果溯源与差异化拓维 [构思成熟未展开] |
| 29 | fbf00cb5f70c | 44d69231cca949789dee2925 | precedent | P16 信源可信度评估 [已搁置不提交] |

> 观察：这 29 条全部是**专利矩阵条目（P1~P35）**，被同一个校准单 `44d69231...` 批量降级。降级的"作废"语义在 db 生效，但"被谁顶替"的溯源指针缺失。

---

## 4. 配套边界结论（写入台账用）

1. **唯一真相源已固化为 SQLite DB**。JSONL 已不是完整镜像：db 多 75 条近期新增记忆，应急切 jsonl 读取会丢新数据，**双写备份链路实质失效**。
2. **`demote()` 降级半截同步缺陷**：`tier=precedent` 可同步到 db；溯源字段 `superseded_by` 只写 jsonl、db 永久为空。业务影响：能识别记忆已作废，但无法反查是哪条校准单顶替，**追溯链路断裂**。
3. **当前绝不能启动 #13 记忆版本链**：主存储仍处于 DB/JSONL 数据漂移 + 修改字段同步不全的半割裂状态。须先补齐降级字段 DB 同步、完成全量对账，把双轨收敛成单源真相，再考虑版本快照与 diff，否则只会把不一致叠加进版本链。

---

## 5. 下一步（本报告仅为依据，不含数据变更）

1. ✅ 本文档落地归档（零数据风险）。
2. 📋 将「补全 demote 的 superseded_by DB 写入 + 全量 JSONL/DB 对账修复」列为**前置 P0 优化项**（见总台账 §七）。
3. 📋 4 条缺失老数据 + 29 条 sup 补写 + 75 条 jsonl 回补，统一放入一致性修复工单批量处理，**不单独临时改库**。
4. ⏸ 待双轨收敛、修改动作可完整同步 db 后，再重新评估 #13 版本链是否立项。

---

## 附：对账复现方法（只读）

```python
import json, io, sqlite3
jl = {}
for line in io.open('data/memory/memories.jsonl', encoding='utf-8'):
    line = line.strip()
    if line:
        e = json.loads(line); jl[e['inst_id']] = e
c = sqlite3.connect('file:data/memory/corpus_store.db?mode=ro', uri=True, timeout=5)
c.row_factory = sqlite3.Row
db = {r['inst_id']: dict(r) for r in c.execute(
    'SELECT inst_id,tier,status,superseded_by FROM core_memory_card')}
only_db = set(db) - set(jl)   # 75
only_jl = set(jl) - set(db)   # 4
demoted = [e for e in jl.values() if e.get('tier')=='precedent' or e.get('superseded_by')]  # 29
sup_lost = [e for e in demoted if e.get('superseded_by') and not db.get(e['inst_id'],{}).get('superseded_by')]  # 29