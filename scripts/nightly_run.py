"""
夜间总驱动（AGI 自维护底盘主链）：串联 E2(分钟级) → E1(小时级) → E4/E5/E6 → E7 治理 → T1 归档。

2026-11-15 用户裁决重构（管线分级）:
  eval/core/       常驻自维护底盘——本主链只串这些
  eval/drill/     定期演习(E8 影子对话)——已摘出主链, 按需手动触发
  eval/audit/      一次性/专项审计(E3 SFT体检/打分对账)——跑完即归档, 不常驻
  eval/experiment/ F系推演原型——实验性质, 晋升需过四条固定门槛(见 README 管线分级)
主链原则: 无人值守、纯本地、稳定闭环——依赖人工输入(标签)或外部资源的步骤一律不进主链。

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
ENV = {**os.environ, "NO_MEMORY": "1", "PYTHONIOENCODING": "utf-8",
       "TOPIC_LAYER_ON": "1"}   # 2026-11-16 用户裁决: 主题锚层全局开关正式启用
                                # (方案A)——新入库卡片自动走 mount_topic 锚判定,
                                # 零锚全部强制人工 hold, 不会自动放行/自动建锚

STEPS = [
    # E0 开关守卫(2026-11-16 用户指令: TOPIC_LAYER_ON 断层曾全程静默——本探针把
    # "开关被意外关闭/锚管道断裂"变成硬 FAIL, 不再默默业务断层)
    ("E0 topic锚开关守卫", [PY, "eval/core/e0_topic_gate_guard.py"], None, 60),
    # 2026-11-15 用户裁决: E3(一次性审计, 归 eval/audit/)与E8(影子演习, 归 eval/drill/)
    # 均摘出主链; 主链=纯本地无人值守内务闭环, 只串 eval/core/ 六脚本
    ("E2 反问门标定扫描", [PY, "eval/core/e2_gate_calibration.py"], "logs/e2_gate_calibration.json", 60),
    ("E1 探针触发率全链回归", [PY, "eval/core/e1_probe_trigger.py"], "logs/e1_probe_result.jsonl", 90),
    # E4 维护巡逻（只读报警）+ E5 自动整理（幂等软层操作，只追加不删除）
    ("E4 记忆库维护巡逻", [PY, "eval/core/e4_maintenance_patrol.py"], "logs/e4_maintenance.json", 60),
    ("E5 记忆自动整理", [PY, "eval/core/e5_consolidation.py"], "logs/e5_consolidation.json", 60),
    # E6 思维图巡逻（只读报警，2026-11-09 用户确认：记忆侧有E4/E5，思维侧专项体检）
    ("E6 思维图巡逻", [PY, "eval/core/e6_mind_graph_patrol.py"], "logs/e6_mind_graph_patrol.json", 60),
    # E7 夜间治理（2026-11-15 用户指令#30：夜间提案/hold复审/范式标定/证据回写，
    # 机器只提案不裁决，终裁=人闸门晨报）
    ("E7 夜间治理", [PY, "eval/core/e7_nightly_governance.py"], "logs/e7_governance.json", 240),
    # E10 语义离群巡检(2026-11-16 用户裁决独立立项: 本地bge embedding审卡片
    # 语义归属, 补v2.2统计链路看不见的"cue命中+统计合格但语义错挂"脏卡;
    # 与E7故障隔离(embedding异常不拖垮统计巡检); 增量触发默认+全量兜底;
    # 只读告警人闸终审; 独立E10不并E7)
    ("E10 语义离群巡检", [PY, "eval/core/e10_semantic_outlier_patrol.py"], "logs/e10_semantic_audit.json",240),
    # E9 夜间对话治理(2026-11-15 记忆门控定稿新增: 归档主通道——当日 session_dialog
    # 复跑归档判定, 高价值拟卡送pending, 只提案绝不直写主库; probe假阴性攒scar_case;
    # transcript兜底: 只读扫applog提取当日用户提交→simhash去重→复本隔离入raw_corpus)
    ("E9 对话治理", [PY, "eval/core/e9_dialog_governance.py"], "logs/e9_dialog_governance.json", 240),
    # 对话碎片巡检(2026-11-21 用户裁决: 挂 nightly 做调度底座, 但脚本内周频锁——
    # 本周已跑→静默 exit 0 不出报告不推失败; 本周首跑→统计 ≤20 字节碎片占比,
    # 超 15% 阈值 exit 1 触发晨报高亮。报告含长度分桶+t/h 通道拆分, 便于定位
    # 是批量导入还是实时 hook 引入脏数据。--force 可临时手动全量)
    ("碎片巡检(周频)", [PY, "eval/core/patrol_dialog_fragments.py"], "logs/patrol_dialog_fragments.json", 60),
    # E11 元核原子技能冒烟(2026-11-17 立项: f_reason+memory_gate 上线双准入之
    # nightly 冒烟步骤。真实 LLM 固定样例各跑一次, 断言非降级+schema+红线
    # (f_reason 无plausibility档/memory_gate 绿档类目封闭); FAIL 退出1晨报高亮;
    # 只调技能本体零 DB 写, 夜间不产生绿卡写入(不调 commit_green_cards))
    ("E11 原子技能冒烟", [PY, "eval/core/e11_atomic_skill_smoke.py"], "logs/e11_atomic_smoke.json", 90),
    # E12 skill_router 冒烟(2026-11-17 七段定稿段六③a变体: 编排组件双准入之
    # nightly 步骤, 豁免盲评。真实 LLM 一条标准故障根因目标走完整原子管线,
    # 断言 ok=True/exit_channel合法/steps≥3/fallback≤1/单技能≤2次;
    # FAIL 退出1晨报高亮; 零 DB 写, 不调 commit_green_cards)
    ("E12 路由器冒烟", [PY, "eval/core/e12_router_smoke.py"], "logs/e12_router_smoke.json", 90),
    # trace 月度归档（2026-11-15 #31 切换步骤④：主库流水开始灌入后归档能力直接就位；
    # 热窗30天, 归档前对账守恒, 切换读源后主库不再无限膨胀）
    ("T1 trace月度归档", [PY, "memory/trace_archive.py"], "logs/e7_governance.json", 60),
    # F20 夜间守卫（2026-11-16 用户裁决：挂主链尾部, 归属 experiment 不升级进 core;
    # 仅跑 baseline β=1.0 守卫基线, 保守/激进档位只在手动消融启用; PASS/FAIL 翻转→
    # 晨报高亮告警但不阻塞 E 系 core 主链; 完整快照独立 logs/f20/nightly_<date>/,
    # 晨报只放摘要。守卫自身吞错恒退出 0——experiment 层不惊扰主链步骤状态）
    ("F20 基线守卫(experiment)", [PY, "eval/experiment/f20_nightly_guard.py"], "logs/f20/guard_latest.json", 30),
]


def run_step(name: str, cmd: list, out: str, timeout_min: int):
    print(f"\n{'='*60}\n[NIGHT] >>> {name}\n{'='*60}", flush=True)
    t0 = time.time()
        # P-nightly-timeout(2026-11-18 运维合并段二裁决): 废除一刀切15h, 分级超时由
        # STEPS 注册表第四列 timeout_min 按类型绑定(纯本地60/LLM探针90/重治理240/F20 30)
    try:
        # 不用 capture——直接继承 stdout，后台日志文件里能看到全部进度
        r = subprocess.run(cmd, cwd=HERE, env=ENV, timeout=timeout_min * 60)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        print(f"[NIGHT] {name} 超时 {timeout_min}min(分级阈值)，跳到下一步", flush=True)
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
                # bad_json 兼容两代产物: v1(e3_sft_audit)=int计数, v2=e3_sft_audit_v2=list
                bj = p.get("bad_json", 0)
                bj = bj if isinstance(bj, int) else len(bj)
                detail = (f"占位{len(p.get('placeholder', []))} "
                          f"空内容{len(p.get('empty', []))} "
                          f"坏JSON{bj} "
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

    # F20 基线守卫摘要（2026-11-16 用户裁决：experiment 层, 挂主链尾部,
    # 翻转晨报高亮但不阻塞 E 系 core; 摘要来自 guard_latest.json,
    # 完整链路快照在 logs/f20/nightly_<date>/）
    # 夜间回归规约: 只校验链路可达性(判定)与排序相对位次稳定; 权重具体
    # 数值允许小幅浮动(F 因子 90d 半衰逐晚衰减属正常), 链路出局(判定翻转)
    # 或座次颠倒(top-k 位次变化)才判故障。λ_debt=0.5 锁定(用户裁决)。
    lines.append("\n## F20 基线守卫（experiment, 不阻塞 core）\n")
    try:
        with open(os.path.join(LOGS, "f20", "guard_latest.json"), encoding="utf-8") as f:
            g = json.load(f)
        if g.get("flipped") or g.get("rank_shifted"):
            lines.append("**⚠️ F20 基线异常（高亮告警，不阻塞 E 系 core 主链）**")
        else:
            lines.append("基线稳定：五场景判定与排序位次全部一致（λ_debt=0.5, β=baseline）")
        for s in g.get("scenarios", []):
            mark = (" ⚠️翻转" if s.get("flip") else "") + \
                   ("" if s.get("rank_ok") else " ⚠️位次移动")
            lines.append(f"- {s['name']}: {s['verdict']}（基线 {s['expected']}）{mark}")
        if g.get("errors"):
            lines.append(f"- 执行异常 {len(g['errors'])} 场景（吞错不阻塞）: "
                         + "; ".join(e["name"] for e in g["errors"]))
        lines.append(f"- 完整快照: `{g.get('snapshot_dir', 'logs/f20/nightly_?')}/`")
    except FileNotFoundError:
        lines.append("(守卫尚未跑过——首晚挂载后自动生成)")
    except Exception as e:
        lines.append(f"(guard_latest.json 读取失败: {e})")

    # E8 影子对话实测（2026-11-13 摘出主链, 用户裁决: 夜间主线=无人值守纯本地内务;
    # E8 依赖人工喂标签+外部DS资源, 改为按需手动触发 eval/drill/e8_shadow_dialog.py。
    # 晨报仍汇报最近一次演习落盘结果——只读 jsonl, 不跑不报错）
    lines.append("\n## E8 影子对话（记忆系统实战演习, 已摘出主链）\n")
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

    # E7 夜间治理提案（2026-11-16 自治闸门升级: 三路分流——清晰可判自动 release/keep,
    # zero_anchor/multi_anchor_ambiguity/cluster_conflict 强制人工; 标准字段对齐约定:
    # card_id/summary/check_type/rule_triggered/state/last_judge/expire_at/evidence/next_action,
    # 不随意增删; 紧急总开关 data/memory/E7_EMERGENCY_STOP 一键暂停自动挂载与消池;
    # 回滚: e7_nightly_governance.py --rollback <card_id|all> [--since 日期]）
    lines.append("\n## E7 夜间治理（自治闸门三路分流）\n")
    try:
        e7 = json.load(open(os.path.join(LOGS, "e7_governance.json"), encoding="utf-8"))
        if e7.get("emergency_stop"):
            lines.append("**⚠ 紧急总开关生效中——自动治理全暂停（file: data/memory/E7_EMERGENCY_STOP）**")
        p1 = e7.get("p1_hold", {})
        if p1.get("emergency_stop"):
            lines.append("- P1: 总开关暂停自动放行, 全部转人工提案")
        else:
            lines.append(f"- hold池分流: 待审 {p1.get('pending',0)} | **自动放行 {p1.get('auto_released',0)}** "
                         f"| 自动留置 {p1.get('auto_kept',0)} | 人工队列 {len(p1.get('manual_queue',[]))}")
        # 标准化审计明细表(固定 9 字段, 支持告警/导出复盘)
        for a in p1.get("audit_records", []):
            flag = "⚠" if a["check_type"] in ("zero_anchor", "multi_anchor_ambiguity", "cluster_conflict") else "-"
            lines.append(f"  - {flag} `{a['card_id'][:12]}…` [{a['check_type']}] {a['state']} → {a['next_action']}")
            lines.append(f"    - 卡: {a['summary'][:60]}")
            lines.append(f"    - 规则: {a['rule_triggered'][:60]} | 裁决: {a['last_judge']['by']}@{a['last_judge']['at'][:16]} | 到期: {(a['expire_at'] or '?')[:10]}")
        p2 = e7.get("p2_high_freq", {})
        if p2.get("emergency_stop"):
            lines.append("- P2: 总开关暂停高频簇巡检")
        else:
            lines.append(f"- 高频簇巡检: {p2.get('cards_checked',0)} 卡, 可疑混入 {len(p2.get('suspicious',[]))}"
                         "（p2_exempt 豁免标记正常生效）")
            for s in p2.get("suspicious", [])[:5]:
                lines.append(f"  - ⚠ {s['topic_id']}/{s['inst_id'][:12]}…: {s['note']}")
        p3 = e7.get("p3_calibration", {})
        for p in p3.get("proposals", []):
            lines.append(f"  - {'⚠' if p['proposal'] != 'propose_keep_pending' else '-'} [{p['proposal']}] "
                         f"{p.get('table','').replace('core_','').replace('_paradigm','')}/{p['pid']}: {p['note']}")
        p4 = e7.get("p4_evidence", {})
        lines.append(f"- 维护案例 {p4.get('cases',0)} 条, 候选证据回写 {p4.get('written',0)} 条(proposed非实证)")
        lines.append("  - 注: 自动放行仅 resolved_by='e7-auto' 可被 --rollback 回滚; 人工裁决(user-*)永不回滚")
    except Exception as e:
        lines.append(f"(读取失败: {e})")

    # 影子库指标看板（2026-11-15 P2-b：监控影子库增长是否正常，提前发现入库埋点/同步遗漏）
    lines.append("\n## 影子库指标看板 (corpus_store.db 影子账本)\n")
    try:
        import sqlite3 as _sq
        _db = os.path.join(HERE, "data", "memory", "corpus_store.db")
        _c = _sq.connect(f"file:{_db}?mode=ro", uri=True, timeout=5)
        _cards = _c.execute("SELECT COUNT(*) FROM core_memory_card WHERE status='active'").fetchone()[0]
        _cards_all = _c.execute("SELECT COUNT(*) FROM core_memory_card").fetchone()[0]
        _tp = _c.execute("SELECT COUNT(*) FROM core_thinking_paradigm WHERE status='active'").fetchone()[0]
        _sp = _c.execute("SELECT COUNT(*) FROM core_skill_paradigm WHERE status='active'").fetchone()[0]
        _trace = _c.execute("SELECT COUNT(*) FROM trace_cited").fetchone()[0]
        _trace_max = (_c.execute("SELECT MAX(ts) FROM trace_cited").fetchone()[0] or "?")[:16]
        _fts = _c.execute("SELECT COUNT(*) FROM fts_memory_card").fetchone()[0]
        _pending = sum(_c.execute(f"SELECT COUNT(*) FROM {t} WHERE verdict IS NULL").fetchone()[0]
                       for t in ("core_hold_pool", "core_conflict_queue", "core_alias_pending",
                                 "core_governor_pending", "core_quarantine", "core_watchlist"))
        _c.close()
        lines.append(f"- 卡片: active {_cards} / 全部 {_cards_all}（jsonl 侧 {sum(1 for _ in open(os.path.join(HERE,'data','memory','memories.jsonl'), encoding='utf-8') if _.strip())}——差值=迁移漂移，切换前须归零）")
        lines.append(f"- active 范式: thinking {_tp} + skill {_sp}")
        lines.append(f"- trace 流水: {_trace} 条（最新 {_trace_max}）")
        lines.append(f"- FTS 索引: {_fts} 条（应≈卡片全部数 {_cards_all}，缺口=索引待重建）")
        lines.append(f"- 待审队列 (verdict IS NULL): hold/conflict/alias/governor/quarantine/watchlist 合计 {_pending}")
    except Exception as e:
        lines.append(f"(读取失败: {e})")

    # ---- 存储快修余项观测校验(2026-11-18 运维合并段二裁决#3) ----
    # R14-a: 只观测不自动调参。两项均为固定检查条目, 结果只陈列供人闸晨间判读。
    lines.append("\n## 固定观测校验（R14-a 只观测不调参）\n")
    # 1) P0-1: E12 gate摘要 bigram-Jaccard 分布(E12尾部埋点落盘 e12_router_smoke.json)
    try:
        _j = json.load(open(os.path.join(LOGS, "e12_router_smoke.json"), encoding="utf-8"))
        _jd = _j.get("jaccard_observation") or {}
        lines.append(f"- P0-1 gate摘要Jaccard分布: {_jd}（阈值决策须待多晚分布积累, 勿单晚调参）")
    except Exception as e:
        lines.append(f"- P0-1 gate摘要Jaccard分布: (E12未跑或无埋点: {type(e).__name__})")
    # 2) E4 drift: P0-3 库路径修复后, 主库对账漂移是否收敛(只观测)
    try:
        _e4 = json.load(open(os.path.join(LOGS, "e4_maintenance.json"), encoding="utf-8"))
        lines.append(f"- E4 drift 对账: P0-3修复后观测——{_e4}（只看是否收敛, 不自动调参）")
    except Exception as e:
        lines.append(f"- E4 drift 对账: (读取失败: {type(e).__name__})")
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
        from eval.core.e6_mind_graph_patrol import snapshot_for_nightly
        snap = snapshot_for_nightly()
        print(f"[NIGHT] 图快照: {snap}", flush=True)
    except Exception as e:
        print(f"[NIGHT] 图快照失败(不阻断夜跑): {e}", flush=True)
    durations = []
    # P-nightly-timeout(2026-11-18 段二裁决): 连续3步超时/FAIL -> 整轮熔断,
    # 剩余步骤跳过, 晨报置顶高亮; 单步失败不熔断, 继续流转
    streak = 0
    fused = False
    for name, cmd, _, timeout_min in STEPS:
        if fused:
            print(f"[NIGHT] 熔断, 跳过: {name}", flush=True)
            durations.append((f"{name} [SKIPPED-FUSED]", 0))
            continue
        ok, mins = run_step(name, cmd, _, timeout_min)
        durations.append((f"{name} [{'OK' if ok else 'FAIL'}]", mins))
        streak = streak + 1 if not ok else 0
        if streak >= 3:
            fused = True
            print("[NIGHT] !!! 熔断: 连续3步超时/FAIL, 剩余步骤全部跳过 -- "
                  "晨报置顶高亮, 检查环境(LLM通道/DB/路径)", flush=True)
    if fused:
        durations.insert(0, ("本轮熔断: 连续3步异常, 部分步骤被跳过", 0))
    write_report(durations)
    print(f"[NIGHT] 全部结束于 {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


if __name__ == "__main__":
    main()