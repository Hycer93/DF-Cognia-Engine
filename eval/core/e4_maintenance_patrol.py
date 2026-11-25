# eval/e4_maintenance_patrol.py — 夜间维护巡逻（只读，只报警，不自动处理任何数据）
# 对策来源：2026-11-09 用户风险审查（风险2/4/6）。
# 原则：机器不私自处理数据——多主题混杂、000超期、隔离区、watchlist 都只统计计数，
#       提醒人处理；不能越堆越多没人管。
# 产物：logs/e4_maintenance.json + stdout 摘要；nightly_run.py STEPS 第4步。

import json
import io
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))
MEM_DIR = os.path.join(HERE, "data", "memory")

import memory_organize as mo  # noqa: E402
import memory_distill as md   # noqa: E402
import memory_relations as mr # noqa: E402


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_calib():
    """阈值统一收拢（件③）：读 calibration/nightly_calib.toml；无文件时返回内联安全默认。
    变更阈值必须改 toml 并留 [changelog]，晨报输出当前值——代码内不出现判定阈值数字。"""
    path = os.path.join(HERE, "calibration", "nightly_calib.toml")
    if os.path.exists(path):
        try:
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            # py<3.11 fallback：极简行解析（仅支持 key = value）
            out = {}
            section = None
            with io.open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if line.startswith("["):
                        section = line.strip("[]"); out[section] = {}
                    elif "=" in line and section:
                        k, v = [x.strip() for x in line.split("=", 1)]
                        try:
                            out[section][k] = int(v)
                        except ValueError:
                            try:
                                out[section][k] = float(v)
                            except ValueError:
                                out[section][k] = v
            return out
    return {"judge_governance": {"store_pending_warning": 10, "store_pending_critical": 50,
                                 "discard_rate_warning": 0.60, "hold_age_days": 14,
                                 "batch_limit": 8}}


