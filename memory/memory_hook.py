# memory_hook.py — CodeChat 会话接入入口（Step5）
#
# 两个使用路径：
# 1. CLI 手动：python memory_hook.py add --domain 005-002 --behavior Ht --relation P \
#              --confidence L0 --summary "..." --detail "..." --source "会话"
#    适合会话中由主AI主动调用（我在会话里直接 subprocess 调用）
# 2. CodeChat hook（settings.json 配 PreCompact/Stop 事件）调用 memory_hook.py session
#    ——自动采集当前会话的裁决/纠错/洞察。Week1 先以手动+主AI调用为主，
#    hook 全自动归档待摘要自动生成质量验证后再开（乐观更新原则同源：先小窗口试运行）
#
# 管道顺序：atomic_store → classify_and_link（四档判定）→ distill（经验聚类）

import argparse
import json
import sys
import io
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_organize import atomic_store, load_memories
import memory_relations
import memory_distill
import topic_layer             # Topic锚层挂载(规约3; 开关默认关)


def add(domain, behavior, relation, confidence, summary, detail, source,
        asset_class="runtime", repro=None, auto_link=True, auto_distill=True,
        event_ts=None, memory_class=None):
    """单条入库 + 管道联动。返回报告 dict。
    event_ts：事件真实发生时间（ISO 格式）。会话入库时主AI传会话当天，
    语料回填时传语料日期；缺省 None=时间未知（不伪造）。
    memory_class：记忆分类五类（件①）——strategy_evolve/skill_refine/scar_case/
    relation_milestone/key_pact。缺省 None 合法（E4 统计无分类存量），但 judge 钩子
    （件②）入库时必填——LLM 自觉调用本入口也应尽量带上，这是"评估固化形式"的落点。"""
    tags = {"domain": domain, "behavior": behavior, "relation": relation, "confidence": confidence}
    if memory_class is not None:
        tags["memory_class"] = memory_class
    if repro:
        tags["repro"] = repro
    st, msg = atomic_store(summary, detail, source, tags, asset_class=asset_class,
                           event_ts=event_ts)
    report = {"store": {"status": st, "msg": msg}}
    if st != "stored":
        return report
    # 取刚入库的 inst_id（store 返回截断 id，从库里找最新）
    inst_id = next(e["inst_id"] for e in reversed(load_memories())
                   if e["summary"] == summary or e.get("full_hash") is not None)
    # 精确定位
    inst_id = None
    for e in reversed(load_memories()):
        if e["summary"] == summary:
            inst_id = e["inst_id"]; break
    if auto_link and inst_id:
        r = memory_relations.classify_and_link(inst_id)
        report["link"] = r
        if r.get("relation") == "conflict":
            report["note"] = "检测到同源分叉冲突，已送评审队列（元思域对撞素材）"
    # Topic锚层挂载（规约3, 2026-11-13 design_topic_layer.md）:
    # 归一四档(anchor/hold)；白名单关闭时跳过=零行为变化。hold 池只记录不阻塞入库。
    if inst_id and topic_layer.topic_enabled():
        report["topic"] = topic_layer.mount_topic(
            inst_id, summary, detail, method="judge")
    if auto_distill:
        d = memory_distill.distill_all()
        report["distill"] = {k: d[k] for k in ("created", "updated")}
    return report


def _overlay_date(inst_id):
    """回填叠加层查事件时间（2026-11-09 时间概念工程）。"""
    from memory_query import _load_event_ts_overlay
    return _load_event_ts_overlay().get(inst_id)


