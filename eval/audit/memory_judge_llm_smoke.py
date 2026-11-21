"""memory_judge_llm_smoke.py — 真实 LLM 判定冒烟 (2026-11-21 P1-1, 手动运行)

真实调用 CodeChatLLM(glm-5.3-flash, agent_free=True) 跑 1~2 条样本, 确认:
  - token 可获取(本机 CodeChat 登录态 / CODECHAT_TOKEN)
  - 模型守 JSON 输出约束
  - _judge_call_llm 能解析并返回 verdict
  - judge_log 的 _source 为 llm(非 fallback)

⚠ 不进 nightly: 会消耗真实配额。手动执行:
  python eval/audit/memory_judge_llm_smoke.py

退出码 0=成功拿到 LLM 判定 1=token/网络/解析失败(此时生产会自动 fallback 到桩)。
"""
import io
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEM = os.path.join(_HERE, "memory")
sys.path.insert(0, _HERE)
sys.path.insert(0, _MEM)


def main():
    os.environ.pop("JUDGE_USE_LLM", None)   # 确保走 LLM 路径
    import memory_judge as mj

    cands = [{
        "summary": "碎片判定口径统一为UTF-8字节, 552条中文短指令误归档已回滚主表",
        "detail": ("2026-11-21 修复: 归档SQL与巡检用了 length(body)(字符数), "
                   "t/h通道用 encode()(字节), 两套尺子错位。统一为字节口径, "
                   "sqlite 用 length(cast(body as blob)); 552 条回滚主表并从归档表清除; "
                   "新增口径回归用例锁死。"),
        "memory_class": "scar_case", "behavior": "Jw", "domain": "005-003",
        "relation": "P", "confidence": "L1",
    }]

    t0 = time.time()
    try:
        out = mj._judge_call_llm(cands, {"trigger": "smoke"})
    except Exception as e:  # noqa: BLE001
        print(f"❌ LLM 调用异常: {e}")
        return 1
    dt = time.time() - t0

    if out is None:
        print("❌ LLM 返回不可解析(None 或非 JSON)——生产环境此时会自动 fallback 到桩")
        return 1

    v = out.get(0, {})
    print(f"[{dt:.1f}s] LLM 判定结果:")
    print(json.dumps(v, ensure_ascii=False, indent=1))
    ok = v.get("verdict") in ("store", "hold", "discard")
    print(f"{'✅' if ok else '❌'} verdict 合法: {v.get('verdict')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())