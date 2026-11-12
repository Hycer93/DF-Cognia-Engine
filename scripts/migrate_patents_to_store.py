# -*- coding: utf-8 -*-
"""scripts/migrate_patents_to_store.py — 41件专利交底书按内容锚定入库（2026-11-12）
用户裁决：编号不重要（编号随时更新），重要的是内容。
- 树地址：patent.<名称slug>（发明名称=唯一稳定锚，编号无关）
- 字典键：cluster（技术簇，从技术领域段自动判定）/ status 从txt内容/文件名线索初判，后续可改
- 正文原文不动入 raw_corpus；名称解析双轨（标准交底书轨 + markdown评审稿轨）
"""
import sys, os, re, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import corpus_store as cs

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "patent_evidence")

# 技术簇判定：按"技术领域"段关键词（内容锚定，非编号）
CLUSTERS = [
    ("记忆系", ["记忆", "经验权重", "知识库", "知识蒸馏", "召回", "检索", "遗忘"]),
    ("调度协同系", ["调度", "协同", "指令", "通信协议", "任务分发", "路由", "多模型", "多算子", "仲裁"]),
    ("安全伦理系", ["防泄露", "伦理", "安全", "防失控", "管控", "身份锚点", "追溯", "强制"]),
    ("架构推理系", ["神经网络", "Transformer", "推理", "正态扩域", "认知头", "认知分支", "张量"]),
    ("训练进化系", ["训练", "梯度", "反向传播", "SFT", "进化", "认知断点", "置信度", "蒸馏"]),
    ("情感个体系", ["情感", "个体", "叙事", "性格"]),
]

def slugify(name: str) -> str:
    """名称→树地址slug：去常见前缀，截核心词，保唯一性由调用方保证"""
    s = name
    for pre in ("一种基于", "一种面向", "一种具备", "基于", "面向", "一种"):
        if s.startswith(pre):
            s = s[len(pre):]; break
    s = re.sub(r"[（(].*?[)）]", "", s)          # 去括号注记 (CIPP) 等
    s = re.sub(r"[，。、""''\s:：✅✳\*#]", "", s)
    s = re.sub(r"(方法及系统|系统及方法|方法|系统)$", "", s)
    return s[:24] or name[:16]

def parse_name(fn: str, txt: str) -> str:
    """双轨名称解析：标准交底书（发明名称:标签）→ markdown评审稿 → 文件名回退"""
    for m in re.finditer(r"发明名称[：:]*\s*\n", txt):
        after = txt[m.end():m.end()+120]
        for line in after.split("\n"):
            s = line.strip().strip("*#").strip()
            s = re.split(r"[✅✳\*（(##]", s)[0].strip()
            if not s or s in ("发明名称",):
                continue
            if s.startswith("（") or len(s) < 4:
                continue
            return s
    # md轨：首个加粗/非空行
    for line in txt.strip().split("\n")[:8]:
        s = line.strip().strip("*#").strip()
        if 4 < len(s) < 70 and not s.startswith(("发明", "**", "-")):
            return s
        if s.startswith("**") and s.endswith("**"):
            s2 = s.strip("*").strip()
            if 4 < len(s2) < 70:
                return s2
    # 文件名回退
    name = fn.rsplit(".", 1)[0]
    return re.split(r"[_（(✅]", name)[0].strip()

def parse_field(txt: str) -> str:
    m = re.search(r"技术领域\s*\n(.+?)(?:\n\s*\n|发明人|申请人|一、|##)", txt, re.S)
    return (m.group(1).strip().replace("\n", "") if m else "")[:200]

def judge_cluster(name: str, field: str) -> str:
    blob = name + " " + field
    best, score = "其他", 0
    for cl, kws in CLUSTERS:
        s = sum(1 for k in kws if k in blob)
        if s > score:
            best, score = cl, s
    return best

def guess_status(fn: str, txt: str) -> str:
    """从内容线索初判状态——不确定标 unknown，人工终裁可改"""
    head = txt[:600]
    if "已提交" in head or "✅" in fn or "提交" in head and "未提交" not in head:
        return "submitted_guess"
    if "未提交" in head or "📝" in head:
        return "draft"
    if "撤回" in head:
        return "withdrawn_guess"
    return "unknown"


def main():
    cs.init_db()
    files = sorted(os.listdir(D))
    seen = {}
    for fn in files:
        if not fn.endswith(".txt"):
            continue
        txt = io.open(os.path.join(D, fn), encoding="utf-8").read()
        name = parse_name(fn, txt)
        slug = slugify(name)
        # 同名去重（如"树形统一编码"两版/“结构化二进制”CIPP两版）: 后缀v2
        base = slug
        n = 2
        while f"patent.{slug}" in seen:
            slug = f"{base}v{n}"
            n += 1
        seen[f"patent.{slug}"] = fn
        field = parse_field(txt)
        cluster = judge_cluster(name, field)
        status = guess_status(fn, txt)
        cid = f"patent.{slug}"
        h = cs.upsert_corpus(
            cid, "patent_doc", txt, title=name,
            source_ref=f"文件迁移: data/patent_evidence/{fn} (2026-11-12)")
        cs.add_tags(cid, {"cluster": [cluster], "status": [status],
                          "evidence_file": [fn]})
        print(f"{cid:44s} [{cluster}] {status:16s} {len(txt):6d}字 {name[:36]}")
    print(f"\n共 {len(seen)} 件入库")


if __name__ == "__main__":
    main()