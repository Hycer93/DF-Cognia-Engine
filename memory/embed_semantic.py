# embed_semantic.py — bge-small-zh ONNX 语义嵌入（风险1对策原型，2026-11-11）
# 用途：替代 char2gram 做 _embed 的语义基座。模板句式碰撞（专利P37/P39/P43
# 互撞 0.61 vs 去模板 0.30）实测证明 2-gram 无语义能力，bge 感知"格式相似≠内容相似"。
# 加载：onnxruntime CPU + tokenizer.json（tokenizers 库或手写 BERT wordpiece）。
# EMBEDDING_BASE = "bge-small-zh-v1.5-onnx-int8"

import os
import json
import numpy as np
import onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(os.path.dirname(HERE), "calibration", "embed_bge_small_zh")
EMBEDDING_BASE = "bge-small-zh-v1.5-onnx-int8"

_sess = None
_tok = None


def _load():
    global _sess, _tok
    if _sess is not None:
        return
    _sess = ort.InferenceSession(
        os.path.join(MODEL_DIR, "model_quantized.onnx"),
        providers=["CPUExecutionProvider"])
    from tokenizers import Tokenizer
    _tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))


def _pool(last_hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """BGE = CLS 池化 + L2 归一（sentence-transformers 配置）。"""
    cls = last_hidden[:, 0, :]
    cls = cls / (np.linalg.norm(cls, axis=1, keepdims=True) + 1e-12)
    return cls


_cache = {}          # text → 向量（库级缓存：全库 summary 常驻，查询增量）


def warmup(texts: list) -> int:
    """批量预热：缺失文本一次性 batch 推理填缓存（159 条单跑 8s → 批跑 <1s）。"""
    miss = [t for t in set(texts) if t and t not in _cache]
    if not miss:
        return 0
    for i in range(0, len(miss), 128):
        v = embed(miss[i:i + 128])
        for j, txt in enumerate(miss[i:i + 128]):
            _cache[txt] = v[j]
    return len(miss)


def embed_cached(text: str) -> np.ndarray:
    """单条带缓存接口——E2/检索高频调用同文本，避免重复推理。"""
    if text not in _cache:
        v = embed([text])
        _cache[text] = v[0] if v.ndim == 2 else v
    return _cache[text]


def embed(texts: list) -> np.ndarray:
    """中文文本 → (n, 512) 归一化语义向量。批量单次推理。"""
    _load()
    enc = _tok.encode_batch(texts)
    ids = np.zeros((len(enc), 64), dtype=np.int64)      # 512维模型,seq 64够摘要用
    mask = np.zeros((len(enc), 64), dtype=np.int64)
    typ = np.zeros((len(enc), 64), dtype=np.int64)
    for i, e in enumerate(enc):
        t = e.ids[:62]
        ids[i, :len(t) + 2] = [e.ids[0]] + t + [e.ids[-1]] if len(e.ids) >= 2 else t
        ids[i, 0] = e.ids[0] if e.ids else 0
        seq = e.ids[:62]
        ids[i, 1:len(seq) + 1] = seq
        mask[i, :len(seq) + 2] = 1
    out = _sess.run(None, {
        "input_ids": ids, "attention_mask": mask, "token_type_ids": typ})
    return _pool(out[0], mask)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))     # 已归一化,点积=余弦


if __name__ == "__main__":
    # 自检:模板碰撞实验复现——P37/P39/P43 + 模板对照 + 真同物对
    tests = [
        "专利P37:一种基于密钥加密的策略密文盲调度方法及系统[已提交个人(保留)]",
        "专利P39:一种基于ODL域外联动机制的分层正态网络样本处理方法[已提交个人(保留)]",
        "专利P43:一种基于多偏离度张量注意力的神经网络[已提交个人(保留)]",
        # 真同物对(新旧两套账)
        "专利P3:本地仲裁隔离与最小信息下发安全方法[已提交公司]",
        "P3 本地仲裁隔离与最小信息下发的用户数据防泄露与AI行为防失控 [已提交(德赛西威)]",
        # 真不同物但同域(应低于同物)
        "专利P1:基于可复现性判定的AI记忆分级存储方法[已提交公司]",
    ]
    v = embed(tests)
    print("== bge-small-zh 判别实验 ==")
    pairs = [("P37-P39(假撞)", 0, 1), ("P37-P43(假撞)", 0, 2), ("P39-P43(假撞)", 1, 2),
             ("P3新旧账(真同物)", 3, 4), ("P3-P37(不同物)", 3, 0),
             ("P1-P37(不同物)", 5, 0)]
    for name, i, j in pairs:
        print(f"  {name}: {cosine(v[i], v[j]):.4f}")