"""鲁棒 JSON 对象提取（2026-11-21 P0-1：替换 find/rfind 简单切片）。

背景：executor._parse_node_json 与 router._classify_llm 原先用
`t.find("{")` / `t.rfind("}")` 抠 JSON，LLM 输出里一旦出现嵌套 JSON、
字符串字面量内含花括号、或正文先出现花括号后跟 JSON，切片边界即错位，
`json.loads` 失败 → 结构化信号（analysis/deepen）整条丢失，走链降级为纯文本。

本模块用括号计数扫描器提取候选对象，正确处理：
- 字符串字面量（"..." / '...'）内的花括号不计入层级
- 反斜杠转义（\\" \\\\）
- 多个候选对象时，按出现顺序返回第一个能成功 json.loads 且为 dict 的
- 兼容 markdown ```json fence 包裹

零外部依赖（项目无 requirements.txt，不引 json_repair/json5）。
"""
from __future__ import annotations

import json
import re


def _strip_fence(text: str) -> str:
    """剥掉 markdown 代码围栏（```json ... ``` / ``` ... ```）。"""
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def _iter_balanced_objects(t: str):
    """扫描字符串，逐个产出顶层花括号平衡的候选子串。

    以每个 '{' 为起点尝试配对；配对时跳过字符串字面量内部的括号。
    不跨越已闭合对象重复匹配：找到一个平衡对象后从其后继续。
    """
    n = len(t)
    i = 0
    while i < n:
        if t[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_str = False
        quote = ""
        escaped = False
        j = i
        closed = False
        while j < n:
            ch = t[j]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    in_str = False
            else:
                if ch in ("\"", "'"):
                    in_str = True
                    quote = ch
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        closed = True
                        break
            j += 1
        if closed:
            yield t[start:j + 1]
            i = j + 1          # 从对象之后继续，避免嵌套对象重复产出
        else:
            i = start + 1      # 未闭合，试下一个起点


def extract_json_obj(text: str):
    """从任意文本中提取第一个可解析的 JSON 对象（dict）。

    返回 dict；找不到返回 None。调用方自行决定降级策略。
    """
    if not text or not text.strip():
        return None
    t = _strip_fence(text)

    # 快路径：整段就是合法 JSON
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass

    for cand in _iter_balanced_objects(t):
        try:
            obj = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None