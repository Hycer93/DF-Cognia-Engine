# -*- coding: utf-8 -*-
"""
atomic_skill.py — 原子化元核·统一技能基类与轻量思考会话 State(2026-11-16 用户定案)

范式: 「Python 薄壳 + Prompt 内核」(沿用 F20/E7/mem_governor/topic锚/term别名桥
已验证范式, 不切换新范式):
  Python 侧(刚性, 全部代码化不交 LLM):
    - state 读写权限控制(技能只能读写邻接表声明的 state_inputs/state_outputs)
    - result schema 强校验(脏数据不向下流转, 降级兜底)
    - next_options/terminate_suggest 判定(取自邻接表+Python 条件, 不由模型生成)
    - 审计轨迹落盘(弹性模式选择留痕; 保守模式同样记轨迹)
    - 异常降级(技能失败不炸链路, 产出带 error 标记的降级 result)
  Prompt 侧(柔性, 仅语义工作):
    - 目标收敛/歧义识别/摘要归纳等, 输出受固定 JSON Schema 约束

轻量 State(内存态, 不持久化——单次思考任务 5 分钟内闭环):
  goal / goal_anchored / sub_questions / summary / trail / flags
权限红线: 技能不做事实裁决、不触发任何记忆提案、不读写 DFCP 卡片
(dfcp_link 声明的除外, 如未来 skill_f_reason)。
"""
from __future__ import annotations

import json
import re
import os
import sqlite3
import time
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADJACENCY_PATH = os.path.join(_HERE, "data", "atomic_skills_adjacency.json")

# 复用邻接表校验器(双触发规约: 加载时校验, 不过拒绝加载)
import sys
sys.path.insert(0, os.path.join(_HERE, "eval", "experiment"))
from adjacency_validator import assert_loadable, load_table

# 审计落库复用 audit_topic_log 表(E7 同风格; 元核 skill 轨迹独立归属, 不进 E7 晨报)
AUDIT_DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")


# ---------------- 轻量思考会话 State(内存态) ----------------

@dataclass
class ThinkState:
    """单次思考任务的最小状态。不存全量中间快照、不落盘。
    标记位: conflict/sensitive/confidence——技能只能通过 state_outputs 声明的键写。"""
    goal: str = ""
    goal_anchored: str = ""
    sub_questions: list = field(default_factory=list)
    summary: str = ""
    trail: list = field(default_factory=list)       # [(skill_id, chosen, candidates, reason, ts)]
    flags: dict = field(default_factory=dict)       # {"confidence": float, "sensitive": bool, "conflict": bool}
    steps: int = 0

    def read(self, key: str):
        """state 读取(编排层控制: 只允许技能读邻接表 state_inputs 声明的键)"""
        if "." in key:                              # flags.confidence 型嵌套
            k1, k2 = key.split(".", 1)
            return (self.flags if k1 == "flags" else getattr(self, k1, None) or {}).get(k2) \
                if isinstance(getattr(self, k1, None), dict) else None
        return getattr(self, key, None)

    def write(self, key: str, value):
        """state 写入(只允许技能写邻接表 state_outputs 声明的键)"""
        if "." in key:
            k1, k2 = key.split(".", 1)
            if k1 == "flags":
                self.flags[k2] = value
                return
        setattr(self, key, value)


# ---------------- 原子技能基类 ----------------

