# 现状摸底报告 — P2-5 改造准备（只读调研）

## 1. sqlite3.connect 调用点清单（共 80+ 处）

> **筛选范围**：排除 `.venv/`、`site-packages/`、`node_modules/`。仅统计项目内 `*.py` 文件。
> **标注规则**：✓ = 已带 `timeout=`；✗ = 无 timeout（需补）。

### 1a. 无 timeout 的调用（P2-5 改造重点，按文件聚合）

| # | 文件:行号 | 连接字符串 / 模式 | 备注 |
|---|-----------|-------------------|------|
| 1 | `cgraph/executor.py:205` | `_sq.connect(_os.path.join(..., "corpus_store.db"))` | 内部 import `sqlite3 as _sq` |
| 2 | `cgraph/executor.py:216` | 同上 | 同函数内备用路径 |
| 3 | `cgraph/executor.py:303` | `_sq.connect(AUDIT_DB)` | 内部 import，AUDIT_DB 是常量 |
| 4 | `cgraph/executor.py:519` | `sqlite3.connect(db)` | 直接 import `sqlite3` |
| 5 | `cgraph/executor.py:564` | `sqlite3.connect(db)` | 同上 |
| 6 | `cgraph/atomic_skill.py:285` | `sqlite3.connect(AUDIT_DB)` | 顶层 `import sqlite3`，审计专用库 |
| 7 | `memory/corpus_store.py:97` | `sqlite3.connect(DB_PATH)` | DB_PATH = `data/memory/corpus_store.db` |
| 8 | `memory/corpus_registry.py:38` | `sqlite3.connect(path)` | path 由参数传入 |
| 9 | `memory/memory_query.py:41` | `_sq.connect(f"file:{_DB_PATH}?mode=ro", uri=True)` | 只读查询主链路 |
| 10 | `memory/memory_query.py:62` | 同上 | 只读回退路径 |
| 11 | `memory/memory_query.py:164` | 同上 | 只读条件分支 1 |
| 12 | `memory/memory_query.py:171` | 同上 | 只读条件分支 2 |
| 13 | `eval/core/e10_semantic_outlier_patrol.py:62` | `sqlite3.connect(os.path.join(HERE, ...))` | helper 返回 conn |
| 14 | `eval/core/e12_router_smoke.py:218` | `_sq0.connect(os.path.join(HERE, ...))` | 内部 import `sqlite3 as _sq0` |
| 15 | `eval/core/e12_router_smoke.py:260` | `_sq.connect(...)` | 内部 import `sqlite3 as _sq` |
| 16 | `eval/core/e12_router_smoke.py:301` | `_sq.connect(_db)` | 可变 db 路径 |
| 17 | `eval/core/patrol_dialog_fragments.py:59` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 18 | `eval/core/e0_topic_gate_guard.py:69` | `sqlite3.connect(db)` | 内部 import |
| 19 | `eval/experiment/hltm_shadow_trial.py:79` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 20 | `eval/experiment/hltm_shadow_trial.py:93` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 21 | `eval/core/e7_nightly_governance.py:63` | `sqlite3.connect(DB)` | 可写巡检 |
| 22 | `eval/experiment/future_sim_minloop.py:80` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 ×7 |
| 23 | `eval/experiment/future_sim_minloop.py:115` | 同上 | |
| 24 | `eval/experiment/future_sim_minloop.py:216` | 同上 | |
| 25 | `eval/experiment/future_sim_minloop.py:277` | 同上 | |
| 26 | `eval/experiment/future_sim_minloop.py:336` | 同上 | |
| 27 | `eval/experiment/future_sim_minloop.py:356` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | |
| 28 | `eval/experiment/future_sim_minloop.py:372` | 同上 | 嵌套函数内 |
| 29 | `cgraph/test_skill_anchor_target.py:111` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 30 | `memory/render_persona.py:67` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 31 | `memory/schema_migrate.py:162` | `sqlite3.connect(db_path)` | 迁移脚本，可写 |
| 32 | `memory/schema_migrate.py:196` | `sqlite3.connect(db_path)` | 同上 |
| 33 | `eval/experiment/f20_weight.py:51` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 34 | `cgraph/test_skill_f_reason.py:142` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 35 | `cgraph/test_skill_scope_scan.py:117` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 36 | `memory/seed_paradigm_batch2.py:52` | `sqlite3.connect(DB)` | 迁移脚本 |
| 37 | `memory/seed_paradigm_migrate.py:71` | `sqlite3.connect(DB)` | 迁移脚本 |
| 38 | `eval/audit/confidence_read_source_regression.py:47` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 39 | `cgraph/test_skill_source_intent.py:190` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 40 | `cgraph/test_skill_terminate.py:144` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 41 | `eval/audit/dialog_frag_unit_regression.py:37` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 42 | `eval/audit/dialog_frag_unit_regression.py:61` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 43 | `cgraph/test_skill_weakest_link.py:172` | `sqlite3.connect(os.path.join(HERE, ...))` | 测试脚本 |
| 44 | `eval/audit/e7_gate_regression.py:57` | `sqlite3.connect(test_db)` | 临时测试库 |
| 45 | `scripts/nightly_run.py:456` | `_sq.connect(f"file:{_db}?mode=ro", uri=True)` | 内部 import `sqlite3 as _sq` |
| 46 | `memory/topic_layer.py:64` | `sqlite3.connect(DB_PATH)` | DB_PATH 为 module-level 常量 |
| 47 | `memory/trace_archive_replay_test.py:24` | `sqlite3.connect(SHADOW)` | 测试用 shadow DB |
| 48 | `memory/trace_archive_replay_test.py:46` | `sqlite3.connect(f"file:{p}?mode=ro", uri=True)` | 只读测试 |
| 49 | `memory/trace_archive_replay_test.py:69` | `sqlite3.connect(SHADOW)` | 测试用 |
| 50 | `memory/v3_migrate_batch1.py:42` | `sqlite3.connect(DB)` | 迁移脚本 |
| 51 | `eval/audit/governance_reconcile.py:186` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 52 | `eval/audit/governance_reconcile.py:200` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 第二连接（并行比对） |
| 53 | `memory/trace_archive.py:48` | `sqlite3.connect(p)` | 可写归档写入 |
| 54 | `memory/trace_archive.py:117` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读主库 |
| 55 | `memory/trace_archive.py:123` | `sqlite3.connect(f"file:{p}?mode=ro", uri=True)` | 只读归档表 |
| 56 | `memory/trace_archive.py:140` | `sqlite3.connect(DB)` | 可写主库 |
| 57 | `memory/v3_schema.py:272` | `sqlite3.connect(DB)` | schema 创建/校验 |
| 58 | `eval/audit/schema_migrate_regression.py:37` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 59 | `eval/audit/paradigm_read_regress.py:71` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 |
| 60 | `eval/audit/paradigm_read_regress.py:90` | `sqlite3.connect(DB)` | **可写**对照库 |
| 61 | `eval/audit/shadow_regress.py:62` | `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读 ×7 |
| 62 | `eval/audit/shadow_regress.py:83` | 同上 | |
| 63 | `eval/audit/shadow_regress.py:101` | 同上 | |
| 64 | `eval/audit/shadow_regress.py:190` | 同上 | |
| 65 | `eval/audit/shadow_regress.py:212` | 同上 | |
| 66 | `eval/audit/shadow_regress.py:292` | 同上 | |
| 67 | `eval/audit/topic_layer_regression.py:100` | `sqlite3.connect(os.path.join(HERE, ...))` | 顶部执行态 |
| 68 | `eval/audit/topic_layer_regression.py:155` | `_sq.connect(os.path.join(HERE, ...))` | 内部 import |
| 69 | `eval/audit/topic_layer_regression.py:206` | `_sq13.connect(_dbp13)` | 嵌套作用域 |
| 70 | `eval/audit/topic_layer_regression.py:250` | `_sq14.connect(_os14.path.join(...))` | 嵌套作用域 |
| 71 | `tools/can-tools/can_query.py:26/81/101/121/151/200` | `sqlite3.connect(DB_PATH)` | CAN 协议工具，外部 DB（非 corpus_store） |
| 72 | `tools/can-tools/import_can_xlsx.py:161` | `sqlite3.connect(DB_PATH)` | CAN 导入工具，外部 DB |
| 73 | `scripts/f40_cross_cluster_eval.py:36` | `return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` | 只读，helper 返回 |

### 1b. 已带 timeout 参数的调用（无需改动）

| # | 文件:行号 | 代码原文 |
|---|-----------|----------|
| 1 | `memory/memory_organize.py:164` | `_wc = _sq.connect(_db, timeout=5)` |
| 2 | `memory/memory_query.py:754` | `_wc = _sq.connect(_DB_PATH, timeout=5)` |

### 1c. 小结

- **总计约 82 处** `sqlite3.connect` / `.connect()` 调用
- **仅 2 处** (`memory_organize.py:164`, `memory_query.py:754`) 已带 `timeout=5`
- **约 80 处** 缺失 `timeout` 参数 — **P2-5 主要改点**
- 注意区分「只读模式 (`mode=ro`)」与「可写模式」：可写调用优先级最高（可能遇 WAL 锁等待）

---

## 2. 无条件吞异常 (`except ...:` + 纯 `pass` / `continue` / 裸 `return`) 清单

> 定义：`except <E>:` 块体内只有 `pass`、`continue` 或 `return <const>` 且无任何日志/告警输出。
> 排除项：`except ImportError`（模块加载失败属正常降级）、`except (TypeError, ValueError)` 用于解析兜底（有 fallback 值）。

### 2a. `except OSError: pass` / `except OSError: return`

| # | 文件:行号 | 代码原文 | 上下文 | 评估 |
|---|-----------|----------|--------|------|
| 1 | `memory/memory_gate.py:78-79` | `except OSError:\n    pass` | 函数 `_log()` — 守护日志写入失败 | ⚠️ 高优：静默丢失审计线索 |
| 2 | `eval/core/e12_router_smoke.py:74-75` | `except OSError:\n    pass` | 函数 `build_s1_samples()` — CIPP 文件读取失败 | ✅ 低危：非关键样本缺失 |

### 2b. `except OSError: return <value>`（降级但不告警）

| # | 文件:行号 | 代码原文 | 上下文函数 | 评估 |
|---|-----------|----------|-----------|------|
| 3 | `memory/memory_gate.py:89-90` | `except OSError:\n    return {}` | `get_family_cache()` — mtime 读取失败 | ⚠️ 可能导致缓存重建失效 |
| 4 | `memory/memory_query.py:89-90` | `except OSError:\n    return {}` | `get_registry()` — `getmtime` 失败 | ⚠️ registry 元数据不可见 |
| 5 | `eval/core/e12_router_smoke.py:54-55` | `except OSError:\n    return None` | `load_patent_full()` — 专利文件不存在 | ✅ 预期行为（文件可选） |
| 6 | `eval/core/e12_router_smoke.py:212-213` | `except OSError:\n    patent_full = "可复现性判定..."` | `main()` — 测试材料降级文本 | ✅ 设计意图明确 |
| 7 | `eval/core/e9_dialog_governance.py:82-83` | `except OSError:\n    return []` | `_scan_transcript()` — applog.log 不存在 | ✅ 平台环境差异降级 |

### 2c. `except Exception: pass`（最危险的静默吞错）

| # | 文件:行号 | 代码原文 | 上下文函数/类 | 评估 |
|---|-----------|----------|--------------|------|
| 8 | `cgraph/atomic_skill.py:249-250` | `except Exception:\n    pass` | `BaseAtomicSkill.run()` — token 埋点失败 | ✅ 观测数据非核心 |
| 9 | `cgraph/atomic_skill.py:290-291` | `except Exception:\n    pass` | `_audit_log()` — audit_topic_log INSERT 失败 | ⚠️ 审计丢失但不阻断业务 |
| 10 | `cgraph/executor.py:210-211` | `except Exception:\n    pass` | `ChainExecutor._run_memory_gate()` 内嵌套 try | ⚠️ 审计失败不阻断 |
| 11 | `cgraph/executor.py:221-222` | `except Exception:\n    pass` | 备用审计路径 | ⚠️ 审计失败不阻断 |
| 12 | `eval/core/e0_topic_gate_guard.py:76-77` | `except Exception:\n    pass` | 守卫清理步骤 | ⚠️ 清理失败但结论仍出 |
| 13 | `eval/core/e7_nightly_governance.py:76-77` | `except Exception:\n    pass` | 夜间治理逻辑 | ⚠️ 治理阶段静默跳过 |
| 14 | `eval/core/e9_dialog_governance.py:66-67` | `except Exception:\n    pass` | e9 治理流程 | ⚠️ 治理阶段静默跳过 |
| 15 | `eval/audit/e11_blind20.py:90-91` | `except Exception:\n    pass` | JSON parse 后继续执行（fallback 在下方） | ✅ 有 fallback 值 |
| 16 | `scripts/debate_live.py:42-43` | `except Exception:\n    pass` | 意图回归分析失败 | ⚠️ ratio/note 变为 None |
| 17 | `memory/memory_organize.py:184-185` | `except Exception:\n    pass` | dual-write error 日志落盘失败 | ⚠️ 双写错误记录丢失 |
| 18 | `memory/memory_relations.py:127-128` | `except Exception:\n    pass` | placeholder 检测 | ✅ 安全默认（is_placeholder=False） |
| 19 | `memory/v3_migrate_batch1.py:58` | `except Exception: tag = {}` | 同一条 — `json.loads` 失败时置空 | ✅ 合理降级 |
| 20 | `cgraph/router_atomic.py:243-244` | `except Exception:\n    pass` | 路由结果解析 | ⚠️ 返回空串""作为 fallback |
| 21 | `cgraph/json_utils.py:93-94` | `except (ValueError, TypeError):\n    pass` | JSON 深度序列化辅助 | ✅ 遍历中单对象失败跳过 |

### 2d. `except Exception: continue`（遍历中静默跳过）

| # | 文件:行号 | 代码原文 | 上下文 | 评估 |
|---|-----------|----------|--------|------|
| 22 | `cgraph/atomic_skill.py:312-313` | `except Exception:\n    continue` | 邻接表 entries 遍历时单个解析失败 | ✅ 遍历安全 |
| 23 | `cgraph/llm_codechat.py:56-57` | `except Exception:\n    continue` | JWT token 检测循环 | ✅ 单条失败不影响其他 |
| 24 | `memory/memory_relations.py:373-374` | `except Exception:\n    continue` | jsonl 逐行解析 | ✅ 坏行跳过 |

### 2e. `except Exception: return <default>`（无声降级）

| # | 文件:行号 | 代码原文 | 上下文函数 | 评估 |
|---|-----------|----------|-----------|------|
| 26 | `cgraph/matrix_knowledge.py:40-41` | `except Exception:\n    return []` | matrix_knowledge 读取 | ⚠️ 知识矩阵为空无告警 |
| 27 | `cgraph/matrix_knowledge.py:94-95` | `except Exception:\n    return []` | 同上 | ⚠️ |
| 28 | `cgraph/matrix_knowledge.py:126-127` | `except Exception:\n    return ""` | matrix_knowledge 字符串返回 | ⚠️ |
| 29 | `cgraph/memory.py:67-68` | `except Exception:\n    return []` | memory retrieve 方法 | ⚠️ 记忆检索失败返回空 |
| 30 | `cgraph/ref_retrieval.py:131-132` | `except Exception:\n    return []` | ref_retrieval | ⚠️ |
| 31 | `cgraph/ref_retrieval.py:160-161` | `except Exception:\n    return []` | ref_retrieval | ⚠️ |
| 32 | `cgraph/llm.py:95-96` | `except Exception:\n    self.last_usage = None` | LLM usage 提取 | ✅ 非核心 |
| 33 | `cgraph/llm.py:104-105` | `except Exception:\n    return False` | token 有效性检查 | ⚠️ 假阴性导致功能不可用 |
| 34 | `cgraph/llm_codechat.py:75-76` | `except Exception:\n    return 0` | token 计数辅助 | ✅ 辅助指标 |
| 35 | `cgraph/engine.py:43-44` | `except Exception as e:\n    print("[warn]...")` | Engine init chromadb 不可用 | ⚠️ 已打印 warn，但无 stderr 标记 |
| 36 | `scripts/debate_collect.py:40-41` | `except Exception:\n    return None, ""` | 辩论收集辅助 | ⚠️ |
| 37 | `scripts/collect_trajectories.py:20-21` | `except Exception:\n    return ""` | bare_answer | ⚠️ 回答变空串无提示 |
| 38 | `scripts/collab_pipeline.py:228-229` | `except Exception:\n    return []` | term extraction 辅助 | ⚠️ |

### 2f. 小计

- **真正纯 `pass` 的**：~13 处（上面 2c 中的纯 pass 项）
- **`return/continue` 降级无声的**：~15 处
- **建议优先级**：涉及 `audit_log`、`_log`（守护日志）、`matrix_knowledge`、`ref_retrieval`、`memory get` 等高频通道的应优先改为 `print(..., file=sys.stderr)` 或统一 logging。

---

## 3. 数据库路径来源清单

所有核心项目的 DB 路径均以硬编码常量定义，集中于各模块顶部。格式：`(module_var = value)`。

| # | 文件:行号 | 变量名 & 赋值 | 实际路径 |
|---|-----------|--------------|---------|
| 1 | `memory/corpus_store.py:21` | `DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory", "corpus_store.db")` | `<repo>/data/memory/corpus_store.db` |
| 2 | `memory/memory_query.py:32` | `_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory", "corpus_store.db")` | 同上 |
| 3 | `memory/v3_schema.py:12` | `DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory", "corpus_store.db")` | 同上 |
| 4 | `memory/topic_layer.py:30` | `DB_PATH = os.path.join(os.path.dirname(HERE), "data", "memory", "corpus_store.db")` | 同上 |
| 5 | `memory/schema_migrate.py:32` | `DB_PATH = os.path.join(os.path.dirname(HERE), "data", "memory", "corpus_store.db")` | 同上 |
| 6 | `memory/trace_archive.py:23` | `DB = "data/memory/corpus_store.db"` | **相对路径**（依赖 cwd） |
| 7 | `memory/render_persona.py:20` | `DB = "data/memory/corpus_store.db"` | **相对路径** |
| 8 | `memory/seed_paradigm_migrate.py:17` | `DB = "data/memory/corpus_store.db"` | **相对路径** |
| 9 | `memory/seed_paradigm_batch2.py:20` | `DB = "data/memory/corpus_store.db"` | **相对路径** |
| 10 | `cgraph/atomic_skill.py:39` | `AUDIT_DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同 repo + `/corpus_store.db` |
| 11 | `eval/core/e7_nightly_governance.py:38` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 12 | `eval/core/patrol_dialog_fragments.py:26` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 13 | `eval/experiment/future_sim_minloop.py:30` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 14 | `eval/experiment/hltm_shadow_trial.py:31` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 15 | `eval/experiment/f20_weight.py:31` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 16 | `eval/audit/shadow_regress.py:35` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 17 | `eval/audit/governance_reconcile.py:31` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 18 | `eval/audit/paradigm_read_regress.py:34` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 19 | `eval/audit/confidence_read_source_regression.py:27` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 20 | `eval/audit/dialog_frag_unit_regression.py:28` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 21 | `eval/audit/schema_migrate_regression.py:24` | `DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")` | 同上 |
| 22 | `scripts/f40_cross_cluster_eval.py:26` | `DB = os.path.join(ROOT, "data", "memory", "corpus_store.db")` | 同上 |
| 23 | `eval/audit/e7_gate_regression.py:43` | `test_db = os.path.join(tmpdir, "gate_test.db")` | **临时目录**（运行时生成） |

