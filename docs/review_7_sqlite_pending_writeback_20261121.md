# #7 sqlite 分片 + pending 写回 现状盘点（2026-11-21）

> 定位：主线任务 #7 启动前的现状盘点。**只做盘点，不改代码。**
> 前序：#5 F20 已结案（保留告警不重定基线），主线交接至 #7。

---

## 0. 一句话结论（先纠偏任务名假设）

**任务名「sqlite 分片」所假设的问题，当前并不存在也不需要。**

- 全库 **1 个活跃 DB**（`corpus_store.db`，54 表，63,666 行 / 33MB），无任何 shard/分片代码（grep 零命中）。
- 33MB / 6.3 万行对 SQLite 是小库，**分片带来的收益趋近于零、复杂度陡增**，不建议做。
- 真正值得做的是名字里后半段「**pending 写回**」——现状有 3 处硬缺陷：
  1. **`store_pending.jsonl` 无自动消费调度**：`review_holds()` 全仓**零调用点**（只在注释里出现），
     hold 池 13 条只进不出，靠人工/未来脚本手动触发。
  2. **JSONL 消费是 read-all/write-all 非原子覆盖**：`io.open(path,"w")` 直接截断重写，
     无 temp+rename，进程中断会丢整个 pending 池。
  3. **写 DB 无事务声明、无 busy 重试**：全部 autocommit 单语句，冲突 >5s 直接抛
     `database is locked`，无 busy_handler/retry。

**建议**：把 #7 重定义为「**pending 队列可靠写回**」（消费调度化 + 原子落盘 + 写事务加固），
**放弃物理分片**；若坚持要分片，先给出为什么要分片的数据依据（当前无）。

---

## 1. 当前实现

### 1.1 pending 载体清单（全部 JSONL，非 DB 队列、非内存队列）

| 载体 | 文件 | 写入点 | 消费方式 | 有自动调度? |
|---|---|---|---|---|
| **store_pending** | `data/memory/store_pending.jsonl`（13 行） | `memory_judge.py` L298/L329 judge hold | `review_holds()` L338-401 | ❌ **零调用点** |
| alias_pending | `data/memory/alias_pending.jsonl` | `memory_alias.py` L39-45 | 手动 `_load_pending` 流程 | ❌ |
| class_backfill_pending | `data/memory/class_backfill_pending.jsonl` | `memory_class_backfill.py` | 脚本驱动 | ❌ |
| conflict_queue | `conflict_queue.jsonl` + `core_conflict_queue` 表 | 双写 | 代码读 JSONL | ❌ |
| quarantine | `quarantine.jsonl` + `core_quarantine` 表 | `memory_organize.py` L103 | 代码读 JSONL | ❌ |
| topic_hold_pool | `topic_hold_pool.jsonl` | `topic_layer.mount_topic` | Topic 层独立 | ❌ |

### 1.2 store_pending 完整生命周期（`memory/memory_judge.py`）

```python
# L43  路径
STORE_PENDING_JSONL = os.path.join(DATA_DIR, "store_pending.jsonl")

# L70-74  全量读入
def _load_pending():
    if not os.path.exists(STORE_PENDING_JSONL): return []
    with io.open(STORE_PENDING_JSONL, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

# L329-331  hold 时追加
append_jsonl(STORE_PENDING_JSONL, {**c, "hold_ts": now,
                                   "hold_reason": v.get("evidence_gap") or "held", "reviews": [...]})

# L338-401  review_holds(): 全量读 → 逐条六问 → 转正/维持/丢弃 → 覆盖重写整个文件
pending = _load_pending()
for p in pending:
    ...
    elif promoted: memory_hook.add(...)     # 转正写主库
    else: survivors.append(...)             # 维持 hold
with io.open(STORE_PENDING_JSONL, "w", encoding="utf-8", newline="\n") as f:   # ← 截断覆盖
    for p in survivors: f.write(json.dumps(p, ensure_ascii=False) + "\n")
```

`append_jsonl`（`memory_organize.py` L208-211）追加打开 `"a"`，单条 flush 依赖 with 关闭；无 fsync。

### 1.3 写主库路径

| 路径 | 函数 | 事务 | timeout |
|---|---|---|---|
| A 落卡 | `memory_organize.atomic_store` → `INSERT OR IGNORE core_memory_card` | autocommit 单语句 | 5 |
| B 入口 | `memory_hook.add` → atomic_store + classify_and_link + mount_topic + distill | 无跨子系统事务 | — |
| C 语料 | `corpus_store.upsert_corpus` UPDATE+INSERT+INSERT | 单 commit | 5 |
| D 判定 | `judge_and_store`（hook session 触发） | 见 memory_judge | — |

双写模式：`atomic_store` **先写 memories.jsonl，再写 DB，DB 失败不回滚 jsonl**，只记
`dual_write_errors.jsonl`（最终一致，非强一致）。