def main():
    ap = argparse.ArgumentParser(description="DFCP 记忆管道入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--domain", required=True)
    p_add.add_argument("--behavior", required=True)
    p_add.add_argument("--relation", required=True)
    p_add.add_argument("--confidence", required=True)
    p_add.add_argument("--summary", required=True)
    p_add.add_argument("--detail", default="")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--asset-class", default="runtime")
    p_add.add_argument("--repro", default=None)
    p_add.add_argument("--event-ts", default=None,
                       help="事件真实发生时间 ISO 格式（2026-11-09 时间概念工程），缺省=时间未知")

    p_q = sub.add_parser("query")
    p_q.add_argument("text")
    p_q.add_argument("--domain", default=None)
    p_q.add_argument("--behavior", default=None)
    p_q.add_argument("--top", type=int, default=5)

    p_stat = sub.add_parser("stat")

    p_session = sub.add_parser(
        "session",
        help="hook 入口（PreCompact/Stop）：从 stdin/文件读会话候选走 judge 管道")
    p_session.add_argument("--file", default=None,
                           help="候选 JSON 文件路径（list[dict]）；缺省读 stdin")

    args = ap.parse_args()
    if args.cmd == "add":
        r = add(args.domain, args.behavior, args.relation,
                args.confidence, args.summary, args.detail, args.source,
                asset_class=args.asset_class, repro=args.repro,
                event_ts=getattr(args, "event_ts", None))
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif args.cmd == "query":
        from memory_query import ask
        tag = {}
        if args.domain: tag["domain"] = args.domain
        if args.behavior: tag["behavior"] = args.behavior
        results = ask(args.text, query_tag=tag, top_k=args.top)
        v = getattr(ask, "last_verdict", {})
        if v.get("verdict") in ("uncertain", "marginal_ask", "marginal_hit"):
            if v["verdict"] == "uncertain":
                flag = "⚠ 低置信"
            elif v["verdict"] == "marginal_ask":
                flag = "◐ 边缘带无实体锚"
            else:
                flag = "◉ 边缘带·实体锚放行"
            print(f"{flag} match_eff={v['match_eff']} total={v['total']}——我猜是: {v['guess']}")
            if v.get("hint"):
                print(f"  {v['hint']}")
            print("  候选:")
        tw = getattr(ask, "last_time_window", None)
        if tw:
            print(f"[时间窗] {tw['expr']}: {tw['from']} ~ {tw['to']}")
        for score, m, _ in results:
            ed = m.get("event_ts") or _overlay_date(m["inst_id"])
            t_label = f"[{str(ed)[:10]}]" if ed else "[时间未知]"
            print(f"{score:.4f} {t_label} [{m['dfcp_tag']['domain']}|{m['dfcp_tag']['behavior']}] {m['summary'][:60]}")
    elif args.cmd == "session":
        # hook 入口：读候选（stdin 或 --file），走 judge 四档管道。
        # 候选格式 = judge_and_store 的 candidates（summary/detail/source/domain/
        # behavior/relation/confidence/memory_class/event_ts）。
        # 设计（memory_hook.py 头注）：hook 全自动归档先小窗口试运行——
        # 候选由 hook 端摘要生成（PreCompact 时主 AI 落盘会话裁决清单后调用），
        # 本命令只做管道执行不做摘要提取，边界清晰。
        raw = io.open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        try:
            candidates = json.loads(raw) if raw.strip() else []
            if isinstance(candidates, dict):
                candidates = [candidates]
        except Exception as e:
            print(json.dumps({"error": f"候选JSON解析失败: {e}"}, ensure_ascii=False))
            sys.exit(1)
        from memory_judge import judge_and_store
        r = judge_and_store(candidates, context={"trigger": "hook"})
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif args.cmd == "stat":
        mems = load_memories()
        from collections import Counter
        doms = Counter(m["dfcp_tag"]["domain"].split("-")[0] for m in mems)
        classes = Counter(m["asset_class"] for m in mems)
        exps = memory_distill.load_experiences()
        graph = memory_relations.load_graph()
        print(f"原子: {len(mems)}（{dict(classes)}）")
        print(f"域分布: {dict(doms)}")
        print(f"关系: {len(graph)} | 经验: {len(exps)}")


if __name__ == "__main__":
    main()