### 3a. 环境变量配置

| # | 文件:行号 | 代码原文 | 作用 |
|---|-----------|----------|------|
| 1 | `memory/memory_query.py:34` | `_MEMORY_SOURCE = os.environ.get("DF_MEMORY_SOURCE", "sqlite")` | 切换 read 源为 jsonl |

### 3b. 总结规律

- **主力模式**：`DB = os.path.join(<base>, "data", "memory", "corpus_store.db")` 使用 `_HERE` / `HERE` 等本地目录做绝对路径锚定
- **相对路径风险**：`trace_archive.py`、`render_persona.py`、`seed_paradigm_migrate.py`、`seed_paradigm_batch2.py` 使用硬编码 `"data/memory/corpus_store.db"` — 当 cwd 不在 repo 根时会找不到文件
- **唯一独立库**：`AUDIT_DB` 指向同一 `corpus_store.db`，但通过独立的 `atomic_skill.py` 内的 `sqlite3.connect(AUDIT_DB)` 操作

---

## 4. 已有的日志/告警惯例

项目**未引入统一的 `logging` 模块**，全部采用 `print()` 语句，分三种风格：

### 4a. `print(..., file=sys.stderr)` — 正式告警

用于明确的错误/异常通知，标准格式 `[模块标签] 消息`：

