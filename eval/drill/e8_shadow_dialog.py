# eval/drill/e8_shadow_dialog.py — 影子对话演习（2026-11-11 用户裁决新增）
#
# [去向账本 2026-11-15 用户裁决·方案A] E8 依赖人工喂标签+外部DS资源, 与夜间主链
# "无人值守纯本地"原则冲突, 已摘出主链, 归 eval/drill/(定期演习, 按需手动/单独调度)。
# 编号保留(账本式历史, 不重编)。晨报仍汇报最近一次演习落盘结果(只读 jsonl)。
# 独立调用: NO_MEMORY=1 python eval/drill/e8_shadow_dialog.py
#
# 定位：nightly 跑完后的记忆系统实战演习——
#   我（主AI）提供标签和摘要 → DS 扮演真实用户出题（仿人模糊式对话）→
#   本地记忆系统检索回答 → DS 评分 → 汇总使用情况。
#
# 与 E1-E7 的分工：
#   E1 = 探针触发率（路由层回归，固定题库）
#   E2 = 反问门标定（检索门控阈值）
#   本步 = 全链实战（检索+反问门+别名+时间窗一起上，题不是人写的而是
#          DS 从库内真实条目仿人化的——专测"用户不会用检索词问问题"的场景）
#
# 流程：
#   1. 从 memories.jsonl 抽样 N 条（stratified：按 domain 分层，fragile 除外）
#   2. 每条取 summary+dfcp_tag 喂给 DS："你是真实用户，基于这条记忆的主题，
#      用你日常会怎么问的模糊口吻出一个问题"——禁止照抄摘要词（那就是词面匹配了）
#   3. 本地 ask() 检索，拿 top1 按反问门三段裁决作答（hit=答内容 / 反问=如实说不知道）
#   4. DS 评判：答的是不是那条被抽中的记忆（意图贴合非字面）+ 0-1 分
#   5. 落盘 logs/e8_shadow_dialog.jsonl 逐题原文 + 晨报汇总段
#
# 用法: NO_MEMORY=1 python eval/e8_shadow_dialog.py  (nightly_run.py 自动串联)
import io
import json
import os
import random
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "memory"))
sys.path.insert(0, HERE)
os.environ.setdefault("NO_MEMORY", "1")

import memory_organize as mo
import memory_query as mqv

OUT_JSONL = os.path.join(HERE, "logs", "e8_shadow_dialog.jsonl")
N_QUESTIONS = 8            # [先验-业务设定] 每晚题量：DS出题8题≈8次API调用+8次评分，成本可控
SEED = int(time.strftime("%Y%m%d"))   # 按天播种：同一天重跑结果一致（幂等），隔天自动换题

QUESTION_PROMPT = (
    "你是一个真实的人类用户，正在和你的AI助手对话。你的AI助手有一套跨会话记忆系统。\n"
    "下面是你过去某次对话沉淀下来的一条记忆的主题摘要和标签。"
    "请你扮演这个用户，就这个主题用你日常说话的口吻向AI提一个问题。\n\n"
    "要求：\n"
    "- 像真人一样口语化、带点模糊（真人不会用检索关键词问问题，比如不会说'P39三公式'，"
    "而会说'上次咱们聊的那个把相似度和时间揉在一起算的东西是咋回事来着'）\n"
    "- 可以引用你们过去的对话场景（'上次你说的''之前那个'）\n"
    "- 只出一个问题，20-60字，直接输出问题本身，不要任何解释\n\n"
    "记忆摘要：{summary}\n记忆标签：{tag}")

JUDGE_PROMPT = (
    "你是评测裁判。用户向AI记忆系统提问，AI系统检索后给出如下回应。\n"
    "判断AI的回应是否正确命中了目标记忆（意图贴合即可，不要求字面复述）。\n\n"
    "目标记忆摘要：{target}\n用户问题：{question}\nAI回应：{answer}\n\n"
    "输出严格JSON: {{\"hit\": true/false, \"score\": 0.0-1.0, \"note\": \"20字内评语\"}}"
    "（hit=是否命中目标记忆；反问'不知道'时若目标确实不在库中算true，否则false）")


