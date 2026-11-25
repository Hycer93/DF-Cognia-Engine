# #29 记忆库同事件多版本治理 —— 存量消重报告

> 执行日期：2026-11-23
> 性质：纯数据操作（tier→precedent + superseded_by 回填），代码逻辑零改动
> 脚本：`eval/audit/demote_superseded_shells.py`（dry-run / --apply 双模式）

---

## 一、问题

2026-11-16T22:40:48~49 一分钟内批量写入 10 张 derived 卡（9 corpus + 1 runtime），特征：`repro=derived`、`detail=None`、`source_ref=None`、`weight={match:0,cited:0}`、`entity_id=None`。其中 8 张与已有正本卡同 summary 并存，形成"同事件多 active 版本"。

**根因（详见 #30 立项）**：三重查重失效叠加 —— ① full_hash 仅逐字精确匹配，措辞微变即失效；② derived 卡被派生层强制清空 detail/source_ref，无法算 embedding；③ corpus/runtime 池完全隔离，互不比对。

---

## 二、分类与处置规则（用户 2026-11-23 拍板）

| 类别 | 组 | 特征 | 处置 |
|---|---|---|---|
| A. derived 空壳 | 组1-7 | corpus/derived/detail 空（11-16） | 降级，指向 11-13 带 detail 的 corpus 正本 |
| A. derived 空壳 | 组8 | runtime/derived/detail 空（11-16） | 降级，指向 11-15 corpus 标识卡 |
| C. 真·不同事件 | 组9 | dumpsys 两个观测点(node_Dk/node_Di) | **不动** |

**⚠️ 组8 关键备注**：留存的 corpus 版（32684974ebae）本身 `detail` 也为空，**它只是版本锚点/权威标识（带 event_ts 2026-11-15T00:30:00），不是带详情的正本**。降级 runtime 版仅消除重复，不增加信息量。组8 的 detail 缺失属**独立证据回填任务，不在 #29 范围**。后续任何人不得误认为"降级后即得到带 detail 的正本"。

**⚠️ 字段口径**：记忆卡**无 status 字段**（全库 None），活性由 **tier** 表示（active/precedent）。本报告"变非 active"= `tier: active → precedent`。

---

## 三、执行结果

- 前置备份：`memories.jsonl.bak_29_20261123_125020` / `corpus_store.db.bak_29_20261123_125020`
- 逐张 demote（不批量，保证 superseded_by 各自绑定正确目标），8/8 全部 `applied`，`db_synced=1 db_failed=0`

| 组 | 空壳 inst_id | 正本 inst_id | 正本 detail |
|---|---|---|---|
| 1 | 525d9e052c584c47b95891bfe00d32a0 | f767248deafa4cc1a176f5a710048a63 | 81 字 |
| 2 | cfd09e407b144e5ba9ae702d2a2b0def | e1afda16f28741f29f2fe97248fe95bd | 41 字 |
| 3 | 978385d4539f41fdb54629c1d5ec953e | 7e92e275daa84758a523b64b8320e801 | 48 字 |
| 4 | 5f4e9ea285b6459cabbe0fac0d1b8626 | ff01d79115c240f49246dbff006c3abd | 38 字 |
| 5 | fc24f5ea53684a36bce392603f57d713 | 6e2a9c36170b43a0a0952d7916af738c | 31 字 |
| 6 | 029ff34883fc4fa3a7fcbf779c2990b2 | 8cbe367d36b44768a22443529837f3e1 | 73 字 |
| 7 | e6ed253884104c3fb6c9a94cf8aaf9a3 | 13a2379e8a524f978142b8baf5e5f4ce | 54 字 |
| 8 | 9b48551b2b054963a2810e633bc70936 | 32684974ebae425c8a9385db8d8ecff0 | **0 字（锚点）** |

---

## 四、执行后校验（全部通过 ✅）

1. **8 张空壳**：jsonl 与 DB 双侧 `tier=precedent` + `superseded_by` 正确绑定对应正本 ID ✅
2. **8 张正本**：全部保持 `tier=active`，无改动 ✅
3. **全库 tier 分布**：active 291→283、precedent 29→37（正好 -8/+8）✅
4. **残留重复复查**：active 中同 summary 多卡仅剩 1 组 = 组9（dumpsys 两观测点，**故意保留**）✅
5. **E4 晨报**：`db_jsonl_drift=0`（未破），降级率 0.0906→0.1156（29→37，符合 +8）✅

---

## 五、边界说明

- 本次 #29 **只做消重与版本标记**，不删任何卡、不改任何代码逻辑。
- 组8 corpus 锚点 detail 缺失、组9 双观测点保留，均为**设计内结果**，非遗留缺陷。
- 可回滚：demote 前已备份 jsonl + db；如需回滚，恢复两份备份即可（全部 8 张同时回退）。

## 六、后续（#30 立项，不在本次）

根治入库链路三道闸：P0 summary 语义查重（阈值宁高勿低，先拿这批 8 张做灰度）/ P1 derived 不再清空 detail / P2 跨池弱匹配告警 / P3 E4 指标兜底。改 `atomic_store` 主流程，单独立项走七段评审。