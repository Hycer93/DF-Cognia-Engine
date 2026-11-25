# 05 数据治理（按问题分 4 子目录）

存量数据治理过程档。每类问题一个子目录，按 dryrun(预演)→exec(执行)→归档 排列。

## dup_cards/ — 重复卡根治（#30 主问题，16 份）
主线：design_30_derived_dup_fix(立项) → recon_30_checklist/report(侦察) → risk_30_phase2(风控) → impl_30_phase3(实现) → test_30_phase4(测试) → audit_30_phase6(审计) → rollout_30_phase7(灰度) → exec_30_switch_on(切on)
存量：#31 dryrun_31 + #43 dryrun_43 + duplicate_cards_audit + attach_30(样本) + dryrun_40(写入入口巡检) + p0_switch_on_eval + exec_31_35_36

## backfill/ — 回填（8 份）
- #34 memory_class：dryrun_34_memory_class / dryrun_34_backfill_high_medium / exec_34_memory_class / exec_34_batch2
- #37 event_ts：dryrun_37_event_ts / dryrun_37_a1a2_preview / exec_37_event_ts
- #44 detail 护栏：exec_44_detail_guard

## drift_demote/ — 漂移/demote/并发（9 份）
diag_db_jsonl_drift → disposal_20261124(问题A/B) → dryrun_41_unified_update → dryrun_46/exec_46_demote + green_card_gate_concurrency_fix + exec_50_raw_leaf_edge + exec_f_chain_first_run + exec_f30_realdb_validation

## quarantine/ — 隔离区/裁决（6 份）
dryrun_35_36_quarantine_watchlist + quarantine_triage + quarantine_fix_report.json + p2_suggested_class.json + p3_governor_verdict.json + topic_memory_ops_misattach_audit

> 上级索引：../README.md　|　合并候选见 ../README.md §四
