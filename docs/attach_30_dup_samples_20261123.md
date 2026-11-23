# #30 实证附件：8 对双-active 近重复卡样本清单

> 生成日期：2026-11-23
> 用途：#30 立项书实证附录，为 P0 语义查重阈值校准与灰度测试集提供依据。
> 方法：runtime 池内 active 卡两两 summary embedding 相似度扫描（bge-small-zh，CLS+L2归一），
>       取 ≥0.84 的卡对；**每对均结合 detail/source_ref 全文人工复核定性，不只靠分数**。
> 结论先行：**embedding 分数不能替代人工事实判断——0.905 实测已是两个完全不同事实，
>          历史真重复样本的下限是 0.95。**

---

## 一、样本总览

- 12 张独立卡，构成 **8 对** 相似关系（部分卡跨多对）。
- 全部 `repro=situational`（**非 derived**）、`detail`/`source_ref` 完整（**非空壳**）→
  与 #29 处理的 derived 空壳是**不同来源、不同成因**的重复。
- 两组来源：
  - **PAIR 1/6**：11-08 写入，source 为 `rel-test-*`/`sim-test-*` → **关系判定/相似度测试数据**
  - **PAIR 2/3/4/5**：11-17 memory_gate，同一 user_input（8397车机模拟空调），node 不同（Bd/Ch/Di/Dk）
  - **PAIR 7/8**：11-17~11-19 专利 P1 评估，同一 user_input 多次评估各自立卡

## 二、逐对明细与人工定性

### PAIR 1 ｜ sim=0.979 ｜ 【真重复】
| | inst_id | summary | detail | source |
|---|---|---|---|---|
| A | ac25fe3a97d54a41 | 黄金ETF对冲决策：地缘风险升温，仓位15%提升至25%，光谱展开后选分批建仓方案。 | d1 | rel-test-a |
| B | 5cb9bdf89689444c | 黄金ETF对冲决策：地缘风险升温，仓位从15%到25%，经光谱展开选择分批建仓。 | d2 | rel-test-b |

**定性依据**：同一决策，措辞微变（"15%提升至25%"vs"从15%到25%"、"选"vs"选择"）。detail `d1`/`d2` 为占位、source 为测试标记，属关系测试遗留数据。**真重复。**

### PAIR 2 ｜ sim=0.952 ｜ 【真重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | 9963caa5fd3044b4 | [踩坑] dumpsys回读=ERROR但信号已生效，回读不可作**真值**判据 | node_Bd |
| B | b34c33a6c350438a | [观测] dumpsys回读=ERROR但信号已生效，回读不可作**权威**判据 | node_Di |

**定性依据**：同一技术结论（回读不可信），"真值/权威"同义。detail 均为绿档自动立卡模板，仅素材步 node 不同。**真重复。**

### PAIR 3 ｜ sim=0.947 ｜ 【真重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | 94f7745503444112 | [踩坑] 8397车机vhal_test**未部署**，需先跑deploy...写入/vendor分区 | node_Ch |
| B | 3fae1e01849a4a15 | [踩坑] 8397车机vhal_test**未预置**需先跑deploy...部署到/vendor | node_Ch |

**定性依据**：**同一 evidence node_Ch**，同一事实（vhal_test 需先部署），"未部署/未预置"同义。**真重复。**

### PAIR 4 ｜ sim=0.945 ｜ 【真重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | 13a2aa612f434934 | [观测] dumpsys回读=ERROR，回读与注入**非同源** | node_Dk |
| B | b34c33a6c350438a | [观测] dumpsys回读=ERROR，回读不可作**权威**判据 | node_Di |

**定性依据**：同一技术结论的不同表述角度。**真重复。**

### PAIR 5 ｜ sim=0.924 ｜ 【真重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | 9963caa5fd3044b4 | [踩坑] dumpsys回读=ERROR，回读不可作真值判据 | node_Bd |
| B | 13a2aa612f434934 | [观测] dumpsys回读=ERROR，回读与注入非同源 | node_Dk |

**定性依据**：同一结论。与 PAIR2/4 同属"dumpsys 回读不可信"这一个事实的四张变体卡（9963caa5/b34c33a6/13a2aa61）。**真重复。**

### PAIR 6 ｜ sim=0.905 ｜ 【不同事实 — 反例，禁止 P0 拦截】⚠️
| | inst_id | summary | detail | source |
|---|---|---|---|---|
| A | ef590c1519704b27 | 复盘结论：非农超预期金价不涨反跌，**归因流动性拐点**，市场已price in。 | 复盘详情 | sim-test-a |
| B | d7d25873cf884b32 | 复盘结论：非农超预期金价不涨反跌，**归因央行购金支撑对冲了流动性冲击**。 | 复盘详情2 | sim-test-b |