def patrol(as_of=None):
    now = datetime.now()
    mems = mo.load_memories()
    by_id = {m["inst_id"]: m for m in mems}
    report = {"ts": now.isoformat(), "total_memories": len(mems), "alerts": [], "items": {}}

    # 0. DB↔jsonl 双写对账(P-db-recon, 2026-11-18): corpus_store.db 与 memories.jsonl
    #    count 差值即迁移漂移, 差值>0 报警——此前仅晨报人工看一行数字, 无自动告警。
    #    2026-11-18 晨报实证: db 286 vs jsonl 267, 差值 19 无人感知。
    try:
        import sqlite3 as _sq
        _db = os.path.join(HERE, "data", "memory", "corpus_store.db")
        _c = _sq.connect(f"file:{_db}?mode=ro", uri=True, timeout=5)
        _db_n = _c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
        _c.close()
        _drift = _db_n - len(mems)
        report["items"]["db_jsonl_drift"] = _drift
        report["items"]["db_card_total"] = _db_n
        if _drift != 0:
            report["alerts"].append(
                "DB↔jsonl 迁移漂移 " + ("+" if _drift > 0 else "") + str(_drift) +
                f" (db={_db_n} vs jsonl={len(mems)})——差值非零须人工对账归零后才可切换读源")
    except Exception as _e:
        report["items"]["db_jsonl_drift"] = None
        report["alerts"].append(f"DB↔jsonl 对账失败(不计入失败项, 须查): {_e}")

    # 1. 多主题混杂统计（风险2）——冻结的 + 嫌疑的都数
    multi_frozen = [m for m in mems if m.get("multi_topic")]
    report["items"]["multi_topic_frozen"] = len(multi_frozen)
    if multi_frozen:
        report["alerts"].append(
            f"多主题冻结 {len(multi_frozen)} 条待人工拆解（multi_topic=true，认知关联层已隔离）")
    # 嫌疑粗口径：runtime 条目 detail+summary 里命中 ≥2 个不同域的谓词关键词组。
    # Week2 multi_topic_suspect 检测门上线后以正式判定为准（届时替换本段）。
    import schema as sch
    suspect_n = 0
    for m in mems:
        if m.get("multi_topic") or m.get("asset_class") == "corpus":
            continue
        text = (m.get("detail") or "") + (m.get("summary") or "")
        if not text:
            continue
        hit_domains = set()
        for kw, dom in sch.PREDICATES:
            head, _, tail = kw.partition("=")
            probes = {head, tail}
            if any(p and p in text for p in probes):
                hit_domains.add(dom.split("-")[0])
        if len(hit_domains) >= 2:
            suspect_n += 1
    report["items"]["multi_topic_suspect_rough"] = suspect_n
    if suspect_n > 0:
        report["alerts"].append(
            f"疑似多主题 {suspect_n} 条（粗口径：跨域关键词共现，Week2 检测门上线后以正式判定为准）")

    # 2. 000 待分类超期（风险4）——只报警，绝不自动删/降级
    expired = mo.enforce_unclassified_limit()
    near = [m["inst_id"][:8] for m in mems
            if m["dfcp_tag"]["domain"].startswith("000")
            and m.get("unclassified_cycles", 0) == 1]
    report["items"]["unclassified_expired"] = len(expired)
    report["items"]["unclassified_near_limit"] = len(near)
    if expired:
        report["alerts"].append(
            f"000 待分类超期 {len(expired)} 条：{expired}——需人工归类，机器不自动处理")
    if near:
        report["alerts"].append(f"000 待分类临期（cycles=1）{len(near)} 条，下周期将超期")

    # 3. 隔离区积压（非法编码拒收）
    quar = mo.load_quarantine()
    recent_quar = [q for q in quar
                   if datetime.fromisoformat(q["ts"][:19]) > now - timedelta(days=7)]
    report["items"]["quarantine_total"] = len(quar)
    report["items"]["quarantine_last7d"] = len(recent_quar)
    if recent_quar:
        # 2026-11-16 用户终审: 按拒收原因分组统计, 明确告知校验拦截正常非管道故障
        def _quar_category(reasons):
            rs = reasons if isinstance(reasons, list) else [reasons]
            cats = set()
            for r in rs:
                if "枚举" in r or "白名单" in r or "非法关联码" in r or "非法置信度" in r:
                    cats.add("枚举非法值")
                elif "可复现性" in r or "repro" in r:
                    cats.add("repro来源非法")
                elif "缺" in r:
                    cats.add("标签缺失")
                elif "骨架" in r or "编码段数" in r:
                    cats.add("骨架编码错误")
                else:
                    cats.add("其他")
            return cats
        cat_count = {}
        for q in recent_quar:
            for cat in _quar_category(q.get("reason", "")):
                cat_count[cat] = cat_count.get(cat, 0) + 1
        cat_str = " | ".join(f"{k}:{v}" for k, v in sorted(cat_count.items()))
        report["items"]["quarantine_by_cat"] = cat_count
        report["alerts"].append(
            f"隔离区近7天新增 {len(recent_quar)} 条校验拦截(拦截正常, 非管道故障)——{cat_str}")

    # 4. 冲突评审队列积压（conflict 档送元思域对撞，待人工）
    conflicts = _load_jsonl(os.path.join(MEM_DIR, "conflict_queue.jsonl"))
    pending = [q for q in conflicts if q.get("status") == "pending_review"]
    report["items"]["conflict_pending"] = len(pending)
    if pending:
        report["alerts"].append(f"冲突评审积压 {len(pending)} 条（同源分叉待元思域对撞）")

    # 5. 经验层健康（风险3 提醒面）：split_pending + 覆写仅记数，真相核对永远回原子层
    exps = md.load_experiences()
    splits = [e["exp_id"] for e in exps if e.get("split_pending")]
    report["items"]["experiences"] = len(exps)
    report["items"]["experience_split_pending"] = len(splits)
    if splits:
        report["alerts"].append(f"经验超限待分裂 {len(splits)} 条：{splits}——注意经验是总结非事实源，核对回原子层")

    # 6. entity_timeline 一致性（风险6）：member 引用悬空 / 库里 runtime 无实体挂靠提醒
    try:
        import memory_query as mqv
        tl = mqv.load_entity_timeline()
    except Exception:
        tl = _load_jsonl(os.path.join(MEM_DIR, "entity_timeline.jsonl"))
    dangling = []
    for ent in tl:
        for iid in ent.get("member_inst_ids", []):
            if iid not in by_id:
                dangling.append((ent["entity_key"], iid[:8]))
    graph = mr.load_graph()
    linked_ids = {g["inst_id"] for g in graph}
    runtime_nolink = sum(1 for m in mems
                         if m.get("asset_class") != "corpus"
                         and m.get("summary") and m["inst_id"] not in linked_ids)
    report["items"]["entities"] = len(tl)
    report["items"]["entity_dangling_members"] = len(dangling)
    report["items"]["runtime_unlinked"] = runtime_nolink
    if dangling:
        report["alerts"].append(f"实体时间线悬空成员 {len(dangling)} 条（inst_id 已不在库）：{dangling[:5]}")
    if runtime_nolink:
        report["alerts"].append(
            f"runtime 未跑四档判定 {runtime_nolink} 条——entity_timeline 挂靠需先补 classify_and_link")

    # 7. event_ts null 存量进度（契约维度④）
    overlay = {r["inst_id"] for r in _load_jsonl(os.path.join(MEM_DIR, "event_ts_backfill.jsonl"))}
    nulls = [m for m in mems if not m.get("event_ts") and m["inst_id"] not in overlay]
    report["items"]["event_ts_null_remaining"] = len(nulls)
    if nulls:
        report["alerts"].append(
            f"event_ts 空值存量 {len(nulls)} 条待回填（有线索就补 backfill 叠加层）")

    # 8. watchlist 池巡检（风险6 明确点名）
    wl = _load_jsonl(os.path.join(MEM_DIR, "watchlist_pool.jsonl"))
    report["items"]["watchlist"] = len(wl)
    if wl:
        stales = [w for w in wl if w.get("status") in (None, "watching")]
        report["alerts"].append(
            f"watchlist {len(wl)} 条在池（{len(stales)} 条仍 watching 未推进）——巡检是否该转正/关闭")

    # 9. 别名表健康（2026-11-09 二级备用标签机制上线，首日泛指事故后加巡逻）
    # 脏别名=含 GATE_ANCHOR_STOPGRAMS 套话词的历史沉淀（提取侧已修，存量靠巡逻报警人工清）
    try:
        import memory_alias as malias
        alias_map = malias.effective_alias_map()
        stop = set()  # 从 constants 间接引，避免循环依赖
        sys.path.insert(0, os.path.join(HERE, "memory"))
        import constants as _C
        stop = getattr(_C, "GATE_ANCHOR_STOPGRAMS", set())
        dirty = []
        for iid, terms in alias_map.items():
            for t in terms:
                if any(w in t for w in stop):
                    dirty.append((iid[:8], t))
        pending = malias._load_pending()
        report["items"]["alias_entries"] = sum(len(v) for v in alias_map.values())
        report["items"]["alias_pending"] = len(pending)
        # v2 三层防御 L3：30天零命中读侧已失效的物理行（append-only 保留，供人决定是否 reactivate）
        recs = malias._load_alias_records()
        effective = malias.effective_alias_map()
        expired_rows = [r for r in recs if not r.get("op") and r["inst_id"] not in effective
                        and r.get("status") != "expire_inactive" and not r.get("reactivated")]
        report["items"]["alias_expired_inactive"] = len(expired_rows)
    except Exception as e:
        report["items"]["alias_patrol_error"] = str(e)

    # ---- 件②专项巡检（2026-11-10 用户终裁：memory_class 升维 dfcp_tag 第五维）----
    # 1) high_value_content_without_class：内容特征明显属五类但 memory_class 为空（双读：dfcp 内嵌+顶层）——
    #    报警不拦截（用户点名防御：防"嫌麻烦随手标null"废掉分类的附带风险）
    _MC_FEATURES = {
        "strategy_evolve": ("路线切换", "范式切换", "终裁定案", "架构切换"),
        "skill_refine": ("阈值调优", "参数微调", "流程优化"),
        "scar_case": ("事故复盘", "bug复盘", "失效复盘", "错误方案"),
        "relation_milestone": ("实体建库", "血缘打通", "关联打通"),
        "key_pact": ("规约确立", "契约定案", "准入规则", "边界约束"),
    }
    suspect_no_class = []
    for m in mems:
        mc = (m.get("dfcp_tag") or {}).get("memory_class") or m.get("memory_class")
        if mc:
            continue
        s = m.get("summary") or ""
        for cls, ws in _MC_FEATURES.items():
            if any(w in s for w in ws):
                suspect_no_class.append((m["inst_id"][:8], cls, s[:40])); break
    report["items"]["high_value_content_without_class"] = len(suspect_no_class)
    if suspect_no_class:
        report["alerts"].append(
            f"[warning] {len(suspect_no_class)} 条内容疑似五类但 memory_class=null（如 {suspect_no_class[0][2]}… "
            f"→ 疑似 {suspect_no_class[0][1]}）——人工复核是否补填；null 合法但防高价值记忆被随手标空")
    # 2) 迁移进度（双读过渡期监控）
    emb = sum(1 for m in mems if (m.get("dfcp_tag") or {}).get("memory_class"))
    top = sum(1 for m in mems if m.get("memory_class"))
    report["items"]["mc_in_dfcp_tag"] = emb
    report["items"]["mc_legacy_top_level"] = top
    # 3) 矛盾样本：顶层与 dfcp 内嵌同条不同值（迁移期口径冲突）
    dual_conflicts = sum(1 for m in mems
                         if (m.get("dfcp_tag") or {}).get("memory_class")
                         and m.get("memory_class")
                         and m["dfcp_tag"]["memory_class"] != m["memory_class"])
    report["items"]["mc_dual_field_conflict"] = dual_conflicts
    if dual_conflicts:
        report["alerts"].append(
            f"[warning] {dual_conflicts} 条顶层/dfcp 内嵌 memory_class 双值矛盾——迁移期口径冲突，人工裁定权威值")

    # 9b. 核心编码缺失巡检（2026-11-25：dfcp_tag 缺 behavior 等硬编码位）。
    #    背景: 历史 15 张旧卡 dfcp_tag 仅 {domain,relation}, 绿卡写入源硬编码遗留
    #    (见 schema.py 注释 + memory_relations.py:75 同批 11-23 补丁)。这类卡在
    #    validate_memory_tags 上线前入库, 未被隔离闸门拦截, 却会让蒸馏聚类/DFCP近邻
    #    等读取侧硬取抛 KeyError。读取侧已加防御(不崩), 此处独立巡检把它们捞出来告警,
    #    不留黑盒——需回炉重判(补 behavior)或降级审计, 机器只报不自动改。
    _missing_core = []
    for m in mems:
        dt = m.get("dfcp_tag") or {}
        miss = [f for f in ("domain", "behavior", "relation") if not dt.get(f)]
        if miss:
            _missing_core.append((m["inst_id"][:8], ",".join(miss)))
    report["items"]["core_tag_missing"] = len(_missing_core)
    if _missing_core:
        _by_field = {}
        for iid, fields in _missing_core:
            _by_field.setdefault(fields, []).append(iid)
        _sample = _missing_core[0]
        report["alerts"].append(
            f"[warning] {len(_missing_core)} 条卡 dfcp_tag 缺核心编码(如 {_sample[0]} 缺 {_sample[1]})——"
            f"按字段分布 {dict((k, len(v)) for k, v in _by_field.items())}；"
            f"读取侧已加防御不崩, 但需回炉重判补码或降级审计(机器只报不改)")

    # 10. 质量门巡检（2026-11-11 四缺口裁决: registry版本锚+占位复合检测+fragile隔离）
    try:
        import memory_quality_gate as mq
        import corpus_registry as cr
        # 10a. registry 版本锚校验: 语料变了但锚没刷 = 证据文本漂移, 卡片判定可能失真
        drifted = cr.verify_hashes()
        report["items"]["corpus_hash_drift"] = len(drifted)
        for d in drifted:
            report["alerts"].append(
                f"[quality] 语料版本漂移 {d['corpus_id']}({d.get('status')})——"
                f"指向此语料的卡片判定需复检, 合法更新应 refresh_hash")
        # 10b. 占位卡复合检测(缺口3: 相似高 AND 无新增实体/谓词/指针; 极简规约豁免)
        ph_hits = []
        for m in mems:
            d = m.get("detail") or ""
            s = m.get("summary") or ""
            if not d:
                continue
            r = mq.detect_placeholder(s, d, m.get("source_ref"))
            if r["is_placeholder"]:
                ph_hits.append((m["inst_id"][:8], s[:30], r["hint"]))
        report["items"]["placeholder_cards"] = len(ph_hits)
        for h in ph_hits[:5]:
            report["alerts"].append(
                f"[quality] 占位卡 {h[0]} 『{h[1]}』——{h[2]}(禁止带病入库, 待增肥或降级)")
        # 10c. fragile 卡统计(缺口2: 会话闪念类不参与高权重冲突判定)
        frag = [m for m in mems if m.get("fragile")]
        report["items"]["fragile_cards"] = len(frag)
    except Exception as e:
        report["items"]["quality_gate_error"] = str(e)

    # 10d. #30 护栏2（2026-11-24）：语义等价拦截全量留痕的增量告警。
    #   数据源 = data/memory/equiv_intercepts.jsonl（dry-run would_skip 与 on 真 skip 均写，见 memory_organize）。
    #   目的：解决"无声吞卡"黑盒——误拦不可感知。增量语义：与上次巡检已读条数比对，只看新增。
    #   前 EQUIV_ALERT_DETAIL 条新增加完整摘要+sim 提示人工抽检；超过后退化为计数统计。
    #   状态文件 logs/equiv_intercepts_seen.json 记录上次已读条数，避免每轮刷屏。
    try:
        _ei_path = os.path.join(getattr(mo, "MEM_DIR", os.path.join(HERE, "data", "memory")),
                                "equiv_intercepts.jsonl")
        _ei_rows = _load_jsonl(_ei_path) if os.path.exists(_ei_path) else []
        _ei_total = len(_ei_rows)
        _seen_path = os.path.join(HERE, "logs", "equiv_intercepts_seen.json")
        _seen = 0
        try:
            if os.path.exists(_seen_path):
                with io.open(_seen_path, encoding="utf-8") as _f:
                    _seen = int(json.load(_f).get("seen", 0))
        except Exception:
            _seen = 0
        _new = _ei_rows[_seen:] if _ei_total >= _seen else _ei_rows   # 日志被轮转/清空时兜底全读
        report["items"]["equiv_intercepts_total"] = _ei_total
        report["items"]["equiv_intercepts_new"] = len(_new)
        EQUIV_ALERT_DETAIL = 20
        if _new:
            report["alerts"].append(
                f"[equiv-intercept] 语义等价拦截新增 {len(_new)} 条（累计 {_ei_total}）——"
                f"请人工抽检是否误拦，误拦可从 equiv_intercepts.jsonl 捞出完整内容重入库")
            for r in _new[:EQUIV_ALERT_DETAIL]:
                report["alerts"].append(
                    f"  被拦 mode={r.get('mode')} sim={r.get('best_sim')} origin={r.get('origin')} | "
                    f"{(r.get('summary') or '')[:60]}")
            if len(_new) > EQUIV_ALERT_DETAIL:
                report["alerts"].append(
                    f"  …其余 {len(_new) - EQUIV_ALERT_DETAIL} 条见 equiv_intercepts.jsonl（已退化计数）")
        # 无论有无新增都推进 seen 水位到当前总数
        try:
            os.makedirs(os.path.dirname(_seen_path), exist_ok=True)
            with io.open(_seen_path, "w", encoding="utf-8") as _f:
                json.dump({"seen": _ei_total, "ts": now.isoformat()}, _f, ensure_ascii=False)
        except Exception:
            pass
    except Exception as e:
        report["items"]["equiv_intercepts_error"] = str(e)

    # 11. 记忆系统健康度聚合（#20 可观测性）：从既有 jsonl 聚合业务效果指标，
    #     只读统计、不改任何规则。输出到 report["health"] 供晨报人读+趋势追踪。
    try:
        report["health"] = _memory_health(mems)
    except Exception as e:
        report["health"] = {"error": str(e)}

    report["ok"] = len(report["alerts"]) == 0
    return report


