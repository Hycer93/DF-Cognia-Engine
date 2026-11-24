# #30 根治入库三道闸 —— 切 on 收尾归档

- 日期：2026-11-24 14:25
- 决策：用户拍板「段七证据已充足，直接切 on 收尾」（不死等 clean_skip_equiv≥8）
- 结论：**DF_SUMMARY_EQUIV_DEDUPE 已切 on（持久化），P0 语义查重从灰度观测转为正式拦截**

---

## 一、纠偏：#30 早已编码完成

台账此前记「📋 待立项(走七段)」，**严重过时**。实际 git 历史显示四道闸全部落地并经段一~段六评审 + 段七第一阶段 dry-run 上线：

```
0edbf79 feat(#30): commit-1 R1查重池口径 + full_hash切换
5b9ae9a feat(#30): commit-2 P1保留detail + R4统一读取封装
ec297df feat(#30): commit-3 P0语义等价查重 + 三态开关 + gate_log
a9d4514 feat(#30): commit-4 P2 corpus↔runtime跨池疑似等价告警
a255b68 feat(#30): commit-5 P3 E4巡检新增三指标 + D组回归
79d1e40 test(#30): commit-6 E组非回归 + S1-S6 dry-run冒烟脚本
51e6bcc feat(#30): 校验脚本加 skip_equiv≥8 门槛判定 + 存档文档补重跑记录
```

四道闸实现位置：
| 闸 | 位置 | 回归 |
|---|---|---|
| P0 语义查重 | `memory_organize.py:158 _p0_equiv_check` + 三态开关 | e30_p0 ✅ |
| P1 derived 保留 detail | `atomic_store:267` derived detail=True | ✅ |
| P2 跨池告警 | `memory_relations.py:190 _cross_pool_warn` | e30_p2_p3 ✅ |
| P3 E4 指标 | `e4_maintenance_patrol.py` 三指标 | e30_p2_p3 ✅ |
| R1 查重池口径 | `load_active_pool` + full_hash 切换 | e30_pool ✅ |

---

## 二、切 on 依据（段七证据链）

| 证据 | 数据 |
|---|---|
| 影子重放全量 | n=280，≥0.95 命中 8/280=2.9%，**全部人工核实真同义，零假阳性** |
| 6 类对抗梯度 | 完全重复 1.0 拦✅ / 同义改写 0.9672 拦✅ / 同主题不同结论 0.866 不拦✅ / 同域不同事 0.8333 不拦✅ / 换实体 0.9226 不拦✅ / 语义无关 0.6225 不拦✅ |
| dry-run 灰度 | 32 条 p0_equiv_dry，26 条 would_skip=true（全为脚本回放），business origin 命中 0 |
| 慢调用 | max 4959ms 均值 2176ms（p0_slow 4 条，无超时风险）|

**关键判断**：clean_skip_equiv=0 是因 `p0_equiv_dry` 仅由真实会话 PreCompact 触发、nightly 不产生，靠日常对话累积不现实；而影子重放 n=280 + 对抗验证已提供等价且更强的证据。故切 on。

---

## 三、执行记录

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 基线核查 | reg query 确认原值 dry-run | ✅ |
| 2 备份 | gate_log + DB → `.bak_30on_20261124_142258` | ✅ |
| 3 切换 | `setx DF_SUMMARY_EQUIV_DEDUPE on` | ✅ 成功保存 |
| 4 注册表确认 | reg query = on | ✅ |
| 5 新进程实证 | SUMMARY_EQUIV_MODE=on，阈值 0.95 | ✅ |
| 6 on 实弹验证 | 见第四节 | ✅ 3/3 正确 |

---

## 四、on 模式实弹验证（tmp 隔离库）

| 用例 | 输入 | 预期 | 实际 |
|---|---|---|---|
| 完全重复 | 现有卡 summary 原文 | replica 拦截 | ✅ replica（sim=1.000）|
| 语义无关 | Python asyncio 事件循环 | stored | ✅ stored |
| 同主题不同事实 | 黄金ETF仓位15%→30%方案B | stored 不误拦 | ✅ stored |

**拦截路径确认**：`atomic_store` L256-261 命中时 `return "replica"`，不进建卡流程，不产生新卡、不落 jsonl，安全幂等。

---

## 五、回退方案（秒级）

```
setx DF_SUMMARY_EQUIV_DEDUPE dry-run    # 退回只记不拦
setx DF_SUMMARY_EQUIV_DEDUPE off        # 完全关闭
```
无需改代码，靠环境变量三态开关（off/dry-run/on）。

---

## 六、回归（全 PASS）

e7_gate / e30_p0 / e30_p2_p3 / e30_pool / e46 / e44 / f40 **全部通过**。

---

## 七、影响面

- **正面**：入库时真实语义等价卡被拦截（不建重复卡），从源头根治「同事件多版本重复卡」。
- **风险控制**：阈值 0.95 宁高勿低（静态断言防改低）；异常降级放行（p0_unavailable → pass，绝不阻断入库）；慢调用有红线告警。
- **注意**：切 on 后**新会话**才生效（setx 对新进程有效），当前运行中的进程仍读旧值，重启后统一 on。

## 八、闭环
- ✅ DF_SUMMARY_EQUIV_DEDUPE=on 持久化
- ✅ 备份齐全（gate_log + DB）
- ✅ on 实弹 3/3 正确，回归全过
- ✅ 回退路径秒级可用
- ⏳ 建议后续观察：切 on 后 gate_log 的 `skip_equivalent_derived` op 计数、E4 `active_same_summary_multi` 是否下降