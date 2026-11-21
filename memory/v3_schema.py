"""
v3 底座重设计·第一批建表脚本（2026-11-15 用户裁决：单库/三前缀/一次性迁移/单向导出）

core_*   根资产（绝不丢）
audit_*  审计事件（留主库）
trace_*  流水埋点（30天窗口+月度归档）

幂等：可重复执行（CREATE IF NOT EXISTS + 表结构检查）。
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "memory", "corpus_store.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

-- ============ core_* 根资产 ============

-- ① 认知原子卡（241张迁入；superseded_by 版本链为新增能力）
CREATE TABLE IF NOT EXISTS core_memory_card (
  inst_id       TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,              -- 入库时间（永不改）
  event_ts      TEXT,                       -- 事件真实时间（2026-11-09 时间概念工程）
  full_hash     TEXT NOT NULL UNIQUE,       -- 幂等护栏①
  asset_class   TEXT NOT NULL,              -- corpus | runtime
  repro         TEXT NOT NULL,              -- flash | situational | derived
  confidence    TEXT NOT NULL,              -- L0-L4
  dfcp_tag      TEXT NOT NULL,              -- JSON {domain,behavior,relation,memory_class}
  affect_tags   TEXT,                       -- JSON
  summary       TEXT NOT NULL,
  summary_converged TEXT,
  detail        TEXT,
  source_ref    TEXT,
  origin_ids    TEXT,                       -- JSON list
  entity_id     TEXT,
  derived_from  TEXT,                        -- JSON list（蒸馏溯源，上限50）
  tier          TEXT,
  weight        REAL DEFAULT 1.0,
  protected     INTEGER DEFAULT 0,
  superseded_by TEXT,                        -- ★新：版本链（被覆盖/合并时指向新版）
  status        TEXT NOT NULL DEFAULT 'active',  -- active|superseded|merged（不删除只降权）
  memory_class  TEXT                        -- 冗余列：dfcp_tag.memory_class 提取，便于索引
);
CREATE INDEX IF NOT EXISTS idx_mc_class   ON core_memory_card(memory_class, status);
CREATE INDEX IF NOT EXISTS idx_mc_entity ON core_memory_card(entity_id);
CREATE INDEX IF NOT EXISTS idx_mc_ts      ON core_memory_card(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_memory_card USING fts5(
  summary, detail, content='core_memory_card', content_rowid='rowid'
);

-- ⑥ 思维范式（定稿 design_paradigm_layer.md）
CREATE TABLE IF NOT EXISTS core_thinking_paradigm (
  pid TEXT PRIMARY KEY, name TEXT NOT NULL,
  paradigm_type TEXT NOT NULL,
  trigger_when TEXT NOT NULL, procedure TEXT NOT NULL,
  precondition TEXT, failure_mode TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  superseded_by TEXT,
  evidence_count INTEGER DEFAULT 0, evidence_pass INTEGER DEFAULT 0,
  origin TEXT, ts TEXT NOT NULL, event_ts TEXT, active_time TEXT,
  last_trigger_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_tp_status ON core_thinking_paradigm(status, paradigm_type);

-- ⑥ 技能范式
CREATE TABLE IF NOT EXISTS core_skill_paradigm (
  pid TEXT PRIMARY KEY, name TEXT NOT NULL,
  skill_domain TEXT NOT NULL,
  trigger_when TEXT NOT NULL, op_sequence TEXT NOT NULL,
  precondition TEXT, failure_mode TEXT, requires_tools TEXT,
  status TEXT NOT NULL DEFAULT 'candidate', superseded_by TEXT,
  evidence_count INTEGER DEFAULT 0, evidence_pass INTEGER DEFAULT 0,
  origin TEXT, ts TEXT NOT NULL, event_ts TEXT, active_time TEXT,
  last_trigger_ts TEXT
);

-- ⑥ 出厂设置（persona_anchor 迁入）
CREATE TABLE IF NOT EXISTS core_persona_anchor (
  id INTEGER PRIMARY KEY, phase TEXT NOT NULL,
  summary TEXT NOT NULL, detail TEXT,
  source_ref TEXT, ts TEXT NOT NULL
);

-- ⑥ 经历演化账（新：性格基线数据源）
CREATE TABLE IF NOT EXISTS core_persona_evolution (
  eid TEXT PRIMARY KEY, event_ts TEXT NOT NULL,
  kind TEXT NOT NULL,               -- milestone|pattern|scar|identity_ruling
  summary TEXT NOT NULL, detail TEXT,
  evidence TEXT,                    -- JSON: 来源卡inst_id/对话log_id
  impact TEXT,                      -- 影响prompt维度
  weight REAL DEFAULT 1.0,
  active INTEGER DEFAULT 1,
  ts TEXT NOT NULL
);

-- ⑥ 他人实体血统
CREATE TABLE IF NOT EXISTS core_entity_timeline (
  entity TEXT NOT NULL, event TEXT NOT NULL,
  event_ts TEXT, summary TEXT, evidence TEXT,
  ts TEXT NOT NULL, PRIMARY KEY (entity, event)
);

-- ② 关系边（177条迁入）
CREATE TABLE IF NOT EXISTS core_relation (
  me_inst TEXT NOT NULL, other_inst TEXT NOT NULL,
  relation TEXT NOT NULL,            -- equivalent|augment|conflict|new
  judge_meta TEXT, ts TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  PRIMARY KEY (me_inst, other_inst)
);
CREATE INDEX IF NOT EXISTS idx_rel_other ON core_relation(other_inst);

-- ② 主题锚 / 挂载（自旧表迁移，结构与 topic_abstraction/topic_links 一致）
CREATE TABLE IF NOT EXISTS core_topic_abstraction (
  topic_id TEXT PRIMARY KEY, topic_abstract TEXT NOT NULL,
  topic_vector TEXT, cue_anchors TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  create_ts TEXT NOT NULL, update_ts TEXT NOT NULL,
  mount_count INTEGER NOT NULL DEFAULT 0,
  access_count INTEGER NOT NULL DEFAULT 0,
  manual_seed INTEGER NOT NULL DEFAULT 0,
  inspect_freq TEXT NOT NULL DEFAULT 'normal'
);
CREATE TABLE IF NOT EXISTS core_topic_links (
  inst_id TEXT NOT NULL, topic_id TEXT NOT NULL,
  method TEXT NOT NULL, ts TEXT NOT NULL,
  PRIMARY KEY (inst_id, topic_id)
);

-- ② 经验层（17条迁入）
CREATE TABLE IF NOT EXISTS core_experience (
  xid TEXT PRIMARY KEY, summary TEXT NOT NULL, detail TEXT,
  polarity TEXT,                     -- positive|negative（负经验禁固化）
  referenced_by TEXT,                -- JSON: 关联原子
  tier TEXT, status TEXT NOT NULL DEFAULT 'active',
  ts TEXT NOT NULL, event_ts TEXT
);

-- ① 语料登记（raw_corpus 迁入）
CREATE TABLE IF NOT EXISTS core_raw_corpus (
  id INTEGER PRIMARY KEY, kind TEXT, tree_vol TEXT, tree_ch TEXT,
  title TEXT NOT NULL, body TEXT, version TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  superseded_by TEXT, content_hash TEXT, source_ref TEXT, ts TEXT NOT NULL
);

-- ① 语料技术簇标签（corpus_tags 迁入）
CREATE TABLE IF NOT EXISTS core_corpus_tags (
  corpus_id INTEGER NOT NULL, tag_key TEXT NOT NULL, tag_value TEXT,
  PRIMARY KEY (corpus_id, tag_key)
);

-- ⑤ 对话全量原文（新）
CREATE TABLE IF NOT EXISTS core_dialogue_log (
  log_id TEXT PRIMARY KEY,           -- dlg_<date>_<seq>
  session_id TEXT NOT NULL, ts TEXT NOT NULL,
  role TEXT NOT NULL,                -- user | assistant
  turn INTEGER, content TEXT NOT NULL,
  session_topic TEXT,
  content_hash TEXT,                 -- 幂等
  retrieved INTEGER DEFAULT 0,
  linked_cards TEXT,                 -- JSON: 沉淀卡inst_id列表（双向锚）
  fts TEXT
);
CREATE INDEX IF NOT EXISTS idx_dlg_session ON core_dialogue_log(session_id, turn);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_dialogue USING fts5(
  content, content='core_dialogue_log', content_rowid='rowid'
);

-- ============ 治理队列组（core_*，+生命周期字段 3.8） ============

CREATE TABLE IF NOT EXISTS core_hold_pool (
  inst_id TEXT NOT NULL, reason TEXT, reference_mention INTEGER DEFAULT 0,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT,
  PRIMARY KEY (inst_id, enqueued_ts)
);
CREATE TABLE IF NOT EXISTS core_conflict_queue (
  me_inst TEXT NOT NULL, other_inst TEXT NOT NULL,
  kind TEXT, enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT,
  PRIMARY KEY (me_inst, other_inst, enqueued_ts)
);
CREATE TABLE IF NOT EXISTS core_quarantine (
  inst_id TEXT PRIMARY KEY, reason TEXT,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS core_alias_pending (
  id INTEGER PRIMARY KEY, alias TEXT NOT NULL, target TEXT NOT NULL,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS core_term_alias (
  alias TEXT NOT NULL, canonical TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', ts TEXT NOT NULL,
  PRIMARY KEY (alias, canonical)
);
CREATE TABLE IF NOT EXISTS core_governor_pending (
  gpid TEXT PRIMARY KEY, proposal TEXT NOT NULL, kind TEXT,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS core_class_backfill_pending (
  inst_id TEXT NOT NULL, suggested_class TEXT,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT,
  PRIMARY KEY (inst_id, enqueued_ts)
);
CREATE TABLE IF NOT EXISTS core_watchlist (
  wid TEXT PRIMARY KEY, subject TEXT, note TEXT,
  enqueued_ts TEXT NOT NULL,
  verdict TEXT, verdict_ts TEXT, resolved_by TEXT
);

-- ============ audit_* 审计事件 ============

CREATE TABLE IF NOT EXISTS audit_judge (
  jid INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  me_inst TEXT, other_inst TEXT, relation TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS audit_distill (
  did INTEGER PRIMARY KEY, ts TEXT NOT NULL, op TEXT,
  inst_id TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS audit_confidence (
  cid INTEGER PRIMARY KEY, inst_id TEXT NOT NULL,
  old_level TEXT, new_level TEXT, reason TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_topic_log (
  lid INTEGER PRIMARY KEY, ts TEXT NOT NULL, op TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS audit_iteration (
  iid INTEGER PRIMARY KEY, ts TEXT NOT NULL, target TEXT,
  action TEXT, what TEXT, why TEXT
);
CREATE TABLE IF NOT EXISTS audit_code_change (
  cid INTEGER PRIMARY KEY, ts TEXT NOT NULL, target TEXT, change TEXT
);
CREATE TABLE IF NOT EXISTS audit_maintenance (
  mid INTEGER PRIMARY KEY, ts TEXT NOT NULL, case_desc TEXT, resolution TEXT
);
CREATE TABLE IF NOT EXISTS audit_pool (
  pid INTEGER PRIMARY KEY, ts TEXT NOT NULL, pool TEXT, event TEXT
);
CREATE TABLE IF NOT EXISTS audit_paradigm_evidence (
  eid INTEGER PRIMARY KEY, pid TEXT NOT NULL, ts TEXT NOT NULL,
  outcome TEXT NOT NULL, context TEXT, delta TEXT, source_log TEXT
);

-- ============ trace_* 流水埋点（30天窗口+月度归档） ============

CREATE TABLE IF NOT EXISTS trace_cited (
  tid INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  query TEXT, inst_id TEXT, score REAL, source TEXT  -- source: human/e8(2026-11-15切换加列, E8演习防自肥)
);
CREATE INDEX IF NOT EXISTS idx_cited_ts ON trace_cited(ts);
CREATE INDEX IF NOT EXISTS idx_cited_inst ON trace_cited(inst_id);
CREATE TABLE IF NOT EXISTS trace_call (
  tid INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  entry TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS trace_embedding (
  tid INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  model TEXT, n_items INTEGER
);
"""


def build():
    c = sqlite3.connect(DB, timeout=5)
    c.executescript(SCHEMA)
    # 与旧表并存：旧 topic_abstraction/topic_links/persona_anchor/raw_corpus/corpus_tags/iterations
    # 暂不 DROP，迁移对账完成后再冻结（第二批清退）
    c.commit()
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    n_new = len([t for t in tables if t.split('_')[0] in ('core','audit','trace','fts')])
    print(f"建表完成: 新体系表 {n_new} 张（含FTS），全部已存在表保留不动")
    c.close()


if __name__ == "__main__":
    build()