def _memory_health(mems):
    """#20 记忆健康度快照：检索/冲突/去重/归档四维，只读聚合。
    指标口径见 docs/review_20_observability_20261122.md §四。"""
    import glob as _glob
    from collections import Counter
    db_dir = os.path.join(HERE, "data", "memory")
    # #30 P3 测试隔离：若 memory_organize.MEM_DIR 被重定向（回归测试），gate_log 跟随
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "memory"))
        import memory_organize as _mo
        if getattr(_mo, "MEM_DIR", None) and _mo.MEM_DIR != os.path.join(HERE, "data", "memory"):
            db_dir = _mo.MEM_DIR
    except Exception:
        pass
    mtime = lambda p: os.path.getmtime(p) if os.path.exists(p) else 0

    # — 检索：gate_log 里 probe/judge 分布 + miss_case 计数 —
    gate_rows = _load_jsonl(os.path.join(db_dir, "gate_log.jsonl"))
    op_cnt = Counter(r.get("op", "?") for r in gate_rows)
    deep_retrieve = sum(1 for r in gate_rows if r.get("deep_retrieve"))
    forced = sum(1 for r in gate_rows if r.get("forced"))
    miss_rows = _load_jsonl(os.path.join(db_dir, "gate_miss_case.jsonl"))
    retrieve_total = len(gate_rows)
    miss_rate = round(len(miss_rows) / retrieve_total, 3) if retrieve_total else None

    # — 冲突：conflict_queue 状态分布 + 待处理积压 —
    cq = _load_jsonl(os.path.join(db_dir, "conflict_queue.jsonl"))
    cq_status = Counter(r.get("status", "pending") for r in cq)
    conflict_pending_n = cq_status.get("pending", 0)

    # — 冲突三级级联分级（#20 Phase2）：relation_graph 里 best 对命中层分布 —
    # layer 1=summary 即判即冲突；2=下钻 detail 后判定；3=再下钻 source_ref。
    # 仅统计带 cascade_layer 的条目（新代码写入），旧条目自然不计。
    rg = _load_jsonl(os.path.join(db_dir, "relation_graph.jsonl"))
    cascade_dist = Counter(r.get("cascade_layer") for r in rg
                           if r.get("cascade_layer") is not None)
    cascade_total = sum(cascade_dist.values())

    # — 去重：store_pending hold_reason 分布（judge_failure/simhash 类）—
    sp = _load_jsonl(os.path.join(db_dir, "store_pending.jsonl"))
    hold_reasons = Counter(r.get("hold_reason", "?") for r in sp)

    # — 去重触发计数（#20 Phase2）：gate_log 里 dialog_dedup op + 带 simhash 的写入 —
    dedup_hits = op_cnt.get("dialog_dedup", 0)
    simhash_bearing = sum(1 for r in gate_rows if r.get("simhash"))

    # — #30 P3（2026-11-23）：P0/P2 运行效果观测三指标 —
    # #10/#11（2026-11-24）口径修复：区分 raw（原始全量）与 clean（真实业务有效）。
    # raw_skip_equiv_count: 原始全部计数（兼容旧日志回看）。
    # clean_skip_equiv_count: 仅 would_skip=true AND origin=business 的真实业务有效命中，
    #   作为切 on 评估依据。存量 29 条 re-ingest 污染已标 origin=script-reingest 被排除。
    #   origin 缺省（旧日志无该字段）视为 business（历史真实业务，保守计入 clean）。
    raw_skip_equiv_count = op_cnt.get("skip_equivalent_derived", 0) + op_cnt.get("p0_equiv_dry", 0)
    _p0_rows = [r for r in gate_rows
                if r.get("op") in ("skip_equivalent_derived", "p0_equiv_dry")]
    clean_skip_equiv_count = sum(
        1 for r in _p0_rows
        if r.get("would_skip") is True and r.get("origin", "business") == "business")
    cross_pool_warn = op_cnt.get("warn_corpus_runtime_equivalent", 0)
    # active_same_summary_multi: active 卡 summary 近似(embedding≥0.95)的多卡组数
    # 依赖 embed_semantic，失败降级为 None（不阻断巡检）
    active_same_summary_multi = None
    try:
        active_cards = [m for m in mems if (m.get("tier") or "active") == "active"
                        and not m.get("superseded_by") and m.get("summary")]
        if active_cards:
            import numpy as _np
            _mem_dir = os.path.dirname(os.path.dirname(HERE))
            sys.path.insert(0, os.path.join(_mem_dir, "memory"))
            import embed_semantic as _E
            _sums = [m["summary"] for m in active_cards]
            _E.warmup(_sums)
            _mat = _np.array([_E.embed_cached(s) for s in _sums])
            _n = len(_sums)
            _group_cnt = 0
            _seen = set()
            for i in range(_n):
                if i in _seen:
                    continue
                dup = False
                for j in range(i + 1, _n):
                    if float(_mat[i] @ _mat[j]) >= 0.95:
                        _seen.add(j); dup = True
                if dup:
                    _group_cnt += 1
                    _seen.add(i)
            active_same_summary_multi = _group_cnt
    except Exception:
        active_same_summary_multi = None

    # — 误归档率（#20 Phase2）：demote(降级 precedent) 占全库比例 —
    # demote = 外部校准把 active 降为 precedent（superseded_by 非空），非误删；
    # 比例偏高说明存入门槛/校准偏严，供趋势跟踪。只读。
    _total_mem = len(mems)
    _demoted = sum(1 for m in mems if m.get("superseded_by"))
    demote_rate = round(_demoted / _total_mem, 4) if _total_mem else None

    # — 归档：store_pending hold 老化（hold_ts 距今天数）—
    from datetime import datetime as _dt
    now = _dt.now()
    aged = []
    for r in sp:
        ts = r.get("hold_ts") or ""
        try:
            d = (now - _dt.fromisoformat(ts)).days
            aged.append(d)
        except Exception:
            pass
    max_age = max(aged) if aged else 0
    over14 = sum(1 for d in aged if d > 14)

    # — 日级趋势（#20 Phase2）：gate_log 按天聚合活动/存储/miss，供晨报看走向 —
    # 数据量尚小（初期几天样本少），指标先搭好随积累成形；只读。
    daily = {}
    for r in gate_rows:
        day = (r.get("ts") or "")[:10]
        if not day:
            continue
        dd = daily.setdefault(day, {"gate": 0, "store": 0, "dedup": 0, "miss": 0})
        dd["gate"] += 1
        op = r.get("op", "")
        if op in ("dialog_stored", "judge"):
            dd["store"] += 1
        if op == "dialog_dedup":
            dd["dedup"] += 1
    for r in miss_rows:
        day = (r.get("ts") or "")[:10]
        if day in daily:
            daily[day]["miss"] += 1
    daily_trend = dict(sorted(daily.items())[-7:])   # 近 7 天

    return {
        "retrieve": {
            "gate_total": retrieve_total,
            "deep_retrieve": deep_retrieve,
            "forced": forced,
            "op_dist": dict(op_cnt),
            "miss_cases": len(miss_rows),
            "miss_rate": miss_rate,
            "daily_trend": daily_trend,
        },
        "conflict": {
            "queue_total": len(cq),
            "status_dist": dict(cq_status),
            "pending": conflict_pending_n,
            "cascade_dist": {str(k): v for k, v in cascade_dist.items()},
            "cascade_total": cascade_total,
            "cross_pool_warn": cross_pool_warn,
        },
        "dedup": {"store_pending_total": len(sp), "hold_reason_dist": dict(hold_reasons),
                  "gate_dedup_hits": dedup_hits, "simhash_bearing": simhash_bearing,
                  "skip_equiv_count": raw_skip_equiv_count,   # 兼容旧字段名=raw
                  "raw_skip_equiv_count": raw_skip_equiv_count,
                  "clean_skip_equiv_count": clean_skip_equiv_count,
                  "active_same_summary_multi": active_same_summary_multi},
        "archive": {"hold_aged": len(aged), "max_hold_age_days": max_age,
                    "over14d": over14, "mem_total": _total_mem,
                    "demoted": _demoted, "demote_rate": demote_rate},
    }


