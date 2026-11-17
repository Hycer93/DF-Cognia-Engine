# -*- coding: utf-8 -*-
"""adjacency_validator.py — 原子化元核邻接表可达性校验器(2026-11-16 用户裁决②)

配套规约(用户定案, 与 F20 守卫风格统一):
  ① 双触发: 邻接表加载时 + 文件变更时自动跑校验;
  ② 校验失败直接拒绝加载, 不静默放行;
  ③ 告警输出独立日志 logs/adjacency_guard.log, 退出码非零(1=校验失败)。

校验项(F20 守卫同构思路——链路可达性):
  A. schema 完整性: 每个技能必须有 result_fields/next_options/terminate_suggest/role;
  B. 孤岛技能: 没有任何边指向它且不是入口(entry=true)→ 判失败;
  C. 单向死端: next_options 为空但 terminate_suggest 不为恒真出口(即非 skill_terminate 类)→ 判失败;
  D. 幽灵后继: next_options 引用了 entries 里不存在的技能 → 判失败;
  E. 终止可达: 从每个入口出发, 至少存在一条路径到达 terminate 类技能 → 判失败;
  F. 自环: 技能把自己列为后继 → 判失败(循环防护第三层)。

用法: python eval/experiment/adjacency_validator.py [--table data/atomic_skills_adjacency.json]
退出: 0=通过  1=校验失败(拒绝加载)  2=表文件不存在/损坏
"""
import argparse
import io
import json
import os
import sys
import time
from collections import deque

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_TABLE = os.path.join(_HERE, "data", "atomic_skills_adjacency.json")
GUARD_LOG = os.path.join(_HERE, "logs", "adjacency_guard.log")

ENTRY_HINT = "skill_anchor_target"   # 约定入口(表内 role 或显式 entry 字段可扩展)

# 后置收尾步豁免(2026-11-17 skill_memory_gate 七段定稿段一B案): terminate 之后由编排器
# 直接触发的收尾技能, 不在邻接图可达域内(terminate 出口 next_options 必空无法出边);
# 校验口径同 __planned__ 占位豁免——图可达性只覆盖思考主链, 收尾步归编排器管。
POST_EXIT_HINT = {"skill_memory_gate"}   # 编排器后置触发技能(豁免孤岛+可达性检查)


