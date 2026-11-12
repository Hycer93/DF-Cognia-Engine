"""
走链执行器：按 RouteDecision 的骨架路径，分阶段驱动 LLM。

关键设计：
- 每个骨架节点转成一段阶段性指令注入对话，LLM 完成该节点的行为问题
- 中途加深：若 LLM 在某节点自行判断需要更深（输出含特定标记），可把
  domain_nodes 中未走的节点追加（模型自决走链，符合"最长链是上限"）
- 结束后产出 (instruction, path, answer, meta) 轨迹四元组，供赫布更新和 A/B 埋点
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from cgraph.graph import MindGraph
from cgraph.router import RouteDecision

DEEPEN_MARK = "[[DEEPEN:"   # 模型输出此标记请求加深，如 [[DEEPEN:G18]]


@dataclass
class WalkResult:
    user_input: str
    final_answer: str
    path: list                    # 实际走过的骨架序列
    planned_path: list
    deepen_events: list = field(default_factory=list)
    llm_trace: list = field(default_factory=list)   # 每节点的 (code, prompt_len, resp_len, ms)
    verify_note: str = ""
    node_outputs: dict = field(default_factory=dict)  # code -> 完整输出（轨迹组装器原料）
    event_counts: dict = field(default_factory=dict)  # 情感阀1事件计数：failed/retries/total_ms/deepens


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
        node_outputs = {}          # code -> 完整输出（不截断，轨迹组装器用）
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
            node = self.g.nodes[code]
            walked.append(code)

            context = f"\n\n[上一思维节点要点]\n{prev_summary}" if prev_summary else ""
            # Jw 总结节点：必须重新看到用户原问题，防止长链稀释主问题
            if code == "Jw":
                context = (f"\n\n[用户原始问题·必须直接回应]\n{user_input}"
                           f"\n\n[上一思维节点要点]\n{prev_summary}" if prev_summary
                           else f"\n\n[用户原始问题·必须直接回应]\n{user_input}")
            messages = [system, orig_user,
                        {"role": "user", "content": self._node_prompt(node) + context}]

            t0 = time.time()
            answer = self.llm.chat(messages)
            ms = int((time.time() - t0) * 1000)
            # 节点失败降级：重试后仍无返回，用占位但记录失败节点；
            # Jw 失败时后面会用最后一个成功节点的输出兜底
            if not answer:
                answer = f"（{code} 节点调用失败，本轮基于已有分析继续）"
                failed_nodes.append(code)

            # 中途加深：模型自决（全文扫描标记，不再只认开头行——形式主义放宽）
            if deepens_left > 0 and DEEPEN_MARK in answer:
                target = self._parse_deepen(answer)
                if target and target in decision.domain_nodes and target not in walked and target not in queue:
                    queue.insert(0, target)
                    deepens_left -= 1
                    deepen_events.append(target)
                    # 全文移除标记（标记可能在任意位置，不只开头）
                    answer = re.sub(r"\[\[DEEPEN:[^\]]*\]\]\s*", "", answer).strip()

            trace.append({"code": code, "resp_chars": len(answer), "ms": ms})
            node_outputs[code] = answer
            # 摘要传递：给下一节点的是本节点输出的压缩要点（≤400字），非全文
            if code not in failed_nodes:
                prev_summary = answer[:400]
                last_good_answer = answer
            last_answer = answer

        # Jw 总结失败时，用最后一个成功节点的输出兜底（总比空回答好）
        if "Jw" in failed_nodes and last_good_answer:
            final = last_good_answer
            walked = walked  # 保留完整路径记录（含失败节点）
        else:
            final = last_answer or last_good_answer or "（无输出）"
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
                          event_counts=event_counts)

    # ---------- Prompt 构造 ----------
    def _system_prompt(self, d: RouteDecision) -> str:
        domain_lines = "\n".join(
            f"  {self.g.nodes[c].to_prompt_line()}" for c in d.domain_nodes[:15])
        return (
            "你是一个按认知骨架图思考的助手。本次任务复杂度: {c}，计划走链: {p}。\n"
            "可用认知节点：\n{d}\n\n"
            "【自决加深机制】这是你的权利：如果在处理当前节点时，你发现现有计划走链不够深——"
            "比如问题存在隐藏动机、多个利益方、值得量化的风险、或需要质疑前一步结论——"
            "你应该在本回答的**开头**单独一行输出 [[DEEPEN:节点码]]（如 [[DEEPEN:G18]]），"
            "系统会把该节点插入走链，你将先完成当前节点，随后进入加深节点获得专门的一道工序。"
            "每轮最多加深 2 次。简单情况不要用，复杂疑点不要忍。\n\n"
            "规则：除加深标记外不要输出节点编码；每个节点只回答该节点的行为问题；"
            "最后由 [Jw] 节点汇总输出面向用户的最终回答，必须直接回应用户原问题。"
        ).format(c=d.complexity, p="→".join(d.path), d=domain_lines or "  （仅保底节点）")

    def _node_prompt(self, node) -> str:
        qs = " ".join(f"{b.get('question', '')}" for b in node.behaviors[:3])
        return f"〔思维节点 {node.code} · {node.label}〕{qs}"

    # ---------- 结果处理 ----------
    @staticmethod
    def last_answer_of(walked: list, last_answer: str) -> str:
        """最终答案 = 最后走过节点的输出（Jw 总结节点的回答）"""
        return last_answer or "（无输出）"

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

    def _parse_deepen(self, text: str):
        try:
            seg = text.split(DEEPEN_MARK, 1)[1]
            code = seg.split("]]")[0].strip()
            return code[:2]
        except Exception:
            return None