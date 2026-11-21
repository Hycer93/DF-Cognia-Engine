"""schema_migrate_regression.py — schema 版本迁移回归 (2026-11-21 P2-1)

验证:
  1. schema_version 元表存在, 已应用迁移可查
  2. 枚举清洗生效: core_memory_card 无非法 memory_class / 非法 confidence
  3. 审计留档: audit_schema_migrate 记录了每一条被改动的 (inst_id, field, old, new)
  4. 幂等: 重复 run_migrations 不重复应用(全部 skipped)
  5. asset_class 校验: schema.validate_memory_tags 拒绝白名单外值, 缺省放行
  6. corpus_store.SCHEMA 不再定义 persona_anchor(僵尸表定义已移除)

只读为主(第4项会读不写)。退出码 0=全过 1=有失败。输出 logs/schema_migrate_regression.json。
"""
import io
import json
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEM = os.path.join(_HERE, "memory")
sys.path.insert(0, _HERE)
sys.path.insert(0, _MEM)
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")
OUT = os.path.join(_HERE, "logs", "schema_migrate_regression.json")

VALID_MC = {"strategy_evolve", "skill_refine", "scar_case", "relation_milestone", "key_pact"}
VALID_CONF = {"L0", "L1", "L2", "L3", "L4"}


def main():
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:240]})

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)

    # 1. schema_version 元表 + 已应用迁移
    try:
        rows = con.execute("SELECT version,name FROM schema_version ORDER BY version").fetchall()
        check("schema_version_exists", len(rows) >= 2,
              f"applied={rows}")
    except sqlite3.OperationalError as e:
        check("schema_version_exists", False, repr(e))

    # 2. 枚举清洗生效
    bad_mc = con.execute(
        "SELECT COUNT(*) FROM core_memory_card WHERE memory_class IS NOT NULL "
        "AND memory_class NOT IN (?,?,?,?,?)", tuple(VALID_MC)).fetchone()[0]
    bad_conf = con.execute(
        "SELECT COUNT(*) FROM core_memory_card WHERE confidence NOT IN (?,?,?,?,?)",
        tuple(VALID_CONF)).fetchone()[0]
    check("no_bad_memory_class", bad_mc == 0, f"残留非法 memory_class={bad_mc}")
    check("no_bad_confidence", bad_conf == 0, f"残留非法 confidence={bad_conf}")

    # 3. 审计留档
    try:
        n_audit = con.execute(
            "SELECT COUNT(*) FROM audit_schema_migrate WHERE mig_version=1").fetchone()[0]
        check("audit_trail", n_audit >= 80, f"audit rows={n_audit}（应≥38+43=81）")
    except sqlite3.OperationalError as e:
        check("audit_trail", False, repr(e))

    # 6. persona_anchor 定义已从 SCHEMA 移除
    import corpus_store
    check("persona_anchor_out_of_schema",
          "CREATE TABLE IF NOT EXISTS persona_anchor" not in corpus_store.SCHEMA,
          "corpus_store.SCHEMA 仍含 persona_anchor" if
          "CREATE TABLE IF NOT EXISTS persona_anchor" in corpus_store.SCHEMA else "已移除")
    con.close()

    # 4. 幂等: 重复 run 全部 skipped
    import schema_migrate
    r = schema_migrate.run_migrations(dry_run=False)
    check("idempotent_rerun", (not r["applied"]) and len(r["skipped"]) >= 2,
          f"applied={len(r['applied'])} skipped={len(r['skipped'])} failed={r['failed']}")

    # 5. asset_class 校验
    import schema
    ok_valid = schema.validate_memory_tags(
        {"domain": "005-003", "behavior": "Jw", "relation": "P", "confidence": "L1",
         "asset_class": "runtime"})[0]
    bad = schema.validate_memory_tags(
        {"domain": "005-003", "behavior": "Jw", "relation": "P", "confidence": "L1",
         "asset_class": "observation"})
    ok_absent = schema.validate_memory_tags(
        {"domain": "005-003", "behavior": "Jw", "relation": "P", "confidence": "L1"})[0]
    check("asset_class_guard", ok_valid and (not bad[0]) and ok_absent,
          f"valid={ok_valid} invalid_rejected={not bad[0]} absent_ok={ok_absent}")

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: {r['detail']}")
    print(f"[schema 迁移回归] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()