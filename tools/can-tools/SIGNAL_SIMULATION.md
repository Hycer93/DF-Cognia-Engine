# 信号模拟操作指南（vhal_test via can_protocol.db）

> 目标：让任何开发者/AI **无需深入 SystemUI 代码**，即可把某个车辆信号"模拟"成指定值，
> 从而触发/验证 SystemUI 对某状态（无线充电、中控锁、氛围灯、车窗等）的展示与交互。
> 本文档给出**完整可执行步骤 + 决策边界**，按默认优先级操作即可。

文档位置：`01_Doc/can-tools/`（配套工具 `can_query.py`、数据库 `can_protocol.db`）。

---

## 0. 决策优先级（用户未说明时，默认按此走）

```
1. 用户明确给了信号名/值  → 直接方法A注入。
2. 用户只说"模拟XX"（如"模拟关闭左侧无线充电"、没给信号）
   → 方法A：can_protocol.db 模糊查询定位信号+值 → --sim 注入。 ✅ 默认优先
3. 数据库模糊查询查不到/不确定
   → 方法B：从设备 `/vendor/etc/automotive/vhalconfig/` pull json 信号库交叉验证。
4. 仍不明确 / 需要确认"注入后界面上哪个元素、显示成什么"
   → 方法C：回看 SystemUI 代码消费链，确认信号→UI 映射后再注入。
```

**核心原则：能用数据库解决问题，绝不去追代码。** 模拟所需的全部输入
（信号名、值编码、属性 PropID、方向 READ/WRITE、中文含义）都来自 CAN 信号表
（即 `can_protocol.db`），与 SystemUI 内部如何订阅/展示无关。

---

## 1. 边界（read 这段再动手）

### 1.1 数据库能直接给出的（无需代码）
- 关键词 → **信号名**（如 "无线充电" → `CWC_workingSts` / `CWC_ChargingSts` / ...）
- 信号 → **值编码**（`signal_value_desc`：如 `0x0关闭/0x1打开`）
- 信号 → **属性 PropID / VDBID / Access 方向 / 中文描述**

### 1.2 数据库给不了的（此时才需要代码）
- **信号 → UI 元素的对应关系**（注入后界面上具体哪个图标/开关/弹框变、显示成什么）
- **SystemUI 消费链 / 产品语义映射**（如 WiFi 档位 4档vs5档这种字段语义转换）
- 为什么注入后界面**没按预期变化**（信号到了，但消费/展示逻辑有门槛）

### 1.3 关键约束
- **只对 `Access=READ` 的上行信号注入才有意义**：`vhal_test --setsig` 模拟的是"车辆上行报告状态"，
  让 SystemUI 从信号层读到变化。**`Access=WRITE` 是下行控制信号**（SystemUI 发往车机），`--sim` 注入上行无意义。
- 模糊查询可能命中**别名/多属性**（如旧信号名 `LHFDoorLockSts` → 现 `DrvDoorLockSts`），需人工确认选正确的那条。
- 台架注意：SPI Read 物理通道 I/O error 时，`--setsig` 可能只触发下行 Write，抓不到真实上行，需 HIL/真车。

---

## 2. 方法A：数据库模糊查询 + 一键模拟（默认首选）

### 2.1 环境
- 数据库：`01_Doc/can-tools/can_protocol.db`（SQLite，CAN.xlsx 导入，7.2MB）
- 工具：`01_Doc/can-tools/can_query.py`（python3）
- 设备已 adb 连接，且 `vhal_test` 已部署（在 PATH 内，`which vhal_test` 可见；注入工具）。

