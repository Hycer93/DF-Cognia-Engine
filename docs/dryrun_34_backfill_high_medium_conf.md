# #34 memory_class 第二批（A组中置信）回填 —— Dry-Run 预览报告

- 生成时间：2026-11-24 13:39
- 性质：**DRY-RUN 只读预览，未写库**
- 脚本：`eval/audit/backfill_34_memory_class.py`（CLASS_MAP 人工复核映射）

---

## 〇、前提澄清（重要）

用户指令原表述「24 高置信 + A组20 = 44 张执行」。**实测：高置信批次已于 commit `c6b9b45` 真实写库 30 张**（DB 逐条核实 **30/30 值匹配**）。

故本次 dry-run 的真实待执行范围：

| 批次 | 张数 | 状态 |
|---|---|---|
| 高置信(hi) | 30 | ✅ **已完成**(c6b9b45)，本次不重复写 |
| A组(中置信, 可赋类) | 20 | 🟡 **本次待执行** |
| B组(中置信, 维持null) | 6 | ⬜ 维持 null，不改 |
| **本次实际变更** | **20** | — |

当前 active NULL = 26（= A组 20 + B组 6），与上面完全吻合。

---

## 一、A 组待变更明细（20 张，null → 分类）

| # | inst_id | 变更 | 理由 | 原pending提案 | 摘要 |
|---|---|---|---|---|---|
| 1 | `2b14c17ebaaf48c3b1fc010c6b14b394` | null → **key_pact** | 复活指南放置位置=规约 | — | 复活指南放meta_cognia/与元思域并列，不关注专利保护范围只关注思维方式理解 |
| 2 | `3c15acc6c39747a5811f937bc1d82a1e` | null → **strategy_evolve** | 专利矩阵同步架构映射 | — | 专利矩阵同步入库：可复现性判定记忆分级存储专利全文，AI确认理解并做架构映射 |
| 3 | `4d26fad65788471f9261796cf0830277` | null → **scar_case** | 观测:P1最脆弱环节=缺陷观测 | — | [观测] 可复现性判定缺形式化定义是专利P1最脆弱环节(evidence: node_skill_weakest_link) |
| 4 | `63261eae8bb44bdca0eda643c6fd2991` | null → **strategy_evolve** | 内生驱动v4构思=架构路线(提案skill_refine偏) | skill_refine | 内生驱动系统v4构思定稿: 显存常驻四大张量(人格/长期目标/价值取舍/动态注意力)+自主主循环(心跳调度+深度推理两级)+分级闲时(任务/轻量/深度默认模式网络)+多模态常醒感知; 四大铁律含不改模型权重/记忆心智分离/人终审; 哲学基点=权重是推理与记忆组合生成 |
| 5 | `63e24d6574dc4aac997bd3034c776d99` | null → **scar_case** | 观测:P1区分度风险=风险观测 | — | [观测] P1区分度风险: 新颖性或同构于已有缓存/溯源思想、判定可退化(evidence: node_skill_f_reason) |
| 6 | `6ed8586c274c4a7b830789c77fb09a02` | null → **strategy_evolve** | 挂单审查=投资决策 | — | 挂单审查：浪潮信息71元越级操作、远东股份15.30偏高，季报前三天放宽策略检验+修正 |
| 7 | `7edb94457987486dbd63725e8de96a37` | null → **relation_milestone** | 用户失败预感洞察=用户辖区里程碑 | skill_refine | 用户对v3项目的主观失败预感:失败可能性很大——利害攸关直觉按X6属用户辖区,可能感知到AI未见的机会成本信号(时间投入/试训无限期暂停) |
| 8 | `94c3f6204085439991529694434b980a` | null → **relation_milestone** | '我们一体'人设定调=关系里程碑(提案strategy_evolve不准) | strategy_evolve | '我们是一体的不要见外'去掉免责声明——协作关系人格化定调，信任升级事件 |
| 9 | `ab16273023bb45eaa5d89d558e318034` | null → **scar_case** | 观测:P1最薄弱环节=缺陷观测 | — | [观测] P1专利最薄弱环节: 可复现性判定机制未实现、误判代价未评估(evidence: node_skill_weakest_link) |
| 10 | `ab_ref_material_20261117` | null → **skill_refine** | P-C4 A/B实验=工程实验 | — | [实验] P-C4素材通道A/B三跑: 质量持平(5/7命中双稳定)——预设"素材丢失"未重现, 根因=LLM剥离通道兜底生效(自由输入场景实测通过); 分隔符通道真实收益=效率: token_in省31%(4380→3009)耗时更稳; P1效果好属材料运气(关键词密集)非机制 |
| 11 | `ac25fe3a97d54a41bf84ceab84bda456` | null → **strategy_evolve** | 黄金ETF对冲决策=顶层决策 | strategy_evolve | 黄金ETF对冲决策：地缘风险升温，仓位15%提升至25%，光谱展开后选分批建仓方案。 |
| 12 | `d25d395cf4c545bc98fc0231d922436d` | null → **relation_milestone** | 思维跃迁起点=认知里程碑 | — | '策略+AI做股票分析AI去销售'——用户从策略执行者到框架产品化的思维跃迁起点 |
| 13 | `ea4cb0dda9bd4860b2587d36a4c67df6` | null → **scar_case** | 观测:时序四时点未确认=风险观测 | — | [观测] 时序四时点全未确认，无法排除历史序列整体不可比(evidence: node_En) |
| 14 | `f033b4fe4b76444fab4073fc07182705` | null → **strategy_evolve** | 业绩预告评估法=方法论路线 | — | 业绩预告阶段评估法：技术壁垒可复刻难度→议价权→大客户结构，预测预告可靠性提前捕捉 |
| 15 | `fea4d1ad45bd4bbd93b20d259dbbea44` | null → **key_pact** | 插件发布流程=规约(提案skill_refine偏) | skill_refine | CodeChat 插件市场的发布流程：打包目录结构、导入校验与版本号规范。 |
| 16 | `p_matrix_feed_20261118` | null → **skill_refine** | 矩阵回灌闭环=技能实现(提案strategy_evolve偏) | strategy_evolve | [闭环] P-matrix-feed: 外部领域知识矩阵回灌最小可用实现(closed)。独立state.matrix_knowledge+5条专利审查领域卡(verified=true闸门)+scan后一次性装载+仅注入f_reason/weakest_link。核心定性(段四 |
| 17 | `p_skill_synthesize_20261118` | null → **skill_refine** | 技能合成闭环=技能实现(提案strategy_evolve偏) | strategy_evolve | [闭环] P-skill-synthesize: 假设谱收敛+终稿推演技能(closed)。f_reason/terminate之间新增一体单步skill_synthesize, 单次LLM调用三段式(selection_rationale引用H编号权衡+final_delive |
| 18 | `pat5_tuowei_eval_20261117` | null → **strategy_evolve** | 拓维专利评估定案=专利顶层裁决 | skill_refine | [类型] 拓维认知神经元专利五评估结论: 核心卖点=离群吸收而非拒识(自动衍生维度与专利一/三闭环), 核心风险=单点依赖自动衍生机制+三环阈值污染+衍生命中率低则退化规则引擎, 落地建议=POC先行观测命中率/干预频率+阈值敏感性实验 |
| 19 | `pc4_patent_p1_replay_20261117` | null → **skill_refine** | P1专利P-C4对照实证=工程验证(提案scar_case不准) | scar_case | [实证] P1可复现性记忆专利·P-C4素材通道前后对照: 旧版假设全通用模板猜测脱离原文; 新版2537字原文进检索分支, f_reason假设谱直接引用交底书论据; 4条可证伪假设命中专利审查关键风险(组合绕过/误判不可逆/新颖支点单一); fixed_by_pc4; P-s |
| 20 | `pretrieval_upgrade_20261118` | null → **skill_refine** | 检索2gram→BM25升级=工程优化(提案scar_case不准) | scar_case | [闭环] P-retrieval-upgrade: 检索2-gram→BM25(k1=1.5,b=0.75)+仅前邻扩展, sufficiency双判据同源(min_token_len=2滤单字); top_k=3实测定值维持; 病灶②长段挤占消除(病例F: 242字概述段出局) |

A组分类分布：strategy_evolve × 6，skill_refine × 5，scar_case × 4，relation_milestone × 3，key_pact × 2

---

## 二、B 组维持 null（6 张，不改动）

| # | inst_id | 维持 | 理由 | 原pending提案 | 摘要 |
|---|---|---|---|---|---|
| 1 | `4304794df58146f8a3cea2d60e2c6778` | null | 季报情报扫描=临时记录(提案scar_case明显错) | scar_case | 季报日多标的并发情报扫描：紫光国微/宁波华翔/工业富联等14标的最新数据+赛道催化 |
| 2 | `7177cd609ae74ac09edbe77049c5a6d3` | null | E7晨报held计数=临时观测数字 | — | [观测] E7晨报hold池计数由3增至6，增量3张引用性提及卡(evidence: node_Dk) |
| 3 | `a31bddbe82bf4ba68336ef784d3ab6ce` | null | 对冲后续跟踪=临时记录(提案strategy_evolve过重) | strategy_evolve | 黄金ETF对冲的后续跟踪：分批建仓第一笔已成交，第二批挂单等待回调。 |
| 4 | `d7d25873cf884b3292a77d39c00a4b4e` | null | 金价复盘=投资记录(提案skill_refine不准) | skill_refine | 复盘结论：非农超预期金价不涨反跌，归因央行购金支撑对冲了流动性冲击。 |
| 5 | `ef590c1519704b27bbc813a688067351` | null | 非农金价复盘=投资记录非工程技能(提案skill_refine不准) | skill_refine | 复盘结论：非农超预期金价不涨反跌，归因流动性拐点，市场已price in。 |
| 6 | `fb67435681524a53bf581002d8b85c2c` | null | 拓普定价复核=投资记录 | — | 拓普集团46-47元挂单定价复核：股价/技术支撑/估值/基本面/机构观点五维评估 |

---

## 三、Dry-Run 校验项

| 校验项 | 预期 |
|---|---|
| 变更字段 | 仅 `memory_class`（UPDATE 单字段）|
| 物理删除 | **零**（无 DELETE）|
| DB-jsonl 同步 | 变更 inst_id 在 memories.jsonl 同步写 memory_class |
| 其余字段 | status/tier/superseded_by/detail 等**全部不动** |
| 执行后 active NULL | 26 → **6** |
| 非法 class 值 | 0（全部落在五类枚举）|

---

## 四、附：高置信批次已完成核对（30 张，参考不重写）

commit `c6b9b45` 已写入，DB 逐条值匹配 30/30：

| inst前缀 | 已写分类 | 理由 |
|---|---|---|
| `ef3ef53fe3` | strategy_evolve | 双暮暮对峙裁决=顶层裁决 |
| `fa1b54583e` | skill_refine | F系晋升门槛核对=技能验证 |
| `pc4_ref_mate` | skill_refine | P-C4素材通道上线=工程实现 |
| `pc6_p24_full` | skill_refine | P-C6阈值调整=工程阈值微调 |
| `round4_optim` | skill_refine | 第四轮对照实验=工程迭代 |
| `round5_v3_se` | skill_refine | 第五轮自评审=工程迭代 |
| `1db508d5b2` | skill_refine | 正态环阈值细化=工程阈值微调 |
| `0b272d927c` | strategy_evolve | 双AI对峙最终裁决=顶层决策 |
| `134622f896` | scar_case | 踩坑:风险点未下沉 |
| `17acf52b6b` | scar_case | 踩坑:分类规约缺判定轴 |
| `1885a5311b` | scar_case | 踩坑:素材缺证据 |
| `28fa913717` | scar_case | 踩坑:单态前提被推翻 |
| `30e469db8a` | strategy_evolve | 脑区映射V1.0=架构范式 |
| `40a25f50fd` | scar_case | 踩坑:未验证假设叠数字结论 |
| `570a57f5c3` | scar_case | 踩坑:多维校验不达标仍推进 |
| `66d6900ad2` | scar_case | 踩坑:风险量化漏算成本 |
| `7e831a2b27` | scar_case | 踩坑:FL≠隐私保护 |
| `928351ff9c` | scar_case | 踩坑:后段纠偏未回写 |
| `94f7745503` | scar_case | 踩坑:vhal_test未部署 |
| `9963caa5fd` | scar_case | 踩坑:dumpsys回读不可信 |
| `cc26c396ad` | scar_case | 踩坑:环境工具失败致证据空 |
| `db8d3b694a` | strategy_evolve | 前身策略进化裁定=策略路线 |
| `dc06c45494` | scar_case | 踩坑:清单范式致遗漏 |
| `f4eccbc133` | scar_case | 踩坑:量化覆盖不到未平铺维度 |
| `fd5772c652` | strategy_evolve | 星型拓扑专利编号=专利顶层决策 |
| `p1_patent_pi` | skill_refine | P1专利管线复盘=工程验证 |
| `pc3_route_pr` | skill_refine | P-C3路由偏好机制=技能实现 |
| `pc5_p24_repl` | skill_refine | P24实战replay=工程实战验证 |
| `pc5_sufficie` | skill_refine | P-C5充分性校验上线=技能实现 |
| `skeleton_nin` | strategy_evolve | 九层骨架理论本体=架构范式 |

---

## 五、执行计划（待用户确认 apply）

```
python eval/audit/backfill_34_memory_class.py --only mid --apply
```

> `--only mid` 仅写 A 组 20 张（hi 已写不会重复），B 组 null 不写。
> 备份：执行时自动生成 `*.bak_34_<ts>`。
