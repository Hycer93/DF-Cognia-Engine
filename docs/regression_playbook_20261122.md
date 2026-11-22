# 后端恢复后 · 正式回归全套脚本清单

> 生成于 2026-11-22 | 用途：后端限速（单次 LLM 40-90s）解除后，一键照跑，无需临时想步骤
> 前置：`cd cognitive_engine_v3`，Windows Git Bash，已 `git pull` 到 `8a280e5` 之后
> 通用环境变量：`PYTHONIOENCODING=utf-8`（中文不乱码）
>
> ⚠ **三级验证口径**：冒烟(已完成) → 本清单的正式回归 → 人工终审。本清单只到"行为/统计"层，质量结论仍需人看。

---

## 0. 恢复判定（先跑这条确认后端已恢复）

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/experiment/smoke_router_23.py
```

| 校验点 | 通过标准 |
|---|---|
| 冒烟 4/4 | `冒烟结果：4/4 符合预期` |
| 单次耗时 | 每条 < 15s（当前 40-50s 属未恢复，应 >3 倍即中止后续） |
| 路径 | simple=3 / medium=8 / complex=14 |

**若仍 40s+ → 后端未恢复，停止，改日再跑。** 恢复后继续下列 §1~§4。

---

## §1 #23 全套 LLM 分类回归

### 1.1 主分类回归（dl_route_regression，11 条对抗/审计题）

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/audit/dl_route_regression.py
```

| 项 | 说明 |
|---|---|
| 输入 | `data/probe/dl_route_regression.jsonl`（11 条，target=Dl 的对抗检验/反向复盘题） |
| 输出产物 | `logs/dl_route_regression.json`（含每例 depth/in_path/in_domain） |
| 成功标准 | **strong + weak ≥ 9/11**（对比历史基线：E1 夜跑 Dl 召回 12/25≈48%、heldout 0/3 的烂局） |
| 退化红线 | 若 `strong=0` 或 `fail ≥ 4` → **#23 的 simple 收紧过度，回退 classify 改动** |
| 关注 | 每条 depth 是否合理；审计长题不应被判 simple（原 known bug 应已修复） |

### 1.2 补 simple 召回专项（新增，需先建用例）

**前置改造**：`eval/experiment/smoke_router_23.py` 的 `CASES` 追加 8-10 条 simple 正例（问候/单点事实/翻译/单句改写），复用其结构。或用下面临时命令快速验：

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python -c "
import sys; sys.path.insert(0,'.')
import yaml
from cgraph.graph import MindGraph
from cgraph.router import ComplexityRouter
from cgraph.llm_codechat import CodeChatLLM
cfg=yaml.safe_load(open('config/config.yaml',encoding='utf-8'))
g=MindGraph.load('data/'+cfg['graph']['data_file'].split('data/')[-1])
r=ComplexityRouter(g,cfg.get('router'),llm=CodeChatLLM(model=cfg['llm']['codechat'].get('model')))
qs=['你好','今天天气怎么样','把这句话翻译成英文：我饿了','Python是什么','谢谢你的帮助','1+1等于几','帮我把这句改正式：这玩意不行','苹果的英文怎么说']
for q in qs:
    d=r.route(q); print(f'[{d.complexity:7}] {len(d.path)}节点 {q}')
"
```

| 成功标准 | simple 召回 ≥ 7/8 |
|---|---|
| 退化红线 | < 5/8 → classify prompt 的 simple 判据失效，回查 §1.1 |

### 1.3 分流日志审计（跑完 1.1/1.2 后）

```bash
PYTHONIOENCODING=utf-8 python -c "
import json,collections
rows=[json.loads(l) for l in open('logs/router_log.jsonl',encoding='utf-8') if l.strip()]
c=collections.Counter(r['complexity'] for r in rows)
lens=collections.defaultdict(list)
for r in rows: lens[r['complexity']].append(r['path_len'])
print('档位分布:',dict(c))
for k,v in lens.items(): print(f'  {k}: 路径长 min{min(v)} max{max(v)} avg{sum(v)/len(v):.1f} n={len(v)}')
"
```

| 成功标准 | simple∈[3,3]、medium∈[7,9]、complex∈[13,14]，无跨档污染 |

---

## §2 #23 medium 档位答题质量盲评

> 只对 **medium 档**做质量评估，验证"14→8 节点裁短是否掉质"。

### 2.1 采集 A/B 数据（图 vs 裸模型）

准备一个 medium 问题文件 `medium_qs.txt`（每行一题，建议 10 条单一领域分析题），然后：

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/experiment/ab_test.py --file medium_qs.txt
```