### 1.4 journal_mode / 并发参数实测

- `corpus_store.db`：`journal_mode=wal`（**被动**——代码无一处 `PRAGMA journal_mode=WAL`，
  是历史遗留状态），`wal_autocheckpoint` 用默认 1000。
- 其余 `.db`（dfcp_memory/memory/store/topic_store/patent_evidence）：`journal_mode=delete` 或空壳（0 表）。
- 全仓无 `BEGIN IMMEDIATE`/`EXCLUSIVE`、无 `busy_handler`、无 retry。

---

## 2. 已知瓶颈

| # | 瓶颈 | 证据 | 影响 |
|---|---|---|---|
| B1 | store_pending 无消费调度 | `review_holds()` 全仓零调用点；13 条只进不出 | hold 证据补齐后永不转正，记忆永久滞留 pending |
| B2 | JSONL 覆盖重写非原子 | `io.open(path,"w")` 无 temp+rename | 消费中途崩溃 → 整个 pending 池丢失 |
| B3 | 写 DB 无事务/busy 重试 | 全 autocommit，timeout=5 固定 | 并发写冲突直接抛 `database is locked`，judge 管道异常 |
| B4 | 双写不一致窗口 | jsonl 成功、DB 失败不回滚 | memories.jsonl 与 DB 出现幽灵条目（靠 dual_write_errors 事后补） |
| B5 | trace_cited 单表 40239 行 | 表行数实测 | 唯一"较大"表，但索引良好时不影响；非分片级问题 |

> **B5 说明**：若未来真要考虑"分片"，唯一有数据量依据的候选是把审计/追踪类表
> （trace_cited/audit_*，合计 ~5 万行）拆到独立 `audit.db`，与业务主库物理隔离。
> 但当前 33MB 完全不构成性能压力，**属 YAGNI，不推荐现在做**。

---

## 3. 并发风险

| 风险 | 现状 | 等级 |
|---|---|---|
| 双消费者同时 review_holds 覆盖写 | 无锁、无文件 flock；两人同时跑会互相覆盖 pending | **中高**（当前因零调度未暴露，一旦自动化即触发） |
| judge 写入 pending 与 review 消费竞争 | append 与 覆盖重写无互斥 | 中 |
| 同进程多子系统写 DB | atomic_store 短事务 + relations/distill 各自写，无全局事务 | 中 |
| 写冲突无退避 | 无 busy_handler，5s 超时即失败 | 中 |
| WAL 无显式管理 | 依赖自动 checkpoint；无 `-wal`/`-shm` 残留（已自动清理） | 低 |

**最大隐患**：一旦把 review_holds 接入夜间调度（这是 #7 理应做的），B1 的"零调度"会变成
"有调度但无并发保护"，反而制造 B2/B3 的竞态。**所以调度化必须与原子落盘+文件锁一起做。**

---

## 4. 现存缺陷

- **D1**（致命）：`review_holds()` 无任何调用点 → pending 队列死水。这是 #7 的核心可交付。
- **D2**：pending 消费非原子（截断重写），无备份/临时文件交换。
- **D3**：无文件级互斥（flock/lockfile），并发消费会丢数据。
- **D4**：DB 写无 `BEGIN IMMEDIATE` + `busy_timeout` + retry。
- **D5**：WAL 模式未在代码中显式声明，依赖环境状态，换机器/重建库可能回落 delete 模式。
- **D6**：分片（任务名的假设）无实现且无必要——登记为"不做的决策"，而非缺陷。
- **D7**：无任何 pending 写回时序/并发/一致性回归脚本。

---

## 5. 外部依赖

| 依赖 | 说明 |
|---|---|
| `data/memory/corpus_store.db` | 主库；WAL 状态；未被 git 跟踪（数据漂移不可 git 回溯） |
| `data/memory/*.jsonl` | 全部 pending 载体；无 schema 版本、无校验 |
| `memory/memory_hook.py` | judge 管道入口（`session` 子命令，PreCompact 触发） |
| `scripts/nightly_run.py` | 夜间治理主链；**当前不调度 review_holds**（仅在晨报统计 pending 数） |
| `eval/core/e6_mind_graph_patrol.py` | Jw 新认知评估，调 judge_and_store |
| `memory_class_backfill.py` | E5 批量提案写 pending，注释称由 review_holds 消化（但无实际调用） |

**关键断层**：`memory_class_backfill` 注释假设"review_holds 消化"，但 review_holds 无调度——
这是 D1 的文档层面证据：设计意图存在，实现缺失。

---

## 6. 回归点

