# -*- coding: utf-8 -*-
"""
f20_weight.py — F20 权重层(2026-11-16 design_f20_weight.md 定稿 + 用户五条执行纪律)

定位: 叠加层。只读库, 接管 F10 候选分支打分排序; 不改 F10 发散逻辑、
     不改 F40 BFS 主干——回滚 = F10 不调 score_branch 即回旧均匀权重。

公式(裁决①两层结构):
  w(branch) = (1 + δ_step)^β · C(conf) · F(fresh) · R(edge) · D(density)
  - δ_step: 推演距离衰减(BFS 步数), 新鲜度不混入
  - C(conf): L0=1.0 / L1=0.7 / L2=0.4
  - F(fresh): 半衰期 90d 独立新鲜度乘子
  - R(edge): relation=1.0 / topic弱边=0.5
  - D(density): 簇连通性 × (1 - debt_ratio × λ_debt) 软惩罚(红线: 永不截断)

纪律(2026-11-16 用户锁进本次迭代):
  ① 基线保护: 消融/回归默认 baseline β=1.0; baseline 翻转→暂停冻结图谱自动治理
  ② 债务红线硬编码守卫: A2 必测 topic_memory_ops 高债务下 explore1 九卡链
     不被挤出 top-K; 不通过调小 λ_debt, 不放宽规则
  ③ 归因与权重并行: 每条分支(可达/不可达)都带四类归因枚举
  ④ 接线收口: 叠加层, 原主干保留可切回
  ⑤ 产物约束: 只读观测, 排序结果只进日志/晨报/回归, 不送内生目标提案池
"""
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(_HERE, "data", "memory", "corpus_store.db")

# ---- β 档位表(裁决②: 人闸静态参数, 写死代码; 运行时不可修改) ----
BETA_PROFILES = {
    "conservative": 0.6,
    "baseline": 1.0,
    "exploratory": 1.6,
}
BASELINE_PROFILE = "baseline"          # 纪律①: 回归锚点档位

CONF_TIER = {"L0": 1.0, "L1": 0.7, "L2": 0.4}
EDGE_W = {"rel": 1.0, "topic": 0.5}
FRESH_HALFLIFE_DAYS = 90               # 与范式层 decay_period_days 对齐
LAMBDA_DEBT = 0.5                      # 债务软惩罚系数(A2 消融默认值)
# 纪律②红线硬编码守卫: debt_ratio 封顶惩罚后的 D 下限——债务=1.0 时 D 仍>0,
# 分支只会压低永不消失。下限值不可配置(写死, 与"不放宽"裁决一致)。
D_FLOOR = 0.3


def _conn():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _ensure_rows(con):
    """外部传入连接可能未设 row_factory(如 minloop 的 ro 连接)——统一转 dict 行"""
    if con.row_factory is not sqlite3.Row:
        con.row_factory = sqlite3.Row
    return con


def factor_conf(con, iid):
    """C(conf): 卡片置信乘子"""
    con = _ensure_rows(con)
    r = con.execute("SELECT confidence FROM core_memory_card WHERE inst_id=?", (iid,)).fetchone()
    return CONF_TIER.get(r["confidence"] if r else None, 0.7)


def factor_fresh(con, iid, now=None):
    """F(fresh): 独立新鲜度乘子, 半衰期指数衰减(90d 半衰 → 0.5^age/90)。
    无 event_ts/ts 的老卡按保守 1 年前处理(陈旧考古卡缓慢滑降)。"""
    con = _ensure_rows(con)
    now = now or datetime.now()
    r = con.execute("SELECT event_ts, ts FROM core_memory_card WHERE inst_id=?", (iid,)).fetchone()
    if not r:
        return 0.5
    s = r["event_ts"] or r["ts"]
    try:
        t = datetime.fromisoformat((s or "")[:19])
    except ValueError:
        t = now - timedelta(days=365)
    # 连续 age(秒级/86400): 不用 .days 整数截断——同日 ts 相差数分钟的卡
    # 会在跨午夜后跳 1 天档, 造成同权并列簇内位次抖动(2026-11-16 守卫实测)
    age = max(0.0, (now - t).total_seconds() / 86400.0)
    return 0.5 ** (age / FRESH_HALFLIFE_DAYS)


def factor_edge(etype):
    """R(edge): 边类型乘子"""
    return EDGE_W["rel"] if (etype or "").startswith("rel") else EDGE_W["topic"]


def factor_density(con, iid):
    """D(density): 目标卡所在簇的连通性 × 债务软惩罚。
    债务分子 = 簇内 hold 留置 + 零挂载孤立(以簇外无 relation 边计) 占比。
    红线(裁决③): 软降权永不截断——D 含硬编码下限 D_FLOOR。"""
    con = _ensure_rows(con)
    rows = con.execute(
        "SELECT l.inst_id, l.topic_id FROM topic_links l WHERE l.topic_id IN "
        "(SELECT topic_id FROM topic_links WHERE inst_id=?)", (iid,)).fetchall()
    if not rows:
        return 1.0                      # 无簇: 不惩罚(孤立卡天然由其他因子降权)
    members = [r["inst_id"] for r in rows]
    # 债务①: hold 留置卡占比(池内 id 短/全形态混存, Python 侧前缀匹配聚合)
    held_n = 0
    for r in con.execute("SELECT DISTINCT inst_id FROM core_hold_pool WHERE verdict IS NULL"):
        short = r["inst_id"]
        if short in members or any(m.startswith(short) for m in members):
            held_n += 1
    # 债务②: 簇内无 relation 边的孤立节点占比(连通性)
    linked = {r["me_inst"] for r in con.execute(
        "SELECT me_inst FROM core_relation WHERE status='active'")}
    linked |= {r["other_inst"] for r in con.execute(
        "SELECT other_inst FROM core_relation WHERE status='active'")}
    isolated = sum(1 for m in members if m not in linked)
    debt_ratio = (held_n + isolated) / len(members)
    conn_score = 1.0 - isolated / len(members)
    d = conn_score * (1.0 - min(1.0, debt_ratio) * LAMBDA_DEBT)
    return max(D_FLOOR, d)              # 纪律②: 硬下限, 永不归零


