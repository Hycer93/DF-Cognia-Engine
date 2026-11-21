"""
CAN.xlsx → ChromeDB 导入脚本
三个 Sheet 关系：
- VDB SPI: PropertyID (0xA000~) ↔ Property 名称
- CAN FD:    报文信号级定义，含 Signal Name, Msg ID
- DBC转VDB:  桥梁表 —— Msg ID + Signal Name → Property + VDB ID + PropertyID

主查询路径：
1. 查某个 Property/PropertyID 对应的 CAN 信号：DBC转VDB ←→ CAN FD
2. 查某个 CAN 报文的所有信号：CAN FD (按 Msg ID 分组)
3. 查 VDB SPI 中的 Property 列表：VDB SPI
4. 完整链路（CAN 信号 → VDB Property）：DBC转VDB + CAN FD JOIN
"""

import sqlite3
import openpyxl
from pathlib import Path

XLSX_PATH = r"D:\project\chery\8397\EHV\DsvSystemUI\01_Doc\CAN.xlsx"
DB_PATH = r"D:\project\chery\8397\EHV\DsvSystemUI\01_Doc\can_protocol.db"


def create_tables(cur):
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS vdb_spi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property TEXT,
            signal_type TEXT,
            property_id TEXT,
            property_meaning TEXT
        );

        CREATE TABLE IF NOT EXISTS can_fd (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT,
            frame_format TEXT,
            brs TEXT,
            msg_name TEXT,
            msg_type TEXT,
            msg_length TEXT,
            msg_send_type TEXT,
            msg_cycle_time TEXT,
            msg_cycle_time_fast TEXT,
            msg_nr_of_reption TEXT,
            msg_delay_time TEXT,
            signal_name TEXT,
            multiplexor_type TEXT,
            multiplex_value TEXT,
            start_byte TEXT,
            start_bit TEXT,
            bit_length TEXT,
            byte_order TEXT,
            date_type TEXT,
            resolution TEXT,
            offset_val TEXT,
            signal_min_phys TEXT,
            signal_max_phys TEXT,
            signal_min_hex TEXT,
            signal_max_hex TEXT,
            initial_value_hex TEXT,
            invalid_value_hex TEXT,
            unit TEXT,
            signal_value_desc TEXT,
            signal_description TEXT,
            inactive_value_hex TEXT,
            signal_send_type TEXT,
            asil TEXT,
            icc_int_fl TEXT,
            icc_ihu TEXT,
            icc_fl TEXT,
            icc TEXT,
            source TEXT
        );

        CREATE TABLE IF NOT EXISTS dbc_to_vdb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT,
            signal_name TEXT,
            msg_send_type TEXT,
            resolution TEXT,
            offset_val TEXT,
            initial_value_hex TEXT,
            invalid_value_hex TEXT,
            signal_value_desc TEXT,
            signal_description TEXT,
            msg_cycle_time TEXT,
            signal_send_type TEXT,
            access_flag TEXT,
            access TEXT,
            property TEXT,
            ehv_vdb_id TEXT,
            property_id TEXT,
            area TEXT,
            type TEXT,
            change_mode TEXT,
            `group` TEXT,
            property_meaning TEXT,
            write_map_a2 TEXT,
            read_map_b2 TEXT,
            write_map_a1 TEXT,
            read_map_b1 TEXT,
            update_time TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_can_fd_msg_id ON can_fd(msg_id);
        CREATE INDEX IF NOT EXISTS idx_can_fd_signal ON can_fd(signal_name);
        CREATE INDEX IF NOT EXISTS idx_dbc_property ON dbc_to_vdb(property);
        CREATE INDEX IF NOT EXISTS idx_dbc_property_id ON dbc_to_vdb(property_id);
        CREATE INDEX IF NOT EXISTS idx_dbc_ehv_vdb ON dbc_to_vdb(ehv_vdb_id);
        CREATE INDEX IF NOT EXISTS idx_dbc_msg_id ON dbc_to_vdb(msg_id);
        CREATE INDEX IF NOT EXISTS idx_vdb_spi_prop_id ON vdb_spi(property_id);
        CREATE INDEX IF NOT EXISTS idx_vdb_spi_prop ON vdb_spi(property);
    """)


def insert_vdb_spi(cur, ws):
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        cur.execute("INSERT INTO vdb_spi (property, signal_type, property_id, property_meaning) VALUES (?,?,?,?)",
                    (str(row[0]), str(row[1]), str(row[2]).strip() if row[2] else None, str(row[3]) if row[3] else None))


def insert_can_fd(cur, ws):
    cols = [
        'msg_id', 'frame_format', 'brs', 'msg_name', 'msg_type', 'msg_length',
        'msg_send_type', 'msg_cycle_time', 'msg_cycle_time_fast', 'msg_nr_of_reption',
        'msg_delay_time', 'signal_name', 'multiplexor_type', 'multiplex_value',
        'start_byte', 'start_bit', 'bit_length', 'byte_order', 'date_type',
        'resolution', 'offset_val', 'signal_min_phys', 'signal_max_phys',
        'signal_min_hex', 'signal_max_hex', 'initial_value_hex', 'invalid_value_hex',
        'unit', 'signal_value_desc', 'signal_description', 'inactive_value_hex',
        'signal_send_type', 'asil', 'icc_int_fl', 'icc_ihu', 'icc_fl', 'icc', 'source'
    ]
    placeholders = ','.join(['?'] * len(cols))
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None and row[11] is None:
            continue
        vals = tuple(str(v) if v is not None else '' for v in row[:len(cols)])
        cur.execute(f"INSERT INTO can_fd ({','.join(cols)}) VALUES ({placeholders})", vals)


def insert_dbc_to_vdb(cur, ws):
    cols = [
        'msg_id', 'signal_name', 'msg_send_type', 'resolution', 'offset_val',
        'initial_value_hex', 'invalid_value_hex', 'signal_value_desc', 'signal_description',
        'msg_cycle_time', 'signal_send_type', 'access_flag', 'access', 'property',
        'ehv_vdb_id', 'property_id', 'area', 'type', 'change_mode', '`group`',
        'property_meaning', 'write_map_a2', 'read_map_b2', 'write_map_a1', 'read_map_b1', 'update_time'
    ]
    placeholders = ','.join(['?'] * len(cols))
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None and row[1] is None:
            continue
        vals = tuple(str(v) if v is not None else '' for v in row[:len(cols)])
        cur.execute(f"INSERT INTO dbc_to_vdb ({','.join(cols)}) VALUES ({placeholders})", vals)


def main():
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()

    # Drop existing tables for clean re-import
    cur.execute("DROP TABLE IF EXISTS vdb_spi")
    cur.execute("DROP TABLE IF EXISTS can_fd")
    cur.execute("DROP TABLE IF EXISTS dbc_to_vdb")

    create_tables(cur)
    print(f"Sheet count: {len(wb.sheetnames)}")

    sheets = {'VDB SPI': insert_vdb_spi, 'CAN FD': insert_can_fd, 'DBC转VDB': insert_dbc_to_vdb}
    for sheet_name, insert_fn in sheets.items():
        ws = wb[sheet_name]
        print(f"Inserting {sheet_name}: {ws.max_row - 1} rows...")
        insert_fn(cur, ws)

    conn.commit()

    # Verify counts
    for tbl in ['vdb_spi', 'can_fd', 'dbc_to_vdb']:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {cur.fetchone()[0]} rows")

    conn.close()
    print(f"\nDatabase created: {DB_PATH}")


if __name__ == "__main__":
    main()
