# -*- coding: utf-8 -*-
"""
render_persona.py — 身份 SystemPrompt 渲染层 (2026-11-15 用户指令)

职能: 纯衍生物层。只读 data/memory/corpus_store.db (影子账本), 不碰检索主链路,
     不碰切换开关。读取身份基线 + 范式两表, 渲染产出:
       prompts/prompt_persona_v{N}.md   — 身份+范式融合 SystemPrompt (人闸门定稿)
       prompts/archive/                 — 历史版本归档
       prompts/diff_log.md              — v(N-1)→vN 变更 diff 日志

范式筛选排序规则 (实证通过率):
  1. status='active' 优先; candidate 附带但标注 [candidate 未达实证门槛]
  2. 排序键 = evidence_pass DESC, evidence_count DESC (实证通过率高的靠前)
  3. 长期衰减 (decay_period_days=90): active_time 超期未触发则排序降权 (排尾, 标 [衰减待激活])

版本机制: 写 v{N+1} 草稿, 不覆盖已定稿版本; 首次运行产出 v1。
"""
import sqlite3, difflib, datetime, re, os, sys

DB = "data/memory/corpus_store.db"
OUT_DIR = "prompts"
DECAY_DAYS = 90  # 与 design_paradigm_layer.md 配置 decay_period_days 对齐

def _ts(): return datetime.datetime.now().isoformat(timespec="seconds")

def load_anchor(con):
    """身份基线: 过滤已迁范式行, 按 phase 分组"""
    rows = con.execute("SELECT phase,summary,detail,source_ref,ts FROM core_persona_anchor "
                       "WHERE phase NOT LIKE '[已迁范式]%' ORDER BY id").fetchall()
    return [dict(zip(("phase","summary","detail","source_ref","ts"), r)) for r in rows]

