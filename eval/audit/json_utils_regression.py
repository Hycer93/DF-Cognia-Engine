"""json_utils_regression.py — 鲁棒 JSON 提取回归 (2026-11-21 P0-1)

背景: executor._parse_node_json 与 router._classify_llm 原用 find("{")/rfind("}")
     抠 JSON, 嵌套 JSON / 字符串内花括号 / 正文前缀花括号时切片错位, 走链降级。
     已换 cgraph/json_utils.extract_json_obj(括号计数扫描器)。
本用例锁死边界行为, 防回退。纯函数测试, 不碰库。

退出码 0=全过 1=有断言失败。输出 logs/json_utils_regression.json。
"""
import io
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _HERE)
OUT = os.path.join(_HERE, "logs", "json_utils_regression.json")


def main():
    from cgraph.json_utils import extract_json_obj
    results = []

    def expect(name, text, want_analysis, want_deepen):
        got = extract_json_obj(text)
        ok = (got is not None
              and got.get("analysis") == want_analysis
              and got.get("deepen") == want_deepen)
        results.append({"name": name, "ok": ok,
                        "got": got, "want": {"analysis": want_analysis, "deepen": want_deepen}})

    # 正常
    expect("plain", '{"analysis":"ok","deepen":"G08"}', "ok", "G08")
    # 嵌套 JSON 在字符串值内
    expect("nested_in_str", '{"analysis":"{\\"inner\\":1} 带嵌套","deepen":null}', '{"inner":1} 带嵌套', None)
    # 正文前置花括号(非 JSON 干扰)
    expect("prefix_brace", '说明 {不是JSON} 然后 {"analysis":"真实","deepen":"G24"}', "真实", "G24")
    # 字符串值内出现右花括号
    expect("brace_in_value", '{"analysis":"含 } 括号","deepen":"G08"}', "含 } 括号", "G08")
    # 转义引号
    expect("escaped_quote", '{"analysis":"他说\\"好\\"","deepen":null}', '他说"好"', None)
    # markdown fence
    expect("fence", '```json\n{"analysis":"fence","deepen":"G01"}\n```', "fence", "G01")
    # 多对象取第一个成功解析的
    expect("multi_obj", '{"analysis":"first"} 中间 {"analysis":"second"}', "first", None)

    # None 场景
    for name, text in [("pure_text", "这里没有任何 JSON"), ("empty", ""), ("only_open", "{未闭合")]:
        got = extract_json_obj(text)
        results.append({"name": name, "ok": got is None, "got": got, "want": None})

    failed = [r for r in results if not r["ok"]]
    report = {"ts": datetime.now().isoformat(timespec="seconds"),
              "total": len(results), "passed": len(results) - len(failed),
              "failed": len(failed), "results": results}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['name']}: got={r['got']} want={r['want']}")
    print(f"[json_utils 回归] {report['passed']}/{report['total']} 通过")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()