# design_skill_router.md — 技能路由器七段定稿（2026-11-17）

> 组件: `cgraph/router_atomic.py` (RouterAtomic + RouteReport) | 范式登记: sk_router → candidate
> 定位一句话: **RouterAtomic：消费邻接表的刚性调度循环——冷启动硬编码锚定，LLM 千 token 瘦身选路，四通道冗余退出，乒乓软防护，异常只审计不修表，永远返回可用 state；编排钩子归 executor，准入双门槛（12 单测+E12 冒烟），八技能零改动。**

## 分歧裁决总表

| 段 | 分歧 | 裁决 |
|---|---|---|
| 一 | ①组件落点 | a: cgraph/router_atomic.py 新文件, 编排层不进 entries 不继承 AtomicSkill(自指规避), 钩子留 executor |
| 一 | ②与骨架链路关系 | a: 并行独立, engine 分流属后续议题 |
| 一 | ③(并入段二) | 四步循环定稿: 取菜单→LLM选chosen→刚性校验→执行记trail |
| 二 | ①冷启动 | a: anchor_target 硬编码入口, 不走 LLM(goal_anchored 契约保障) |
| 二 | ②决策记录 | b: trail 单条目扩展 {skill_id, chosen, candidates, reason, step_summary, result_fields, ts}, 复用 ThinkState 预留结构, memory_gate/terminate 零改造 |
| 二 | ③非法 chosen | 单次回退菜单首个非 terminate+reason 备注; 连续 2 次→强制 terminate(信任崩塌) |
| 三 | ①LLM 调用形态 | a: 自建轻量 prompt 不走基类生命周期; 上下文瘦身=goal_anchored+summary+skill_id 序列+steps 计数, 千 token 级 |
| 三 | ②三类故障 | LLM 空返回/解析失败→回退+计数; 菜单空→直接 terminate 零调用; 技能降级→透传不补救 |
| 三 | ③审计归属 | c: 仅异常审计, op=orchestrate:skill_router |
| 四 | ①退出信号 | c: 四通道并联(terminate_suggest / llm_terminate / collapse / max_steps); is_exit 仅校验器豁免标记不进运行时 |
| 四 | ②memory_gate 对接 | b: executor.run_atomic 薄入口, trail 映射伪 node_outputs{skill_id: step_summary}, 复用 _run_memory_gate+commit_green_cards |
| 四 | ③自环防护 | b: chosen≠上一步 + 单技能≤2 次, 违反走回退计数, max_steps 兜底 |
| 五 | ①契约 | b: 类 RouterAtomic(llm, adjacency), run(state)->ThinkState, 暴露 steps_log |
| 五 | ②菜单清洗 | a: prompt 组装前过滤 __planned__/邻接表外项, LLM 不可见; 非法项触发 menu_anomaly 审计; 清洗后空→直接 terminate |
| 五 | ③输出 | RouteReport{ok, exit_channel, final_state, steps_log, fallback_count}; 内部异常兜底 ok=False+残 trail, gate 后置照走 |
| 五 | ④单测 | 12 项冻结清单(见测试矩阵) |
| 六 | ①审计 payload | a: kind 封闭四枚举 route_fallback_parse/route_fallback_illegal/route_collapse/menu_anomaly, 轻量不含 reason 全文 |
| 六 | ②steps_log | a: 探针 steps.jsonl 超集 {step, skill_id, chosen, candidates, reason, fallback, degrade, secs, ts}, 剔除 raw/snapshot |
| 六 | ③nightly | a 变体: 独立 eval/core/e12_router_smoke.py 挂 E11 后, E11 本体不动 |
| 六 | ④指标口径 | 回退率=fallback_count/总路由步数; 崩塌率=collapse 任务数/总任务数; 阈值归 E7, router 只产数据 |
| 六 | ⑤审计边界 | 谁执行谁审计, 调度异常归编排; 同表不同 op 前缀, 零新表 |
| 七 | 红线+准入 | R1-R10 全锁; 组件双准入(单测+E12)豁免盲评; 遗留全进 planned |

## R1-R10 边界红线

R1 不进邻接表不继承基类; R2 只读邻接表不修改; R3 __planned__ 绝不进候选; R4 冷启动不走 LLM; R5 不干预技能内部; R6 零 DB 写零记忆写入; R7 决策素材瘦身不喂 sub_questions; R8 无阈值告警逻辑只产信号; R9 审计只记异常; R10 永远返回可用 state。

## 准入分档（范式档案新规）

- 推理原子技能: 单测 + nightly 冒烟 + 20 样本盲评（三准入）
- 编排调度组件: 单测 + nightly 冒烟（双准入）

## 改动面承诺（已执行）

**新增**: router_atomic.py / test_skill_router.py(12 用例) / executor.run_atomic() / eval/core/e12_router_smoke.py / nightly STEPS+E12 / 范式登记 sk_router(candidate) / 邻接表 global.router 元信息(不进 entries)
**零改动**: 8 原子技能、邻接 entries、atomic_skill.py 基类、memory_gate 本体、E11 本体、audit_topic_log 表结构

## 代码骨架

```
cgraph/router_atomic.py
  @dataclass RouteReport: ok/exit_channel/final_state/steps_log/fallback_count
  class RouterAtomic(llm, adjacency):
    run(state) -> ThinkState          # 冷启动 anchor_target(不调LLM) + while 循环
    _clean_menu(next_options) -> list # 过滤 __planned__/表外项; 异常→menu_anomaly审计; 空→terminate
    _route_llm(state, menu) -> (chosen, reason)   # 自建prompt千token级; 空返回/解析失败→(None,降级)
    _validate(chosen, menu, state) -> bool|fallback   # ∈菜单 ∧ ≠上一步 ∧ ≤2次 ∧ steps<max
    _audit(kind, chosen, candidates, step)         # op=orchestrate:skill_router 仅异常
  executor.py
    run_atomic(user_input) -> ...     # router跑网 → trail映射伪node_outputs → _run_memory_gate + commit_green_cards
```

