"""
旧库迁移：C:\\opt\\astra\\core\\chromadb → 新库 data/chromadb

只拷贝旧库中三个集合（flash_v2 / knowledge_v2 / chat_full_v2），逐条 add 到新集合。
embedding 由新库重新生成（默认嵌入函数），跳过已存在 id。

用法: python scripts/migrate_memory.py [--dry-run]
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OLD_PATH = r"C:\opt\astra\core\chromadb"
OLD_COLLECTIONS = {"flash_v2": "flash_v3", "knowledge_v2": "knowledge_v3", "chat_full_v2": "chat_v3"}


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.exists(OLD_PATH):
        print(f"旧库不存在: {OLD_PATH}（跳过迁移）")
        return

    import chromadb
    from cgraph.memory import MemoryStore

    import yaml
    cfg = yaml.safe_load(open(os.path.join(HERE, "config", "config.example.yaml"), encoding="utf-8"))
    new_store = MemoryStore(cfg)

    old = chromadb.PersistentClient(path=OLD_PATH)
    for old_name, new_name in OLD_COLLECTIONS.items():
        try:
            oc = old.get_collection(old_name)
        except Exception:
            print(f"跳过 {old_name}（不存在）")
            continue
        target = {"flash_v3": new_store.flash, "knowledge_v3": new_store.knowledge,
                  "chat_v3": new_store.chat}[new_name]
        data = oc.get(include=["documents", "metadatas"])
        n = len(data["ids"])
        print(f"{old_name}: {n} 条")
        if dry:
            continue
        batch_ids, batch_docs, batch_meta = [], [], []
        for i, (cid, doc) in enumerate(zip(data["ids"], data["documents"])):
            if doc is None:
                continue
            batch_ids.append(cid)
            batch_docs.append(doc)
            batch_meta.append(data["metadatas"][i] or {})
            if len(batch_ids) >= 200:
                target.add(ids=batch_ids, documents=batch_docs, metadatas=batch_meta)
                batch_ids, batch_docs, batch_meta = [], [], []
        if batch_ids:
            target.add(ids=batch_ids, documents=batch_docs, metadatas=batch_meta)
        print(f"  → 迁入 {new_name}: {target.count()} 条")

    print("迁移完成:", new_store.stats())


if __name__ == "__main__":
    main()