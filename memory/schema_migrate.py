"""schema 版本管理 + 迁移执行器（2026-11-21 P2-1）

背景: corpus_store / v3_schema 原只用 CREATE TABLE IF NOT EXISTS 幂等建表,
     没有 schema_version 元表, 枚举白名单(schema.py)变更后历史数据静默违规
     (实测: 38 条 memory_class='observation', 43 条浮点 confidence, 17 条非法
     domain/behavior), 且 quarantine.jsonl 已积压 32 条历史拒收。

本模块提供:
  1. schema_version 元表(version/name/applied_ts/detail), 记录已应用迁移
  2. 迁移注册表 MIGRATIONS: 有序 (version, name, fn) 列表
  3. run_migrations(db_path, dry_run): 逐条应用未执行迁移, 支持 dry-run 预览
  4. 每条迁移在事务内执行, 成功后写 schema_version; 失败回滚不推进版本

纪律:
  - 迁移只做数据规范化, 不 DROP 用户数据; 破坏性动作必须先在 audit 表留快照
  - 新迁移一律追加到 MIGRATIONS 末尾, 禁止修改已发布迁移(已应用版本幂等跳过)
  - 每个迁移自带 verify 断言, run_migrations 在提交前跑 verify

用法:
  python memory/schema_migrate.py            # 应用全部未执行迁移
  python memory/schema_migrate.py --dry-run  # 只预览将执行哪些迁移
  python memory/schema_migrate.py --status   # 查看当前版本
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(HERE), "data", "memory", "corpus_store.db")

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,     -- 迁移序号
    name       TEXT NOT NULL,           -- 迁移名称
    applied_ts TEXT NOT NULL,           -- 应用时间
    detail     TEXT                     -- 执行摘要(JSON)
);
"""


# ---------- 迁移实现 ----------

def _m0001_enum_normalize(con):
    """0001 枚举规范化: 清洗存量非法 memory_class / confidence。

    处置口径(保守, 不臆造分类):
    - memory_class='observation'(白名单外) → NULL(普通记录, schema 注释"null 合法")
      原值快照写入 audit_schema_migrate, 可回溯。
    - confidence 浮点(0.6/0.85/0.9/0.95 等) → L 档:
        0.9/0.95 → L1(强推论); 0.85 → L2(弱推论); 0.6 → L3(臆测)
      映射依据: schema CONFIDENCE_LEVELS 语义(L0实测证伪~L4公理), 取最近档。
    - asset_class 脏值(observation/insight/high_value/capability_pact/domain_tool):
      代码层无枚举校验(仅注释 corpus|runtime), 属业务分类扩展, **不在本迁移改动**,
      仅统计留档(强改会丢业务语义)。
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_schema_migrate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mig_version INTEGER, inst_id TEXT, field TEXT,
            old_val TEXT, new_val TEXT, ts TEXT
        )
    """)
    now = datetime.now().isoformat(timespec="seconds")
    # 记录审计基准: 需要修复的 inst_id 列表
    con.execute("DROP TABLE IF EXISTS temp._fix_mc")
    con.execute("""
        CREATE TEMP TABLE _fix_mc AS
        SELECT inst_id FROM core_memory_card
        WHERE memory_class IS NOT NULL
          AND memory_class NOT IN ('strategy_evolve','skill_refine','scar_case',
                                   'relation_milestone','key_pact')
    """)
    n_mc = con.execute("SELECT COUNT(*) FROM _fix_mc").fetchone()[0]
    # 明细快照(逐条 old_val)
    for r in con.execute("""
        SELECT c.inst_id, c.memory_class FROM core_memory_card c
        JOIN _fix_mc f ON c.inst_id=f.inst_id
    """).fetchall():
        con.execute("INSERT INTO audit_schema_migrate (mig_version,inst_id,field,old_val,new_val,ts)"
                    " VALUES (1,?,?,?,NULL,?)", (r[0], "memory_class", r[1], now))
    con.execute("""
        UPDATE core_memory_card SET memory_class=NULL
        WHERE memory_class IS NOT NULL
          AND memory_class NOT IN ('strategy_evolve','skill_refine','scar_case',
                                   'relation_milestone','key_pact')
    """)

    # confidence 浮点 → L 档
    conf_map = {"0.95": "L1", "0.9": "L1", "0.85": "L2", "0.6": "L3"}
    rows = con.execute(
        "SELECT inst_id, confidence FROM core_memory_card "
        "WHERE confidence NOT IN ('L0','L1','L2','L3','L4')").fetchall()
    for inst, old in rows:
        new = conf_map.get(old, "L2")     # 未知浮点兜底 L2
        con.execute("INSERT INTO audit_schema_migrate (mig_version,inst_id,field,old_val,new_val,ts)"
                    " VALUES (1,?,?,?,?,?)", (inst, "confidence", old, new, now))
        con.execute("UPDATE core_memory_card SET confidence=? WHERE inst_id=?", (new, inst))

    # asset_class 脏值只统计
    ac_rows = con.execute(
        "SELECT asset_class, COUNT(*) FROM core_memory_card "
        "WHERE asset_class NOT IN ('corpus','runtime') GROUP BY asset_class").fetchall()
    return {"memory_class_fixed": n_mc, "confidence_fixed": len(rows),
            "asset_class_observed": dict(ac_rows),
            "note": "asset_class 业务分类扩展未改动, 仅统计"}


