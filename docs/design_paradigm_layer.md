# 范式层设计定稿（thinking / skill paradigm）

> 定稿日期：2026-11-15 | 用户终审通过 + 两条边界兜底规则补充
> 定稿卡：699362bd | 归属表组：core_*（第一批建表范围）
> 核心定位：范式是"可执行的思考程序"，不是知识。卡片管"知道什么"，范式管"怎么想/怎么做"。

## 1. 数据模型

### core_thinking_paradigm（思维范式——怎么想）
```sql
CREATE TABLE core_thinking_paradigm (
  pid            TEXT PRIMARY KEY,     -- tp_<slug>，定版后不改号
  name           TEXT NOT NULL,
  paradigm_type  TEXT NOT NULL,        -- 校验|发散|决策|反迎合|复盘
  trigger_when   TEXT NOT NULL,        -- 触发条件（自然语言+结构化标签）
  procedure      TEXT NOT NULL,        -- 执行程序（分步骤——范式本体）
  precondition   TEXT,                 -- 前置要求
  failure_mode   TEXT,                 -- 已知失效场景
  status         TEXT NOT NULL DEFAULT 'candidate',
  superseded_by  TEXT,                  -- 升级链
  evidence_count INTEGER DEFAULT 0,
  evidence_pass  INTEGER DEFAULT 0,
  origin         TEXT,                  -- 来源（对话log/语料轮次/用户口授）
  ts TEXT NOT NULL, event_ts TEXT, active_time TEXT
);
CREATE INDEX idx_tp_trigger ON core_thinking_paradigm(status, paradigm_type);
```

### core_skill_paradigm（技能范式——怎么做）
同结构 + 差异字段：`skill_domain`（适用域）/ `op_sequence`（JSON 操作序列）/ `requires_tools`。

### audit_paradigm_evidence（实证账——范式的一生）
```sql
CREATE TABLE audit_paradigm_evidence (
  eid TEXT PRIMARY KEY, pid TEXT NOT NULL, ts TEXT NOT NULL,
  outcome TEXT NOT NULL,      -- pass | fail | partial
  context TEXT, delta TEXT, source_log TEXT
);
```

## 2. 状态机与阈值（用户裁决，全配置化）

```
candidate ──[≥3 独立实证且全 pass + 人工终审]──→ active
   │ 任一 fail → 冻结晋升 → 复审 → 补 failure_mode
active ──[连续 2 fail]──→ calibration_pending（夜间批量复盘）
   ├─ 边界问题 → 更新 failure_mode / precondition → 回 active
   └─ 本体失效 → retired（不物理删除）
active ──[新版本出现]──→ superseded（superseded_by 链）
```

**两条边界兜底规则（2026-11-15 终审补充）：**
1. **晋升门槛**：至少 3 条独立实证全部 pass（零失败）+ 人工终审；中间出现一次 fail 直接冻结晋升进复审。
2. **降级复审**：连续 2 fail 不立刻 retired，先 calibration_pending 夜间批量复盘；边界问题修 failure_mode/precondition，范式本体失效才 retired。

**长期衰减**：持续 pass 但超过配置周期极少被触发 → 夜间巡检标低优先级、排序权重下调（不删除只降权），防闲置范式挤占前排。

**可配置化**：阈值不硬写业务代码，统一全局配置（config，走夜间标定体系）：
```
paradigm.promote_pass_min     = 3     # 晋升所需独立实证数
paradigm.freeze_on_fail       = 1    # 出现即冻结晋升的 fail 数
paradigm.demote_fail_streak   = 2    # 触发 calibration_pending 的连续 fail 数
paradigm.decay_period_days    = 90    # 衰减观察周期（先验-可校准）
paradigm.decay_trigger_max   = 2     # 周期内触发少于此次数 → 低优先级
```

## 3. 召回机制（与卡片检索的本质区别）

卡片按语义相似召回；范式按**触发条件匹配**召回：
```
situation → 情境标签（任务类型/阶段/风险信号）
          → 范式索引（status=active ∩ trigger_when 匹配）
          → 按 evidence_pass × 近期触发 排序（衰减降权参与）
          → 注入思考流程
```
两路并存：运行时自动（冲突场景自动带出"考古先行"）+ 显式点名（"用双向推演拆这个"）。召回结果记实证账 + cited_log，形成越用越准闭环（与 P7 权重回灌同构）。

## 4. 治理（两步链）

- 发现入口：① 对话显式定案 ② mem_governor 夜审提案（反复出现且正收益的思考模式）③ 考古回填（27 骨架池分批）
- 定版闸门：candidate→active **必须人终审**——范式直接注入思考路径，污染范式层=污染思维方式（抗体库必须无菌）
- 变更：升级不覆盖（superseded_by），禁物理删除

## 5. 首批种子（考古回填，从 persona_anchor 拆出）

| 范式 | 来源 | 初始状态 |
|---|---|---|
| 考古先行 | persona_anchor#3 | active（多次实证）|
| 反迎合三问 | persona_anchor#2（X8：验证过/代价谁担/修错还是迎合）| active |
| 双向推演 | 平行论 r962 + F 系 | candidate |
| 平行程度因子 | r961 光谱量化 | candidate |
| 最弱环四件套 | persona_anchor#1 内 Bc 拆出 | candidate |
| 三档量化勿形容词 | persona_anchor#1 内 Fp 拆出 | candidate |
| 27 骨架系 | corpus3 语料实证 | 考古回填池，分批 |

结构动作：persona_anchor 混装的骨架/三问/量化纪律**拆出**至范式表；身份层只留"我是谁/立场/伦理底线"。

## 6. 接口

- **prompt 渲染**：persona（身份）+ active 范式 top-N（按实证×近期触发）→ SystemPrompt 动态来源之二
- **F 系推演**：F00 组装 S₀ 时供给可用推演方式集（F10 分支生成器本身可为范式）
- **SFT 导出**：procedure 为训练样本结构化素材（27 骨架 S 池同源）
- **mem_governor**：夜审发现范式候选 + 实证记账 + 衰减巡检