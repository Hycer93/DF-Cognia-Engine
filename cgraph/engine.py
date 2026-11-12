"""
引擎入口：单轮对话全流程。

graph 路由执行 → 意图回归自检 → 赫布更新 → 记忆归档。
"""
from __future__ import annotations

import os

from cgraph.graph import MindGraph
from cgraph.router import ComplexityRouter
from cgraph.executor import ChainExecutor
from cgraph.hebbian import HebbianUpdater, WalkFeedback
from cgraph.llm import LLMClient
from cgraph.memory import MemoryStore

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Engine:
    def __init__(self, config_path: str | None = None):
        import yaml
        cfg_path = config_path or os.path.join(HERE, "config", "config.yaml")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(HERE, "config", "config.example.yaml")
        self.cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))

        graph_file = os.path.join(HERE, self.cfg["graph"]["data_file"])
        self.graph_file = graph_file
        if not os.path.exists(graph_file):
            raise FileNotFoundError(
                f"图数据不存在: {graph_file}，先运行 python scripts/build_graph.py")
        self.graph = MindGraph.load(graph_file)

        self.llm = self._make_llm(cfg_path)
        self.router = ComplexityRouter(self.graph, self.cfg.get("router"), llm=self.llm)
        self.executor = ChainExecutor(self.graph, self.llm)
        self.hebbian = HebbianUpdater(self.graph, self.cfg["graph"].get("hebbian"))

        self.edge_log = os.path.join(HERE, "logs", "edge_log.jsonl")
        try:
            self.memory = MemoryStore(self.cfg)
        except Exception as e:
            print(f"[warn] 记忆层不可用（chromadb 未安装?）: {e}")
            self.memory = None
        # 代理环境下 chroma 首次下载 embedding 模型会 SSL 失败且重试极慢，
        # 显式提供开关；A/B 测试时建议关闭（--disable-memory 或 NO_MEMORY=1）
        if os.environ.get("NO_MEMORY") == "1":
            self.memory = None

    def _make_llm(self, cfg_path: str):
        """按配置构建 LLM 后端；active: codechat 时走 CodeChat 协议适配器"""
        active = self.cfg["llm"]["active"]
        if active == "codechat":
            from cgraph.llm_codechat import CodeChatLLM
            model = self.cfg["llm"]["codechat"].get("model") or None
            return CodeChatLLM(model=model)
        return LLMClient(cfg_path)

    def run(self, user_input: str, utility: float = 0.0) -> dict:
        """utility: 外部效用反馈。+1=本轮回答有用 / -1=没用 / 0=未评价"""
        # 1. 路由（保底三条强制）
        decision = self.router.route(user_input)
        assert decision.is_valid, "路由结果缺保底节点"

        # 2. 记忆上下文（可选，失败不阻断）
        context = ""
        if self.memory:
            hits = self.memory.query_knowledge(user_input, top_k=2)
            if hits:
                context = "\n\n".join(hits)[:3000]

        # 3. 走链执行
        result = self.executor.run(user_input + (f"\n\n[相关记忆]\n{context}" if context else ""),
                                   decision)

        # 4. 意图回归比率 → 赫布更新（快尺度）；未验证则跳过本题赫布（宁缺毋滥）
        import re
        m = re.search(r"比率\s*([\d.]+)", result.verify_note or "")
        ratio = float(m.group(1)) if m else None
        if ratio is None:
            update = {"skipped": True, "why": "意图回归未验证，赫布跳过"}
        else:
            fb = WalkFeedback(path=result.path, verify_ratio=ratio, utility=utility)
            update = self.hebbian.apply(fb, self.graph_file, self.edge_log)

        # 5. 记忆归档（总结保进化）
        if self.memory:
            self.memory.save_chat(user_input, result.final_answer,
                                  {"path": result.path, "complexity": decision.complexity})
            if decision.complexity != "simple":
                self.memory.save_flash(
                    f"Q: {user_input[:200]}\nA(要点): {result.final_answer[:600]}",
                    category="轮末总结")

        return {
            "answer": result.final_answer,
            "planned_path": result.planned_path,
            "actual_path": result.path,
            "deepen_events": result.deepen_events,
            "verify_note": result.verify_note,
            "hebbian": update,
            "complexity": decision.complexity,
        }


if __name__ == "__main__":
    import json
    import sys
    q = " ".join(sys.argv[1:]) or input("问题: ")
    e = Engine()
    r = e.run(q)
    print("\n" + "=" * 60)
    print(r["answer"])
    print("=" * 60)
    print("路径:", "→".join(r["actual_path"]), "|", r["verify_note"])
    if r["deepen_events"]:
        print("模型自决加深:", r["deepen_events"])