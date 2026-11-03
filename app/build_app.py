"""Build copy-studio.html: embed the writing rules, style profile, example pool and one sample result.

Usage: python3 app/build_app.py   (writes app/copy-studio.html)
Rerun after editing rules/writer-rules.md or rules/style-profile.md, then republish the page.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SAMPLE_ID = '2507100017'


def section(result: str, name: str) -> str:
    match = re.search(rf'【{name}】\s*(.*?)(?=\n【|\Z)', result, re.S)
    return match.group(1).strip() if match else ''


def main() -> None:
    catalog = {r['id']: r for r in map(json.loads, (ROOT / 'data/catalog.jsonl').read_text(encoding='utf-8').splitlines())}
    cats = json.loads((ROOT / 'data/categories.json').read_text(encoding='utf-8'))
    pool_ids = json.loads((ROOT / 'data/example-pool.json').read_text(encoding='utf-8'))['example_pool_ids']
    rules = (ROOT / 'rules/writer-rules.md').read_text(encoding='utf-8')
    rules = rules.split('**輸出格式**')[0].rstrip()  # the page asks for JSON instead
    style = (ROOT / 'rules/style-profile.md').read_text(encoding='utf-8')
    pool = []
    for pid in pool_ids:
        product, cat = catalog[pid], cats['products'].get(pid) or []
        if not cat:
            continue
        pool.append({'id': pid, 'name': product['name'], 'category': cats['labels'].get(cat[0], cat[0]),
                     'material': product['material'], 'description': product['description']})
    labels = sorted({p['category'] for p in pool}, key=lambda c: -sum(p['category'] == c for p in pool))
    raw = json.loads((ROOT / f'pilot-v5-結果/outputs-r5/{SAMPLE_ID}-S.json').read_text(encoding='utf-8'))['result']
    sample = catalog[SAMPLE_ID]
    example = {
        'name': sample['name'], 'category': '鞋子', 'colors': '、'.join(c['name'] for c in sample['colors']),
        'material': sample['material'], 'target': section(raw, '目標商品'),
        'details': [l.lstrip('- ').strip() for l in section(raw, '照片細節').splitlines() if l.strip()],
        **{k: [l.strip() for l in section(raw, f'路線{k}').splitlines() if l.strip()] for k in 'ABC'}}
    data = {'rules': rules, 'style': style, 'pool': pool, 'categories': labels, 'example': example}
    blob = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    page = (HERE / 'copy-studio.template.html').read_text(encoding='utf-8').replace('/*DATA*/null', blob)
    (HERE / 'copy-studio.html').write_text(page, encoding='utf-8')
    print(f'copy-studio.html: {len(pool)} pool products, {len(labels)} categories, {len(page) // 1024} KB')


if __name__ == '__main__':
    main()