def load_evolution(con):
    rows = con.execute("SELECT * FROM core_persona_evolution ORDER BY rowid DESC LIMIT 20").fetchall()
    cols = [d[0] for d in con.execute("SELECT * FROM core_persona_evolution LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]

def _decay_flag(p):
    """超期未触发 -> True"""
    t = p.get("active_time") or p.get("last_trigger_ts") or p.get("ts")
    if not t: return True
    try:
        d = datetime.datetime.fromisoformat(t)
        return (datetime.datetime.now() - d).days > DECAY_DAYS
    except ValueError:
        return True

def load_paradigms(con):
    """两表范式, active 优先, evidence_pass DESC / evidence_count DESC"""
    out = {"thinking": [], "skill": []}
    for table, key, body_col in (("core_thinking_paradigm","thinking","procedure"),
                                  ("core_skill_paradigm","skill","op_sequence")):
        rows = con.execute(f"""SELECT pid,name,trigger_when,{body_col} AS body,
                status,evidence_count,evidence_pass,active_time,last_trigger_ts,ts,origin
                FROM {table} WHERE status NOT IN ('superseded','retired')""").fetchall()
        cols = ["pid","name","trigger_when","body","status","evidence_count",
                "evidence_pass","active_time","last_trigger_ts","ts","origin"]
        items = [dict(zip(cols, r)) for r in rows]
        # 排序: active 在前, 再按 evidence_pass DESC, evidence_count DESC; 衰减排尾
        items.sort(key=lambda p: (p["status"]!="active", -(p["evidence_pass"] or 0),
                                  -(p["evidence_count"] or 0)))
        decayed = [p for p in items if p["status"]=="active" and _decay_flag(p)]
        fresh = [p for p in items if p not in decayed]
        out[key] = (fresh + decayed, len(decayed))
    return out

def render():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    anchor = load_anchor(con)
    evolution = load_evolution(con)
    think, think_decayed = load_paradigms(con)["thinking"]
    skill, skill_decayed = load_paradigms(con)["skill"]
    con.close()

    L = []
    L.append("# Persona — DF-Cognia 身份基线与范式融合 SystemPrompt")
    L.append(f"<!-- render_persona.py 渲染于 {_ts()}; 渲染源: core_persona_anchor {len(anchor)} 条 / "
             f"thinking {len(think)} 条 / skill {len(skill)} 条; 定稿=人闸门 -->")
    L.append("")
    L.append("## 一、身份基线 (出厂设置 + 交互史沉淀)")
    for a in anchor:
        L.append(f"### {a['phase']}")
        L.append(f"- {a['summary']}")
        L.append(f"  - 细则: {a['detail']}")
        L.append(f"  - 溯源: {a['source_ref']}")
    if evolution:
        L.append("")
        L.append("## 二、身份演化轨迹 (persona_evolution)")
        for e in evolution:
            keys = [k for k in e.keys() if k not in ("id",)]
            L.append("- " + "; ".join(f"{k}={e[k]}" for k in keys if e[k] is not None))
    L.append("")
    L.append(f"## {'三' if evolution else '二'}、思考范式 (触发条件召回, 非全量常驻)")
    L.append("> 范式仅在 trigger_when 场景命中时生效; 排序按实证通过率降序")
    for p in think:
        tag = "[衰减待激活]" if p["status"]=="active" and _decay_flag(p) else (
              "[candidate 未达实证门槛]" if p["status"]=="candidate" else "")
        L.append(f"### {p['name']} {tag}")
        L.append(f"- 触发: {p['trigger_when']}")
        L.append(f"- 程序: {p['body']}")
        L.append(f"- 实证: {p['evidence_pass']}/{p['evidence_count']} pass; 溯源: {p['origin']}")
    L.append("")
    L.append(f"## {'四' if evolution else '三'}、技能范式 (操作纪律)")
    for p in skill:
        tag = "[衰减待激活]" if p["status"]=="active" and _decay_flag(p) else (
              "[candidate 未达实证门槛]" if p["status"]=="candidate" else "")
        L.append(f"### {p['name']} {tag}")
        L.append(f"- 触发: {p['trigger_when']}")
        L.append(f"- 操作序列: {p['body']}")
        L.append(f"- 实证: {p['evidence_pass']}/{p['evidence_count']} pass; 溯源: {p['origin']}")
    L.append("")
    L.append(f"## {'五' if evolution else '四'}、边界")
    L.append("- candidate 范式未达 3 独立实证全 pass + 人终审, 不得升 active")
    L.append("- 本 prompt 为渲染衍生物, 真相在库; 任何修改走库→重渲染, 禁手改本文件")
    L.append("- 衰减超期(>90d)范式降权排尾, 连续 2fail 进 calibration_pending 夜审")
    return "\n".join(L) + "\n"

def next_version():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(f"{OUT_DIR}/archive", exist_ok=True)
    existing = [f for f in os.listdir(OUT_DIR) if re.fullmatch(r"prompt_persona_v\d+\.md", f)]
    if not existing: return 1
    return max(int(re.search(r"v(\d+)", f).group(1)) for f in existing) + 1

def write_with_diff(content):
    v = next_version()
    fname = f"{OUT_DIR}/prompt_persona_v{v}.md"
    with open(fname, "w", encoding="utf-8") as f: f.write(content)
    # diff 日志
    prev_v = v - 1
    log_path = f"{OUT_DIR}/diff_log.md"
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n## v{prev_v if prev_v>0 else '0(无)'} -> v{v}  {_ts()}\n")
        if prev_v >= 1:
            pf = f"{OUT_DIR}/prompt_persona_v{prev_v}.md"
            if os.path.exists(pf):
                old = open(pf, encoding="utf-8").read().splitlines()
                new = content.splitlines()
                diff = list(difflib.unified_diff(old, new, lineterm="", n=1))
                log.write("\n".join(diff[:200]) if diff else "(无变更)")
                log.write("\n")
        else:
            log.write("首版建立, 无前版 diff。\n")
    # 旧版归档
    if prev_v >= 1:
        os.replace(f"{OUT_DIR}/prompt_persona_v{prev_v}.md",
                   f"{OUT_DIR}/archive/prompt_persona_v{prev_v}.md")
    print(f"OK 渲染 {fname} (v{v}); 归档目录 {OUT_DIR}/archive; diff 日志 {log_path}")

if __name__ == "__main__":
    write_with_diff(render())