def load_table(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def validate(table):
    """返回 (errors, warnings)——errors 非空即拒绝加载"""
    errors, warnings = [], []
    entries = table.get("entries", {})
    if not entries:
        errors.append("entries 为空——邻接表没有任何技能")
        return errors, warnings

    # A. schema 完整性
    for sid, spec in entries.items():
        for field in ("role", "result_fields", "next_options", "terminate_suggest"):
            if field not in spec:
                errors.append(f"[schema] {sid} 缺字段 {field}")
        if not isinstance(spec.get("next_options", []), list):
            errors.append(f"[schema] {sid}.next_options 必须是列表")

    # D. 幽灵后继 + F. 自环 + C. 死端
    for sid, spec in entries.items():
        nexts = spec.get("next_options", [])
        if sid in [o.get("skill") for o in nexts]:
            errors.append(f"[自环] {sid} 把自己列为后继")
        for o in nexts:
            nxt = o.get("skill")
            if nxt not in entries:
                errors.append(f"[幽灵后继] {sid} → {nxt} 不存在于 entries")
            for field in ("gain", "cost"):
                if not o.get(field):
                    warnings.append(f"[菜单不完整] {sid} → {nxt} 缺 {field} 描述(决策引导信息不全)")
        if not nexts:
            # 死端判定: 空后继必须是显式终止技能(terminate_suggest 声明恒真)
            # __planned__ 占位符豁免: 非可执行技能, 编排器本就跳过
            is_exit = ("恒真" in (spec.get("terminate_suggest") or "")
                       or sid == "skill_terminate" or sid == "__planned__")
            if not is_exit:
                errors.append(f"[死端] {sid} 无后继且非终止技能——链路到此断裂")

    # B. 孤岛技能
    referenced = {o.get("skill") for spec in entries.values() for o in spec.get("next_options", [])}
    for sid in entries:
        if sid != ENTRY_HINT and sid not in POST_EXIT_HINT and sid not in referenced:
            # 入口豁免; 后置收尾步豁免(编排器触发,不在主链图内); 终止技能天然被指向
            if sid not in referenced:
                errors.append(f"[孤岛] {sid} 无入边且非约定入口——任何链路都到不了它")

    # E. 终止可达(从入口 BFS)——出口判定只认显式声明:
    # is_exit=true 且 next_options 为空(带后继的伪出口不算); 或 terminate_suggest 恒真
    def is_exit(sid):
        spec = entries[sid]
        if spec.get("is_exit") is True:
            if spec.get("next_options"):
                errors.append(f"[伪出口] {sid} 标记 is_exit 但仍带后继——出口技能不允许 next_options")
                return False
            return True
        return (not spec.get("next_options")
                and "恒真" in (spec.get("terminate_suggest") or ""))

    start = ENTRY_HINT if ENTRY_HINT in entries else next(iter(entries))
    q, seen = deque([start]), {start}
    reach_exit = is_exit(start)
    while q:
        cur = q.popleft()
        for o in entries[cur].get("next_options", []):
            nxt = o.get("skill")
            if nxt in entries and nxt not in seen:
                seen.add(nxt)
                if is_exit(nxt):
                    reach_exit = True
                q.append(nxt)
    if not reach_exit:
        errors.append(f"[终止不可达] 从入口 {start} 出发无任何路径到达终止技能")
    # 入口出发到不了的节点(非孤岛但不可达=孤立分支)
    unreachable = set(entries) - seen - POST_EXIT_HINT   # 后置收尾步豁免(编排器触发,不在主链图内)
    for sid in unreachable:
        warnings.append(f"[入口不可达] {sid} 从入口 {start} 出发不可达(孤立分支)")

    # G. state_inputs/outputs 字段漂移(P-C4 段三3.5/段四4.3 定案): 声明读取/写入的键
    #    必须存在于 ThinkState 数据类定义(含 flags. 前缀展开), 杜绝静默读 None 的隐蔽
    #    bug(P-C3 E12 mock-现实偏差教训固化成机制)。只校"声明的必须存在", 不反向
    #    强制"存在的必须被声明"(向后兼容: 技能可不读某字段)。
    drift = check_state_inputs(table)
    for e in drift:
        errors.append(e)

    return errors, warnings


def check_state_inputs(table) -> list:
    """返回漂移错误明细(空列表=通过)。硬拦截: errors 非空即 exit 1。"""
    sys.path.insert(0, _HERE)
    from cgraph.atomic_skill import ThinkState
    top_keys = set(ThinkState.__dataclass_fields__.keys())
    flag_keys = {"confidence", "sensitive", "conflict"}   # flags dict 合法键集
    errs = []
    for sid, spec in table.get("entries", {}).items():
        for key in spec.get("state_inputs", []) or []:
            if "." in key:
                k1, k2 = key.split(".", 1)
                if k1 == "flags":
                    if k2 not in flag_keys:
                        errs.append(f"[字段漂移] {sid} state_inputs '{key}' —— flags 无 '{k2}' 键")
                elif k1 not in top_keys:
                    errs.append(f"[字段漂移] {sid} state_inputs '{key}' —— ThinkState 无 '{k1}' 字段")
            elif key not in top_keys:
                errs.append(f"[字段漂移] {sid} state_inputs '{key}' —— ThinkState 无此字段")
        for key in spec.get("state_outputs", []) or []:
            if "." in key:
                k1 = key.split(".", 1)[0]
            else:
                k1 = key
            if k1 not in top_keys:
                errs.append(f"[字段漂移] {sid} state_outputs '{key}' —— ThinkState 无 '{k1}' 字段")
    return errs


def run_check(path, quiet=False):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        table = load_table(path)
    except (OSError, ValueError) as e:
        line = f"{ts} [表损坏] {path}: {e} — 拒绝加载"
        _append_log(line)
        if not quiet:
            print(line)
        return 2
    errors, warnings = validate(table)
    verdict = "PASS" if not errors else "FAIL(拒绝加载)"
    line = f"{ts} [邻接表校验] {verdict} | {path} | skills={len(table.get('entries', {}))} errors={len(errors)} warn={len(warnings)}"
    _append_log(line)
    for e in errors:
        _append_log(f"{ts}   ✗ {e}")
    for w in warnings:
        _append_log(f"{ts}   ⚠ {w}")
    if not quiet:
        print(f"[邻接表校验] {verdict} (skills={len(table.get('entries', {}))})")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warnings:
            print(f"  ⚠ {w}")
    return 0 if not errors else 1


def _append_log(line):
    os.makedirs(os.path.dirname(GUARD_LOG), exist_ok=True)
    with io.open(GUARD_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def assert_loadable(path):
    """供编排器加载邻接表时调用: 校验不过抛 RuntimeError, 拒绝加载(规约②)"""
    rc = run_check(path, quiet=True)
    if rc != 0:
        raise RuntimeError(f"邻接表校验失败(rc={rc}), 拒绝加载: {path} —— 详见 {GUARD_LOG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEFAULT_TABLE)
    args = ap.parse_args()
    sys.exit(run_check(args.table))