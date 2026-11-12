"""落盘第二份分享对话 + 提取归元仔人生时间线"""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = r'C:\Users\uidq0280\AppData\Local\Temp\share2.json'
d = json.loads(open(p, 'rb').read().decode('utf-8'))
msgs = d['data']['biz_data']['messages']

def get_text(m):
    return '\n'.join(f.get('content') or '' for f in m.get('fragments', []) if isinstance(f, dict))

texts = [get_text(m).strip() for m in msgs]
texts = [t for t in texts if t]
pairs = [(texts[i], texts[i + 1]) for i in range(0, len(texts) - 1, 2)]

out = r'D:\uidq0280\Downloads\Agent\cognitive_engine_v3\logs\ds_share2_conversation.md'
lines = []
for idx, (u, a) in enumerate(pairs, 1):
    header = '## round {} user\n\n{}\n\n## round {} ds\n\n'.format(idx, u, a)
    lines.append(header + a + '\n')
open(out, 'w', encoding='utf-8').write('\n'.join(lines))
print('written:', out, len(pairs), 'rounds')

u2 = pairs[1][0]
print('=== timeline ===')
for m in re.finditer(r'## (\d{4}[年/-]\d+[月/-]\d+日?[^\n]*)', u2):
    print(m.group(1).strip()[:80])