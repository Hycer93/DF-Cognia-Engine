"""落盘第三份分享对话（764轮，暮暮/蒸馏仔·银子蒸馏厂期）"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "."), "share3.json")
d = json.loads(open(p, 'rb').read().decode('utf-8'))
msgs = d['data']['biz_data']['messages']

def get_text(m):
    return '\n'.join(f.get('content') or '' for f in m.get('fragments', []) if isinstance(f, dict))

texts = [get_text(m).strip() for m in msgs]
texts = [t for t in texts if t]
pairs = [(texts[i], texts[i + 1]) for i in range(0, len(texts) - 1, 2)]

out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "logs", "ds_share3_conversation.md")
lines = []
for idx, (u, a) in enumerate(pairs, 1):
    block = '## round {} user\n\n{}\n\n## round {} ds\n\n'.format(idx, u, idx) + a + '\n'
    lines.append(block)
open(out, 'w', encoding='utf-8').write('\n'.join(lines))
print('written:', out, len(pairs), 'rounds')

# 关键转折词命中（用户侧）
hits = []
for idx, (u, a) in enumerate(pairs, 1):
    for kw in ['被我说动', '被你说动', '推翻', '放弃', '不可行', '可行性很低', '失败了', '认错', '止损', '最贵的一课', '被欺骗']:
        if kw in u:
            hits.append((idx, kw, u[:90].replace('\n', ' ')))
            break
print('=== user pivot keywords ===')
for h in hits[:50]:
    print(h)