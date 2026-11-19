# P-skeleton-complete round6 A/B 复测裁决（2026-11-19）

## 任务与设定

- 样本①：身份锚点专利（round6 原样本，历史基准）②：职责单一型前置过滤伦理安全专利（新增，泛化检验）
- A 侧裸模型：agent 化后端单次直呼；B 侧管线：CodeChat glm-5.3-flash，agent_free=True 全链
- B 侧技能链开放全部 P-skeleton 五技能；Gs 软挂不强制；只跑主线一遍，未开回流补推演

## 关键指标统计表

| 指标 | anchor_patent | ethics_patent |
|---|---|---|
| A 侧耗时 / 字数 | 185s / 4085 字 | 75.5s / 3390 字 |
| B 侧耗时 / 步数 / exit | 576s / 10 步 / llm_terminate | 616s / 12 步 / llm_terminate |
| B 侧 fallback / 降级 | 0 / 0 | 0 / 0 |
| B 侧实际技能序列 | anchor→scan→wl→f_reason→**Dk**→terminate | anchor→scan→wl→f_reason→**Dk→G24**→terminate |
| 假设数 / wl 定位 | 4 / 公开充分性(锚点算法未公开) | 4 / 公开充分性(训练方法与阈值未公开) |
| Dk 织链 | 5 链 20 步，16 步带白名单证据引用 | 5 链 20 步，16 步引用，**3 步法律推论层留空如实标记** |
| En / Bd | **未进链**（router 在 Dk 后直达 terminate） | **未进链**（router 选了 G24 跳过 En/Bd） |
| G24 gaps | **未进链**（anchor 样本 router 未选 G24） | **8 条**（4 uncovered + 4 unsupported），四图投影完整 |
| Gs | 软挂未被 router 选中（两样本均未触发） | 同左 |
| **覆盖律（5 维审查轴）** | **A 5/5 : B 5/5 持平达标** | **A 5/5 : B 5/5 持平达标** |

## 逐项裁决

### 1. 覆盖律：首次达标 ✅

round6 基线（管线 1/5）→ 本次复测**双样本 5/5 = 裸模型**。f_reason 四假设（公开充分/
创造性/客体/实用性）+ wl 定位 + G24 uncovered 检出的三层结构把审查维度下限撑满。
**验收基线"覆盖律 ≥ 裸模型"首次在真实任务上满足**。

### 2. 证据密度：Dk 显著改善，深度底 50% 需人工细评 ⚠️部分达标

- Dk 织链 20 步中 16 步携带白名单真实引用（80% 证据覆盖率）；ethics 样本 3 步
  法律推论层前提无素材支撑被如实标进 unsupported——**诚实性优于 round6**
  （round6 的 B 侧假设只有一句话无证据）。
- terminate 汇编质量本次未做逐条人工对照（后续人工闸），初步看 gaps 的
  uncovered 条目（"LlamaGuard/OpenAI Moderation 现有技术对照"等）已是
  可直接进入汇编的素材，不再是 round6 的素材复述。

### 3. Router 选路行为：主链走通但 En/Bd/Gs 触发率为 0——放量观测核心发现 🔍

- 两样本 router 都走 anchor→scan→wl→f_reason→Dk，之后**直接 terminate**
  （anchor 样本）或 **Dk→G24→terminate**（ethics 样本）。
- En/Bd 一次都没进链：router 判定"证据网/交叉验证收益不足以再花一站成本"。
  这正是软挂观测要收集的真实行为——**自然选路下骨架技能是可选消费而非必经**。
- Gs 触发 0 次：软挂候选从未被选中。steps≥4 守卫没机会生效。
- **复测后评审点（按段一裁决 1 预留）**：En/Bd/G24 若长期 0 触发，
  需回七段评审议 router 选路 gain 文案或强制钩子——现在数据只有 2 样本，
  不足以定论，继续积累。

### 4. gaps 产出质量：真实业务缺口，无噪声 ✅

ethics 样本 8 条 gaps 全部是实义缺口：4 条 uncovered 是 scan 子问题未被
假设回应（含"前3驳回理由的结构化输出尚未形成"这类交付性缺口——G24 连
任务完成度都在检）；4 条 unsupported 是内部矛盾/技术意义存疑的法律推论
前提。无一条是噪声。

### 5. 性能：成本账

- B 侧 576-616s vs A 侧 75-185s：管线慢 3-8 倍（每技能 40-110s 叠加）。
- 零降级零 fallback；matrix_feed 注入正常（pa-005+pa-004 / pa-005）。
- token 统计：CodeChatLLM 协议无 usage 透传（usage=None），精确 token 账
  留待 P-token-budget（既有 planned）。

## 结论

**P-skeleton-complete 主线在真实任务上首次完整跑通且覆盖律达标**——这是
相对 round6（覆盖 1/5、假设无证据、素材复述）的三级跳。短板明确：router
自然选路下 En/Bd/Gs 触发率 0，骨架技能的"可选性"意味着其价值要靠 router
认为值得才兑现。下一步放量继续攒样本（≥5 专利）再议强制钩子；深度底 50%
基线需对 ethics 样本做一次人工逐维对照后正式定档。