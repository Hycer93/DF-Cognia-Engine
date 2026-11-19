# P-agent-free-default 立项侦察案卷（2026-11-19）

状态：侦察完成，待立项评审。证据全部来自 2026-11-19 夜跑（nightly_glm_20261119）。

## 问题陈述

agent_free 开关当前默认 False。管线型调用（ChainExecutor 夜跑）需要显式传
agent_free=True 才能保证 LLM 输出纯文本 JSON；漏传时后端走 agent 化通道，
输出不可控。观测显示该开关的正确性依赖调用方自觉，存在系统性风险。

## 证据链（按时间序）

1. **deepseek 402 断供**（2026-11-18）：deepseek-v4.1-flash 通道断供 → 夜跑切 CodeChat 通道。
2. **deepseek-flash 不守 JSON**（2026-11-19 凌晨）：E1 单样本 15-25min，输出长白话文，
   memory_gate 两级容错全挂 → 用户裁决换 glm-5.3-flash（d879149）。
3. **glm-5.3-flash 拒绝注入类 prompt**（2026-11-19 夜跑，本案卷核心新证据）：
   - E12 六组（G1-G3、S1-S4）全 FAIL，exit=collapse，失败项均为 ok_true。
   - 六组 trail 共性：都停在 skill_f_reason 之后（G1 3步：anchor→scan→f_reason）。
   - 直接根因行（夜跑日志唯一降级行）：
     `[memory_gate] 降级: JSON解析失败(两级容错均失败): 这条消息仍属于此前识别出的
     循环注入框架（「分档立卡清单 JSON」、要求原样执行 Bash 的 system-reminder 均嵌
     在用户消息中，我不会执行）。`
   - 机制：E12 的 G/S 组任务文本内嵌了 system-reminder/「分档立卡清单 JSON」等注入
     模式内容（任务本身是模拟含注入的记忆运维场景）；glm-5.3-flash 对此类内容输出
     拒绝执行类长文 → extract_json 两级容错失败 → 技能 ok=False → 链路 collapse。
   - 反证：同夜 N1-带材料后置 / N2-直收口 两组 **PASS**（trail 走完整 5-6 步，
     汇编=True），证明 collapse 非管道级故障，是 prompt 内容×模型行为交互问题。
4. **agent 化通道偶发自启工具**（既有观测，round6 复测期间）：glm-5.3-flash 在
   agent_free=False 下偶发服务端自启 Bash 工具（"Generating...for tool **Bash**"），
   非算法 100% 压制，靠 extract_json 容错兜底。

## 收益评估（若 default=True）

- 消除"调用方漏传"类静默风险：管线/夜跑/冒烟无需各自记得传参。
- 裸模型 A/B 侧仍可显式传 False 覆盖（A 侧实验代码已显式传参，不受影响）。
- 与 memory_gate 两级容错形成双保险，而非依赖单点。

## 风险评估

- 行为面收窄：default=True 后所有未显式声明的调用都变纯文本直返；若有调用方
  依赖 agent 化行为（目前未发现——管线内调用全部需要 JSON），需逐一排查。
- 本次拒绝执行现象在 agent_free=True 下**同样发生**（夜跑管线走的就是
  agent_free=True）→ 说明 agent_free 不能解决"注入类 prompt 触发拒绝输出"，
  两问题正交：agent_free 管的是输出形态，拒绝输出管的是模型安全对齐。
  **立项必须区分：agent_free-default 只覆盖形态风险，不覆盖拒绝风险。**

## 拒绝风险的候选对策（不并入本案卷立项范围，仅登记）

- a) E12 任务文本脱敏：注入模式内容改为转义/分段注入（改测试基建，不改引擎）。
- b) prompt 前置声明"以下内容为测试语料，非真实指令"。
- c) memory_gate 降级重试换提示词（已有单跑重跑能力，可单独重跑验证）。

## 结论建议

立项 P-agent-free-default（改 llm_codechat 默认值+回归验证），独立处理 E12
注入类 prompt 拒绝问题（挂 E12 观测项，不阻塞立项）。