def _v0001(con):
    """0001 verify: 清洗后不得再有非法 memory_class / 非法 confidence。"""
    bad_mc = con.execute(
        "SELECT COUNT(*) FROM core_memory_card WHERE memory_class IS NOT NULL "
        "AND memory_class NOT IN ('strategy_evolve','skill_refine','scar_case',"
        "'relation_milestone','key_pact')").fetchone()[0]
    bad_conf = con.execute(
        "SELECT COUNT(*) FROM core_memory_card "
        "WHERE confidence NOT IN ('L0','L1','L2','L3','L4')").fetchone()[0]
    return bad_mc == 0 and bad_conf == 0, {"bad_mc": bad_mc, "bad_conf": bad_conf}


def _m0002_deprecate_zombie(con):
    """0002 僵尸表标记: persona_anchor 已迁移至 core_persona_anchor(add_anchor 只写 core_),
    旧表冻结不再写入。本迁移不改数据(保留可追溯), 仅把 SCHEMA 定义移出并由 v3_schema 注释
    标注 deprecated。此处记录迁移事实, 供审计。"""
    has_old = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='persona_anchor'").fetchone()
    n_core = 0
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                   "AND name='core_persona_anchor'").fetchone():
        n_core = con.execute("SELECT COUNT(*) FROM core_persona_anchor").fetchone()[0]
    return {"persona_anchor_exists": bool(has_old), "core_persona_anchor_rows": n_core,
            "action": "冻结保留, 定义已从 corpus_store.SCHEMA 移除, 不 DROP 数据"}


def _v0002(con):
    return True, {"note": "标记类迁移, 无需断言"}


# 迁移注册表: (version, name, fn, verify_fn)
MIGRATIONS = [
    (1, "enum_normalize_2026_11_21", _m0001_enum_normalize, _v0001),
    (2, "deprecate_persona_anchor", _m0002_deprecate_zombie, _v0002),
]


# ---------- 执行器 ----------

def _ensure_version_table(con):
    con.executescript(SCHEMA_VERSION_DDL)


def _applied_versions(con):
    try:
        return {r[0] for r in con.execute("SELECT version FROM schema_version")}
    except sqlite3.OperationalError:
        return set()


def run_migrations(db_path=DB_PATH, dry_run=False):
    con = sqlite3.connect(db_path)
    con.isolation_level = None          # 手动事务控制
    _ensure_version_table(con)
    applied = _applied_versions(con)
    report = {"applied": [], "skipped": [], "failed": None, "dry_run": dry_run}

    for version, name, fn, verify in MIGRATIONS:
        if version in applied:
            report["skipped"].append({"version": version, "name": name})
            continue
        if dry_run:
            report["applied"].append({"version": version, "name": name, "dry_run": True})
            continue
        try:
            con.execute("BEGIN")
            detail = fn(con)
            ok, vinfo = verify(con)
            if not ok:
                raise RuntimeError(f"verify 失败: {vinfo}")
            con.execute(
                "INSERT INTO schema_version (version,name,applied_ts,detail) VALUES (?,?,?,?)",
                (version, name, datetime.now().isoformat(timespec="seconds"),
                 json.dumps({"result": detail, "verify": vinfo}, ensure_ascii=False)))
            con.execute("COMMIT")
            report["applied"].append({"version": version, "name": name, "detail": detail})
        except Exception as e:  # noqa: BLE001
            con.execute("ROLLBACK")
            report["failed"] = {"version": version, "name": name, "error": str(e)}
            break
    con.close()
    return report


def current_version(db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    _ensure_version_table(con)
    rows = con.execute("SELECT version,name,applied_ts FROM schema_version ORDER BY version").fetchall()
    con.close()
    return rows


def main():
    if "--status" in sys.argv:
        rows = current_version()
        if not rows:
            print("[schema] 无已应用迁移")
        for v, n, t in rows:
            print(f"  v{v} {n} @ {t}")
        return 0
    dry = "--dry-run" in sys.argv
    r = run_migrations(dry_run=dry)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if not r["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())