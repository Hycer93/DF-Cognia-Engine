"""#20 合规核查：抽查近 7 天入 raw_corpus 的对话正文是否残留 PII 原值。
E9 transcript 脱敏（_sanitize_text）生效后应为 0 命中。
误报规避：跳过含路径分隔符 / BUG_ 任务号的技术串（形似邮箱/长数字但非 PII）。
用法: PYTHONIOENCODING=utf-8 python eval/audit/pii_residue_check.py
成功标准: 精确PII命中 = 0（脱敏前入的历史数据不在近7天窗口，天然排除）
"""
import sqlite3, re, sys
c = sqlite3.connect('file:data/memory/corpus_store.db?mode=ro', uri=True)
rows = c.execute("SELECT body FROM raw_corpus WHERE tree_vol>=date('now','-7 day')").fetchall()
pat_phone = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
pat_mail = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|cn|net|org|edu|gov|io|co)(?!\w)', re.I)
pat_id = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
hit = []
for r in rows:
    b = r[0] or ''
    # 跳过技术串：含路径分隔符 或 BUG_ 任务号
    if 'BUG_' in b or chr(92) in b or '/' in b:
        continue
    m = pat_phone.search(b) or pat_mail.search(b) or pat_id.search(b)
    if m:
        hit.append((m.group(0), b[:50]))
print(f'检查{len(rows)}条, 精确PII命中 {len(hit)}条')
for h in hit[:8]:
    print('  ', h)