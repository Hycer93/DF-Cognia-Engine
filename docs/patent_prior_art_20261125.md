# 专利现有技术检索存档：一种车载端云交互方法及系统

> 立档 2026-11-25。用途：**存档备用**（收到 OA 想救 / 复审 / 写相关专利避开对比文件时取用）。
> 定性：**非正式 FTO/新颖性检索**，仅为可及学术库范围内的快速摸底。

---

## 一、标的信息

| 项 | 内容 |
|---|---|
| 标题 | 一种车载端云交互方法及系统 |
| 申请号 | 202611329072.X（**校验位存疑**：模11算法应为 8，用户提供 X；`2026`=申请年、第5位`1`=发明专利，结构与"过初审"吻合） |
| 稿件文件 | `Patent.2026000405-专利稿件-创合-陈石燕-0819.docx`（235段，权利要求1-10 + 说明书S100-S600 + 图1-3） |
| 当前状态 | **已过初步审查**。通知书抬头（"初审合格" vs "进入实审"）未确认；主动修改窗口状态未知 |

## 二、本专利技术方案拆解（权1 要素）

**核心主张**：车机端本地把自然语言脱敏成「最小参数骨架」当**唯一真值标尺**，云端大模型只当算力外包，任何超出原始意图的「增量衍生内容」必须**用户确权 + 本地仲裁器双校验**才落地车控。

| 技术特征 | 要素 |
|---|---|
| F1 | 端侧小模型语义解析 + 云端大模型（端云分工） |
| F2 | 本地标签映射 + 前置隐私脱敏（语义标签→无标识业务参数，绑定关系不出车） |
| F3 | 分层树形协议解析（场景/行为两级根节点） |
| F4 | **最小参数骨架（场景/行为/参数三层）作为唯一真值标尺** |
| F5 | **以标尺逐层反查云端应答，识别超出范围的「增量衍生内容」** |
| F6 | **增量内容强制用户确权（同意/修改/拒绝）** |
| F7 | **本地仲裁器：权限三层校验 + 硬件状态二次校验** |
| F8 | **云端大模型物理无车控接口（部署层排除接口注册）** |
| F9 | 进化提案机制（本地统计→待确权→写入本地权限库） |

## 三、检索到的现有技术（威胁清单）

> 检索源：OpenAlex 学术库（Google Patents / Espacenet / CNIPA / Semantic Scholar 均被墙或限流，**专利库与中文库未覆盖**）。

### 3.1 高威胁（同一场景+同一问题）

| # | 文献 | 年份 | 与本案冲突点 |
|---|---|---|---|
| T1 | Enhancing Privacy-Preserving Capability of In-Vehicle LLM-Empowered Agents on Contrastive | 2026 | 车载LLM Agent 隐私保护，同场景同问题 |
| T2 | Privacy-Preserving Offloading for Large Language Models in 6G Vehicular Networks | 2025 | 车载场景 LLM 卸载 + 隐私保护，端云分工思路 |
| T3 | Collaborative Inference and Learning between Edge SLMs and Cloud LLMs: A Survey | 2026 | **端侧SLM+云端LLM协同已被写成综述并归纳统一分类法**（大框架新颖性基本归零的强证据） |
| T4 | On the security and privacy of LLMs in Mobility | 2026 | 移动出行 LLM 安全隐私综述，问题定义已公开 |

### 3.2 中高威胁（部分要素重合）

| # | 文献 | 年份 | 冲突点 |
|---|---|---|---|
| T5 | Compromise Is Not Consequence: Evaluating Task-Scoped Authorization in LLM Agents | 2026 | **任务范围授权**，与"以原始基准指令为界判越权"（F4/F5）高度相关 |
| T6 | When Lower Privileges Suffice: Investigating Over-Privileged Tool Selection in LLM Agents | 2026 | Agent 越权工具选择，与 F7 权限校验相关 |
| T7 | AgentAuditor: Human-level Safety and Security Evaluation for LLM Agents | 2025 | Agent 越权/安全失败检测审计（F5/F6 相关） |
| T8 | TraceProbe: An Execution-Based Benchmark for Diagnosing Safety Failures in LLM Agents | 2026 | Agent 安全失败诊断 |
| T9 | Constitutional Black-Box Monitoring for Scheming in LLM Agents | 2026 | Agent 越权行为监控 |
| T10 | A Survey on LLM security and privacy (Good/Bad/Ugly) | 2024 | 通用 LLM 安全隐私综述（高被引 2137） |

## 四、创造性风险评估（三步法口径）

**结论：通过率下调至 45-55%。核心风险从"赌点"变为"实打实的挑战"。**

- **大框架已饱和**：F1（端云分工）+ F2（脱敏）已被 T1/T2/T3 覆盖，属公开成熟范式。
- **可争的区别特征（存活空间）**：
  - **F4+F5 组合**：「以本地生成的最小参数骨架为**唯一真值标尺**反查增量」——检索未见完全相同表述，**最可能的区别点**。
  - **F4+F5+F6+F7 的特定组合**：标尺反查 → 增量强制用户确权 → 本地仲裁器双校验 → 车控闭环。
- **答复逻辑（若收到 OA）**：
  1. **反常规论证**：常规是"云端输出过事后黑名单过滤器"，本案是"本地预置真值基线，云端任何偏离都算增量"（事前白名单）——**两种相反思路**，切换本身可能非显而易见。
  2. **技术效果**：把"云端自由生成"变为"以用户原始意图为硬边界"，消除 AI 自主追加车控动作的风险。
  3. **技术偏见克服（若有）**：业界认为"限制云端输出会牺牲LLM能力"，本案"限执行不限推理"可能构成克服偏见。

## 五、检索的硬缺陷（务必知悉）

| 缺陷 | 影响 |
|---|---|
| Google Patents / Espacenet / CNIPA 全被墙 | **专利库完全没查**，只覆盖学术论文 |
| 中文关键词检索失效（Bing/百度降级、patenthub 需登录、xjishu 路径失效） | **中文专利文献未查**——而本案是中国申请，中文对比文件最关键 |
| Semantic Scholar 429 限流、PatentsView 301 | 学术覆盖面不全 |

> **准确表述**：在可及学术文献范围内，大框架新颖性已很弱；但**专利库（尤其中文）未查**，最终创造性结论必须补充专业检索才能定。

## 六、建议动作（存档备查，非当前任务）

1. **权1 主动收缩**（若仍在主动修改窗口）：把 F4+F5 显式提到特征部分，压缩 F1/F2/F3 前序。
2. **补做专业检索**：至少查 CNIPA（国知局）+ 中文关键词（"车载 大模型 脱敏 越权"等），这是本案主场。
3. **答辩弹药预置**：T1/T2/T3 几乎必被引用，提前备"标尺反查 vs 事后过滤"的区别论证。

## 七、可复现的检索命令（curl 可用源）

```bash
# OpenAlex 精准检索（可用）
curl -s "https://api.openalex.org/works?search=in-vehicle%20LLM%20privacy&filter=from_publication_date:2022-01-01&per-page=8&select=title,publication_year,cited_by_count"

# 取摘要（abstract_inverted_index 需重建）
curl -s "https://api.openalex.org/works?search=<term>&per-page=8&select=title,publication_year,abstract_inverted_index,doi"
```

> 备注：本机 WebSearch 令牌已过期；Google Patents/Espacenet/CNIPA/Bing/百度均不可用；仅 OpenAlex + curl 直连可用。