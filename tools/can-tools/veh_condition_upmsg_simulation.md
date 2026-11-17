# 车辆告警（仪表故障码）SPI 上行报文模拟指南

> 目标：让任何开发者/AI 无需深入代码，即可模拟车辆告警/仪表故障码上报，触发/验证
> SystemUI 消息中心告警弹框与铃铛红点（`meterFaultCodes`）。

文档位置：`01_Doc/can-tools/`（与 `SIGNAL_SIMULATION.md` 配套——**本文是该文档的特例补充**：
车辆告警不走 CAN 矩阵信号、`--setsig` 查不到，必须用 `--upmsg` 直发 SPI 报文）。

---

## 0. 一句话结论

```
vhal_test --upmsg "03 73 53 06 00 F7 <cmdID> 02 <内容b0> <内容b1>"
```

设备路径：`/vendor/bin/vhal_test`（本机 D:\project\chery\8397\vhal_test\ 同源）。

---

## 1. 报文帧格式（字节逐位拆解）

以 `03 73 53 06 00 F7 00 02 01 02` 为例：

| 偏移 | 字节 | 含义 |
|---|---|---|
| 0 | `03` | 通道 |
| 1~2 | `73 53` | 报文头（MCU→Android 上行，同无线充电 `73 4F` 家族） |
| 3 | `06` | 长度 |
| 4 | `00` | 标识符（无意义） |
| 5 | `F7` | **functionID**（车辆告警/仪表故障专用） |
| 6 | `xx` | **cmdID**（故障分组号，0x00~0x54） |
| 7 | `02` | 参数个数（固定 2） |
| 8~9 | `b0 b1` | **内容 bitmap**（2字节小端） |

## 2. 故障码 bitmap 解析规则

cmdID 分组内每 16 位 bitmap 展开为 16 个故障码：

```
value = (b1 << 8) | b0          // 小端 16 位
offset = cmdID * 16
bit i 置位  → 故障码 = offset + i  （加入集合）
bit i 清零 → 故障码从集合移除     （LinkedHashSet 语义，跨分组累积）
```

**速查表（按位算好的常用值）：**

| 内容字节 | value | 置位 bit | cmdID=00 时故障码 | cmdID=01 | cmdID=02 |
|---|---|---|---|---|---|
| `01 02` | 0x0201 | 0, 9 | [0, 9] | [16, 27] | [32, 41] |
| `01 08` | 0x0801 | 0, 11 | [0, 11] | [16, 27]¹ | [32, 43] |
| `01 03` | 0x0301 | 0, 8, 9 | [0, 8, 9] | [16, 24, 25] | [32, 40, 41] |
| `00 01` | 0x0100 | 8 | [8] | [24] | [40] |
| `FF FF` | 0xFFFF | 0~15 全部 | 0~15 全组 | 16~31 全组 | 32~47 全组 |
| `00 00` | 0x0000 | 无 | **清除本组全部** | 同 | 同 |

¹ `01 08` → bit0、bit11 → cmdID=01: 16+0=16, 16+11=27

**清除故障**：对目标 cmdID 发 `00 00`，只清该分组；全清需逐组发（cmdID 0~0x54）。

## 3. 常用模拟命令

```bash
# 注入故障码（示例：第一组 bit0+bit9）
adb shell "/vendor/bin/vhal_test --upmsg \"03 73 53 06 00 F7 00 02 01 02\""
# 第二组、第三组
adb shell "/vendor/bin/vhal_test --upmsg \"03 73 53 06 00 F7 01 02 01 02\""
adb shell "/vendor/bin/vhal_test --upmsg \"03 73 53 06 00 F7 02 02 01 02\""
# 清除某组
adb shell "/vendor/bin/vhal_test --upmsg \"03 73 53 06 00 F7 00 02 00 00\""
```

注意：git bash 下若报 `D:/Program ... inaccessible`，是 MSYS 路径转换问题，命令整体加双引号包进 `adb shell "..."` 即可。

## 4. 底层链路（理解用，模拟不需要）

```
vhal_test --upmsg（帧头 73 53 + funcID F7 + cmdID + bitmap）
  → VHAL 按 canlinkcmdmap.json 映射：funcID=0xF7 & functionCommand=cmdID
      → propId = 0x2170D31F + cmdID（共85个，cmdID 0x00~0x54）
  → foundation aar（foundation-chery-ehv8397-vehiclesetting.aar）
      CheryVehConditionRepositoryImpl.handleSignalData
      （范围检查 54047≤id<54132，即 0xD31F+cmdID）
  → MeterMalfunctionParser.parse：ByteVector 取2字节小端 bitmap 展开（见§2）
  → CheryVehConditionCallback.onMeterMalFunctionChange(List<Int>)
  → VehicleUseCaseImpl.vehConditionCallback（init 时 registerCallback 注册）
  → StatusBarViewModel.onMeterMalFunctionChanged → viewState.meterFaultCodes
      → NotificationIcon 铃铛红点 + MessageDialog 故障列表
```