> **vhal_test 工具部署（若设备没有）**：vhal_test 是一个独立的本地工具目录（工具二进制 `vhal_test`
> + 信号库 json + 依赖 so），与 can_protocol.db 是两回事（db 管"查"，vhal_test 管"注入"）。
> 按官方方案，vhal_test 及其它文件应部署到 **/vendor 系统分区**（这样 `vhal_test` 就进 PATH 可裸调、
> so 在 /vendor/lib64 自动链接、json 在 /vendor/etc/automotive/vhalconfig/）：
> ```sh
> cd <vhal_test工具目录>
> bash deploy_vhal_test.sh    # userdebug 设备：adb root+remount → 只 push 缺失文件到官方 /vendor 路径 → chmod → 验证
> ```
> 官方映射表（《8397 vhal app临时模拟调试CAN相关接口ID方案说明.docx》）：
> `vhal_test`→`/vendor/bin/vhal_test`；`android.hardware.automotive.vehicle@V4-oem-service`→`/vendor/bin/hw/`；
> 三个 `lib*.so`→`/vendor/lib64/`；`propinfo/propsigsinfo/siginfos/canlinkcmdmap.json`→`/vendor/etc/automotive/vhalconfig/`。

### 2.2 模糊查信号（先知道要模拟哪个信号）
```sh
cd 01_Doc/can-tools
python can_query.py "无线充电"          # 模糊搜：属性/信号/含义含关键字的全列出
python can_query.py --type signal "CWC_workingSts"   # 按精确信号名精查(含值枚举/方向/PropID)
python can_query.py --type property "CAR_SETTING_ID_CAR_WIRLESS_CAHRGE_SET"  # 按 Property 查完整链路
python can_query.py --type can "0x455"  # 按 CAN 报文查其下所有信号
```
输出里重点看：`signal_name`、`PropID`、`Access`(READ才可上行注入)、`signal_value_desc`(值枚举)。

### 2.3 一键模拟（查→注入→回读验证）
```sh
python can_query.py --sim "CWC_workingSts:0"    # 从DB定位信号→adb vhal_test --setsig注入→dumpsys回读验证
```
- 给"信号名:值"：直接执行注入 + `dumpsys --get 0x2141<PropID>` 回读确认值已变。
- 只给"信号名"：不注入，仅打印该信号定义/值枚举/将执行的命令（防误注入）。
- PropID→VHAL 完整属性换算：`(0x2141 << 16) | PropID`（如 0xA02D → `0x2141A02D`），`--sim` 内部已处理。

### 2.4 验证 SystemUI 收到了（可选但推荐）
```sh
adb logcat -c
python can_query.py --sim "CWC_workingSts:1"
adb logcat -d | grep -ai "WirelessCharge\|LeftSwitch"   # 看对应回调，信号不同则 grep 信号名相关关键词
```
> 回调名随信号而异，先 grep 信号/功能关键词抓。若注入后 App 侧无任何相关回调，走方法C排查消费链。

---

## 3. 方法B：数据库查不到 → pull 设备 json 信号库交叉验证

设备 `/vendor/etc/automotive/vhalconfig/` 自带三个信号库 json（与 CAN 信号表同源，用于交叉验证）：
`siginfos.json`(信号定义)、`propsigsinfo.json`(属性↔信号映射)、`propinfo.json`(属性定义)。

### 3.1 pull 到本地（注意 Git Bash 路径转换坑）
```sh
# Git Bash 下 /data /vendor 会被转成盘符路径，必须加 MSYS_NO_PATHCONV=1，目标写到 Windows 路径
export MSYS_NO_PATHCONV=1
WIN_TMP="C:/Users/<你>/AppData/Local/Temp/wc_sigs"
mkdir -p "$WIN_TMP"
adb pull /vendor/etc/automotive/vhalconfig/siginfos.json      "$WIN_TMP/siginfos.json"
adb pull /vendor/etc/automotive/vhalconfig/propsigsinfo.json  "$WIN_TMP/propsigsinfo.json"
adb pull /vendor/etc/automotive/vhalconfig/propinfo.json      "$WIN_TMP/propinfo.json"
```

