"""
CAN 协议查询工具 — ChromeDB
用法: python can_query.py <关键词|SQL>

常用示例:
  python can_query.py "airbag"          # 搜索气囊相关
  python can_query.py "0x49C"           # 查 CAN 报文信号
  python can_query.py "HVAC"            # 搜索 HVAC 相关
  python can_query.py "READ_ONLY_ID_AIR_BAG_FAIL"  # 按 Property 查
  python can_query.py --type signal "BlowSpeedLevelDisplaySts"  # 按信号名查
  python can_query.py --type vdbid "0xA117"            # 按 VDB ID 查
  python can_query.py --type property "CAR_SETTING_ID_PASSENGER_AIRBAG_STATUS_SHOW"  # 按 Property 查
  python can_query.py --sim "CWC_workingSts:0"         # 从DB定位信号→adb vhal_test注入→dumpsys回读验证
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "can_protocol.db"


def search_keyword(keyword):
    """跨三表搜索关键词"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()

    print(f"\n{'='*80}")
    print(f"搜索关键词: {keyword}")
    print(f"{'='*80}\n")

    # 1. DBC转VDB 匹配
    cur.execute(
        "SELECT msg_id, signal_name, property, ehv_vdb_id, property_id, type, access, property_meaning "
        "FROM dbc_to_vdb WHERE msg_id LIKE ? OR signal_name LIKE ? OR property LIKE ? OR property_id LIKE ? OR property_meaning LIKE ?",
        (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")
    )
    rows = cur.fetchall()
    if rows:
        print(f"--- DBC转VDB ({len(rows)} 条) ---")
        for r in rows[:20]:
            print(f"  {r[0]} | {r[1]:40s} | {r[2]:50s} | {r[3]:50s} | {r[4]} | {r[6]} | {r[7]}")
        if len(rows) > 20:
            print(f"  ... 还有 {len(rows)-20} 条")

    # 2. CAN FD 匹配
    cur.execute(
        "SELECT msg_id, msg_name, signal_name, signal_description, byte_order, date_type "
        "FROM can_fd WHERE msg_id LIKE ? OR msg_name LIKE ? OR signal_name LIKE ? OR signal_description LIKE ?",
        (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")
    )
    rows = cur.fetchall()
    if rows:
        print(f"\n--- CAN FD ({len(rows)} 条) ---")
        for r in rows[:20]:
            desc = (r[3] or '')[:40]
            print(f"  {r[0]} | {r[1]:30s} | {r[2]:40s} | {desc}")
        if len(rows) > 20:
            print(f"  ... 还有 {len(rows)-20} 条")

    # 3. VDB SPI 匹配
    cur.execute(
        "SELECT property, property_id, property_meaning FROM vdb_spi "
        "WHERE property LIKE ? OR property_id LIKE ? OR property_meaning LIKE ?",
        (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")
    )
    rows = cur.fetchall()
    if rows:
        print(f"\n--- VDB SPI ({len(rows)} 条) ---")
        for r in rows[:20]:
            print(f"  {r[0]:60s} | {r[1]} | {r[2]}")
        if len(rows) > 20:
            print(f"  ... 还有 {len(rows)-20} 条")

    conn.close()


def query_by_property(prop):
    """按 Property 名称查完整链路"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()
    cur.execute(
        "SELECT msg_id, signal_name, property, ehv_vdb_id, property_id, area, type, access, property_meaning "
        "FROM dbc_to_vdb WHERE property = ?", (prop,)
    )
    rows = cur.fetchall()
    if not rows:
        print(f"未找到 Property: {prop}")
        return
    print(f"\n{'='*100}")
    print(f"Property: {prop}")
    print(f"{'='*100}")
    for r in rows:
        print(f"  MsgID={r[0]} | Signal={r[1]} | VDBID={r[3]} | PropID={r[4]} | Type={r[6]} | Access={r[7]} | {r[8]}")
    conn.close()


def query_by_vdbid(vdbid):
    """按 VDB ID 查"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()
    cur.execute(
        "SELECT msg_id, signal_name, property, property_id, type, access, property_meaning "
        "FROM dbc_to_vdb WHERE ehv_vdb_id = ?", (vdbid,)
    )
    rows = cur.fetchall()
    if not rows:
        print(f"未找到 VDB ID: {vdbid}")
        return
    print(f"\n{'='*100}")
    print(f"VDB ID: {vdbid}")
    print(f"{'='*100}")
    for r in rows:
        print(f"  MsgID={r[0]} | Signal={r[1]} | Property={r[2]} | PropID={r[3]} | Type={r[4]} | {r[6]}")
    conn.close()


def query_signal(signame):
    """按信号名查 CAN + VDB 信息"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()

    # CAN FD
    cur.execute("SELECT msg_id, msg_name, signal_name, byte_order, bit_length, start_bit, date_type, resolution, offset_val, signal_min_phys, signal_max_phys, signal_description FROM can_fd WHERE signal_name = ?", (signame,))
    can_rows = cur.fetchall()

    # DBC
    cur.execute("SELECT msg_id, signal_name, property, ehv_vdb_id, property_id, access, property_meaning FROM dbc_to_vdb WHERE signal_name = ?", (signame,))
    db_rows = cur.fetchall()

    print(f"\n{'='*100}")
    print(f"信号名: {signame}")
    print(f"{'='*100}")

    if can_rows:
        print(f"\n--- CAN FD ({len(can_rows)} 条) ---")
        for r in can_rows:
            print(f"  MsgID={r[0]} | MsgName={r[1]} | ByteOrder={r[3]} | Bits={r[4]} | StartBit={r[5]} | Type={r[6]} | Res={r[7]} | Offset={r[8]} | Range=[{r[9]}, {r[10]}] | {r[11]}")

    if db_rows:
        print(f"\n--- DBC转VDB ({len(db_rows)} 条) ---")
        for r in db_rows:
            print(f"  Property={r[2]} | VDBID={r[3]} | PropID={r[4]} | Access={r[5]} | {r[6]}")

    conn.close()


def query_can_msg(msg_id):
    """按 CAN 报文 ID 查所有信号"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()
    cur.execute(
        "SELECT signal_name, byte_order, bit_length, start_bit, date_type, resolution, offset_val, signal_description "
        "FROM can_fd WHERE msg_id = ? ORDER BY start_byte, start_bit",
        (msg_id,)
    )
    rows = cur.fetchall()
    if not rows:
        print(f"未找到 CAN 报文: {msg_id}")
        return
    print(f"\n{'='*120}")
    print(f"CAN 报文: {msg_id}")
    print(f"共 {len(rows)} 个信号")
    print(f"{'='*120}")
    print(f"  {'Signal Name':40s} | {'Byte':>5} | {'Bit':>4} | {'Bits':>4} | {'Type':>8} | {'Res':>6} | {'Offset':>6} | Description")
    print(f"  {'-'*40} | {'-'*5} | {'-'*4} | {'-'*4} | {'-'*8} | {'-'*6} | {'-'*6} | {'-'*30}")
    for r in rows:
        desc = (r[7] or '')[:30]
        print(f"  {r[0]:40s} | {r[1]:>5} | {r[2]:>4} | {r[3]:>4} | {r[4]:>8} | {r[5]:>6} | {r[6]:>6} | {desc}")
    conn.close()


def to_vhal_prop(prop_id):
    """PropID(如 0xA02D) → VHAL 完整属性(0x2141A02D)，供 dumpsys --get 回读验证。"""
    try:
        v = int(prop_id, 16)
    except (TypeError, ValueError):
        return None
    return hex((0x2141 << 16) | v)


def simulate_signal(spec):
    """--sim 模拟注入：从 can_protocol.db 定位信号 → adb vhal_test --setsig 注入 → dumpsys 回读验证。

    spec 形如:
      "CWC_workingSts:0"   精确信号名 + 值，直接注入
      "无线充电"          关键词，仅列出候选上行信号与合法值(不注入)
    """
    import re

    if ":" in spec:
        sig, _, raw_val = spec.partition(":")
        sig = sig.strip()
        value = raw_val.strip()
    else:
        sig = spec.strip()
        value = None

    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()

    # 精确匹配信号名
    cur.execute(
        "SELECT signal_name, property, property_id, access, signal_value_desc, signal_description "
        "FROM dbc_to_vdb WHERE signal_name = ? COLLATE NOCASE", (sig,)
    )
    rows = cur.fetchall()

    if not rows:
        # 未精确命中 → 关键词反查候选
        print(f"未精确命中信号 [{sig}]，按关键词匹配候选：\n")
        search_keyword(sig)
        conn.close()
        return

    print("=" * 100)
    print(f"信号: {rows[0][0]}  (命中 {len(rows)} 条)")
    print("=" * 100)
    for r in rows:
        print(f"  Property={r[1]} | PropID={r[2]} | Access={r[3]}")
        print(f"  描述: {r[5]}")
        if r[4]:
            print("  值枚举:")
            for line in r[4].splitlines():
                print(f"    {line.strip()}")

    if len(rows) > 1:
        print("\n[警告] 该信号名关联多个属性，仅对第一条执行注入。")

    sig_name, prop, prop_id, access, _vdesc, _d = rows[0]

    # 安全校验：vhal_test --setsig 模拟的是上行 Read 状态，要求属性可读
    if value is None:
        print(f"\n[未注入] 未提供值，用法: python can_query.py --sim \"{sig_name}:<值>\"")
        print(f"[未注入] 可模拟命令: adb shell vhal_test --setsig \"{sig_name}:<值>\"")
        conn.close()
        return

    vhal_prop = to_vhal_prop(prop_id)

    print(f"\n[1/2] 注入上行信号 (vhal_test --setsig {sig_name}:{value}) ...")
    # vhal_test 已部署到 /vendor/bin/vhal_test（PATH 内，so 在 /vendor/lib64 自动链接），直接裸调
    cmd_inject = f"vhal_test --setsig \"{sig_name}:{value}\""
    r = subprocess.run(["adb", "shell", cmd_inject], capture_output=True, text=True)
    out = (r.stdout or "").strip()
    print(f"      -> {out or r.stderr.strip() or '(no output)'} (rc={r.returncode})")

    if vhal_prop:
        print(f"[2/2] 回读验证 (dumpsys --get {vhal_prop}) ...")
        r2 = subprocess.run(
            ["adb", "shell", f"dumpsys android.hardware.automotive.vehicle.IVehicle/default --get {vhal_prop}"],
            capture_output=True, text=True,
        )
        for ln in (r2.stdout or "").splitlines():
            if "value:" in ln or "int32Values" in ln:
                print(f"      {ln.strip()}")
    print("[完成]")
    conn.close()


def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    if args[0] == "--sim":
        if len(args) < 2:
            print("用法: python can_query.py --sim \"信号名:值\"  例: --sim \"CWC_workingSts:0\"")
            return
        simulate_signal(args[1])
        return

    if args[0] == "--type":
        qtype = args[1]
        value = args[2]
        if qtype == "property":
            query_by_property(value)
        elif qtype == "vdbid":
            query_by_vdbid(value)
        elif qtype == "signal":
            query_signal(value)
        elif qtype == "can":
            query_can_msg(value)
    else:
        search_keyword(args[0])


if __name__ == "__main__":
    main()
