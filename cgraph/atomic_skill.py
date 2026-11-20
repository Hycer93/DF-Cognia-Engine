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
    # P-C4 参考素材通道(段二2.1契约): 用户原始附属材料原样保存; 空串=无素材(常态,
    # 行为与旧版等价); 仅 run_atomic 入口与 anchor_target 两处可写(R13 素材只读红线:
    # 写入后链中任何技能禁止修改/覆盖/二次截断); 不落盘、不进 trail 防日志膨胀
    ref_material: str = ""
    # P-matrix-feed(2026-11-18 段二契约): 外部领域知识矩阵注入条目。
    # 独立字段, 绝不混入 ref_material(隔离红线); 仅 executor 在 scan 后一次性装载,
    # 仅 f_reason/weakest_link 只读消费; sufficiency 判据零读取(R15a)。
    # 任何降级(文件缺失/零命中/超限)恒 [], 链路行为与改造前一致。
    matrix_knowledge: list = field(default_factory=list)
    # collab synthesize 旁路(2026-11-20 #9 用户裁决): 仅 scripts/collab_pipeline
    # 的多路协同场景可置 True(经 run_atomic 入参注入), 普通任务恒 False。
    # True 时 router._clean_menu 绕过 goal_type==scheme_design 的 synthesize 门控,
    # 其余路由规则不变; 邻接表不变(f_reason→synthesize→terminate 支线)。
    collab_synthesize_override: bool = False
    sub_questions: list = field(default_factory=list)
    summary: str = ""
    trail: list = field(default_factory=list)       # [(skill_id, chosen, candidates, reason, ts)]
    flags: dict = field(default_factory=dict)       # {"confidence": float, "sensitive": bool, "conflict": bool}
    steps: int = 0
    # 存量动态输出键显性化(P-C4 校验器 G 检查逼出的隐性契约): 此三键由各技能
    # state_outputs 声明并经 write() 动态 setattr 写入, 原本未在 dataclass 声明——
    # 校验器上线后必须显性化否则字段漂移硬拦截; 默认 None=未写入态
    weakest_link: object = None                     # skill_weakest_link 主字段(单点语义)
    hypotheses: list = None                          # skill_f_reason 假设谱
    # P-skill-synthesize(2026-11-18 段三T5): 三输出键显性化(沿上方先例);
    # 默认 None=未写入态, 由 skill_synthesize 的 state_outputs 声明经 write() 写入
    selection_rationale: object = None              # 路线权衡(引用假设编号)
    final_deliverable: object = None                # 终稿推演(交付内容)
    alternatives_appendix: object = None            # 备选假设完整留存
    source_intents: list = None                     # skill_source_intent 意图清单
    # ---- P-skeleton-complete(2026-11-19 段一定稿 v1.0, docs/design_skeleton_skills_v01.md) ----
    # 契约: 以下 9 字段各由对应技能独写(邻接表 state_outputs), 其余技能只读禁覆盖;
    # 默认 None=未写入态; 全家族混合范式——LLM 降级时产出置空+_degraded, 链路不阻塞
    topology: object = None          # skill_g24_topology 独写: 四图投影快照
    gaps: list = None                # skill_g24_topology 独写: 唯一对外缺口出口
                                     #   (合并 uncovered_subquestions+unaddressed+unsupported)
    bd_matrix: list = None           # skill_bd_crosscheck 独写: 假设×薄弱点关联矩阵
    unaddressed: list = None         # skill_bd_crosscheck 独写: 无假设承接的薄弱点
                                     #   (Python 派生, 送 G24 合并; state 独立留观测)
    deduction_chains: list = None    # skill_dk_deduction 独写: 证据→前提→结论链
                                     #   (unsupported_premises 并入本容器字段)
    evidence_net: list = None        # skill_en_evidence_net 独写: 证据横向关联边
    isolated_evidence: list = None   # skill_en_evidence_net 独写: 引用计数=0 证据(Python 派生)
    contradiction_pairs: list = None # skill_en_evidence_net 独写: 矛盾对
                                     #   (不进 gaps——terminate 汇编独立"证据冲突说明"章节)
    gs_view: object = None           # skill_gs_review 独写: 过程检查点
                                     #   (drift_warnings+resume_view; 每次执行覆盖=最新检查点)

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


# ---------------- P-C4 参考素材消费侧阈值分流(段二2.4/2.5 契约) ----------------

# REF_INLINE_THRESHOLD(P-C6 段二裁决 2000→8000, R14 封闭): 消费分流阈值——
#   ≤8000字符: 完整直塞进技能 prompt;
#   >8000字符: 调 ref_retrieval.retrieve() 只拉相关片段(素材本体完整保留在 state)。
# 与 REF_MAX_CHARS=200_000(入口写入硬上限, 见 executor.run_atomic)是两套独立参数(R14-c 职责隔离)。
# R14-a: 取值依据真实分布 P1≈4045/P24≈6401 核心业务样本全落于 2000~8000 区间, 禁止裸拍;
#        旧值 2000 为 P-C4 时期无真实样本的保守初值, 防爆职责已由 token 埋点+200K 硬限替代。
REF_INLINE_THRESHOLD = 8000


def _ref_material_block(state: "ThinkState", skill) -> str:
    """消费技能 build_prompt 统一注入块。
    无素材(空串)返回 ""——prompt 与旧版逐字节等价(向前兼容段三3.6)。
    读 state.ref_material 需技能在邻接表 state_inputs 声明该键, 否则不注入
    (权限模型内自然降级, 不 raise)。检索异常返回[]等价于无片段(段三3.3)。"""
    try:
        if "ref_material" not in skill.spec.get("state_inputs", []):
            return ""
        mat = state.ref_material or ""
        if not mat:
            return ""
        if len(mat) <= REF_INLINE_THRESHOLD:
            return f"\n\n参考素材(完整):\n{mat}"
        from cgraph.ref_retrieval import retrieve
        query = (getattr(state, "goal_anchored", "") or "") + "\n" + \
                "\n".join(str(q.get("q", ""))[:100] for q in (state.sub_questions or [])[:4] if isinstance(q, dict))
        frags = retrieve(query, mat, top_k=3)
        if not frags:
            return ""
        joined = "\n---\n".join(frags)
        return (f"\n\n参考素材(原始{len(mat)}字符超直塞阈值, 以下为与任务最相关的片段, "
                f"素材可能不完整):\n{joined}")
    except Exception:
        return ""                                  # fail-safe: 注入失败=无素材, 不劣于现状


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

        # ⑥ P-C4 token 埋点(段三3.4): 纯观测不干预; try整体包裹——埋点自身故障
        #    绝不干扰业务链路(对齐 _audit_log 哲学); 取最后一次 chat 的 usage;
        #    无 usage 记 None; 多次调用暂不累计(planned P-token-budget)
        try:
            u = getattr(self.llm, "last_usage", None) if self.llm else None
            if u is None:
                result["token_in"] = None
                result["token_out"] = None
            else:
                result["token_in"] = u.get("prompt_tokens") if isinstance(u, dict) else None
                result["token_out"] = u.get("completion_tokens") if isinstance(u, dict) else None
        except Exception:
            pass                                    # 埋点失败: 不写键, 不阻断

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
