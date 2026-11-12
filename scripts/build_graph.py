"""
从旧工程 config/cognition_mapping.json 转译 27 骨架 → mind_graph.json

旧 JSON 尾部截断（"instruction": 处损坏），本脚本做 brace 修复后解析。
用法: python scripts/build_graph.py [old_json_path] [out_path]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from cgraph.graph import MindGraph  # noqa: E402


def repair_json(raw: str) -> dict:
    """旧文件尾部被截断：截去最后一个完整键之后的内容，补齐大括号。"""
    idx = raw.rfind('"training_data_format"')
    if idx > 0:
        raw = raw[:idx]
    raw = raw.rstrip().rstrip(",")
    depth = 0
    in_str = False
    esc = False
    for ch in raw:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    raw += "}" * depth
    return json.loads(raw)


def main():
    old = sys.argv[1] if len(sys.argv) > 1 else r"D:\uidq0280\Downloads\Agent\cognitive_engine\config\cognition_mapping.json"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data", "mind_graph.json")

    raw = open(old, encoding="utf-8").read()
    mapping = repair_json(raw)
    g = MindGraph.from_mapping(mapping)
    g.save(out)

    s = g.stats()
    print(f"图构建完成: {s['nodes']} 节点 / {s['edges']} 边 → {out}")
    # 校验三条保底节点存在
    for m in ("Aa", "Eo", "Jw"):
        assert m in g.nodes, f"保底节点缺失: {m}"
    print("保底节点校验通过: Aa(发散) Eo(验证) Jw(总结)")
    print(f"强边(>1.0): {len(s['strong_edges'])}  弱边(<0.3): {len(s['weak_edges'])}")


if __name__ == "__main__":
    main()