## 测试矩阵

**单测 12 项(冻结)**: ①正常流转trail序列 ②LLM空返回回退+计数 ③非法chosen回退+计数 ④连续2次崩塌collapse ⑤自环拦截(=上一步) ⑥自环拦截(第3次) ⑦四通道逐一mock触发 ⑧冷启动零LLM调用 ⑨__planned__不进prompt ⑩清洗后空菜单零LLM直接terminate ⑪run_atomic端到端伪node_outputs+mock gate ⑫RouteReport schema

**E12 冒烟断言(真实LLM)**: ok=True / exit_channel∈合法枚举 / steps≥3 / fallback_count≤1 / trail 单技能≤2 次

## 遗留(全 planned, 本次零实现)

C3 分流规则(LLM 选路天然消化大半, 观察后定) / engine 两网归一分流 / skill_asset_import / 审计升全量流水(需统计时无损升级)

## P-C3 附录: route-preference 偏好机制(2026-11-17 七段定稿+实测迭代)

**一句话**: eval/root_cause 类任务在 weakest_link 后, prompt 偏好提示优先+最多一次软否决拦截 terminate 转 f_reason; 六条件互锁(类型/上一步/菜单/一次上限/访问上限/预算≥2); 拦截付步数成本; kind=c3_preempt 留痕; fail-safe 退化为无偏好基线; R11/R12 封闭受控先例。

**关键实测事件(2026-11-17 晚)**:
- E12 首跑三题全 WARN → 根因: 真实邻接表 weakest_link.next_options 仅[terminate,__planned__], **无 f_reason 边**——C3 六条件③永不成立, 提示与拦截全不生效。单测 mock 菜单自构造含 f_reason, 掩盖了数据源缺失(mock 不能替代真实 LLM+真实邻接表的 E 系冒烟——教训入 planned 观测)。
- **entries 一次性豁免(commit 965ad00)**: 仅 weakest_link.next_options 追加 skill_f_reason 一处; 性质=数据补边非规约变更(语义: 定位薄弱点后允许假设谱推演, 设计时遗漏的思维通路); 邻接校验器重跑 PASS(skills=8); 未命中 C3 时 LLM 照常可选 terminate, 原行为零改变。**此豁免不构成先例——entries 零改动承诺在其余所有场景继续有效。**
- 补边后 E12 三样本全 PASS 零 WARN: G1/G2 走 f_reason(均为提示生效 LLM 主动采纳, preempt=0——软引导即够, 拦截兜底未动用); G3 干净收口无 f_reason。
- A/B 复测(B 侧完整 run_atomic 含 terminate 汇编): G1/G2 trail 含 f_reason 且终态含≥4 条竞争假设带验证路径; G3 无 f_reason。对照段六判定表=**修复达标档**(f_reason 触发+假设谱产出+负样本纯净)。深度仍薄于骨架 A(汇总篇幅), 归 P-sufficiency 待办不阻塞。

**R11**: 受控拦截先例封闭——仅 C3 场景(六条件全满足+一次上限+付步数成本); 新增触发类型/可干预通道/放宽上限/豁免成本, 一律必走新七段, 禁止直接编码。
**R12**: task_type 五枚举+词表(根因/分析/审查/复盘, 只归 root_cause)+首轮锁定, 全封闭; 增删必走七段。


## P-C4 附录: 参考素材通道(2026-11-17 七段定稿+验收闭环)

**一句话**: 长goal附属参考材料经双通道(入口`---ref_material---`分隔符确定性切片优先, 无标记时anchor_target LLM识别)剥离进state.ref_material; 消费侧阈值分流(≤2000直塞/>2000走ref_retrieval 2-gram检索); 8技能token_in/out纯观测埋点; R13素材只读红线+R14两阈值封闭(200K写入上限/2000分流阈值)。

**病灶重定性**: 非token截断——锚定协议把2719字goal压缩成单句goal_anchored后原始材料被整链丢弃(P1专利实测: f_reason假设全为外部猜测)。加预算无效, 治根=素材通道。

**R8 entries受控变更判例**: state_inputs/outputs扩展≠965ad00补边豁免——邻接表schema职责内合法演化; 配套校验器新增G检查(check_state_inputs: 声明键必须存在于ThinkState, 硬exit 1)。**G检查首跑即逼出3个存量隐性漂移**(weakest_link/hypotheses/source_intents动态setattr从未声明), 全部显性化——隐性契约显性化的首次战果, E12 mock教训从纪律固化为机制。

**R7判例**: 瘦身规约约束中间产物膨胀; ref_material是用户原始输入保真通道, 二者正交。

**验收(4.7口径全过)**: 33单测全绿(31存量+R1-R9+R0) / 校验器PASS(真实邻接表) / E12六样本PASS(G1-G3兼容+S1命中3/5+S2命中3/5+S3不崩) / A-B: G1/G2同构+G3决策自主f_reason(WARN观察, preempt=0)+S1病灶对拍0→3词表命中。

**断言域教训**: S1词表断言首版查terminate final_output全FAIL——汇编仅是60字截断summary拼装, 材料引用活在f_reason假设谱(result_fields)。修正为查全trail产出。terminate汇编对长输入仍单薄(P-sufficiency佐证+2)。

**token埋点定位**: 纯观测不干预; 熔断/预警/硬预算/累计统计全部后置(planned P-token-budget收窄版)。
