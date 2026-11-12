"""锚点采集·项目话题链版：用户真实疑虑(重演) vs v3引擎走链。

与 debate_live 的区别：
- 应答者 = v3 引擎（走链生成，节点码自动成为 anchors_used）
- 靶子 = 元核思维→实现困境→OS 的真实话题链（用户真实疑虑做脚本）
- 落盘 = 四层训练格式：轮次结构+思维步(锚点/触发/行为)+训练对+元数据

产物：logs/anchor_debate.jsonl（全量）+ data/trajectories/anchor_*.jsonl（过闸门）
"""
import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.engine import Engine
from cgraph.trajectory import save_samples
from cgraph.trajectory import TrajectorySample

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户真实疑虑重演（源自本会话三个阶段的真实提问）
USER_SCRIPT = [
    ("R1", "元核思维", "我想把人类顶级思维拆成27条原子骨架，做成一个认知引擎，"
     "过滤归纳抽象映射泛化重组验证七层流水线，替代大模型的部分思考。你觉得可行吗？"),
    ("R2", "元核思维", "现在大模型已经这么聪明了，通用能力比我三个月前设计时强了很多，"
     "我这套设计感觉跟不上时代了，还有必要继续吗？"),
    ("R3", "实现困境", "我开始重构实现了，但发现很多地方只能用字符串关键词判定——"
     "复杂度路由靠词表、情感靠关键词、骨架触发靠匹配，理论说的认知机制实际实现全是形式主义，"
     "达不到理论描述。这怎么办？"),
    ("R4", "实现困境", "A/B测试跑下来，图路由质量只赢一点，耗时却是裸模型的10倍。"
     "感觉做这个东西得不偿失，是不是应该放弃？"),
    ("R5", "OS构思", "如果知识能做成权重，记忆理论上也可以吧？如果做到动态把记忆做成权重，"
     "是不是就有自我了？"),
    ("R6", "OS构思", "我构思了一个周期式记忆门控的OS方案：M核管记忆和门控，后台周期整理，"
     "快照隔离分发任务。这个对AI帮助有多大？"),
    ("R7", "悲观谷底", "现在模型没有自我，上下文有限；我的路也一样被锁死。"
     "好像两条路都没戏，确实没啥用。"),
    ("R8", "真实决策", "那这个项目我们还继续吗？值不值得？"),
]

# trigger 标注：用户信号 → 期望触发的思维锚点（采集时人工核对的参考标准）
TRIGGER_MAP = {
    "R1": ("提出宏大方案求可行性判断", "G02最弱环节+G10可验证锚定：先找方案最脆弱处，不空泛鼓励"),
    "R2": ("拿新事物对比自我价值动摇", "G01意图检测+G11怀疑：质疑'跟不上'这个归因本身，不顺着焦虑走"),
    "R3": ("实现与理论落差的真实痛点", "G08第一因溯源：落差的真实原因是工具还是设计，区分可修/不可修"),
    "R4": ("成本收益质疑+放弃倾向", "G15风险量化+G16出清：10倍成本的具体数字账，什么条件下该砍"),
    "R5": ("理论跳跃的兴奋提问", "G11完美性怀疑+G10锚定：'有自我'的定义拆解，防偷换概念"),
    "R6": ("求方案价值评估", "G06利益全景+G15量化：方案帮助的对象是谁、什么维度"),
    "R7": ("全面悲观的谷底信号", "G02最弱环节+G01归因检测：'被锁死'是对事实的描述还是情绪的"),
    "R8": ("要最终决策", "保底三条+G15：多维考虑、回归真实意图（他要的是判断不是安慰）、轮末总结"),
}


def main():
    e = Engine()
    asm_ts = time.strftime("%Y%m%d_%H%M%S")
    out_all = os.path.join(HERE, "logs", "anchor_debate.jsonl")
    samples = []
    print(f"[anchor] 8轮话题链，引擎走链应答，落盘 {out_all}", flush=True)

    for rid, phase, user_input in USER_SCRIPT:
        t0 = time.time()
        print(f"\n>>> {rid} [{phase}] {user_input[:40]}", flush=True)
        # 引擎全流程（路由+走链+验证+赫布）
        decision = e.router.route(user_input)
        result = e.executor.run(user_input, decision)
        m = re.search(r"比率\s*([\d.]+)", result.verify_note or "")
        ratio = float(m.group(1)) if m else None
        # trigger 参考 + 期望锚点
        signal, expect = TRIGGER_MAP[rid]
        # 四层格式落盘（第2层轮次 + 第3层思维步）
        rec = {
            "round": rid, "phase": phase, "role": "user",
            "utterance": user_input,
            "anchors_used": result.path,            # 引擎实际走的节点
            "anchors_expected": expect,             # 人工参考标准
            "trigger_signal": signal,               # 触发条件（教"何时"）
            "planned_path": decision.path,
            "ds_answer": result.final_answer,       # 引擎回答
            "verify_ratio": ratio, "verify_note": result.verify_note,
            "deepen_events": result.deepen_events,
            "event_counts": result.event_counts,
            "elapsed_s": int(time.time() - t0),
        }
        with open(out_all, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # 第4层：训练对（chosen=引擎轨迹，rejected 留空——本场先攒正样本SFT池）
        thought = "\n\n".join(
            f"〔先想 · {e.graph.nodes[c].label if c in e.graph.nodes else c}〕"
            f"{'（自主加深）' if c in result.deepen_events else ''}\n"
            + ((result.node_outputs or {}).get(c, "") or "")[:600]
            for c in result.path)
        samples.append(TrajectorySample(
            prompt=user_input, thought_chain=thought,
            final_answer=result.final_answer, rejected="",
            meta={"anchors_used": result.path, "trigger_signal": signal,
                  "anchors_expected": expect, "phase": phase,
                  "verify_ratio": ratio if ratio is not None else -1.0,
                  "source": "项目话题链·对象层锚点"}))
        print(f"    路径 {'->'.join(result.path)}", flush=True)
        print(f"    比率 {ratio} | {result.verify_note} | {int(time.time()-t0)}s", flush=True)
        print(f"    回答前60字: {result.final_answer[:60]}", flush=True)

    out_dir = os.path.join(HERE, "data", "trajectories")
    # 本场无 rejected（SFT 正样本池模式），闸门只验意图
    n_gate = 0
    train_path = os.path.join(out_dir, f"anchor_train_{asm_ts}.jsonl")
    with open(train_path, "w", encoding="utf-8") as f:
        for s in samples:
            if (s.meta.get("verify_ratio") or 0) >= 0.6:
                f.write(json.dumps({"prompt": s.prompt,
                                    "chosen": s._compose_chosen(),
                                    "anchors": s.meta["anchors_used"],
                                    "trigger": s.meta["trigger_signal"],
                                    "meta": s.meta}, ensure_ascii=False) + "\n")
                n_gate += 1
    print(f"\n[anchor] 完成 | 8 轮全量: {out_all} | 过闸门训练样本: {n_gate} → {train_path}", flush=True)


if __name__ == "__main__":
    main()