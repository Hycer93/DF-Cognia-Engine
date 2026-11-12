"""
CodeChat 后端适配器：aiplus.desaysv.com task-agent 协议。

协议要点（逆向自桌面端）：
  POST https://aiplus.desaysv.com/chatbot/v3/task-agent
  Authorization: <token>（裸 token，无 Bearer 前缀）
  body: {client, version, record, action:"action.chat", user, token,
         messages:[{role,content}], model, session_id, chat_id, task_id,
         operation:"normal", mode:"action", think, memory}
  响应为流式 JSON 行：{code:"0", data:{block:[{id,type,content}], content, finish_reason}}
  文本回答 = 按 id 去重收集所有 type:text 的 block.content 拼接

token 获取：从桌面端 Local Storage 自动提取（活跃登录态），或环境变量 CODECHAT_TOKEN。

注意：这是 agent 化后端——服务端自带工具调用与编排。用作 A/B 对照时
B 组的优势可能部分来自服务端 agent（变量污染），裁决时需注明。
"""
from __future__ import annotations

import base64
import glob
import json
import re
import time
import urllib.request
import uuid

API_URL = "https://aiplus.desaysv.com/chatbot/v3/task-agent"
DEFAULT_MODEL = "glm-5.3"
# 优先用轻量模型跑测试，减少对主模型配额的占用
TEST_MODEL = "deepseek-v4.1-flash"


def extract_token() -> str:
    """从 CodeChat 桌面端 Local Storage 提取未过期 JWT（本机登录态）"""
    import os
    env = os.environ.get("CODECHAT_TOKEN")
    if env:
        return env.strip()
    best, best_exp = None, 0
    for f in glob.glob(r"C:\Users\*\AppData\Roaming\DSV CodeChat Desktop\Local Storage\leveldb\*") + \
             glob.glob(r"C:\Users\uidq0280\AppData\Roaming\DSV CodeChat Desktop\Local Storage\leveldb\*"):
        try:
            data = open(f, "rb").read()
        except Exception:
            continue
        for m in re.finditer(rb"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9[A-Za-z0-9_\-.]{50,400}", data):
            tok = m.group(0).decode("ascii", errors="ignore")
            exp = _jwt_exp(tok)
            if exp > best_exp:
                best, best_exp = tok, exp
    if not best:
        raise RuntimeError("未找到 CodeChat token：请先登录桌面端，或 set CODECHAT_TOKEN=...")
    if best_exp < time.time():
        raise RuntimeError("CodeChat token 已过期：请重新打开桌面端登录后重试")
    return best


def _jwt_exp(tok: str) -> int:
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return int(json.loads(base64.urlsafe_b64decode(p)).get("exp", 0))
    except Exception:
        return 0


class CodeChatLLM:
    """v3 的 chat(messages) -> str 兼容后端"""

    def __init__(self, model: str = None, timeout: int = 300, verbose: bool = False):
        self.token = extract_token()
        self.model = model or TEST_MODEL
        self.timeout = timeout
        self.verbose = verbose

    def chat(self, messages: list) -> str:
        sid = f"v3-{uuid.uuid4().hex[:16]}"
        payload = json.dumps({
            "client": "desktop", "version": "v3", "record": "record",
            "action": "action.chat", "user": "v3-engine", "token": self.token,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "model": self.model, "session_id": sid, "chat_id": sid, "task_id": sid,
            "operation": "normal", "mode": "action", "think": "no", "memory": "no",
        }).encode("utf-8")
        req = urllib.request.Request(API_URL, data=payload, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": self.token})
        text_blocks: dict = {}   # id -> content（去重累积）
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                buf = b""
                while True:
                    chunk = r.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        end = _json_obj_end(buf)
                        if end < 0:
                            break
                        obj_str, buf = buf[:end].decode("utf-8", errors="ignore"), buf[end:]
                        self._consume(obj_str, text_blocks)
        except Exception as e:
            if self.verbose:
                print(f"[codechat] 请求异常: {e}")
            return None
        answer = "\n".join(text_blocks.values()).strip()
        return answer or None

    @staticmethod
    def _consume(obj_str: str, text_blocks: dict):
        try:
            obj = json.loads(obj_str)
        except json.JSONDecodeError:
            return
        if str(obj.get("code")) != "0":
            return
        data = obj.get("data") or {}
        for b in data.get("block") or []:
            if b.get("type") == "text" and isinstance(b.get("content"), str):
                bid = b.get("id")
                if bid:
                    text_blocks[bid] = b["content"]   # 同 id 覆盖（流式终值）
        # 无 block 时兜底 content 字段
        if not (data.get("block")) and isinstance(data.get("content"), str) and data["content"]:
            text_blocks[f"c_{len(text_blocks)}"] = data["content"]

    def health(self) -> bool:
        return self.chat([{"role": "user", "content": "回复ok"}]) is not None


def _json_obj_end(buf: bytes) -> int:
    """流式缓冲中第一个完整顶层 JSON 对象的结束位置；无则 -1（处理字符串内花括号）"""
    depth, in_str, esc = 0, False, False
    for i, b in enumerate(buf):
        ch = chr(b)
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
                if depth == 0:
                    return i + 1
    return -1


if __name__ == "__main__":
    llm = CodeChatLLM()
    t0 = time.time()
    ans = llm.chat([{"role": "system", "content": "你是测试助手。"},
                    {"role": "user", "content": "请直接回答：1+1等于几？不要调用任何工具。"}])
    print(f"[{time.time()-t0:.1f}s] 回答: {ans}")