| # | 文件:行号 | 代码原文 |
|---|-----------|----------|
| 1 | `memory/confidence_dynamics.py:44` | `print(f"[confidence_dynamics] 读源降级 jsonl: {e}", file=sys.stderr)` |
| 2 | `memory/memory_judge.py:254` | `print(f"[memory_judge] LLM 判定不可用，切规则桩: {str(e)[:120]}", file=sys.stderr)` |
| 3 | `memory/memory_gate.py:210-211` | `print(f"[memory_hook_abnormal] gate 未捕获异常: {type(e).__name__}: {e}", file=sys.stderr)` |
| 4 | `memory/memory_hook.py:188-189` | `print(f"[memory_hook_abnormal] 未捕获异常: {type(e).__name__}: {e}", file=sys.stderr)` |
| 5 | `eval/core/patrol_dialog_fragments.py:55` | `print(f"[dialog碎片巡检] 警告: 周频标记写入失败 {e}", file=sys.stderr)` |

### 4b. `print("[tag] 消息: {detail}")` — stdout 告警（无 file=sys.stderr）

用于过程信息或部分异常：

| # | 文件:行号 | 代码原文 |
|---|-----------|----------|
| 6 | `cgraph/executor.py:280` | `print(f"[post-exit] 后置段异常(不污染主链): {_e}")` |
| 7 | `cgraph/executor.py:321` | `print(f"[sufficiency] 后置校验异常(跳过): {e}")` |
| 8 | `cgraph/executor.py:365` | `print(f"[memory_gate] 执行异常(只告警): {type(e).__name__}: {e}")` |
| 9 | `eval/core/e12_router_smoke.py:231` | `print(f"[E12] {tag} FAIL: run_atomic异常 {e}")` |
| 10 | `cgraph/engine.py:44` | `print(f"[warn] 记忆层不可用（chromadb 未安装?）: {e}")` |

### 4c. `sys.exit(N)` + `print(json.dumps(...))` — CLI 脚本退出码

主要用于 `memory_hook.py` / `memory_gate.py` 的标准退出规范：

| # | 文件:行号 | 代码原文 |
|---|-----------|----------|
| 11 | `memory/memory_hook.py:150-151` | `print(json.dumps({"error": f"候选JSON解析失败: {e}"}, ensure_ascii=False)); sys.exit(1)` |
| 12 | `memory/memory_hook.py:158-159` | `print(json.dumps({"error": f"judge管道异常: {e}"}, ensure_ascii=False)); sys.exit(2)` |

### 4d. 新加告警的风格建议

> **推荐模板**：保持 `stderr` 打印，使用方括号模块标签前缀，格式 `{type(e).__name__}: {e}` 包含异常类型和消息截断：
> ```python
> print(f"[模块标签] 描述: {type(e).__name__}: {e}", file=sys.stderr)
> ```
> 如需限制长度，追加 `[:200]` 截断。这与应用中已有的 `_log` 失败、`memory_hook_abnormal` 等既有模式一致。