### 3.2 本地 python 查询（结构已知）
```python
# siginfos.json:      {"siginfo":[{"signal","msgId","startBit","bitSize","math","endia",...}]}
#                     值编码在 math:"1,0"(即 1/0) 或 inactiveValue；msgId 如 "0x455"
# propsigsinfo.json:  {"propupsigsinfo":[{prop:"CAR_SETTING_ID_...", sigs:"CWC_workingSts"}],
#                      "propdownsigsinfo":[...]，prop 是属性名(非ID)}
# propinfo.json:      {"propinfo":[{"name","hexId":"0x2141a02d","access","changeMode","initialValue"}]}
import json
d=json.load(open('siginfos.json',encoding='utf-8'))
for s in d['siginfo']:
    if 'workingSts' in s.get('signal',''):
        print(s)
```
属性和信号关联：`propsigsinfo` 用属性**名**（如 `CAR_SETTING_ID_CAR_WIRLESS_CAHRGE_SET`）↔ 信号名，
`propinfo` 用属性名↔hexId(`0x2141a02d`)，`siginfos` 用信号名↔报文/bit。

### 3.3 交叉验证后注入
拿到信号名+值后，回到方法A `python can_query.py --sim "信号:值"` 注入（DB 里应也有这条，DB 信息更全更权威）。

### 3.4 设备侧 vhal_vdb_client 订阅比对（可选，官方文档方法）
官方《vhal app 临时模拟调试 CAN 相关接口 ID 方案说明》2.4 提供了**设备侧订阅 VDB 事件**的比对法，
可替代/补充 3.1 的 pull json：

```sh
vhal_vdb_client --subscribe $VDBID     # 订阅某个 VDBID（如中控锁 0xa090）
# 另开一个 shell 注入：
vhal_test --setsig DrvDoorLockSts:01
```
订阅端会实时打印注入后的属性变化（`prop: 0xa090 areaId:0x0 int32Values: 0x1/0x2/0x0`）。

> ⚠️ **注意**：`vhal_vdb_client` 是**独立于 vhal_test 的工具**，当前设备 `/system/bin`、`/vendor/bin`
> 及本交付目录中均**未找到**（`which vhal_vdb_client` 无结果）。若需要它，请向 VHAL/平台开发索取该二进制，
> 或直接用方法A/B 的 dumpsys 回读 + pull json 交叉验证，效果等价。

---

## 4. 方法C：代码定位信号（DB/json 都不够时兜底）

适用：不确定注入哪个信号、或注入后 UI 没变需要确认消费链。**通用思路——从"界面状态"倒推"上游信号"**：

```
UI 状态(State.xxx)
  ↓ StatusBarViewModel 里 reducer/Event 或 getter
domain UseCase getter/setter（如 wirelessChargeUseCase.getLeftWirelessChargeSwitchState()）
  ↓ 实现类(modules/domain-impl/.../setting/.../<feature>UseCaseImpl.kt)
foundation Repository（如 com.desaysv.foundation.vehicle.chery.ability.<feature>.api.Chery<Feature>Repository）
  ↓ registerCallback 的 onXxxChanged(ActivationState/Status)
CAN 信号名（Repository 内部 / 或 DB->can_fd.signal_name）
```

示例（无线充电左侧开关，已探明）：
- `StatusBarViewModel.kt` → `wirelessChargeUseCase.getLeftWirelessChargeSwitchState()`
- `WirelessChargeUseCaseImpl.kt`(domain-impl/.../impl/charge/) → setting 层 `WirelessChargeUseCaseImpl` → `repo.getLeftWirelessCharging()`
- foundation `CheryWirelessChargingRepository.onLeftWirelessChargingChanged(ActivationState)` ← 上行信号 `CWC_workingSts`
- DB 里 `CWC_workingSts`：msg `0x455` bit18，PropID `0xA02D`，值 `0x0关/0x1开` → 用 `--sim "CWC_workingSts:0"` 关闭。

定位到"信号名"后，第一步先在 DB `can_query.py --type signal "<信号名>"` 反查确认值/PropID，
能查到直接 `--sim`；查不到再用方法B json 兜底。

---

## 5. 下行链路（WRITE 控制信号）验证

> 上行（READ）用 vhal_test 模拟"车→APP 报告状态"；**下行（WRITE）是 APP 发往车机的控制命令**，
> 走的是另一条链路（app vdbus.set → VHAL → SPI 写 CAN 帧 `05 75 01`），**不能用 `--sim` 模拟**
> （文档 3.1/3.2/3.3，官方方案说明）。

