"""p25_add_connect_timeout.py — P2-5 一次性 codemod 工具

为项目内所有 sqlite3 连接调用显式补 timeout 参数。
注: Python sqlite3.connect 默认 timeout 已是 5.0s, 本改造目的是显式化/统一/可审计,
     不改变运行时行为。

用法:
  python scripts/p25_add_connect_timeout.py --dry-run   # 只打印计划改动
  python scripts/p25_add_connect_timeout.py --apply     # 落盘

判定: AST 找 attr=='connect' 的 Call, 若 keywords 无 timeout、且 args 非空,
      在最后一个参数结束位置插入 ", timeout=5"。
"""
import argparse
import ast
import io
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ["memory", "cgraph", "eval", "scripts", "tools", "prompts"]
SKIP_DIR_PARTS = (".venv", "site-packages", "node_modules", "__pycache__", ".git")


def _iter_py():
    for d in DIRS:
        base = os.path.join(_HERE, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIR_PARTS]
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)


def _edits_for(src):
    """返回 [(offset, text)]，offset 为插入位置的绝对字符偏移。"""
    tree = ast.parse(src)
    edits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "connect"):
            continue
        if any(k.arg == "timeout" for k in node.keywords):
            continue
        if not node.args:
            continue  # connect() 无参不在改造面
        # 插入点 = 最后一个实参/keyword 的结束位置
        last = max(
            [a for a in node.args] + [k.value for k in node.keywords],
            key=lambda n: (getattr(n, "end_lineno", 0), getattr(n, "end_col_offset", 0)),
        )
        off = _abs_offset(src, last.end_lineno, last.end_col_offset)
        edits.append(off)
    return edits


def _abs_offset(src, lineno, col):
    lines = src.splitlines(keepends=True)
    return sum(len(x) for x in lines[: lineno - 1]) + col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = not args.apply

    total_files = total_sites = 0
    for path in sorted(_iter_py()):
        src = io.open(path, encoding="utf-8").read()
        try:
            edits = _edits_for(src)
        except SyntaxError as e:
            print(f"[skip-syntax] {os.path.relpath(path, _HERE)}: {e}", file=sys.stderr)
            continue
        if not edits:
            continue
        total_files += 1
        total_sites += len(edits)
        rel = os.path.relpath(path, _HERE)
        print(f"{rel}: +{len(edits)} timeout=5")
        if dry:
            continue
        # 从后往前插，避免偏移漂移
        for off in sorted(edits, reverse=True):
            src = src[:off] + ", timeout=5" + src[off:]
        io.open(path, "w", encoding="utf-8", newline="").write(src)
    verb = "将要修改" if dry else "已修改"
    print(f"\n[{verb}] {total_files} 个文件 / {total_sites} 处连接")
    if dry:
        print("（dry-run，未落盘；加 --apply 执行）")


if __name__ == "__main__":
    main()