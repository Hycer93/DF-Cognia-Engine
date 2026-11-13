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
        return WalkResult(user_input=user_input, final_answer=final,
                          path=walked, planned_path=decision.path,
                          deepen_events=deepen_events, llm_trace=trace,
                          verify_note=verify_note, node_outputs=node_outputs,
                          event_counts=event_counts, jw_sidebranch=jw_out)

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