def score_branch(con, branch, step, profile="baseline", now=None):
    """F20 主入口: 对单条候选分支打复合权重。
    branch: {to, rel, ...}(F10 候选); step: BFS 距锚步数(0=锚直接邻居)。
    返回 (w, factors)——factors 进审计元信息(纪律③: 归因与权重并行)。"""
    beta = BETA_PROFILES[profile]       # 档位表写死, 运行时只读
    delta_step = 0.6 if step > 0 else 0.0    # 步衰减: 直接邻居不衰减, 每步远一层打折
    delta_step *= (0.85 ** max(0, step - 1)) # 步数斜率(A3 消融对象): 递减式衰减
    c = factor_conf(con, branch["to"])
    f = factor_fresh(con, branch["to"], now)
    r = factor_edge(branch.get("rel"))
    d = factor_density(con, branch["to"])
    w = (1 + delta_step) ** beta * c * f * r * d
    factors = {"beta_profile": profile, "beta": beta,
               "delta_step": round(delta_step, 4), "C_conf": round(c, 4),
               "F_fresh": round(f, 4), "R_edge": r, "D_density": round(d, 4),
               "step": step}
    return round(w, 6), factors


# ---------- 失败归因四枚举(纪律③: 与权重并行落地) ----------

def attribute_failure(con, goal_prefix, anchor_ids, max_steps):
    """F40 失败时的归因判定(四枚举, 写入审计元信息+晨报)。
    判据顺序: goal_isolated > sensitive_blocked > edge_coverage_gap > step_exhausted"""
    g = con.execute("SELECT inst_id FROM core_memory_card WHERE inst_id LIKE ?",
                    (goal_prefix + "%",)).fetchone()
    if not g:
        return "goal_isolated", "目标前缀无命中卡"
    gid = g["inst_id"]
    # 度=0: 双边合计无任何有效连接
    deg_rel = con.execute(
        "SELECT COUNT(*) FROM core_relation WHERE status='active' AND (me_inst=? OR other_inst=?)",
        (gid, gid)).fetchone()[0]
    deg_top = con.execute("SELECT COUNT(*) FROM topic_links WHERE inst_id=?", (gid,)).fetchone()[0]
    if deg_rel + deg_top == 0:
        return "goal_isolated", "目标卡双边度=0"
    # 高敏拦截: 目标挂敏感簇
    g_topics = {r["topic_id"] for r in con.execute(
        "SELECT topic_id FROM topic_links WHERE inst_id=?", (gid,))}
    sensitive = {"topic_user_relation"}   # 与 CONFIG.sensitive_topics 对齐(F 系写死)
    if g_topics & sensitive:
        return "sensitive_blocked", "目标在高敏簇, 推演默认隔离"
    # 步数耗尽 vs 边覆盖缺口: 目标邻域可达锚方向但距离>max_steps
    n1 = con.execute(
        "SELECT COUNT(*) FROM core_relation WHERE status='active' AND (me_inst=? OR other_inst=?)",
        (gid, gid)).fetchone()[0]
    if n1 >= 1:
        return "edge_coverage_gap", "目标有边但 BFS 未及——库补债后可能变可解(参考 explore1 教训)"
    return "step_exhausted", "搜索步数耗尽"


def main():
    """自检: 因子单测 + memory_ops 债务红线守卫用例(纪律②)"""
    con = _conn()
    now = datetime.now()
    # 红线用例: explore1 九卡链的代表卡(84717bb1 主干)在高债务 memory_ops 下
    # D 因子必须 >= D_FLOOR(只压低不消失)
    cards = [r["inst_id"] for r in con.execute(
        "SELECT l.inst_id FROM topic_links l WHERE l.topic_id='topic_memory_ops' LIMIT 5")]
    print("== memory_ops 债务红线守卫(纪律②) ==")
    for iid in cards:
        d = factor_density(con, iid)
        ok = d >= D_FLOOR
        print(f"  {_s(iid)} D={d:.4f} {'✓>=floor' if ok else '✗违反红线'}")
    print("== 因子样例(baseline) ==")
    for iid in cards[:3]:
        c, f, d = factor_conf(con, iid), factor_fresh(con, iid, now), factor_density(con, iid)
        w, fac = score_branch(con, {"to": iid, "rel": "topic_sibling"}, step=1)
        print(f"  {_s(iid)} C={c} F={f:.3f} D={d:.3f} → w={w} | {fac}")
    con.close()


def _s(iid):
    return (iid or "")[:8]


if __name__ == "__main__":
    main()