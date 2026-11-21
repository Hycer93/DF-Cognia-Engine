"""connect_timeout_regression.py — P2-5 回归 (2026-11-21)

断言项目内所有 sqlite3 连接调用都显式带 timeout 参数。
背景: Python sqlite3.connect 默认 timeout 已是 5.0s, 补参目的是显式化/统一/可审计,
      防止后续新增连接漏掉超时设定而静默引入永久阻塞。

检查:
  1. 全部 *.connect(...) 调用均含 timeout= 关键字 (AST 精确判定)
  2. 无 timeout 的调用清单为空 (违规时逐条打印 文件:行号)
退出码 0=全过 1=有违规。白名单: 本脚本自身。
"""
import ast
import io
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRS = ["memory", "cgraph", "eval", "scripts", "tools", "prompts"]
SKIP_DIR_PARTS = (".venv", "site-packages", "node_modules", "__pycache__", ".git")
SELF = os.path.abspath(__file__)


def iter_py():
    for d in DIRS:
        base = os.path.join(_HERE, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIR_PARTS]
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    if os.path.abspath(p) == SELF:
                        continue
                    yield p


def main():
    offenders, sites = [], 0
    for path in sorted(iter_py()):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except SyntaxError as e:
            print(f"[skip-syntax] {path}: {e}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "connect"):
                continue
            sites += 1
            if not any(k.arg == "timeout" for k in node.keywords):
                offenders.append((os.path.relpath(path, _HERE), node.lineno))

    print(f"[connect_timeout] 扫描 {sites} 处 connect() 调用")
    if offenders:
        print(f"❌ {len(offenders)} 处缺 timeout:", file=sys.stderr)
        for f, ln in offenders:
            print(f"   {f}:{ln}", file=sys.stderr)
        sys.exit(1)
    print("✅ 全部 connect() 调用均显式带 timeout")
    sys.exit(0)


if __name__ == "__main__":
    main()