| 回归项 | 脚本 | 现状 |
|---|---|---|
| 桩判定矩阵（hold→pending 路径） | `eval/audit/memory_judge_stub_regression.py` | ✅ 8/8 |
| LLM 判定通道 | `eval/audit/memory_judge_llm_regression.py` | ✅ 7/7 |
| schema 迁移（含 core_hold_pool 等） | `eval/audit/schema_migrate_regression.py` | ✅ 7/7 |
| DB 完整性 | `PRAGMA integrity_check`（8 库） | ✅ 全 ok |
| connect timeout | `eval/audit/connect_timeout_regression.py` | ✅ 82/82 |
| **pending 写回时序/并发/原子性** | **无** | ❌ 缺（D7） |

→ #7 实施前必须先补一个 pending 写回回归基线，否则改动无护栏。

---

## 7. 待决策点

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 是否做物理分片 | A. 不做（YAGNI）；B. 审计表拆 audit.db；C. 按业务域分片 | **A**，除非给出性能数据；当前 33MB 无依据 |
| Q2 | review_holds 调度方式 | A. nightly 主链尾部；B. hook session 后同步触发；C. 独立 cron | **A**（与现有夜间治理一致，风险最低） |
| Q3 | 原子落盘方案 | A. temp+`os.replace`；B. 写 DB 队列表替代 jsonl | **A**（改动小）；B 属更彻底但迁移成本高 |
| Q4 | 并发互斥 | A. lockfile(跨进程)；B. 单写者约定 | **A**，配合调度化 |
| Q5 | 写事务加固范围 | A. 仅 pending→DB 转正路径；B. 全仓写路径 | **A** 先做；B 排后续 |
| Q6 | hook 是否同步触发 review | 是/否 | **否**，hook 应快返回；review 走夜间 |

---

## 8. 备选实施方案

### 方案 A（推荐）「pending 可靠写回」——不做分片

1. **A-1 原子落盘**：`review_holds` 覆盖写改为"写 `store_pending.jsonl.tmp` → `os.replace` 原子交换"；
   `append_jsonl` 增加 `fsync` 可选。
2. **A-2 并发互斥**：review_holds 入口加 lockfile（`store_pending.lock`，含 pid + 超时），
   抢不到锁直接跳过本轮并告警（不阻塞夜间其他步骤）。
3. **A-3 调度接入**：`scripts/nightly_run.py` 主链尾部加 `review_holds()` 步骤（experiment 层，
   吞错恒退 0），晨报展示 promoted/kept/dropped。
4. **A-4 写事务加固**：pending→DB 转正路径的 `INSERT` 包 `BEGIN IMMEDIATE` + `busy_timeout` + 3 次退避。
5. **A-5 回归**：新建 `eval/audit/pending_writeback_regression.py`：原子性（模拟中途崩溃不留半文件）、
   并发（双进程抢锁互斥）、转正（构造 hold→补齐→store）、幂等（重复 review 不重复入库）。
6. **A-6 WAL 显式化**：建库/连接处显式 `PRAGMA journal_mode=WAL` + `wal_autocheckpoint`。

**优点**：直接消灭 D1-D5，风险低，工作量中等。**缺点**：不解决"分片"（因为不需要）。

### 方案 B「分片 + 写回」（重）

A 全部 + 把审计/追踪表（trace_cited/audit_* ~5 万行）拆到 `audit.db`，跨库写加两阶段协调。

**优点**：物理隔离审计日志与业务库。**缺点**：33MB 下收益微弱，跨库一致性复杂，**不建议当前做**。

### 方案 C「仅补文档与护栏，不改造」

只补 A-5 回归 + 记录 D1-D7 为技术债，调度化留待图谱整体迭代时做。

**优点**：零风险。**缺点**：pending 池继续死水，D1 核心问题未解。

---

## 9. 建议执行顺序

```
第一步  Q1 裁决：不做分片（默认建议）→ #7 重定义为「pending 可靠写回」
第二步  方案 A-1/A-2/A-4 先做（原子+互斥+事务，不改调度，零行为变更可先落）
第三步  A-5 补回归基线（护栏先就位）
第四步  A-3 调度接入 nightly（最后做，因为此时已有原子+互斥+回归保护）
第五步  A-6 WAL 显式化（低风险收尾）
```

**红线建议**：A-3（调度化）必须在 A-1/A-2/A-4/A-5 全部就位后再做，否则重演"有调度无保护"的竞态。

---

## 10. 附：本次盘点实测证据

- `grep -rn "shard\|分片\|partition"` → **零命中**，无分片实现
- 活跃 DB 仅 `corpus_store.db`（54 表 / 63,666 行 / 33MB），其余 5 个 .db 为 0 表空壳
- `grep -rn "review_holds("` → 除定义外**零调用**，仅 `memory_class_backfill.py` 注释提及
- `corpus_store.db` journal_mode=wal（被动）；无显式 PRAGMA；dfcp_memory.db=delete
- 全仓无 BEGIN IMMEDIATE / busy_handler / retry
- store_pending.jsonl 实测 13 行，alias_pending 36KB
- 最大表 trace_cited 40239 行（唯一有量的表，但非分片级压力）