def main():
    r = patrol()
    path = os.path.join(HERE, "logs", "e4_maintenance.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    print("E4 维护巡逻:", "全绿" if r["ok"] else f"{len(r['alerts'])} 项报警")
    for a in r["alerts"]:
        print("  [warn]", a)
    print("计数:", json.dumps(r["items"], ensure_ascii=False))
    if r.get("health") and not r["health"].get("error"):
        h = r["health"]
        print("[记忆健康度] 检索gate=%d miss=%s | 冲突q=%d(待%d) | 待归档=%d(超14d=%d) | hold最长%d天" % (
            h["retrieve"]["gate_total"], h["retrieve"]["miss_rate"],
            h["conflict"]["queue_total"], h["conflict"]["pending"],
            h["dedup"]["store_pending_total"], h["archive"]["over14d"],
            h["archive"]["max_hold_age_days"]))
        # #20 Phase2 新增维度：冲突级联分级 + 去重触发 + 误归档率
        print("[Phase2] 冲突级联分布=%s | 去重触发=%d(simhash卡%d) | 降级率=%s(%d/%d)" % (
            h["conflict"]["cascade_dist"], h["dedup"]["gate_dedup_hits"],
            h["dedup"]["simhash_bearing"], h["archive"]["demote_rate"],
            h["archive"]["demoted"], h["archive"]["mem_total"]))
    print("报告:", path)


if __name__ == "__main__":
    main()