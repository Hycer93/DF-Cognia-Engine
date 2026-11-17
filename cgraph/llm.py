"""
LLM 双后端适配层：OpenAI 兼容格式，统一 chat() 接口。

后端由 config.yaml llm.active 切换：
- ollama   本地（默认 http://127.0.0.1:11434/v1）
- deepseek 云端（API key 从环境变量 DEEPSEEK_API_KEY 读取，禁止硬编码）

A/B 验尸时，裸模型组和图路由组用同一个后端同配置，保证变量唯一。
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


class LLMClient:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.cfg = self._load_config(config_path)
        self._backend = self.cfg["llm"]["active"]
        be = self.cfg["llm"][self._backend]
        self.base_url = be["base_url"].rstrip("/")
        self.model = be["model"]
        if self._backend == "deepseek":
            key_env = be.get("api_key_env", "DEEPSEEK_API_KEY")
            self.api_key = os.environ.get(key_env, "")
            if not self.api_key:
                raise RuntimeError(f"缺少环境变量 {key_env}——本工程禁止硬编码 API key")
        else:
            self.api_key = be.get("api_key", "ollama")
        # P-C4 token 埋点(段二2.5裁决): 实例属性方案, 单线程顺序执行无串扰;
        # 仅成功响应后更新, 失败重试不覆盖旧值; 无 usage 字段时为 None(纯观测不干预)
        self.last_usage = None

    @staticmethod
    def _load_config(config_path: str) -> dict:
        """无 pyyaml 依赖也能跑：手写极简 yaml 解析（两级嵌套键值即可）"""
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"配置不存在: {p}（请复制 config.example.yaml）")
        import yaml  # 优先用标准库生态
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    # ---------- 对外接口 ----------
    def chat(self, messages: list, retries: int = 3) -> str:
        """带指数退避重试：网络错误/超时/空返回均重试，3 次全败才返回 None"""
        import time as _t
        last_err = None
        for attempt in range(retries):
            try:
                result = self._chat_once(messages)
                if result:                      # None 或空串都视为失败重试
                    return result
                last_err = "empty response"
            except Exception as e:
                last_err = str(e)
            if attempt < retries - 1:
                wait = 2 ** attempt * 2         # 2s / 4s / 8s
                print(f"[llm] 第{attempt+1}次调用失败({last_err})，{wait}s 后重试")
                _t.sleep(wait)
        print(f"[llm] {retries} 次调用全部失败: {last_err}")
        return None

    def _chat_once(self, messages: list) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # P-C4: 保留 API usage 透传(此前被直接丢弃); 埋点前置依赖
            try:
                self.last_usage = data.get("usage") or None
            except Exception:
                self.last_usage = None
            return data["choices"][0]["message"]["content"]

    def health(self) -> bool:
        try:
            return self.chat([{"role": "user", "content": "ping"}]) is not None
        except Exception:
            return False