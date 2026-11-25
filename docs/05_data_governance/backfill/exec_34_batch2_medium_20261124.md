# #34 memory_class 第二批（A组中置信）回填 —— 执行归档

- 日期：2026-11-24 13:41
- 性质：**已执行 A 组 20 张**，零物理删除
- 数据：active NULL **26 → 6**（剩余 6 张即 B 组维持 null）

---

## 一、执行范围

| 批次 | 张数 | 状态 |
|---|---|---|
| 高置信(hi) | 30 | ✅ 已完成（commit `c6b9b45`，DB 30/30 匹配）|
| **A组(中置信, 可赋类)** | **20** | ✅ **本次执行** |
| B组(中置信, 维持null) | 6 | ⬜ 维持 null，不改 |

用户逐条评审确认 A 组 20 张全部采纳，分类不作改动。

---

## 二、执行命令与备份

```
python eval/audit/backfill_34_memory_class.py --only all --apply
```

> 说明：脚本 `--only` 原仅支持 hi/all。经 dry-run 验证 `--only all` 在 26 张 active NULL 中正好赋类 20（A组）、保 null 6（B组），hi 的 30 张 DB 已非 NULL 不会重复入选。等价于只写 A 组 20 张。

- 手动备份：`corpus_store.db.bak_34b_20261124_134124` / `memories.jsonl.bak_34b_20261124_134124`
- 脚本自动备份：`memories.jsonl.bak_34_20261124_134148`

---

## 三、A 组 20 张分类分布

| memory_class | 张数 | inst_id |
|---|---|---|
| strategy_evolve | 6 | 3c15acc6、63261eae、6ed8586c、ac25fe3a、f033b4fe、pat5_tuowei |
| skill_refine | 5 | ab_ref_material、p_matrix_feed、p_skill_synthesize、pc4_patent_p1_replay、pretrieval_upgrade |
| scar_case | 4 | 4d26fad6、63e24d65、ab162730、ea4cb0dd |
| relation_milestone | 3 | 7edb9445、94c3f620、d25d395c |
| key_pact | 2 | 2b14c17e、fea4d1ad |

完整明细（含理由/原pending提案对比）见 `docs/dryrun_34_backfill_high_medium_conf.md` 第一节。

---

## 四、完成校验（全部通过）

| 校验项 | 结果 |
|---|---|
| ① active NULL 计数 | **26 → 6** ✅（剩余正是 B 组）|
| ② 非法 class 值 | **0** ✅（全落五类枚举）|
| ③ 剩余 6 张 = B 组 | 4304794d / 7177cd60 / a31bddbe / d7d25873 / ef590c15 / fb674356 ✅ |
| ④ 只更新 memory_class 单字段 | ✅（UPDATE 单字段，status/tier/superseded_by/detail 全保留）|
| ⑤ DB == jsonl | 323 条全覆盖，不一致 **0** ✅ |
| ⑥ 零物理删除 | ✅（无 DELETE）|

**执行后 active 池分布**（总数 279）：
- skill_refine 114
- strategy_evolve 62
- scar_case 42
- key_pact 30
- relation_milestone 25
- NULL 6（B组）

---

## 五、回归（69 PASS / 0 FAIL）

e7_gate 13 / e30_p0 17 / e30_p2_p3 13 / e46 10 / e44 16 = **69 PASS / 0 FAIL**

---

## 六、B 组 6 张（维持 null，本次未动）

| inst_id | 理由 |
|---|---|
| 4304794df581 | 季报情报扫描=临时记录 |
| 7177cd609ae7 | E7晨报计数=临时观测数字 |
| a31bddbe82bf | 对冲后续跟踪=临时记录 |
| d7d25873cf88 | 金价复盘=投资记录 |
| ef590c151970 | 非农金价复盘=投资记录 |
| fb6743568152 | 拓普定价复核=投资记录 |

---

## 七、累计成果（#34 两批合计）

- 第一批高置信 30 张（c6b9b45）+ 第二批 A 组 20 张 = **累计回填 50 张**
- active NULL：56 → 6（B 组 6 张维持 null，挂起待后续统一决策）

## 八、约束确认
- ✅ 零物理删除（仅 UPDATE memory_class）
- ✅ 备份齐全（DB + jsonl，双份）
- ✅ 仅 A 组 20 张（已逐条评审确认），B 组及未评审条目未触碰
- ✅ 全套回归 69 PASS / 0 FAIL