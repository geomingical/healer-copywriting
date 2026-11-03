"""Build editor.html: pick lines from the three routes (A/B/C) and assemble your own copy.

Usage: python3 scripts/build_editor.py <packets folder> <outputs folder> --output editor.html
       [--single <older one-version outputs folder>]   (optional, folded-away comparison)
Products already on the website also show their real copy, folded away, for comparison.
"""
import argparse
import base64
import io
import json
import re
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent.parent
ROUTES = {'A': '心動開場', 'B': '賣點直擊', 'C': '穿搭情境'}
PHOTOS_SHOWN = 8


def thumb(path: Path) -> str:
    image = Image.open(path).convert('RGB')
    image.thumbnail((360, 540))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=78)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()


def routes(result: str) -> dict[str, list[str]]:
    found = {}
    for key in ROUTES:
        match = re.search(rf'【路線{key}】\s*(.*?)(?=\n【|\Z)', result, re.S)
        if not match:
            raise ValueError(f'route {key} missing')
        found[key] = [l.strip() for l in match.group(1).splitlines() if l.strip()]
    return found


def copy_section(result: str) -> str:
    body = re.search(r'【文案】\s*(.*)', result, re.S).group(1)
    return re.split(r'\n-{3,}\s*\n', body)[0].strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('packets')
    parser.add_argument('outputs')
    parser.add_argument('--output', required=True)
    parser.add_argument('--single', help='folder of older one-version outputs to show for comparison')
    args = parser.parse_args()
    packets, outputs = Path(args.packets), Path(args.outputs)
    catalog = {r['id']: r for r in map(json.loads, (HERE / 'data/catalog.jsonl').read_text(encoding='utf-8').splitlines())}
    products = []
    for path in sorted(outputs.glob('*.json')):
        sid = path.stem.rsplit('-', 1)[0]
        info_file = packets / path.stem / 'product.json'
        if info_file.exists():
            info = json.loads(info_file.read_text(encoding='utf-8'))
        else:
            product = catalog[sid]
            info = dict(id=sid, name=product['name'], colors=[c['name'] for c in product['colors']], material=product['material'])
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('is_error'):
            raise SystemExit(f'{path} is a failed generation; delete it and rerun run.sh.')
        single = Path(args.single) / path.name if args.single else None
        photos = sorted((packets / path.stem / 'photos').iterdir())[:PHOTOS_SHOWN]
        products.append({
            'id': sid, 'name': info['name'], 'colors': info['colors'], 'material': info['material'],
            'photos': [thumb(p) for p in photos], 'routes': routes(result['result']),
            'single': copy_section(json.loads(single.read_text())['result']) if single and single.exists() else '',
            'real': catalog[sid]['description'] if sid in catalog else ''})
    if not products:
        raise SystemExit(f'No outputs in {outputs}')
    data = json.dumps({'routes': ROUTES, 'products': products}, ensure_ascii=False).replace('</', '<\\/')
    page = (HERE / 'scripts/editor-template.html').read_text(encoding='utf-8').replace('/*DATA*/null', data)
    Path(args.output).write_text(page, encoding='utf-8')
    print(f'{args.output} written with {len(products)} products')


if __name__ == '__main__':
    main()
