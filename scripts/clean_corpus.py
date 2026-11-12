"""语料清洗：去除训练数据中的 DEEPEN 标记残留 + 规范化。

根因：executor 识别 [[DEEPEN:xx]] 后只在'标记在开头'时剥正文，
全文扫描模式下标记在中间时正文剥离逻辑失效——残留进 chosen。
本脚本做事后清洗（对已落盘数据），根因修复在 executor 侧另做。

清洗规则：
1. 移除 [[DEEPEN:xx]] 标记（含其后紧跟的换行）——训练文本不该教模型输出协议标记
2. 压缩 3+ 连续换行为 2（标记删除后的空洞）
3. 行首尾空白规范化
"""
import json, re, sys, os, glob

DEEPEN_RE = re.compile(r"\[\[DEEPEN:[^\]]*\]\]\s*")


def clean_text(t: str) -> str:
    t = DEEPEN_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_file(path: str, fields=("chosen", "rejected", "prompt", "thought_chain")):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    n_changed = 0
    for r in rows:
        changed = False
        for f in fields:
            if f in r and isinstance(r[f], str) and "[[DEEPEN" in r[f]:
                r[f] = clean_text(r[f])
                changed = True
        if changed:
            n_changed += 1
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return len(rows), n_changed


if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = sys.argv[1:] or glob.glob(os.path.join(base, "data", "trajectories", "*_train_*.jsonl"))
    for p in targets:
        total, changed = clean_file(p)
        print(f"{os.path.basename(p)}: {total} 条, 清洗 {changed} 条")
    # 验证
    for p in targets:
        n = sum(1 for l in open(p, encoding="utf-8") if "[[DEEPEN" in l)
        print(f"{os.path.basename(p)}: 残留 {n} 处")