aar 侧日志 TAG：`CheryVehConditionRepositoryImpl`，成功时打：
`meter_fault_group source=push groupId=<cmdID> propertyId=<54047+cmdID> byteVector=[b0,b1] activeAlarmIds=[...]`

## 5. 验证与排障

注入后按顺序 grep（四级判读）：

```bash
# 1. 报文进 VHAL 了吗
adb shell "logcat -d | grep '73 53 06 00 F7'"
#   期待: VHAL.SVPPipeComm: Read_PIPE: <帧内容> + _report_message to module(0)

# 2. aar 解析并回调了吗
adb shell "logcat -d | grep CheryVehConditionRepositoryImpl"
#   期待: meter_fault_group source=push ... activeAlarmIds=[..]

# 3. UseCase/ViewModel 收到了吗
adb shell "logcat -d | grep -E 'onMeterMalFunctionChange'"
#   期待: VehicleUseCaseImpl: onMeterMalFunctionChange: faultCodes=[..]

# 4. UI 状态更新了吗
adb shell "logcat -d | grep MeterMalFunctionChanged"
#   期待: LoggerMiddleware: Event: MeterMalFunctionChanged(faultCodes=[..])
```

**断在第1级（无 Read_PIPE / 报文被丢）**：VHAL 属性未注册。检查：
```bash
adb shell "grep -c D31F /vendor/etc/automotive/vhalconfig/canlinkcmdmap.json"
# 0 → 设备配置旧版缺 F7 的 85 条映射，见 §6
```

**断在第2级且日志有 `meter_fault_session_create_failed`**：SignalApi session 创建失败
（SignalApiFactory 未 init，SystemUI 启动时 SystemUIStartupInitializer 会做，正常不会出现）。

## 6. 环境前置：VHAL canlinkcmdmap 需含 F7 映射（踩坑记录）

**背景**：2026-09 排查发现部分设备 `/vendor/etc/automotive/vhalconfig/canlinkcmdmap.json`
为旧版（仅 21 条），缺 funcID=0xF7 的 85 条映射（propId 0x2170D31F~D372）→
VHAL 不注册这组属性 → `--upmsg` 报文无法转属性事件 → 回调永远不触发，
且 `vhal_test --listen 0x2170D31F` 报 `no config for property: INVALID_ARG`。

**本地修复**（本机模拟调试用，换设备/刷机会丢）：

```bash
# 本地 D:\project\chery\8397\vhal_test\ 有新版配置（canlinkcmdmap 115条，含85条F7）
adb remount
cd D:/project/chery/8397/vhal_test
adb push canlinkcmdmap.json propinfo.json propsigsinfo.json siginfos.json \
     /vendor/etc/automotive/vhalconfig/          # 路径转换报错时逐个推+MSYS_NO_PATHCONV=1
adb shell "ps -A | grep V4-oem"                  # 拿到 VHAL 进程 PID
adb shell "kill <PID>"                           # 自动重启后加载新配置
# 验证：dumpsys android.hardware.automotive.vehicle.IVehicle/default | grep 561042207
#      出现 prop: 561042207（0x2170D31F）即成功
```

**正式方案**：F7 的 85 条映射需由平台/VHAL 配置维护方合入正式镜像，否则量产车上
MCU 实发 F7 帧（真实告警）同样无法上报。旁证：调试时 MCU 周期帧仅有
`funcID=FE cmdID=77`（内容 FF FF），未见 F7 帧。

## 7. 与其他模拟方式的区别（防混用）

| | 普通CAN信号（无线充电/空调等） | 车辆告警故障码 |
|---|---|---|
| 模拟命令 | `--setsig "信号名:值"` 或 can_query.py --sim | `--upmsg`（本文） |
| 数据来源 | can_protocol.db（CAN.xlsx） | VHAL canlinkcmdmap.json（SPI 帧映射） |
| 信号查得到吗 | 能（msgId/startBit 等） | **查不到**，无CAN矩阵信号 |
| 下行控制 | `--downmsg`（帧头 05 75 01 等） | 无下行（告警只上报） |
| 值格式 | 整数/hex（≥10 按 hex 解析的坑见 SIGNAL_SIMULATION.md） | 2字节 bitmap 小端 |

## 8. 相关记忆/参考

- 本仓库记忆：`veh_condition_upmsg_sim.md`（个人 CodeChat memory，含同一结论）
- 无线充电上行 SPI 对照：`signal_spi_read_format.md`、`wireless_charge_signal_map.md`（01_Doc/capabilities/wifi 同级目录）
- 普通信号模拟总纲：`SIGNAL_SIMULATION.md`