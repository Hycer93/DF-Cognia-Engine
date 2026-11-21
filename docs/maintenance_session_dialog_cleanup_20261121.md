# session_dialog 碎片清理台账（2026-11-21）

## 摘要
巡检发现 `session_dialog` 表严重碎片化：1762 条中 811 条（46%）为 ≤20 字节无意义碎片，836 条 21~100 字节，仅 5 条 >1000 字节完整对话。根因：历史 transcript 批量导入通道（`tree_ch` 前缀 `t`=1759 条）未做长度过滤，PreCompact 原生通道（`h`）仅 1 条。

## 处置（用户裁决选 B：归档而非硬删）

| 步骤 | 内容 | 结果 |
|---|---|---|
| ① 备份 | sqlite backup API 全量快照 | `data/memory/backups/session_dialog_backup_20261121_fragment_clean.db`（33.8MB，1762 条 session_dialog，811 碎片），留存 ≥7 天 |
| ② 归档 | 建 `session_dialog_archive_fragment`（同构 + archive_reason/archive_ts），事务内 INSERT SELECT 811 条 + 原表 DELETE | 归档 811 / 删除 811；原表剩 951 条；`[0,20]` 桶清零 |
| ③ 保留 | 21~100 字节 836 条本次 hold 不动 | 可能含简短有效指令，人工区分成本高，后续再加分类过滤 |
| ③′ 口径修正回滚 | 发现 ② 用了 `length(body)`（字符）而非字节，552 条 21~60 字节中文短指令被误归档；回写主表并从归档表移除（审计 `logs/dialog_frag_byte_rollback_20261121.jsonl`） | 归档表仅留 259 条真字节碎片；主表回到 1508 条；四处判定统一为 UTF-8 字节口径 |

## 迁移前安全检查
- `corpus_tags` / `core_corpus_tags` 对 811 条 id 零引用 → 无孤儿引用
- 811 条全来自 `t` 通道（tseg 系列），非 PreCompact 原生产物
- FTS 索引指向 `core_dialogue_log` 非 raw_corpus，不受影响
- 迁移后 `PRAGMA integrity_check = ok`

## 台账
- 记忆卡：`3fed5497`（scar_case，挂 topic_memory_ops）
- 维护台账：`mc-73698307dd96`（internal_cleanup）
- 备份指纹：1762 条 / 90877 字节 / 811 碎片

## 后续防护（④ 独立立项）
1. `t` 通道导入前置过滤 ≤20 字节碎片
2. `h` 通道加长度+语义校验
3. 每周巡检碎片占比报告
4. hook 异常退出码修复（异常崩溃 RC≠0 + 上游告警 `memory_hook_abnormal`）

## 归档表 DDL（已固化进 `memory/corpus_store.py` SCHEMA，DB 被 gitignore 故由代码复现）
```sql
CREATE TABLE IF NOT EXISTS session_dialog_archive_fragment (
    id TEXT PRIMARY KEY, kind TEXT, tree_vol TEXT, tree_ch TEXT, title TEXT,
    body TEXT, version TEXT, status TEXT, superseded_by TEXT,
    content_hash TEXT, source_ref TEXT, ts TEXT,
    archive_reason TEXT, archive_ts TEXT
);
```

## 碎片巡检与基线（用户裁决 2026-11-21）
- **告警口径**：碎片占比 > 15% 才告警；**本次清理后基线 0%**（1508 条 / 字节碎片 0），作为对照基准
- **碎片定义（口径铁律，2026-11-21 修正）**：碎片 = **UTF-8 编码后字节数 ≤20**，不是字符数。
  SQLite 判定必须用 `length(cast(body as blob))`，**禁用 `length(body)`**（后者是字符数，
  会把「帮我编译apk然后推包」11 字符/27 字节这类有效短指令误判为碎片）。
  四处判定点（归档迁移 / patrol / t 通道 e9 / h 通道 memory_gate）统一字节口径，
  回归用例 `eval/audit/dialog_frag_unit_regression.py` 锁死，多字节中文样例防再错位。
- **调度**：接入 nightly 做调度底座，脚本内做周频锁——本周已跑则静默 `exit 0`（不出报告、不推失败）；
  本周首跑执行完整校验，超阈值 `exit 1` 触发晨报高亮；`--force` 可临时手动全量
- **报告内容**：各长度桶计数（0-20 / 21-100 / 101-1000 / 1001-inf）+ t/h/s/v 通道来源拆分，
  便于定位是批量导入还是实时 hook 引入脏数据
- **脚本**：`eval/core/patrol_dialog_fragments.py`，周频标记 `data/memory/patrol_last_run.json`（gitignore）

## 口径偏差事故记录（2026-11-21）
- **现象**：归档迁移 SQL 与 patrol 用 `length(body)`（SQLite 字符数），而 t/h 通道过滤用
  `len(text.encode())`（UTF-8 字节），两套尺子错位。
- **后果**：811 条归档中仅 259 条真 ≤20 字节，552 条是 21~60 字节（字符≤20）的中文短指令被误归档；
  同时 patrol 按字符判会把合规数据报成碎片。
- **处置**：统一为字节口径；552 条回写主表并从归档表清除（审计留档）；patrol 改
  `length(cast(body as blob))`；补 `eval/audit/dialog_frag_unit_regression.py` 回归锁死。
- **纪律**：此后所有过滤逻辑注释/评审清单强制写明「UTF-8 字节，不是字符」；提需求写阈值必须标注「字节/字符」。

## 备份留存说明
- 全量快照 `data/memory/backups/session_dialog_backup_20261121_fragment_clean.db`（33.8MB，1762 条含 811 碎片），
  留存 ≥7 天供回滚核对；`data/` 整体 gitignore，快照不入库