### 5.1 app 侧触发
App（如 SystemUI）中通过 `vdbus.set` 填入对应的 `VdbEventId` 及 payload，主动下发电控命令。

### 5.2 SPI 日志确认下行帧已写出
```sh
adb logcat --pid $(pidof android.hardware.automotive.vehicle@V4-oem-service) | grep "75 01"
# 出现形如：Write: 05 75 01 11 00 00 0E ... 即下行 SPI 帧已发出
```

### 5.3 横向对比（dumpsys --set 触发下行）
若 5.2 无 `75 01` 日志，用系统指令横向对比，确认目标 PropID 下行是否可行：
```sh
# 以 CAR_SETTING_ID_CAR_ASU_SET_MODE 为例，PropID 0x2141a0a5
adb shell 'dumpsys android.hardware.automotive.vehicle.IVehicle/default --set 0x2141a0a5 -i 0'
# 再看 SPI 日志是否有 Write: 05 75 01 ...
```
- 有输出说明下行链路通；无输出 → 通知 VHAL 开发人员处理（属 VHAL 侧问题，非 APP 问题）。
- 下行 WRITE 信号在 DB 中 `Access=WRITE`（如 `ICC_SuspensionDamping` PropID `0xA0A5`），
  只用于验证 APP→车机，排查"设置发了但车没执行"类问题。

---

## 6. 命令速查表

| 目的 | 命令 |
|------|------|
| 模糊查信号(默认) | `python can_query.py "关键词"` |
| 按信号名精查 | `python can_query.py --type signal "CWC_workingSts"` |
| 按属性查链路 | `python can_query.py --type property "CAR_SETTING_ID_..."` |
| 按报文查信号 | `python can_query.py --type can "0x455"` |
| **一键模拟(推荐)** | `python can_query.py --sim "CWC_workingSts:0"` |
| 只看定义不注入 | `python can_query.py --sim "CWC_workingSts"` |
| 注入上行 | `adb shell 'vhal_test --setsig "CWC_workingSts:0"'`（vhal_test 已在 /vendor/bin/PATH） |
| 回读 VHAL 属性 | `adb shell 'dumpsys android.hardware.automotive.vehicle.IVehicle/default --get 0x2141A02D'` |
| 设备侧订阅比对 | `adb shell vhal_vdb_client --subscribe 0xa090`（需该工具存在，见 3.4）|
| 下行 SPI 日志 | `adb logcat --pid $(pidof android.hardware.automotive.vehicle@V4-oem-service) \| grep "75 01"` |
| 下行 dumpsys --set 对比 | `adb shell 'dumpsys android.hardware.automotive.vehicle.IVehicle/default --set <PropID> -i 0'` |

> 手写注入也可：VHAL 属性 = `0x2141<PropID>`（PropID 大写补足），值用 DB `signal_value_desc` 的枚举。

---

## 7. 常见坑
1. **路径转换**：Git Bash 里 `/data/...` 会被转成盘符，pull/shell 命令加 `export MSYS_NO_PATHCONV=1`。
2. **Direction**：只模拟 `Access=READ` 上行；WRITE 下行别用 `--sim`，走第 5 节（SPI 日志 + dumpsys --set）。
3. **别名/多命中**：模糊查询多条目时，按中文描述 + READ + 目标属性选正确信号。
4. **注入后 UI 没变**：先确认注入值在 `dumpsys --get 0x2141<PropID>` 已变化（信号到了 VHAL）；
   若变但仍无 UI 变化 → 走方法C看消费链（可能是展示逻辑门槛，非信号问题）。
5. **CAN/SPI 物理通道 I/O error**：台架 `--setsig` 可能只出下行 Write，抓不到真实上行，需真车/HIL。
6. **车辆告警/仪表故障码不走本流程**：该能力无 CAN 矩阵信号，`--setsig` 查不到也注不进，
   必须用 `vhal_test --upmsg` 直发 SPI 报文（帧头 `73 53`、funcID `F7`、cmdID 分组 + 2字节 bitmap），
   完整指南见 [veh_condition_upmsg_simulation.md](veh_condition_upmsg_simulation.md)。