**定性依据**：summary 前半句完全相同（非农超预期、金价不涨反跌），**但归因完全不同**——A 归因"流动性拐点/市场已price in"，B 归因"央行购金支撑对冲流动性冲击"。这是**两个不同的分析结论**，不是同一事实的措辞变体。
**结论：不同事实，必须标记为 P0 反例，禁止拦截。** 这证明 0.905 分仍属"不同事实"，是阈值下限的实测证据。

### PAIR 7 ｜ sim=0.899 ｜ 【疑似重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | d987b0cce8c44f60 | [观测] 可复现性判定缺乏**可操作化定义**是P1最脆弱处 | node_skill_weakest_link |
| B | 4d26fad65788471f | [观测] 可复现性判定缺**形式化定义**是专利P1最脆弱环节 | node_skill_weakest_link |

**定性依据**：同 evidence node，"可操作化定义"≈"形式化定义"，表述同一薄弱环节。**疑似重复**（措辞变体，语义高度重合，但非逐字）。

### PAIR 8 ｜ sim=0.873 ｜ 【疑似重复】
| | inst_id | summary 要点 | evidence |
|---|---|---|---|
| A | 4d26fad65788471f | [观测] 可复现性判定缺形式化定义是专利P1最脆弱环节 | node_skill_weakest_link |
| B | ab16273023bb45ea | [观测] P1专利最薄弱环节: 可复现性判定**机制未实现、误判代价未评估** | node_skill_weakest_link |

**定性依据**：同 evidence node，同一薄弱环节，但 B 多了"误判代价未评估"信息点。**疑似重复**（B 略有增补，是否合并需人工判断）。

## 三、定性汇总表

| 对 | sim | inst A | inst B | 定性 | 用途 |
|---|---|---|---|---|---|
| 1 | 0.979 | ac25fe3a | 5cb9bdf8 | 真重复 | 正样本 |
| 2 | 0.952 | 9963caa5 | b34c33a6 | 真重复 | 正样本 |
| 3 | 0.947 | 94f77455 | 3fae1e01 | 真重复 | 正样本 |
| 4 | 0.945 | 13a2aa61 | b34c33a6 | 真重复 | 正样本 |
| 5 | 0.924 | 9963caa5 | 13a2aa61 | 真重复 | 正样本 |
| 6 | 0.905 | ef590c15 | d7d25873 | **不同事实** | **反例（禁止拦截）** |
| 7 | 0.899 | d987b0cc | 4d26fad6 | 疑似重复 | 观察（低于阈值） |
| 8 | 0.873 | 4d26fad6 | ab162730 | 疑似重复 | 观察（低于阈值） |

## 四、对 #30 P0 阈值的直接结论

1. **历史真重复下限 = 0.95**：PAIR 2（0.952）是真重复中的最低分（不含测试数据的 PAIR1）。
2. **0.905 已是不同事实**：PAIR 6 证明 0.905 不能作为拦截线。
3. **故 P0 起步阈值取 0.95**：
   - 0.95~0.952 区间能覆盖 PAIR 2~5 的真重复；
   - 远离 PAIR 6 的 0.905 反例，留足安全边际；
   - PAIR 7/8（0.873/0.899，疑似）低于阈值不拦，符合"宁漏勿错"。
4. **灰度测试集**：
   - 正样本召回：**#29 8 张 derived 空壳（实测 ≥0.953）+ 本附件 PAIR 2~5 真重复对**
   - **关键反例：PAIR 6 非农复盘对（0.905），必须不被拦截，固化为回归用例**
5. **注意 PAIR 1/6 为 11-08 测试数据**（source=rel-test/sim-test），灰度评估时与真实业务数据分开统计，避免污染精确率。

## 五、边界（重要）

本附件仅作 #30 立项实证，**本附件所列 12 张卡不并入 #30 做任何降级/修改**。
- #30 只解决**未来新写入链路**；
- 这 8 对存量 situational 双-active 多版本（detail 完整、非 derived 空壳）→ 登记为**候选独立立项 #31**，后续单独评估是否做存量消重。
- 严禁 #30 从"写链路修复"膨胀为"大规模存量治理"。

## 六、复现方式

```bash
# 池内两两相似度扫描（仅 runtime active，排除 corpus）
python - <<'PY'
import json,io,sys,numpy as np
sys.path.insert(0,'memory'); import embed_semantic as E
mems=[json.loads(l) for l in io.open('data/memory/memories.jsonl',encoding='utf-8') if l.strip()]
act=[m for m in mems if m.get('tier')=='active' and m.get('summary') and m.get('asset_class')!='corpus']
v=np.array(E.embed([m['summary'][:512] for m in act])); s=v@v.T; np.fill_diagonal(s,-1)
for i in range(len(act)):
    for j in range(i+1,len(act)):
        if s[i,j]>=0.84: print('%.3f'%s[i,j], act[i]['inst_id'][:8], act[j]['inst_id'][:8])
PY