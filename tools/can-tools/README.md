# can-tools 资产档案（L1 工具自持）

依据：跨外部资产·技能自持三层范式（key_pact 卡，2026-11-17 定稿）

## 来源
- 来源路径：`D:/project/chery/8397/EHV/DsvSystemUI/01_Doc/can-tools/`（8397 现场原件，只读）
- 拷贝时间：2026-11-17
- 同步 commit：bceec95（文档+脚本）+ a658bd6（can_protocol.db，.gitignore 白名单例外 `!tools/can-tools/can_protocol.db`）

## 适用平台
- 8397 车机专属（用户 2026-11-09 裁决：不外推其他平台）
- 运行环境：本机 Python3 + adb（注入类需设备 userdebug 在线）

## 资产清单
| 文件 | 用途 |
|---|---|
| SIGNAL_SIMULATION.md | 主指南：三方法决策链 + 命令速查 + 7 条坑 |
| veh_condition_upmsg_simulation.md | SPI 直发报文特例（--upmsg 帧结构 + bitmap） |
| can_query.py | 查询/注入工具（查询本地可用，注入需设备） |
| can_protocol.db | 7.2MB 信号库（CAN.xlsx 导出，非全集） |
| CAN.xlsx | 信号表原始表 |
| import_can_xlsx.py | xlsx→db 导入器 |

## 依赖
- Python3（can_query.py 查询功能，sqlite3 标准库）
- adb + vhal_test（设备端 /vendor/bin，注入/回读需 8397 设备在线）

## 关联卡片
- 指针卡：a1f7c3d9169e3623（双源索引）
- L2 能力卡：51ee07f185494059（skill_refine，B1-B5 决策分支 + 失败分支 + L3 冒烟记录）
- 范式卡：a642b04f84734dad（key_pact 三层自持范式）

## L3 冒烟记录
- 2026-11-17 PASS：`python can_query.py "无线充电"` 于本工程副本返回 16 条信号（含 CWC_workingSts 0xA02D READ 0x455 bit18）
- `--sim` / `--upmsg` 注入类需真机在线，冒烟待真机复测

## 维护
- 外部原件更新后不自动同步，需人工重拉 + 重跑 L3 冒烟
- 废弃时打标记并交 E10 巡检淘汰