# Jw 钩子自动归档（PreCompact hook）使用说明

## 配置已生效（~/.codechat/settings.json hooks.PreCompact）

会话上下文压缩前，CodeChat 自动执行：

```
python memory/memory_hook.py session --file data/memory/hook_candidates.json
```

## 主 AI（CodeChat 会话内）的配合动作

hook 触发时读 `data/memory/hook_candidates.json`（候选清单文件）。因此**主 AI 在会话中产生以下四类素材时应随手把候选追加写进该文件**（不是等压缩才想）：

1. 设计裁决（memory_class=strategy_evolve）
2. 用户纠错/事故复盘（scar_case）
3. 规约/契约定案（key_pact）
4. 方法论沉淀（skill_refine）

格式（JSON list，每条）：

```json
[{
  "summary": "一句话主干",
  "detail": "展开：根因/决策/证据/教训",
  "source": "会话", "domain": "005-002", "behavior": "Jw",
  "relation": "P", "confidence": "L1",
  "memory_class": "strategy_evolve", "event_ts": "2026-11-13"
}]
```

judge 管道（四档：store/hold/discard/conflict_suspect）跑完后文件自动清空（防重复入库）。

## 踩坑记录（2026-11-13 首日）

- relation 只能取 7 类白名单：P/C/F/H/D/Q/X/S（写 N 会被隔离区拦截——护栏正常工作）
- 同批候选勿微调措辞重跑（replica 拦截只认精确副本，措辞微调=双胞胎）
- 手动补录：`python memory/memory_hook.py add --domain ... --summary ...`
- 候选文件不存在时 hook 空转（rc=0），不报错——正常行为
