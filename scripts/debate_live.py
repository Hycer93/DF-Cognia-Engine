"""逐轮在环真攻防：引导者(我)每轮读 DS 实际质疑后现场组织反驳。
每轮：落盘引导输入→DS回答→我读后写下一轮反驳。半自动：脚本一次跑一轮。
用法：python -u scripts/debate_live.py "本轮引导输入"
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cgraph.engine import Engine

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "design_consult1.jsonl")
HIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "design_consult1_history.json")


def main():
    guide_input = sys.argv[1]
    e = Engine()
    # 载入攻防历史（DS 视角完整对话）
    history = json.load(open(HIST, encoding="utf-8")) if os.path.exists(HIST) else []
    history.append({"role": "user", "content": guide_input})
    ans = ""
    for attempt in range(3):
        try:
            ans = e.llm.chat([{"role": "system", "content":
                "你是一位资深AI架构评审专家。用户在向你介绍他的认知引擎设计并期待你严格评审。"
                "直接、具体、不客套；被说服就明说被说服，坚持就给出硬理由；"
                "注意：引导者会认真读你的实际质疑，你的立场演变会被逐轮记录。"},
                *history]) or ""
            if ans:
                break
        except Exception:
            time.sleep(2 * attempt + 1)
    # 意图回归
    ratio, note = None, ""
    try:
        P = ("判断这个回答是否真正回应了引导者的问题（意图贴合）。输出严格 JSON: "
             '{"score": 0.0-1.0, "note": "15字内"}'
             f"\n引导者输入: {guide_input[:500]}\n回答: {ans[:3000]}")
        r = e.llm.chat([{"role": "user", "content": P}])
        j = json.loads(r[r.find("{"):r.rfind("}") + 1])
        ratio, note = max(0.0, min(1.0, float(j.get("score", 0.5)))), str(j.get("note", ""))[:20]
    except Exception:
        pass
    n = sum(1 for l in open(LOG, encoding="utf-8") if l.strip()) + 1 if os.path.exists(LOG) else 1
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"round": n, "guide_input": guide_input,
                            "ds_answer": ans, "verify_ratio": ratio,
                            "verify_note": note, "ts": time.strftime("%H:%M:%S")},
                           ensure_ascii=False) + "\n")
    history.append({"role": "assistant", "content": ans})
    json.dump(history, open(HIST, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"=== 轮 {n} DS 回答 ===\n{ans}\n\n[比率 {ratio} {note}] 轮次已落盘")


if __name__ == "__main__":
    main()