"""
记忆层（精简版）：闪念 / 知识库 / 对话 三集合，ChromaDB 持久化。

相比旧工程 7514 行的 memory 模块，这里只做三件事：
1. 写入（对话归档 / 闪念保存）
2. 语义检索（供路由前上下文注入）
3. 迁移兼容（scripts/migrate_memory.py 可把旧库集合拷贝过来）
"""
from __future__ import annotations

import os
from datetime import datetime


class MemoryStore:
    def __init__(self, cfg: dict):
        import chromadb  # 延迟导入，未用到记忆时不加载
        from chromadb.config import Settings
        path = cfg["memory"]["chroma_path"]
        os.makedirs(path, exist_ok=True)
        # 本地持久库 + 关闭 telemetry（避免代理环境 SSL 报错）
        self.client = chromadb.PersistentClient(
            path=path, settings=Settings(anonymized_telemetry=False))
        cols = cfg["memory"]["collections"]
        self.flash = self.client.get_or_create_collection(cols["flash"])
        self.knowledge = self.client.get_or_create_collection(cols["knowledge"])
        self.chat = self.client.get_or_create_collection(cols["chat"])

    # ---------- 写（记忆失败不阻断主流程） ----------
    def _add(self, collection, ids, documents, metadatas):
        try:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as e:
            print(f"[warn] 记忆写入失败（不阻断）: {type(e).__name__}")

    def save_chat(self, user_input: str, answer: str, meta: dict | None = None):
        ts = datetime.now().isoformat()
        cid = f"chat_{int(datetime.now().timestamp()*1000)}"
        self._add(self.chat, [cid],
                  [f"用户: {user_input}\n助手: {answer}"],
                  [{"timestamp": ts, **(meta or {})}])

    def save_flash(self, content: str, category: str = "认知资产", meta: dict | None = None):
        """闪念：每轮对话中值得长期保留的资产（对应你的'总结保进化'）"""
        ts = datetime.now().isoformat()
        fid = f"flash_{int(datetime.now().timestamp()*1000)}"
        self._add(self.flash, [fid], [content[:2000]],
                  [{"category": category, "timestamp": ts, **(meta or {})}])

    def save_knowledge(self, content: str, meta: dict | None = None):
        kid = f"kb_{int(datetime.now().timestamp()*1000)}"
        self._add(self.knowledge, [kid], [content[:4000]],
                  [{"timestamp": datetime.now().isoformat(), **(meta or {})}])

    # ---------- 读 ----------
    def query_flash(self, text: str, top_k: int = 5) -> list[str]:
        return self._query(self.flash, text, top_k)

    def query_knowledge(self, text: str, top_k: int = 3) -> list[str]:
        return self._query(self.knowledge, text, top_k)

    @staticmethod
    def _query(collection, text: str, top_k: int) -> list[str]:
        try:
            r = collection.query(query_texts=[text], n_results=top_k)
            return [d for d in (r["documents"][0] if r["documents"] else [])]
        except Exception:
            return []

    def stats(self) -> dict:
        return {name: col.count() for name, col in
                [("flash", self.flash), ("knowledge", self.knowledge), ("chat", self.chat)]}