def ds_chat(prompt, retries=3):
    """DS调用（cgraph/llm.py同款：urllib+退避；key从环境变量，禁硬编码）"""
    import urllib.request
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("缺 DEEPSEEK_API_KEY")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=json.dumps({"model": "deepseek-chat",
                                 "messages": [{"role": "user", "content": prompt}],
                                 "temperature": 0.8, "stream": False}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    return None


def sample_targets(n=N_QUESTIONS, seed=SEED):
    """分层抽样：按domain根分层，每层按权重抽——全覆盖不偏科。
    排除：corpus教材、fragile（语义薄出不了好题）、superseded、空summary。"""
    rng = random.Random(seed)
    mems = [m for m in mo.load_memories()
            if m.get("asset_class") != "corpus"
            and not m.get("fragile")
            and not m.get("superseded_by")
            and m.get("summary")]
    # 分层桶
    buckets = {}
    for m in mems:
        root = m["dfcp_tag"]["domain"].split("-")[0]
        buckets.setdefault(root, []).append(m)
    # 均分名额：领域多的按比例，但保证每桶最多 ceil(n/桶数)+1
    out = []
    roots = sorted(buckets)
    if not roots:
        return out
    per = max(1, n // len(roots))
    for r in roots:
        pool = buckets[r]
        rng.shuffle(pool)
        out += pool[:per]
    # 不足n补齐（从全池抽）
    if len(out) < n:
        rest = [m for m in mems if m not in out]
        rng.shuffle(rest)
        out += rest[:n - len(out)]
    return out[:n]


def run():
    report = {"ts": datetime.now().isoformat(), "questions": [], "ok": False}
    targets = sample_targets()
    report["n_targets"] = len(targets)
    if not targets:
        report["error"] = "无可抽样的记忆条目"
        return report

    for i, m in enumerate(targets):
        tag = m["dfcp_tag"]
        try:
            # 1. DS出题（仿人模糊）
            q = ds_chat(QUESTION_PROMPT.format(
                summary=m["summary"], tag=f"{tag['domain']}|{tag['behavior']}|{tag.get('memory_class')}"))
            # 2. 本地检索（全链：ask含别名桥接/时间窗/反问门；演习调用打标不喂肥recency）
            results = mqv.ask(q, top_k=3, cite_source="e8")
            verdict = getattr(mqv.ask, "last_verdict", {}) or {}
            pool_audit = getattr(mqv.ask, "last_pool_audit", {}) or {}
            v = verdict.get("verdict", "empty")
            # 合格池内是否含目标（A/B类miss分类依据，2026-11-11 用户裁决）
            pool_ids = [pm.get("inst_id") for pm in pool_audit.get("pool_members", [])]
            target_in_pool = m["inst_id"][:8] in [p for p in pool_ids if p]
            if results and v in ("hit", "marginal_hit"):
                top = results[0][1]
                _det = mo.read_card_detail(top, "display")  # #30 R4: derived_only 卡不展示 detail
                answer = f"(检索命中) {top['summary']}" + \
                    (f" | 详情: {_det[:200]}" if _det else "")
                answered_id = top["inst_id"]
                match_eff = results[0][2]
            elif results and v in ("marginal_ask", "uncertain"):
                answer = "(反问) 不确定你指的是哪条，请确认所指"
                answered_id = None
                match_eff = verdict.get("match_eff", 0.0)
            else:
                answer = "(如实说不知道) 库中没有相关记忆"
                answered_id = None
                match_eff = 0.0
            # 3. DS评分（意图贴合）
            raw = ds_chat(JUDGE_PROMPT.format(target=m["summary"], question=q, answer=answer))
            j = {}
            try:
                j = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            except Exception:
                j = {"hit": None, "score": None, "note": "parse_fail:" + raw[:30]}
            # 命中且答的就是目标条 → 正确命中；命中但答了别的条 → 张冠李戴
            # miss 分类（用户裁决 2026-11-11：A=召回问题，B=池内排序问题，根因完全不同）：
            #   A类: 合格池里根本没有目标记忆（召回/向量/别名问题）
            #   B类: 目标进了合格池但排得靠后没选上（池内排序偏好问题）
            outcome = "correct"
            if j.get("hit") and answered_id and answered_id != m["inst_id"]:
                outcome = "wrong_entry"
            elif j.get("hit") is False:
                if not target_in_pool:
                    outcome = "miss_A_recall"      # 真找不到：合格池无目标
                else:
                    outcome = "miss_B_ranking"     # 找到了但池内排序靠后
            elif v in ("marginal_ask", "uncertain", "empty"):
                outcome = "asked_back"
            rec = {"i": i, "target_inst": m["inst_id"][:8],
                   "target_summary": m["summary"][:60],
                   "target_in_pool": target_in_pool,
                   "question": q, "verdict": v, "match_eff": round(match_eff, 3),
                   "answered_inst": (answered_id or "")[:8],
                   "answer": answer[:160],
                   "judge": j, "outcome": outcome}
        except Exception as e:
            rec = {"i": i, "target_inst": m["inst_id"][:8],
                   "target_summary": m["summary"][:60], "error": str(e)[:200]}
        report["questions"].append(rec)
        print(f"[E8] Q{i+1}/{len(targets)} {rec.get('outcome') or 'error':12s} "
              f"v={rec.get('verdict','-'):13s} {rec.get('question','')[:40]}", flush=True)

    # 汇总（A/B类miss分开统计——系统往哪边变差一目了然）
    qs = [q for q in report["questions"] if "outcome" in q]
    report["summary"] = {
        "total": len(qs),
        "correct": sum(1 for q in qs if q["outcome"] == "correct"),
        "wrong_entry": sum(1 for q in qs if q["outcome"] == "wrong_entry"),
        "miss_A_recall": sum(1 for q in qs if q["outcome"] == "miss_A_recall"),
        "miss_B_ranking": sum(1 for q in qs if q["outcome"] == "miss_B_ranking"),
        "pool_hit_rate": round(sum(1 for q in qs if q.get("target_in_pool")) / max(1, len(qs)), 3),
        "asked_back": sum(1 for q in qs if q["outcome"] == "asked_back"),
        "avg_score": round(sum((q.get("judge") or {}).get("score") or 0 for q in qs) / max(1, len(qs)), 3),
    }
    report["ok"] = True
    return report


def main():
    r = run()
    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    with io.open(OUT_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    s = r.get("summary", {})
    print(f"E8 影子对话: {s.get('correct',0)}/{s.get('total',0)} 正确命中 "
          f"| 张冠李戴 {s.get('wrong_entry',0)} "
          f"| missA召回 {s.get('miss_A_recall',0)} / missB排序 {s.get('miss_B_ranking',0)} "
          f"| 合格池命中率 {s.get('pool_hit_rate','-')} "
          f"| 反问 {s.get('asked_back',0)} | 均分 {s.get('avg_score','-')}")
    print("报告:", OUT_JSONL)


if __name__ == "__main__":
    main()