| 说明 | 记录输出文件名 `logs/ab_result_<时间戳>.jsonl`，下一步要用 |
|---|---|
| 时长 | 单题 ≈ 裸(1次)+图(medium 8节点≈9次)，后端慢时约 3-5 分钟/题 → 10 题约 40 分钟 |

### 2.2 盲评打分（同一 LLM 裁判，交替盲化）

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/audit/score.py logs/ab_result_<时间戳>.jsonl
```

| 输入 | 上一步产出的 ab_result 文件（也可不传参，自动取 logs/ab_result_*.jsonl 全部） |
|---|---|
| 输出产物 | `logs/ab_judge_final.json`（明细+平均分+胜率+分差分布+四维均分） |
| 成功标准 | **graph 均分 ≥ bare 均分 - 1.5**（历史全档基线：均分 34 vs 35.45、分差 -1.45）→ medium 裁短后不应比原全档更差 |
| 关注维度 | `意图贴合` 维度是否因裁短掉分；若掉 >1 分需查是否裁掉了关键分析节点 |
| 退化红线 | graph 胜率 < 20% 且均分差 < -3 → medium_steps 调回 6-8 重测 |

### 2.3 调参（可选，若 2.2 掉质）

`config/config.yaml` 的 `router.medium_steps`：5→6 或 7，重跑 2.1+2.2 对比。改后同步 `config.example.yaml`。

---

## §3 #22 扩充样本复测（≥20 题 + LLM 抽样校验）

> **关键障碍（必须先改代码）**：当前只有 3 条历史样本符合〔先想·节点格式〕；`--live` 硬编码只取 3 题。要 ≥20 题**必须先把 live 题源换成 ≥20 条**。

### 3.1 改 live 题源（一次性改造）

编辑 `eval/experiment/intent_alignment_probe.py` 的 `main()`（约 307-310 行）的 `qs` 列表，扩到 ≥20 条覆盖 simple/medium/complex 的真实 query（可从 §1.2/§2.1 的问题池复用 + 补复杂题）。或改造为从文件读：

```python
# 可选改造：--live 支持从文件读
qs = open('probe_20qs.txt', encoding='utf-8').read().splitlines()[:args.live]
```

### 3.2 跑复测（embedding 全量 + LLM 抽样）

```bash
# 先 20 题离线 embedding（不用 LLM，快）
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/experiment/intent_alignment_probe.py --live 20 --no-llm
# 再补 LLM 抽样校验（慢，单次 40s+，抽样约 9-15 次调用）
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/experiment/intent_alignment_probe.py --live 20
```

| 输出产物 | `logs/obs_intent_alignment/`：`emb_scores.csv`、`trend_by_sample.csv`、`llm_check_sample.csv`、`report.md` |
|---|---|
| 成功标准 | report.md 的「节点维度」表中，**低分(<0.55)节点仍集中在方法论型(G10/11/12/14/21)** → #22 结论「职责决定贴合度」被更大样本印证 |
| 推翻结论的红线 | 若 ≥10 个样本出现**单调下降**且低分节点随机分布 → 说明小样本偏差，「意图稀释」需重新讨论 |
| LLM 一致性 | llm_check_sample.csv 中 LLM 分与 emb 分方向一致率 ≥ 60% |

---

## §4 #20 Phase2 指标采集

### 4.1 E4 记忆健康度（已接入，直接跑）

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/core/e4_maintenance_patrol.py
```

