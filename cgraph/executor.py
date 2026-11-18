"""
走链执行器：按 RouteDecision 的骨架路径，分阶段驱动 LLM。

契约 v2（2026-11-13 定案改造，四条）：
1. 节点产出只是「节点工作结果」，不再天然具备对外输出资格；
   Jw 不再是天然出口节点。
2. 加深跳转：调度器读取节点返回的结构化 JSON 字段 deepen 做判断，
   不再扫描自由文本标记；正文禁止路由暗号（残留标记只抹除，不消费）。
3. 复盘（Jw 经验进化闭环）：主链全部完成后单独支线任务跑，
   产出只写入记忆（engine 归档），不流入面向用户的主回答管线。
4. 面向用户的最终回答由专门的「汇总节点」产出：基于整条链路
   所有主链节点摘要，面向原始用户问题作答，不混入复盘问题。

节点结构化输出协议（每节点一次 LLM 调用，不加调用次数）：
  {"analysis": "节点工作结果正文", "deepen": "节点码 或 null"}
  - analysis：纯内容；deepen：调度器专用的受控通道，不进任何面向用户的文本
  - 解析失败降级：全文当 analysis，deepen=null（宁可不加深，不可中断）
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from cgraph.graph import MindGraph
from cgraph.router import RouteDecision

DEEPEN_MARK = "[[DEEPEN:"   # 旧协议标记：仅用于防御性清洗正文残留，不再作为路由信号


@dataclass
class WalkResult:
    user_input: str
    final_answer: str              # 契约④：汇总节点产出，不再取链尾节点输出
    path: list                     # 实际走过的骨架序列（Jw 记为支线，仍在序列内）
    planned_path: list
    deepen_events: list = field(default_factory=list)
    llm_trace: list = field(default_factory=list)   # 每节点 (code, resp_chars, ms)，含 Jw#side / SUMMARY
    verify_note: str = ""
    node_outputs: dict = field(default_factory=dict)  # code -> 工作结果正文（含 Jw 支线产出）
    event_counts: dict = field(default_factory=dict)  # 情感阀1事件计数
    jw_sidebranch: str = ""        # 契约③：Jw 支线复盘产出，只归档记忆，不进主回答管线


class ChainExecutor:
    def __init__(self, graph: MindGraph, llm, max_deepen: int = 2):
        self.g = graph
        self.llm = llm
        self.max_deepen = max_deepen

    def run(self, user_input: str, decision: RouteDecision) -> WalkResult:
        system = {"role": "system", "content": self._system_prompt(decision)}
        orig_user = {"role": "user", "content": user_input}

        walked = []
        deepen_events = []
        trace = []
        failed_nodes = []
        node_outputs = {}          # code -> 工作结果正文（不截断，轨迹组装器用）
        queue = [c for c in decision.path]
        deepens_left = self.max_deepen
        # 上下文管理：防滚雪球。每节点只携带 system + 原问题 + 上一节点的
        # 摘要 + 本节点指令；完整中间产物不进历史（token 成本 O(1) 而非 O(n)）
        prev_summary = ""
        last_good_answer = ""
        last_answer = ""

        while queue:
            code = queue.pop(0)
            if code not in self.g.nodes:
                continue
            # 契约①③：Jw 出主链——不在链上作为普通节点跑，
            # 主链结束后单独支线执行；路径记录保留（hebbian/轨迹兼容）
            if code == "Jw":
                walked.append(code)
                continue
            node = self.g.nodes[code]
            walked.append(code)

            context = f"\n\n[上一思维节点要点]\n{prev_summary}" if prev_summary else ""
            messages = [system, orig_user,
                        {"role": "user", "content": self._node_prompt(node) + context}]

            t0 = time.time()
            answer = self.llm.chat(messages)
            ms = int((time.time() - t0) * 1000)
            # 节点失败降级：重试后仍无返回，用占位但记录失败节点
            if not answer:
                answer = f"（{code} 节点调用失败，本轮基于已有分析继续）"
                failed_nodes.append(code)

            # 契约②：结构化解析——deepen 只来自 JSON 字段，不扫描正文
            parsed = self._parse_node_json(answer)
            analysis = self._sanitize(parsed["analysis"] or answer)

            # 结构化加深判定：字段值须是图内真实节点（完整码校验，不截断）
            if deepens_left > 0 and parsed["deepen"]:
                target = self._valid_deepen(parsed["deepen"])
                if target and target in decision.domain_nodes \
                        and target not in walked and target not in queue and target != "Jw":
                    queue.insert(0, target)
                    deepens_left -= 1
                    deepen_events.append(target)

            trace.append({"code": code, "resp_chars": len(analysis), "ms": ms})
            node_outputs[code] = analysis
            # 摘要传递：给下一节点的是本节点输出的压缩要点（≤400字），非全文
            if code not in failed_nodes:
                prev_summary = analysis[:400]
                last_good_answer = analysis
            last_answer = analysis

        # ---------- 主链完成，进入两道独立工序 ----------

        # 契约③：支线跑 Jw 复盘——产出只写记忆（engine 归档），不进主回答管线
        jw_out = ""
        if "Jw" in walked:
            t0 = time.time()
            jw_out = self.llm.chat([{"role": "user",
                                     "content": self._jw_sidebranch_prompt(user_input, node_outputs, walked)}]) or ""
            ms = int((time.time() - t0) * 1000)
            if jw_out:
                jw_out = self._sanitize(jw_out)
                node_outputs["Jw"] = jw_out     # 轨迹组装器可见（训练数据），用户管线不可见
            trace.append({"code": "Jw#side", "resp_chars": len(jw_out), "ms": ms})

        # 契约④：专门汇总节点——基于主链全部节点摘要，只面向原始用户问题作答
        t0 = time.time()
        final = self.llm.chat([{"role": "user",
                                "content": self._summary_prompt(user_input, node_outputs, walked)}]) or ""
        ms = int((time.time() - t0) * 1000)
        final = self._sanitize(final)
        # 汇总失败降级：主链最后成功节点正文兜底（总比空回答好）
        if not final:
            final = last_good_answer or last_answer or "（无输出）"
        trace.append({"code": "SUMMARY", "resp_chars": len(final), "ms": ms})

        verify_note = self._verify_gist(user_input, final)
        # 情感阀1事件计数（代码层实测，不靠 LLM 记性）
        event_counts = {
            "failed_nodes": failed_nodes,           # 调用失败节点（tool 类挫折源）
            "retries": sum(1 for t in trace if t["ms"] > 8000),  # 粗粒度：慢响应近似重试
            "deepens": len(deepen_events),
            "total_ms": sum(t["ms"] for t in trace),
        }

        # 契约⑤(2026-11-17 skill_memory_gate 七段定稿段一B案): 记忆写入闸门——
        # terminate/汇总完成后的后置收尾步(Jw经验闭环行为4的落库判定)。
        # 技能零DB写(段二C案): 本体只产 gate_list 分档清单; 绿档落库走
        # commit_green_cards() 机械执行(段六b案: 独立审计行 memory_commit:green);
        # 黄档入人闸提示; 红档仅会话内标记(段七a案)。降级只告警不炸主链路(段四a案)。
        gate_result = self._run_memory_gate(user_input, node_outputs, walked)
        if gate_result is not None:
            result, gate = gate_result
            commit_green_cards(result, gate, user_input)

        return WalkResult(user_input=user_input, final_answer=final,
                          path=walked, planned_path=decision.path,
                          deepen_events=deepen_events, llm_trace=trace,
                          verify_note=verify_note, node_outputs=node_outputs,
                          event_counts=event_counts, jw_sidebranch=jw_out)

    # ---------- 记忆写入闸门(段一B案后置钩子) ----------
    def run_atomic(self, user_input: str):
        """原子技能管线薄入口(2026-11-17 skill_router 七段定稿段四②b)。

        职责划分: router_atomic 只做纯调度循环(R6: 零 DB 写零编排钩子);
        本方法承接编排——跑网 → trail 映射伪 node_outputs → 复用
        _run_memory_gate + commit_green_cards 两连调(零重复开发)。
        不改动原 run() 骨架链路。
        返回 (report, state, gate_result or None)。
        """
        import json as _json
        import os as _os
        import io as _io
        from cgraph.atomic_skill import ThinkState
        from cgraph.router_atomic import RouterAtomic
        _adj_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                 "data", "atomic_skills_adjacency.json")
        with _io.open(_adj_path, encoding="utf-8") as f:
            adjacency = _json.load(f)
        # P-C4 入口分隔符通道(段二2.2契约): ---ref_material--- 独立成行(大小写敏感),
        # split(标记,1) 首次出现切分, 标记丢弃; 无标记/标记后空 → 不处理移交 anchor LLM 通道;
        # 切分异常 → entry_fallback 审计 + 材料留在 goal(回退旧管线行为, fail-safe 段三3.1)
        ref_material = ""
        MARK = "---ref_material---"
        try:
            if MARK in user_input:
                goal_part, _, mat_part = user_input.partition(MARK)
                mat_part = mat_part.lstrip("\r\n")
                if mat_part.strip():
                    goal_part = goal_part.rstrip()
                    MAXC = 200_000            # REF_MAX_CHARS(段四4.1: 写入硬上限, 一次性截断)
                    if len(mat_part) > MAXC:
                        ref_material = mat_part[:MAXC]
                        _audit_note = {"ref_truncated": f"原始{len(mat_part)}字符, 截断至{MAXC}"}
                    else:
                        ref_material = mat_part
                        _audit_note = None
                    user_input = goal_part
                    try:
                        import sqlite3 as _sq, time as _tm
                        _c = _sq.connect(_os.path.join(_os.path.dirname(_adj_path), "memory", "corpus_store.db"))
                        _c.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                                   (_tm.strftime("%Y-%m-%dT%H:%M:%S"), "ref_material:entry_hit",
                                    _json.dumps({"len": len(ref_material), **(_audit_note or {})}, ensure_ascii=False)))
                        _c.commit(); _c.close()
                    except Exception:
                        pass                    # 审计失败不阻断
                # 标记后为空: 视同无标记, 交 anchor LLM 通道
        except Exception:
            try:
                import sqlite3 as _sq, time as _tm
                _c = _sq.connect(_os.path.join(_os.path.dirname(_adj_path), "memory", "corpus_store.db"))
                _c.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                           (_tm.strftime("%Y-%m-%dT%H:%M:%S"), "ref_material:entry_fallback",
                            _json.dumps({"why": "入口切片异常, 材料留goal"}, ensure_ascii=False)))
                _c.commit(); _c.close()
            except Exception:
                pass
            ref_material = ""
        state = ThinkState(goal=user_input, ref_material=ref_material)
        report = RouterAtomic(self.llm, adjacency).run(state)
        # ---- P-matrix-feed(2026-11-18 段二契约2): scan 后一次性统一装载 ----
        # 时机=router run 返回后(此时 scan 已完成, goal+scan_summary 完备);
        # 一次性装载, f_reason/weakest_link 复用同一份(输出可复现, trail 可比对)。
        # 降级契约: 任何异常/零命中/超限 → 恒 [], 链路行为与改造前一致;
        # 只埋 trail 观测标记(matrix_injected / matrix: degraded), 不抛业务告警。
        try:
            from cgraph.matrix_knowledge import load_matrix, matrix_tokens
            _scan_sum = ""
            for _e in state.trail:
                if isinstance(_e, dict) and _e.get("skill_id") == "skill_scope_scan":
                    _scan_sum = str(_e.get("step_summary") or "")
                    break
            _mk = load_matrix(state.goal or "", _scan_sum)
            state.matrix_knowledge = _mk
            _mk_note = ("matrix_injected: %s (tokens=%d)" %
                        ([e.get("matrix_id") for e in _mk], matrix_tokens(_mk))) if _mk \
                else "matrix: degraded(零命中或降级)"
            state.trail.append({"skill_id": "matrix_feed", "chosen": "matrix_feed",
                                "candidates": [], "reason": "P-matrix-feed外部知识装载(scan后一次性)",
                                "step_summary": _mk_note[:120], "result_fields": {},
                                "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")})
        except Exception:
            state.matrix_knowledge = []
        # ---- P-C5 后置时序(段二2.5裁决): 补汇编(病灶A) → sufficiency → gate ----
        # 后置段整体 try/except: 任何后置故障不污染主链结果(fail-safe, 段五5.2)
        try:
            # 病灶A修复: llm_terminate 通道退出时 terminate 汇编技能从未执行,
            # 终态=最后一步60字step_summary。汇编本体纯Python零LLM, 编排器补跑
            # 成本≈0; 补跑条目正常入 trail(段二C: sufficiency/memory_gate 均可读)
            has_terminate = any(isinstance(e, dict) and e.get("skill_id") == "skill_terminate"
                                for e in state.trail)
            if not has_terminate:
                import time as _tm2
                from cgraph.skill_terminate import SkillTerminate
                from cgraph.router_atomic import SKILL_REGISTRY
                _saved_reg = dict(SKILL_REGISTRY)
                try:
                    SKILL_REGISTRY["skill_terminate"] = SkillTerminate
                    _t0 = _tm2.time()
                    _sr_t = SkillTerminate(llm=self.llm, adjacency=adjacency).run(state)
                    # 补跑条目入trail(段二C: 后置技能readable; 与router _exec_skill
                    # trail条目形态对齐; skill_terminate的run内部state.steps+=1属
                    # 汇编计步——此处回滚, 汇编不占主链步数(段四B同源裁决))
                    state.steps -= 1
                    state.trail.append({"skill_id": "skill_terminate", "chosen": "skill_terminate",
                                        "candidates": [], "reason": "llm_terminate后编排器补跑汇编(病灶A修复)",
                                        "step_summary": str(_sr_t.result.get("summary") or "链路汇编")[:120],
                                        "result_fields": _sr_t.result, "ts": _tm2.strftime("%Y-%m-%dT%H:%M:%S")})
                finally:
                    SKILL_REGISTRY.clear(); SKILL_REGISTRY.update(_saved_reg)
            # sufficiency: 后置参谋, 不占步数不进steps_log(段四B), 审计独立op
            self._run_sufficiency_check(state, adjacency)
        except Exception as _e:
            print(f"[post-exit] 后置段异常(不污染主链): {_e}")
        # trail → 伪 node_outputs {skill_id: step_summary}(满足 build_trail_digest 读取,
        # gate 本体零修改); walked = trail 的 skill_id 序列
        pseudo = {e["skill_id"]: e.get("step_summary", "") for e in report.final_state.trail}
        walked = [e["skill_id"] for e in report.final_state.trail]
        gate_result = self._run_memory_gate(user_input, pseudo, walked)
        if gate_result is not None:
            result, gate = gate_result
            commit_green_cards(result, gate, user_input)
        return report, report.final_state, gate_result

    def _run_sufficiency_check(self, state, adjacency):
        """P-C5 后置触发(POST_EXIT_HINT 同 memory_gate 先例): 只告警不裁决(R15);
        审计独立 op=sufficiency:alert; 任何异常静默(机械层内部已有降级)。"""
        try:
            import sqlite3 as _sq, time as _tm, json as _js
            from cgraph.atomic_skill import AUDIT_DB
            from cgraph.skill_sufficiency_check import SkillSufficiencyCheck
            sk = SkillSufficiencyCheck(llm=self.llm, adjacency=adjacency)
            sr = sk.run(state)
            r = sr.result or {}
            # 审计载荷(段五5.3): 独立 op=sufficiency:alert(非 atomic_skill:* 归属,
            # 段二2.5裁决); metrics全量+alerts清单+复核标记; 不进steps_log(段四B)
            con = _sq.connect(AUDIT_DB)
            con.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                        (_tm.strftime("%Y-%m-%dT%H:%M:%S"), "sufficiency:alert",
                         _js.dumps({
                             "coverage_metrics": r.get("coverage_metrics"),
                             "alerts": r.get("alerts", []),
                             "applicable": r.get("applicable"),
                             "llm_review": bool((r.get("sufficiency_report") or {}).get("llm_review"))
                             if isinstance(r.get("sufficiency_report"), dict) else False,
                             "degraded": bool(r.get("_degraded")),
                             "token_in": r.get("token_in"), "token_out": r.get("token_out"),
                             "secs": sr.secs}, ensure_ascii=False)))
            con.commit(); con.close()
            if r.get("alerts"):
                n = len(r["alerts"])
                print(f"[sufficiency] 告警 {n} 条(只告警不裁决): " +
                      "; ".join(str(a)[:60] for a in r["alerts"][:3]) + ("..." if n > 3 else ""))
        except Exception as e:
            print(f"[sufficiency] 后置校验异常(跳过): {e}")

    def _run_memory_gate(self, user_input: str, node_outputs: dict, walked: list):
        """组装 ThinkState(终态) 并跑 skill_memory_gate。
        返回 (gate_result, skill) 或 None(未启用/技能缺失)。降级/失败只告警。"""
        try:
            from cgraph.atomic_skill import ThinkState, _audit_log
            from cgraph.skill_memory_gate import SkillMemoryGate, build_trail_digest
        except ImportError as e:
            print(f"[memory_gate] 技能模块缺失, 跳过: {e}")
            return None
        try:
            # trail 素材: 主链各节点 → 汇总/SUMMARY 视作末步(近似 terminate 自报摘要)
            trail = [{"skill_id": f"node_{c}", "step_summary": str(node_outputs.get(c, ""))[:60],
                      "result_fields": []} for c in walked if c != "Jw"]
            trail.append({"skill_id": "SUMMARY", "step_summary": str(node_outputs.get("SUMMARY", ""))[:60],
                          "result_fields": []})
            state = ThinkState(goal=user_input, goal_anchored=user_input,
                               summary=str(node_outputs.get("SUMMARY", ""))[:400],
                               trail=trail, steps=len(walked))
            gate = SkillMemoryGate(llm=self.llm)
            gate.jw_review_text = str(node_outputs.get("Jw", ""))  # 段三②b: Jw复盘作额外素材
            result = gate.run(state)
            if result.result.get("_degraded"):
                print(f"[memory_gate] 降级(不炸主链路, 可单独重跑): {result.result['_degraded']}")
            # 段六b案: 技能侧审计行带 gate_stats 对账口径
            gl = result.result.get("gate_list") or []
            _audit_log("skill_memory_gate", {
                "result_keys": list(result.result.keys()), "terminate": True,
                "secs": result.secs, "degraded": bool(result.result.get("_degraded")),
                "gate_stats": {"green": sum(1 for x in gl if x["tier"] == "green"),
                               "yellow": sum(1 for x in gl if x["tier"] == "yellow"),
                               "red": sum(1 for x in gl if x["tier"] == "red"),
                               "total": len(gl)}})
            # 红档(段七a案): 仅会话内提示, 不持久化
            for x in gl:
                if x["tier"] == "red":
                    print(f"[memory_gate][红档标记] 本链路存在范式级资产待终审: {x['gist']}")
            # 黄档: 人闸提示
            for x in gl:
                if x["tier"] == "yellow":
                    print(f"[memory_gate][黄档建议立卡, 等确认] {x['asset_type']}: {x['gist']}")
            return (result, gate)
        except Exception as e:
            print(f"[memory_gate] 执行异常(只告警): {type(e).__name__}: {e}")
            return None


    # ---------- Prompt 构造 ----------
    def _system_prompt(self, d: RouteDecision) -> str:
        domain_lines = "\n".join(
            f"  {self.g.nodes[c].to_prompt_line()}" for c in d.domain_nodes[:15])
        return (
            "你是一个按认知骨架图思考的助手。本次任务复杂度: {c}，计划走链: {p}。\n"
            "可用认知节点：\n{d}\n\n"
            "每个节点只回答该节点的行为问题。你的输出只是该节点的内部工作结果，"
            "不会直接展示给用户；最终回答由独立的汇总工序完成。\n"
            "输出格式：严格 JSON {{\"analysis\": \"本节点分析正文\", \"deepen\": null}}\n"
            "- analysis：分析正文。禁止出现节点编码、[[...]] 标记或任何路由指令。\n"
            "- deepen：仅当你判断现有计划走链不够深（存在隐藏动机、多个利益方、"
            "值得量化的风险、或需要质疑前一步结论）时，填写需要追加的节点码"
            "（必须在可用认知节点列表内，完整编码如 G18）；否则为 null。"
            "每轮最多加深 {n} 次，简单情况不要加深。\n"
            "加深请求由系统调度器审查执行；除 deepen 字段外你不掌握任何路由手段。"
        ).format(c=d.complexity, p="→".join(d.path), d=domain_lines or "  （仅保底节点）",
                 n=self.max_deepen)

    def _node_prompt(self, node) -> str:
        qs = " ".join(f"{b.get('question', '')}" for b in node.behaviors[:3])
        return (f"〔思维节点 {node.code} · {node.label}〕{qs}\n"
                f'只输出 JSON: {{"analysis": "本节点分析", "deepen": null 或需要加深的节点码}}')

    def _jw_sidebranch_prompt(self, user_input: str, node_outputs: dict, walked: list) -> str:
        """契约③：支线复盘 prompt。明确告知是内部工序，产出不展示给用户。"""
        jw = self.g.nodes.get("Jw")
        qs = " ".join(f"{b.get('question', '')}" for b in (jw.behaviors[:4] if jw else []))
        return (
            "〔支线任务 · 经验进化闭环〕这是系统内部复盘工序，产出不会展示给用户。\n"
            f"复盘问题: {qs}\n\n"
            f"用户原始问题: {user_input[:1000]}\n\n"
            f"主链各节点工作结果:\n{self._chain_digest(node_outputs, walked)}"
        )
    def _summary_prompt(self, user_input: str, node_outputs: dict, walked: list) -> str:
        """契约④：汇总节点 prompt——唯一任务：面向原始用户问题作答。"""
        return (
            "你是汇总节点。任务只有一个：基于下面各思维节点的分析结果，"
            "直接回答用户的原始问题。\n"
            "要求：\n"
            "1. 只面向用户问题作答，给出明确结论与理由；\n"
            "2. 不要提及思维节点、认知骨架、分析流程等内部机制；\n"
            "3. 不要输出任何节点编码或 [[...]] 标记。\n\n"
            f"用户问题: {user_input[:1000]}\n\n"
            f"各节点分析结果:\n{self._chain_digest(node_outputs, walked)}"
        )

    def _chain_digest(self, node_outputs: dict, walked: list, cap: int = 600) -> str:
        """主链节点摘要（跳过 Jw 支线产出，不流入主回答/汇总输入）"""
        parts = []
        for code in walked:
            if code == "Jw":
                continue
            out = (node_outputs.get(code) or "").strip()
            if not out:
                continue
            node = self.g.nodes.get(code)
            label = node.label if node else code
            parts.append(f"〔{label}〕{out[:cap]}")
        return "\n\n".join(parts) or "（无节点产出）"

    # ---------- 结果处理 ----------
    @staticmethod
    def _parse_node_json(text: str) -> dict:
        """契约②：解析节点结构化输出。失败降级——全文当 analysis，deepen=None。
        调度器不读正文标记：正文里即使残留 [[DEEPEN:xx]] 也不会触发路由。"""
        if not text:
            return {"analysis": "", "deepen": None}
        t = text.strip()
        if t.startswith("```"):                       # 剥 markdown fence
            t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
            t = re.sub(r"\n?```\s*$", "", t)
        s, e = t.find("{"), t.rfind("}")
        if s >= 0 and e > s:
            try:
                j = json.loads(t[s:e + 1])
                analysis = str(j.get("analysis", "")).strip()
                deepen = j.get("deepen")
                if analysis:
                    return {"analysis": analysis,
                            "deepen": deepen if isinstance(deepen, str) else None}
            except Exception:
                pass
        return {"analysis": text, "deepen": None}

    def _valid_deepen(self, code_str: str):
        """加深码校验：完整码精确匹配（修复旧版 [:2] 截断 G08→G0 的 bug）。
        容错仅一步：模型若输出"码+附加文字"，按图内常见码长截断再试。"""
        c = (code_str or "").strip()
        if not c:
            return None
        if c in self.g.nodes:
            return c
        for ln in (2, 3):                             # 其他码 2 字符，G 系 3 字符
            if len(c) > ln and c[:ln] in self.g.nodes:
                return c[:ln]
        return None

    @staticmethod
    def _sanitize(text: str) -> str:
        """输出净化：抹掉正文残留路由标记（防御性；只抹除，不读取内容做路由）"""
        return re.sub(r"\[\[[^\]]*\]\]\s*", "", (text or "")).strip()

    def _verify_gist(self, user_input: str, final: str) -> str:
        """保底'验证'：意图回归检查（LLM 语义判定）。
        LLM 失败时标'未验证'——被证伪的关键词算法不再当兜底，
        未验证的题赫布跳过，宁缺毋滥。"""
        ratio, note = self._llm_verify(user_input, final)
        if ratio is None:
            return "意图回归: 未验证(LLM失败，赫布跳过)"
        return f"意图回归: {note} (比率 {ratio:.2f})"

    def _llm_verify(self, user_input: str, final: str):
        VERIFY_PROMPT = (
            "判断下面这个回答是否真正回应了用户的问题。只看核心疑问是否被回答（意图贴合），"
            "不要求字面复述用户用词。输出严格 JSON: {{\"score\": 0.0到1.0的小数, "
            "\"note\": \"15字以内评语\"}}\n"
            f"用户问题: {user_input[:500]}\n回答: {final[:3000]}"
        )
        try:
            resp = self.llm.chat([{"role": "user", "content": VERIFY_PROMPT}])
            import json as _json
            j = _json.loads(resp[resp.find("{"):resp.rfind("}") + 1])
            score = max(0.0, min(1.0, float(j.get("score", 0.5))))
            return score, str(j.get("note", ""))[:20]
        except Exception:
            return None, ""


def commit_green_cards(result, gate, user_input: str):
    """绿档落库 writer(段二C案: 编排层机械执行, 技能外)。
    仅写绿档条目(asset_type ∈ 观测/测试结果/踩坑), 逐卡 full_hash 去重防重
    (段七红线5幂等), raw 回链锚=本会话 user_input+日期窗口(0f2f4458 硬约束)。
    段六b案: 成败一律落独立审计行 op=memory_commit:green。"""
    import hashlib
    import sqlite3
    import uuid as _uuid
    cards = [x for x in (result.result.get("gate_list") or []) if x["tier"] == "green"]
    if not cards or result.result.get("_degraded"):
        return {"written": 0, "skipped": len(cards)}
    db = "data/memory/corpus_store.db"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    day = time.strftime("%Y-%m-%d")
    written, ids, errs = 0, [], []

    def _jaccard(a: str, b: str) -> float:
        """bigram 重叠度: LLM 措辞漂移的语义级幂等兜底"""
        wa = {a[i:i + 2] for i in range(len(a) - 1)}
        wb = {b[i:i + 2] for i in range(len(b) - 1)}
        return len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0

    try:
        con = sqlite3.connect(db)
        # 语义级幂等补强(段七红线5编排侧实现): full_hash 只防完全同文,
        # 真实 LLM 两次盘点同资产措辞漂移→bigram-Jaccard>0.6 视为同资产跳过。
        # 对称口径: existing summary 剥 "[类型] "前缀与 "(evidence:...)"后缀后再比
        existing = []
        for r in con.execute(
                "SELECT summary FROM core_memory_card WHERE source_ref LIKE 'memory_gate%' "
                "AND ts LIKE ?", (day + "%",)).fetchall():
            e = r[0]
            e = e.split("] ", 1)[-1] if e.startswith("[") else e
            e = e.split("(evidence:", 1)[0]
            existing.append(e.strip())
        for x in cards:
            s = f"[{x['asset_type']}] {x['gist']}(evidence: {x['evidence_step']})"
            fh = hashlib.sha256((s + "\x1f" + user_input).encode()).hexdigest()
            if con.execute("SELECT 1 FROM core_memory_card WHERE full_hash=?",
                           (fh,)).fetchone():
                errs.append(f"dup:{x['gist'][:20]}")
                continue
            if any(_jaccard(x["gist"], e) > 0.6 for e in existing):
                errs.append(f"semidup:{x['gist'][:20]}")
                continue
            inst = _uuid.uuid4().hex
            import json as _json
            con.execute(
                "INSERT INTO core_memory_card (inst_id, ts, event_ts, full_hash, "
                "asset_class, repro, confidence, dfcp_tag, summary, summary_converged, "
                "detail, source_ref, memory_class, tier, weight, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inst, ts, ts, fh, "observation", "situational", 0.6,
                 _json.dumps({"domain": "001-003", "behavior": "Ht", "relation": "P",
                              "memory_class": "observation"}, ensure_ascii=False),
                 s[:120], "0",
                 f"绿档自动立卡(memory_gate 0f2f4458双层写入规约); "
                 f"素材步: {x['evidence_step']}; 档案动词: {x['proposed_action']}",
                 f"memory_gate; raw锚: session {day}; user_input={user_input[:60]}",
                 "observation", "active", '{"match": 0.0, "cited": 0}', "active"))
            ids.append(inst[:16])
            written += 1
        con.commit()
        con.close()
    except Exception as e:
        errs.append(f"db_error:{type(e).__name__}")
    # 段六b案: 成败一律落独立审计行
    try:
        con = sqlite3.connect(db)
        con.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                    (ts, "memory_commit:green",
                     json.dumps({"written": written, "cards": ids,
                                 "skipped": errs, "raw": f"session {day}",
                                 "user_input": user_input[:60]}, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:
        # 段六b案要求成败一律可追溯——审计失败不阻断链路但必须可见
        print(f"[memory_commit] 审计行落库失败(不阻断, 需人工核): {type(e).__name__}: {e}")
    if written:
        print(f"[memory_gate] 绿档落库 {written} 张: {ids}")
    return {"written": written, "skipped": errs}


