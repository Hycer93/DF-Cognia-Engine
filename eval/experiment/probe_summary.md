# probe_summary.md — 多原子技能探针实验(C1-C5)浓缩归档

> 生成: 2026-11-17 | 源产物: `data/experiment/multi_skill_probe_20261116_232353/`(report.md + steps.jsonl)
> 性质: 归档整理, 不新增实验; 关键结论引用库内记忆摘要, 原始日志见源目录

## 一、实验定位与局限(必须先读)

- **定位**: 原子技能管线首次全链真实 LLM 集成测试——5 项语料真题按**硬编码固定路线**串联执行, 收集语义层问题供人工区分 "prompt/解析 bug" vs "业务范式待迭代"
- **核心局限**: 探针**没有技能路由器**——每任务路线为手工指定(`route: [...]` 字面量), 链路不能自主选路。菜单(next_options)已具备, 消费菜单做动态决策的调度外壳是管线正式启用前的最后一个缺口(2026-11-17 已确认为"技能路由器"后续七段讨论对象)
- 邻接表现状: 实验时 skills=7; 至 2026-11-17 已 8(+skill_memory_gate)

## 二、任务终态一览

| 任务 | goal 摘要 | 路线(硬编码) | 步数 | 终态 |
|---|---|---|---|---|
| C1 | 11-13 裁决零入库根因 | anchor→scan→weakest_link | 3 | route_exhausted |
| C2 | topic 锚挂载质量弱点 | anchor→scan→weakest_link→terminate | 7 | terminate 收敛 |
| C3 | 8397引擎与DF-Cognia关系 | anchor→scan→source_intent | 10 | route_exhausted |
| C4 | F系 R^β 清零分支问题 | anchor→scan→f_reason | 13 | route_exhausted |
| C5 | 紫光国微建仓分歧 | anchor→scan→f_reason | 16 | route_exhausted |

## 三、各任务关键结论(引用库内记忆, 不复制原始日志)

- **C1 断档复盘**: 根因定位为**入库触发条件静默失效 + 全链无告警/无对账/无当日监控**, 零入库延迟至次日人工盘点才暴露。结论已入库存档, 并直接催生 E0 topic 锚开关守卫(硬 FAIL 不静默断层哲学)
- **C2 挂载质量**: 产出 degraded(JSON 解析失败 + 上游上下文退化), **已修复**——见下节
- **C3 概念辨析**: source_intent 判定"内部概念澄清、无外部信源", 暴露路线适配问题——内部概念/术语辨析类**不应切 source_intent**。分流规则已记档: 路线选择器增加规则"目标属内部概念辨析、无外部公开信源 → 禁止切 source_intent"(归入范式迭代池, 待技能路由器七段讨论时一并消化)
- **C4 F系推演**: f_reason 生成五假设并列陈列(语义错配/等价性破坏/数值判断缺陷/作用域错位/修法前提可疑), 符合"只陈列不裁决"规约。此样本后纳入 f_reason 真实 LLM 探针验证(T5 四假设达标)
- **C5 建仓决策**: 四方向假设谱(估值/技术支撑/执行匹配/仓位风险), "判断留人"措辞自发达标——开放决策类不越权裁决的口径守住了

## 四、C2 问题与修复闭环(唯一真 bug)

- **现象**: skill_scope_scan JSON 解析失败, 上游 summary/sub_questions 为空 → prompt 上下文退化
- **根因**: LLM 在 JSON 字符串值内输出**未转义裸换行 chr(10)**, `json.loads` 默认 strict 抛 "Invalid control character"
- **修复**(commit `6780cc0`): `extract_json` 两级容错——strict 失败后降级 `strict=False` 再试; 二次失败才降级
- **Mock 固化教训**: 首版 Mock 用 `"\n"` 字面量是**假用例**(合法转义复现不了 strict 报错), 必须用 `chr(10)` 真实换行+前置断言确实触发 strict 报错; 回归 21P 全绿

## 五、局限声明与后续

- 本探针**不能**证明管线可自主运行——固定路线 ≠ 路由能力
- 后续依赖顺序: 技能路由器七段讨论(菜单消费+约束循环) → f_reason(已上线, 双准入全过)与 memory_gate(端到端+E11 冒烟过)经路由器接入正式链路
- 本报告归档后, 源目录 report.md 第四节"人工标注区"职责终结(全部现象已有归属: 1 真 bug 已修 + 1 范式待迭代已记档)