| 输出产物 | `logs/e4_maintenance.json` 的 `health` 字段 + stdout 健康度行 |
|---|---|
| 成功标准 | stdout 有 `[记忆健康度] 检索gate=... miss=... | 冲突q=... | 待归档=...` |
| 关注 | 连续多日跑，比对 miss_rate 是否上升（趋势，非单点） |

### 4.2 E9 脱敏 + transcript 归档

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python eval/core/e9_dialog_governance.py
```

| 输出产物 | `logs/e9_dialog_governance.json` |
|---|---|
| 成功标准 | `report["transcript"]["stored"]` ≥ 0 且无异常；抽查新入库 raw_corpus 正文**无手机号/身份证/邮箱原值** |
| 脱敏核查命令 | 见下方（已封装为脚本，误报已过滤） |

```bash
# 抽查近 7 天入 raw_corpus 的对话正文是否还有 PII 原值（应为 0 命中）
PYTHONIOENCODING=utf-8 python eval/audit/pii_residue_check.py
```

| 成功标准 | 输出 `精确PII命中 0条`；若 >0 且是真 PII（非路径/任务号）→ 脱敏失效需回查 |

### 4.3 全量夜间管线（E4+E9 一起）

```bash
PYTHONIOENCODING=utf-8 NO_MEMORY=1 python scripts/nightly_run.py
```

| 输出产物 | `logs/nightly_console.log` + 各步骤 json |
|---|---|
| 成功标准 | 全 STEPS 绿；E4 健康度行 + E9 transcript stored/high_value 正常 |

---

## §5 收尾：核对与台账

| 步骤 | 命令/动作 | 通过标准 |
|---|---|---|
| 汇总核对 | 逐项对照 §1~§4 各"成功标准" | 每项打勾 |
| 台账更新 | `docs/review_20261121_system_status.md`：#23 转 ✅、#20 转 ✅、#22 复测结论追加 | 状态与实际一致 |
| 文档补记 | 各 review 文档追加"恢复后回归结果"章节 | 含实测数字 |
| 提交 | `git add -A && git commit -m "test: 环境恢复后回归结果" && git push`（443） | 推送成功 |
| 推送命令 | `GIT_SSH_COMMAND="ssh -p 443" git push ssh://git@ssh.github.com:443/Hycer93/DF-Cognia-Engine.git main:main` | — |

---

## 附：跑不动时的降级顺序（后端只恢复一半时）

1. **只够跑轻量**：§0 冒烟 + §1.3 日志审计（零 LLM）→ 确认行为。
2. **能跑几十次调用**：§1.1 dl回归 + §4.1/4.2（E4/E9 各几十次 LLM）→ 确认分类+记忆。
3. **能跑几百次**：§2 盲评（10 题 A/B）+ §3.2 #22 复测。
4. **质量终审**：人工抽读 §2 的 ab_judge_final.json 里 graph 回答 vs bare，看 medium 8 节点答案是否"少而精"未丢要点。

---

## 关键文件索引

| 用途 | 路径 |
|---|---|
| 冒烟 | `eval/experiment/smoke_router_23.py` |
| Dl 分类回归 | `eval/audit/dl_route_regression.py` + `data/probe/dl_route_regression.jsonl` |
| A/B 采集 | `eval/experiment/ab_test.py` |
| 盲评打分 | `eval/audit/score.py` → `logs/ab_judge_final.json` |
| #22 观测 | `eval/experiment/intent_alignment_probe.py` → `logs/obs_intent_alignment/` |
| E4 健康度 | `eval/core/e4_maintenance_patrol.py` → `logs/e4_maintenance.json` |
| E9 脱敏 | `eval/core/e9_dialog_governance.py` → `logs/e9_dialog_governance.json` |
| PII 核查 | `eval/audit/pii_residue_check.py`（近7天 raw_corpus 残留抽查） |
| 夜间全量 | `scripts/nightly_run.py` |
| 分流日志 | `logs/router_log.jsonl` |