class BaseAtomicSkill:
    """原子思维技能基类。子类只需实现 build_prompt(语义) 与 parse_result(清洗);
    刚性框架(权限/schema/菜单/终止/审计/降级)全部由基类承载。

    生命周期: run(state) → 内部:
      ① 权限校验(state_inputs 可读性) → ② build_prompt → ③ llm.chat →
      ④ parse_result+schema 强校验 → ⑤ state_outputs 白名单写回 →
      ⑥ 返回 SkillResult(result/next_options/terminate_suggest/skill_id)
    next_options 与 terminate_suggest 判定逻辑由 Python 条件函数实现,
    取自邻接表——模型永远不生成菜单(漂移防线)。"""

    skill_id: str = ""              # 子类必须声明(与邻接表 entries key 一致)

    def __init__(self, llm=None, adjacency: dict = None):
        if not self.skill_id:
            raise ValueError("子类必须声明 skill_id")
        # 邻接表: 外部注入(编排器已 assert_loadable)或自行加载
        self.adjacency = adjacency or self._load_adjacency()
        spec = self.adjacency["entries"].get(self.skill_id)
        if not spec:
            raise KeyError(f"邻接表中无本技能条目: {self.skill_id}")
        self.spec = spec
        self.llm = llm

    @staticmethod
    def _load_adjacency() -> dict:
        assert_loadable(ADJACENCY_PATH)             # 双触发规约①: 加载时校验
        return load_table(ADJACENCY_PATH)

    # ---- 子类实现语义侧 ----

    def build_prompt(self, state: ThinkState) -> list:
        """返回 messages(list[dict])。只做语义组装, 读 state 用 self._in(state, key)。"""
        raise NotImplementedError

    def parse_result(self, raw: str, state: ThinkState) -> dict:
        """LLM 原文 → result dict(强校验+清洗+降级)。缺字段/非法枚举一律兜底。"""
        raise NotImplementedError

    # ---- 刚性框架(基类承载, 子类不碰) ----

    def _in(self, state: ThinkState, key: str):
        """受控读: 只放行邻接表 state_inputs 声明的键"""
        allowed = self.spec.get("state_inputs", [])
        if key not in allowed:
            raise PermissionError(f"{self.skill_id} 无权读 state.{key}(未在 state_inputs 声明)")
        return state.read(key)

    def run(self, state: ThinkState) -> "SkillResult":
        t0 = time.time()
        # ① 语义执行(子类); 异常降级——技能失败不炸链路
        try:
            messages = self.build_prompt(state)
            raw = self.llm.chat(messages) if self.llm else ""
            result = self.parse_result(raw, state) if raw else self._degraded("llm空返回")
        except Exception as e:
            result = self._degraded(f"技能异常: {e}")

        # ② schema 强校验: result 必须含邻接表声明的全部 result_fields(缺则降级补齐)
        for f in self.spec.get("result_fields", []):
            if f not in result:
                result[f] = ""                      # 脏数据不向下流转——补空而非透传脏值

        # ③ state_outputs 白名单写回
        so = self.spec.get("state_outputs", [])
        mapping = getattr(self, "STATE_OUTPUT_MAP", {})
        for k in so:
            v = mapping.get(k)
            if v is not None and v in result:
                state.write(k, result[v])
            elif k in result:
                state.write(k, result[k])

        # ④ 菜单与终止判定: 纯 Python 条件, 不由模型生成
        terminate = self.decide_terminate(state, result)
        state.steps += 1
        secs = round(time.time() - t0, 2)

        # ⑤ 审计轨迹(保守/弹性都记; 弹性模式的选择记录由编排器追加进 trail)
        _audit_log(self.skill_id, {"result_keys": list(result.keys()),
                                   "terminate": terminate, "secs": secs,
                                   "degraded": bool(result.get("_degraded"))})

        return SkillResult(skill_id=self.skill_id, result=result,
                           next_options=self.spec.get("next_options", []),
                           terminate_suggest=terminate, secs=secs)

    def decide_terminate(self, state: ThinkState, result: dict) -> bool:
        """终止判定: Python 条件实现(子类覆写)。默认: 邻接表 terminate_suggest 文本
        仅作文档; 实际判定必须代码化——试点期各技能显式覆写。"""
        return False

    def _degraded(self, why: str) -> dict:
        d = {f: "" for f in self.spec.get("result_fields", [])}
        d["_degraded"] = why
        return d


@dataclass
class SkillResult:
    """统一输出结构体(2026-11-16 方案定案): result+next_options+terminate_suggest+skill_id。
    next_options 项含 skill/gain/cost(决策引导, 取自邻接表, 模型不可生成)。"""
    skill_id: str
    result: dict
    next_options: list
    terminate_suggest: bool
    secs: float = 0.0


# ---------------- 审计(skill 轨迹独立日志, 不进 E7 晨报) ----------------

def _audit_log(skill_id: str, payload: dict):
    """元核 skill 审计: 落 audit_topic_log(op='atomic_skill'), 独立归属;
    仅当思考产出结论并提案入库时, 整条轨迹作为提案附件过人闸(裁决①)"""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        con = sqlite3.connect(AUDIT_DB)
        con.execute("INSERT INTO audit_topic_log (ts, op, payload) VALUES (?,?,?)",
                    (ts, f"atomic_skill:{skill_id}", json.dumps(payload, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception:
        pass                                        # 审计失败不阻断思考链路


# ---------------- 共享清洗工具(沿用 executor._parse_node_json 范式) ----------------

def extract_json(text: str) -> dict | None:
    """剥 markdown fence + 定位首尾大括号 + json.loads; 失败返回 None(调用方降级)"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        # 两级容错: strict失败后降级 strict=False, 容忍LLM在字符串值内输出裸换行/控制字符
        # (真题案例C2: ambiguity_note值内裸换行, 主体合法但json.loads默认strict抛
        #  "Invalid control character"); 二次失败才返回None走调用方_degraded降级
        for kwargs in ({}, {"strict": False}):
            try:
                return json.loads(t[s:e + 1], **kwargs)
            except Exception:
                continue
        return None
    return None
