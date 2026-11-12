"""
夜间总驱动：串联 E3(秒级) → E2(分钟级) → E1(小时级) → E1汇总。

顺序理由：先跑零成本的静态体检拿即时结论；再跑反问门标定（纯本地嵌入，
分钟级）；最后跑最贵的探针全链（123 题 × ~2-4 分钟 = 4-8 小时，正好覆盖一晚）。

每步独立落盘、独立可重跑；总控只负责串联与失败隔离（一步挂了继续下一步）。
最后写 logs/nightly_report_*.md 晨间速读版。

用法: NO_MEMORY=1 python scripts/nightly_run.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(HERE, "logs")
PY = sys.executable
ENV = {**os.environ, "NO_MEMORY": "1", "PYTHONIOENCODING": "utf-8"}

STEPS = [
    ("E3 SFT七池静态体检", [PY, "eval/e3_sft_audit_v2.py"], "logs/e3_sft_audit.json"),
    ("E2 反问门标定扫描", [PY, "eval/e2_gate_calibration.py"], "logs/e2_gate_calibration.json"),
    ("E1 探针触发率全链回归", [PY, "eval/e1_probe_trigger.py"], "logs/e1_probe_result.jsonl"),
    # E4 维护巡逻（只读报警）+ E5 自动整理（幂等软层操作，只追加不删除）
    ("E4 记忆库维护巡逻", [PY, "eval/e4_maintenance_patrol.py"], "logs/e4_maintenance.json"),
    ("E5 记忆自动整理", [PY, "eval/e5_consolidation.py"], "logs/e5_consolidation.json"),
    # E6 思维图巡逻（只读报警，2026-11-09 用户确认：记忆侧有E4/E5，思维侧专项体检）
    ("E6 思维图巡逻", [PY, "eval/e6_mind_graph_patrol.py"], "logs/e6_mind_graph_patrol.json"),
    # E8 影子对话（2026-11-11 用户裁决新增：DS仿人出题→本地检索回答→DS评分，
    # 实战检验记忆系统使用情况；按天播种幂等，题从库内真实条目抽）
    ("E8 影子对话实测", [PY, "eval/e8_shadow_dialog.py"], "logs/e8_shadow_dialog.jsonl"),
]


def run_step(name: str, cmd: list, out: str):
    print(f"\n{'='*60}\n[NIGHT] >>> {name}\n{'='*60}", flush=True)
    t0 = time.time()
    try:
        # 不用 capture——直接继承 stdout，后台日志文件里能看到全部进度
        r = subprocess.run(cmd, cwd=HERE, env=ENV, timeout=15 * 3600)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        print(f"[NIGHT] {name} 超时 15h，跳到下一步", flush=True)
    except Exception as e:
        ok = False
        print(f"[NIGHT] {name} 异常: {e}", flush=True)
    mins = round((time.time() - t0) / 60, 1)
    print(f"[NIGHT] <<< {name} {'OK' if ok else 'FAIL'} 用时 {mins}min", flush=True)
    return ok, mins


def e1_summary():
    """E1 汇总（全量重算，不依赖运行期计数）"""
    per_target = {}
    try:
        lines = open(os.path.join(LOGS, "e1_probe_result.jsonl"), encoding="utf-8").readlines()
    except FileNotFoundError:
        return per_target
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "error" in r:
            continue
        t = r["target"]
        d = per_target.setdefault(t, {"trigger": [0, 0], "no_trigger": [0, 0],
                                      "hints": [], "deepen": [0, 0], "heldout": []})
        bucket = d[r["expect"]]
        bucket[0] += 1 if r["triggered"] else 0
        bucket[1] += 1
        if r["expect"] == "trigger":
            d["hints"].append(r.get("hint_score", 0))
            d["deepen"][0] += 1 if r.get("deepened") else 0
            d["deepen"][1] += 1
            if r["id"].startswith("HELDOUT"):
                d["heldout"].append({"id": r["id"], "hit": r["triggered"],
                                     "hint": r.get("hint_score")})
    return per_target


def datetime_is_recent(ts_str, cutoff_epoch):
    """ISO 时间串是否晚于 cutoff（epoch 秒）。解析失败视为不统计。"""
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat(ts_str[:19]).timestamp() > cutoff_epoch
    except Exception:
        return False


def write_report(durations):
    ts = time.strftime("%Y%m%d_%H%M")
    path = os.path.join(LOGS, f"nightly_report_{ts}.md")
    lines = ["# 夜间实验报告（自动生成）",
             f"生成时间: {time.strftime('%Y-%m-%d %H:%M')}",
             f"总用时: {sum(d for _, d in durations):.0f} min\n",
             "## 各步状态\n"]
    for name, mins in durations:
        lines.append(f"- {name}: {mins} min")
    # E1
    per = e1_summary()
    lines.append("\n## E1 触发率\n")
    # P1-4（2026-11-12 用户裁决）：行为依赖探针标注
    # Bc 全靠节点 LLM 自发 [[DEEPEN:Bc]] 标记进链（in_planned 恒 False），
    # 属高脆弱软通路——该指标不能单独作为代码回归证据，大幅波动须换判定模型复测。
    BEHAVIOR_DEPENDENT = {"Bc": "Bc-G02依赖LLM自发DEEPEN标记进链，属行为依赖探针：单轮数值不可作代码回归证据；大幅波动须≥2个判定模型交叉复测后归因"}
    if per:
        for t, d in sorted(per.items()):
            tr, trn = d["trigger"]
            ng, ngn = d["no_trigger"]
            hints = sum(d["hints"]) / len(d["hints"]) if d["hints"] else 0
            dp, dpn = d["deepen"]
            lines.append(f"- **{t}** 正例 {tr}/{trn} ({tr/max(1,trn):.0%}) | "
                         f"负例误触 {ng}/{ngn} ({ng/max(1,ngn):.0%}) | "
                         f"hint结构命中 {hints:.0%} | DEEPEN自发 {dp}/{dpn}")
            if t in BEHAVIOR_DEPENDENT:
                lines.append(f"  - ⚠ 行为依赖探针: {BEHAVIOR_DEPENDENT[t]}")
            for h in d["heldout"]:
                lines.append(f"  - held-out {h['id']}: 路由{'✓' if h['hit'] else '✗'} "
                             f"hint={h['hint']}")
    else:
        lines.append("(未跑或无结果)")
    # E2
    lines.append("\n## E2 反问门标定\n")
    try:
        e2 = json.load(open(os.path.join(LOGS, "e2_gate_calibration.json"), encoding="utf-8"))
        lines.append(f"- 正组 n={e2['pos_n']} 负组 n={e2['neg_n']}")
        lines.append(f"- 当前阈值 {e2['current_threshold']}: "
                     f"recall={e2['verdict_015']['recall@0.15']:.0%} "
                     f"误报={e2['verdict_015']['fp@0.15']:.0%}")
        b = e2["best_f1_point"]
        lines.append(f"- F1 最优点: th={b['threshold']} recall={b['recall']:.0%} "
                     f"fp={b['false_rate']:.0%} f1={b['f1']:.2f}")
        lines.append(f"- 负组最高 match_eff: {max(e2['neg_match_eff']):.3f}（若 ≥0.15 说明阈值偏低会误报）")
    except Exception as e:
        lines.append(f"(读取失败: {e})")
    # E3
    lines.append("\n## E3 SFT 七池体检\n")
    try:
        e3 = json.load(open(os.path.join(LOGS, "e3_sft_audit.json"), encoding="utf-8"))
        s = e3["summary"]
        lines.append(f"- 总条数 {s['total_records']}, dirty {s['total_dirty']} "
                     f"({s['dirty_rate']:.1%})")
        for p in e3["pools"]:
            if p["total"] - p["clean"] > 0:
                detail = (f"占位{len(p.get('placeholder', []))} "
                          f"空内容{len(p.get('empty', []))} "
                          f"坏JSON{len(p.get('bad_json', []))} "
                          f"缺src{len(p.get('bad_src', []))} "
                          f"坏骨架码{len(p.get('bad_skeleton', []))} "
                          f"重复{len(p.get('dup', []))}")
                lines.append(f"  - {p['pool']}: {p['total']-p['clean']} 条待修 ({detail})")
                if 'note' in p:
                    lines.append(f"    - note: {p['note'][:120]}")
    except Exception as e:
        lines.append(f"(读取失败: {e})")
    # E4 维护巡逻（只读报警，机器不私自处理数据——2026-11-09 用户风险裁决）
    lines.append("\n## E4 记忆库维护巡逻\n")
    try:
        e4 = json.load(open(os.path.join(LOGS, "e4_maintenance.json"), encoding="utf-8"))
        if e4.get("ok"):
            lines.append("- 全绿：无多主题积压/000超期/隔离区异常/冲突积压/悬空引用/event_ts新增异常")
        else:
            for a in e4.get("alerts", []):
                lines.append(f"- ⚠ {a}")
        it = e4.get("items", {})
        lines.append(f"- 计数: " + " | ".join(f"{k}={v}" for k, v in it.items()))
        # 入库治理阈值当前值（件③：阈值收拢 nightly_calib.toml，晨报输出+变更可溯）
        try:
            import tomllib
            calib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "calibration", "nightly_calib.toml")
            with open(calib_path, "rb") as _f:
                calib = tomllib.load(_f)
            jg = calib.get("judge_governance", {})
            lines.append(f"- 入库治理阈值(当前): pending告警 {jg.get('store_pending_warning')}/{jg.get('store_pending_critical')} "
                         f"| discard率 {jg.get('discard_rate_warning')} | hold老化 {jg.get('hold_age_days')}天 "
                         f"| 批量上限 {jg.get('batch_limit')}")
        except Exception:
            pass
    except Exception as e:
        lines.append(f"(读取失败: {e})")
    # E5 自动整理（四档判定补挂+经验增量归纳；只动软层，原子层不碰）
    lines.append("\n## E5 记忆自动整理\n")
    try:
        e5 = json.load(open(os.path.join(LOGS, "e5_consolidation.json"), encoding="utf-8"))
        c = e5.get("ops", {}).get("classify", {})
        d = e5.get("ops", {}).get("distill", {})
        lines.append(f"- 四档判定补挂: {c.get('ok',0)}/{c.get('candidates',0)}"
                     f"（冲突送审 {c.get('conflict',0)}，跳过 {c.get('skipped',0)}）")
        lines.append(f"- 经验聚类: created={d.get('created',0)} updated={d.get('updated',0)}"
                     f" untouched={d.get('untouched',0)}")
        a = e5.get("after", {})
        lines.append(f"- 库态: 原子 {a.get('memories')} / 图谱 {a.get('graph_edges')} / 经验 {a.get('experiences')}")
        cd = e5.get("ops", {}).get("confidence_dynamics") or {}
        if cd.get("total_events") or cd.get("error"):
            lines.append(f"- 置信度动态: 升级 {cd.get('upgrades',0)} / 降级 {cd.get('downgrades',0)} "
                         f"/ 待重审积压 {cd.get('retrofix_pending',0)} / 类型规则 {cd.get('type_rules',0)}")
    except Exception as e:
        lines.append(f"(读取失败: {e})")
    # E6 思维图巡逻（只读报警——饱和/坍缩/死边/定义-执行缺口/回滚守护，2026-11-09新增）
    lines.append("\n## E6 思维图巡逻\n")
    try:
        e6 = json.load(open(os.path.join(LOGS, "e6_mind_graph_patrol.json"), encoding="utf-8"))
        if e6.get("ok"):
            lines.append(f"- 全绿：{e6.get('nodes')}节点/{e6.get('edges')}边，无饱和/坍缩/缺口")
        else:
            for a in e6.get("alerts", []):
                lines.append(f"- ⚠ {a}")
        it = e6.get("items", {})
        dv = it.get("drift_vs_backup") or {}
        lines.append(f"- 计数: 死边={it.get('dead_edges')} | 首跳集中度={it.get('first_hop_concentration_top3')} "
                     f"| 近上限边={it.get('edges_near_soft_cap')} | 对备份Δ(max_w)={dv.get('max_weight')}")
    except Exception as e:
        lines.append(f"(读取失败: {e})")
    # 赫布漂移
    lines.append("\n## 赫布边权漂移（探针跑完 vs 备份）\n")
    try:
        g_new = json.load(open(os.path.join(HERE, "data/mind_graph.json"), encoding="utf-8"))
        g_old = json.load(open(os.path.join(HERE, "data/mind_graph_backup_20261108.json"), encoding="utf-8"))
        drift = []
        for k, v in g_new["edges"].items():
            old = g_old["edges"].get(k, {}).get("weight", 0)
            d = v["weight"] - old
            if abs(d) >= 0.02:
                drift.append((k, round(old, 2), round(v["weight"], 2), round(d, 2)))
        drift.sort(key=lambda x: -abs(x[3]))
        if drift:
            for k, o, n, d in drift[:15]:
                lines.append(f"- {k}: {o}→{n} ({'+' if d>0 else ''}{d})"
                             + ("  ←已归档2026-11-12: E1探针赫布累计, 正常扰动" if k in
                                ("En->Eo", "Aa->Eo", "Dl->En", "Eo->Jw", "Fq->Jw") else ""))
            lines.append(f"\n共 {len(drift)} 条边 |Δ|≥0.02")
            lines.append("（P2-5 归档注：2026-11-12 漂移源=E1 探针跑完赫布累计，"
                         "edge_log 末条与 E1 末探针时刻吻合；详见 logs/p2_graph_drift_20261112.json）")
        else:
            lines.append("无边权显著漂移（|Δ|<0.02 全部）")
    except Exception as e:
        lines.append(f"(读取失败: {e})")

    # E7 维护范式观察（2026-11-11 案例层：案例≥3同型即列范式候选，晨报提交用户终裁）
    lines.append("\n## E7 维护范式候选\n")
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory"))
        from maintenance_ledger import load_cases, paradigm_candidates
        cases = load_cases()
        lines.append(f"- 维护案例累计: {len(cases)} 条")
        cands = paradigm_candidates()
        if cands:
            for hint, n, ids in cands:
                lines.append(f"- **范式候选** `{hint}` ×{n}（{', '.join(ids[:3])}{'…' if n > 3 else ''}）→ 待终裁 freeze")
        else:
            from collections import Counter
            hints = Counter(c["paradigm_hint"] for c in cases if c.get("paradigm_hint"))
            near = [(h, k) for h, k in hints.most_common() if k > 1]
            if near:
                lines.append("- 接近门槛: " + "; ".join(f"`{h}`×{k}" for h, k in near))
            lines.append("- 无 ≥3 同型候选（范式=3案起步，两案定势太险）")
    except Exception as e:
        lines.append(f"(案例层读取失败: {e})")

    # E8 影子对话实测（2026-11-11：DS仿人出题→检索回答→DS评分，实战使用情况）
    lines.append("\n## E8 影子对话（记忆系统实战演习）\n")
    try:
        last = None
        with open(os.path.join(LOGS, "e8_shadow_dialog.jsonl"), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)
        if last and last.get("summary"):
            s = last["summary"]
            lines.append(f"- {last['ts'][:16]} 出题{s['total']}道: "
                         f"正确 {s['correct']} | 张冠李戴 {s['wrong_entry']} | "
                         f"missA召回 {s.get('miss_A_recall',0)} / missB排序 {s.get('miss_B_ranking',0)} "
                         f"| 反问 {s['asked_back']} | DS均分 {s['avg_score']}")
            # 长期观测①（用户裁决 2026-11-11）：合格池命中率——目标记忆能否稳定进池。
            # 调阈值/换基座后若此指标掉，说明目标直接掉出合格池（完全找不到），比排序病更重
            phr = s.get("pool_hit_rate")
            if phr is not None:
                flag = "⚠ 低于0.8需查召回侧(阈值/基座/别名)" if phr < 0.8 else "ok"
                lines.append(f"- **合格池命中率: {phr:.0%}**（{flag}——长期观测项，掉=目标掉出池，A类病）")
            for q in last.get("questions", []):
                oc = q.get("outcome")
                if oc in ("miss_A_recall", "miss_B_ranking", "wrong_entry") or q.get("error"):
                    lines.append(f"  - {'⚠' if not q.get('error') else '✗'} [{oc or 'error'}] "
                                 f"目标『{q.get('target_summary','')[:36]}』问: {q.get('question','')[:40]} "
                                 f"→ {q.get('judge',{}).get('note','') if isinstance(q.get('judge'),dict) else ''}")
        else:
            lines.append("(未跑或无结果)")
    except Exception as e:
        lines.append(f"(读取失败: {e})")

    # 长期观测②（用户裁决 2026-11-11）：高bias低match拦截数——
    # 昨日 pool_audit.jsonl 里"match<0.22 但 bias>0.30 被拦合格池外"的条目次数。
    # 突然暴涨 = ①老高可信记忆与新问题语义不搭 ②embedding 漂移需重标阈值
    lines.append("\n## 检索池外拦截观测（高bias低match）\n")
    try:
        pa_path = os.path.join(LOGS, "..", "data", "memory", "pool_audit.jsonl")
        if not os.path.exists(pa_path):
            pa_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "memory", "pool_audit.jsonl")
        import time as _t
        cutoff = _t.time() - 86400
        blocked_total, queries = 0, 0
        with open(pa_path, encoding="utf-8") as f:
            for line in f:
                try:
                    pa = json.loads(line)
                except ValueError:
                    continue
                q_ts = pa.get("ts")
                if q_ts and datetime_is_recent(q_ts, cutoff):
                    queries += 1
                    blocked_total += len(pa.get("blocked_high_bias", []))
        if queries:
            avg = blocked_total / queries
            # 首日基线≈10/次（2026-11-11 标定：库内 L0/L1+近期被引条目常态在榜外聚集）
            # "暴涨"判相对基线：>15/次 视为语义漂移/老记忆过时信号
            flag = "⚠ 超常态需查语义漂移/老记忆过时(基线≈10)" if avg > 15 else "ok"
            lines.append(f"- 24h 检索 {queries} 次 | 高bias被拦条目共 {blocked_total} 条"
                         f"（均值 {avg:.1f}/次，{flag}）")
        else:
            lines.append("- 24h 无检索记录")
    except Exception as e:
        lines.append(f"(读取失败: {e})")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[NIGHT] 晨间速读报告: {path}", flush=True)
    return path


def main():
    os.makedirs(LOGS, exist_ok=True)
    print(f"[NIGHT] 开始于 {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    # 夜跑前图快照（E6回滚守护：跑前定格基线，漂移可对照回滚）
    try:
        sys.path.insert(0, HERE)
        from eval.e6_mind_graph_patrol import snapshot_for_nightly
        snap = snapshot_for_nightly()
        print(f"[NIGHT] 图快照: {snap}", flush=True)
    except Exception as e:
        print(f"[NIGHT] 图快照失败(不阻断夜跑): {e}", flush=True)
    durations = []
    for name, cmd, _ in STEPS:
        ok, mins = run_step(name, cmd, _)
        durations.append((f"{name} [{'OK' if ok else 'FAIL'}]", mins))
    write_report(durations)
    print(f"[NIGHT] 全部结束于 {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


if __name__ == "__main__":
    main()