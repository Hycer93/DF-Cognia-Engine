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


# ---------- #12 对话追溯: stdin transcript 解析(cwd 2026-11-22) ----------
# 背景: 旧机制依赖主 AI 手动覆写 dialog_last_segment.md, 契约脆弱 → h 通道曾静默断流。
# 新机制: PreCompact hook stdin 自带 {session_id, transcript_path}, 直接解析 transcript
# JSONL 拿本轮完整对话(用户+模型回复), 不再依赖外部 md 中转。
# transcript 每行: {type:'user'|'assistant', message:{role, content}, timestamp}。
#   content 可能是 str, 也可能是 [{type:'text'|'tool_use'|'tool_result', ...}]。
# 参考生产实现在用的 agent-pipe guard.js(同格式)。

def _read_stdin_payload() -> dict:
    """读 hook stdin JSON payload; 非 TTY 且空/非法时返回 {}。"""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


def _extract_text_from_content(content):
    """从 message.content 提取纯文本(content 为 str 或 [{type:'text',text}] 数组)。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if t:
                    parts.append(t)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _parse_transcript(transcript_path: str) -> str:
    """解析 transcript JSONL → 组装「用户/助手」对话文本。仅取 user/assistant 的 text
    内容(tool_use/tool_result 丢弃, 那是工具流水非对话)。失败抛异常由调用方降级。"""
    out = []
    with io.open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            msg = rec.get("message") or {}
            role = rec.get("type") or msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text_from_content(msg.get("content"))
            if text and text.strip():
                label = "用户" if role == "user" else "模型"
                out.append("【%s】%s" % (label, text.strip()))
    return "\n\n".join(out)


def _session_dialog_from_hook(payload: dict) -> dict:
    """#12 主逻辑: 由 hook payload 落对话原文。
    优先 transcript_path 解析; 缺失/异常降级到 dialog_last_segment.md 旧通路。
    返回 {channel, status, ...} 供调用方打印与退出码判定。"""
    import memory_gate
    session = (payload.get("session_id") or "codechat-session/default")
    tpath = payload.get("transcript_path") or ""
    body = ""
    channel = "none"
    err = None
    if tpath:
        try:
            body = _parse_transcript(tpath)
            channel = "transcript"
        except Exception as e:  # noqa: BLE001
            err = repr(e)
            channel = "fallback-md"
    else:
        channel = "fallback-md"
    if channel == "fallback-md" or (channel == "transcript" and not body.strip()):
        # 降级: 读旧 md 通路(历史行为, 保留向后兼容)
        md = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "data", "memory", "dialog_last_segment.md")
        try:
            body = io.open(md, encoding="utf-8").read().strip()
            channel = "fallback-md"
        except Exception as e:  # noqa: BLE001
            return {"channel": "fallback-md", "status": "md-read-fail",
                    "err": repr(e), "transcript_err": err, "session": session}
    source_tag = "precompact-stdin-transcript" if channel == "transcript" else "compact-boundary"
    r = memory_gate.store_dialog(body, session, source_tag=source_tag)
    r["channel"] = channel
    r["transcript_err"] = err
    # ---- #31 原始证据库：原始会话聚合（碎片→快照+逐条表, 打通证据链） ----
    # 优先直接吃 transcript 原始 JSONL（不扁平化）; 落库失败不影响主链路（已存 raw_corpus）。
    r["evidence"] = _evidence_ingest_and_aggregate(session, tpath, r.get("id"),
                                                   hook_event=payload.get("hook_event_name"))
    return r


# hook 事件 → 证据库 terminate_reason 映射（#31 预留 SessionEnd/Stop 对接点）
# 平台当前只有 PreCompact; 未来接入 Stop/SessionEnd 时按事件名自动归类, 无需改聚合逻辑。
_HOOK_EVENT_TO_REASON = {
    "PreCompact": "precompact_normal",
    "Stop": "session_stop",
    "SessionEnd": "session_end",
    "SessionStart": "session_start",
}


def _evidence_ingest_and_aggregate(session: str, transcript_path: str, ref_id: str,
                                   hook_event: str = None) -> dict:
    """#31: 尝试把本轮原始对话存入证据库（快照+逐条）。全异常吞掉只报告, 绝不阻断主流程。

    hook_event: 事件名（PreCompact/Stop/SessionEnd...）, 映射到 terminate_reason;
                缺省(旧通路/无 payload)按 precompact_normal 处理, 保持向后兼容。
    """
    terminate_reason = _HOOK_EVENT_TO_REASON.get(hook_event, "precompact_normal")
    try:
        import dialog_evidence_store as des
        des.init_tables()
        if transcript_path:
            ing = des.ingest_transcript(session, transcript_path)
        else:
            ing = {"status": "no-transcript"}
        agg = des.aggregate_session(session, ref_archive_fragment=ref_id,
                                    terminate_reason=terminate_reason)
        return {"ingest": ing, "aggregate": agg, "terminate_reason": terminate_reason}
    except Exception as e:  # noqa: BLE001
        return {"status": "evidence-error", "err": repr(e)[:200]}


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
        # hook 入口，双职责(2026-11-22 #12 起):
        #  A) 对话原文落库: 读 hook stdin payload{session_id,transcript_path}, 解析本轮
        #     完整对话(用户+模型)写入 raw_corpus(修复 h 通道断流根因)。
        #  B) 候选记忆 judge 管道: 从 --file(或 payload/旧 stdin)读候选走四档判定。
        # stdin 分流: 若 payload 含 hook 字段(session_id/transcript_path/hook_event_name)
        #  → 是 PreCompact payload, 候选只从 --file 读; 否则旧行为(整个 stdin 当候选 list)。
        payload = {}
        raw = ""
        if args.file:
            raw = io.open(args.file, encoding="utf-8").read()
        elif not (sys.stdin is None or sys.stdin.isatty()):
            raw = sys.stdin.read()
        # 先尝试把 raw 当 hook payload 解析(有 hook 字段才采纳为 payload)
        _hook_keys = ("session_id", "transcript_path", "hook_event_name")
        try:
            _parsed = json.loads(raw) if raw.strip() else None
        except Exception:
            _parsed = None
        if isinstance(_parsed, dict) and any(k in _parsed for k in _hook_keys):
            payload = _parsed
        # ---- A) 对话原文落库(即使候选解析失败也先做, 对话追溯优先) ----
        dlg_report = None
        try:
            dlg_report = _session_dialog_from_hook(payload)
        except Exception as e:  # noqa: BLE001
            dlg_report = {"status": "dialog-error", "err": repr(e)}
            print(f"[memory_hook_abnormal] 对话落库异常: {e!r}", file=sys.stderr)
        # ---- B) 候选 judge 管道 ----
        if payload:
            candidates = payload.get("candidates") or []
            if isinstance(candidates, dict):
                candidates = [candidates]
        else:
            try:
                candidates = (_parsed if _parsed is not None else (json.loads(raw) if raw.strip() else []))
                if isinstance(candidates, dict):
                    candidates = [candidates]
            except Exception as e:
                print(json.dumps({"error": f"候选JSON解析失败: {e}",
                                  "dialog": dlg_report}, ensure_ascii=False))
                sys.exit(1)
        from memory_judge import judge_and_store
        # 2026-11-21 修复(用户裁决): judge 管道内部吞异常会返回"正常"报告,
        # 上游 CodeChat 无法感知故障 → 静默丢记忆。judge_failures>0 时显式非 0 退出。
        try:
            r = judge_and_store(candidates, context={"trigger": "hook"})
        except Exception as e:
            print(json.dumps({"error": f"judge管道异常: {e}", "dialog": dlg_report},
                             ensure_ascii=False))
            sys.exit(2)
        r["dialog"] = dlg_report   # 对话落库结果并入报告, 晨报/排错可见
        print(json.dumps(r, ensure_ascii=False, indent=1))
        if r.get("judge_failures"):
            # 判定层异常已被内部 fallback 成 hold(不丢), 但入口须告警非 0
            print(f"[memory_hook_abnormal] judge_failures={r['judge_failures']}, "
                  f"候选已转 hold 待审", file=sys.stderr)
            sys.exit(3)
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
    # 2026-11-21 修复(用户裁决): 未捕获异常须 RC≠0 且打告警, 不静默。业务跳过
    # (空候选/empty-skip)在分支内正常返回 0, 不经过这里。
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print(f"[memory_hook_abnormal] 未捕获异常: {type(e).__name__}: {e}",
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(4)
