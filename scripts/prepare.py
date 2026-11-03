"""Build one writing packet per product: task.md (rules + 3 similar real copies + opener ideas) and photos/.

This is the round-5 (pilot-v5) logic that produced editor.html, kept as is:
  * rules = rules/writer-rules.md + rules/style-profile.md
  * 3 examples from the example pool, same category, most similar product name
    (falls back to the same top-level group when a sub-category has fewer than 3)
  * pool products that share photos with the target are never used as examples
  * 4 real opening lines per route A and C, drawn per product with a fixed seed
  * official colour names are given; the material line is appended later by the editor

Usage:
  python3 scripts/prepare.py 2507100017 2609070031 --output packets/2026-11-03
  python3 scripts/prepare.py --new new-product.json --output packets/2026-11-03
new-product.json: {"id": "new-001", "name": "...", "category": "長褲",
                   "colors": ["深藍"], "material": "...", "photos": "path/to/photo-folder"}
"""
import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent.parent
SEED = 20261028
EXAMPLES = 3
OPENERS_SHOWN = 4
PHOTO_TYPES = {'.jpg', '.jpeg', '.png', '.webp'}
# "Shares photos" = at least 2 image pairs whose 64-bit dHash differ by <= 2 bits
# (definition recorded in dataset/final-exam-v2/manifest.json).
SHARED_DISTANCE = 2
SHARED_MIN_IMAGES = 2
SCENE_OPENER = re.compile(r'水水|喜歡|適合|春|夏|秋|冬|上班|通勤|約會|出遊|日常|女孩|的妳')
HOOK_OPENER = re.compile(r'！|!|愛|太|最|很少|驚艷|厲害|爆表|紅了|挑戰|終於|回歸|久違|眼睛一亮')


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def name_core(name: str) -> str:
    return re.sub(r'HEALER( Made| X \w+)?|Clina Pick！|S-XL|S/M|[\s!！]', '', name)


def bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = bigrams(name_core(a)), bigrams(name_core(b))
    return len(x & y) / len(x | y) if x | y else 0.0


def fingerprint(path: Path) -> int:
    with Image.open(path) as source:
        pixels = list(ImageOps.exif_transpose(source).convert('L').resize((9, 8)).tobytes())
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return value


class Fingerprints:
    """dHash per image file, cached in data/fingerprints.json by the image's sha256 or path."""

    def __init__(self, cache: Path):
        self.cache = cache
        self.values = json.loads(cache.read_text()) if cache.exists() else {}
        self.dirty = False

    def get(self, key: str, path: Path) -> int:
        if key not in self.values:
            self.values[key] = fingerprint(path)
            self.dirty = True
        return self.values[key]

    def save(self) -> None:
        if self.dirty:
            self.cache.write_text(json.dumps(self.values))


def shares_photos(a: list[int], b: list[int]) -> bool:
    matches = sum(1 for x in a if any((x ^ y).bit_count() <= SHARED_DISTANCE for y in b))
    return matches >= SHARED_MIN_IMAGES


def catalog_photos(product: dict, healer_root: Path) -> list[tuple[str, Path]]:
    photos = []
    for image in product['images']:
        if image['index'] not in product['usable_image_indices']:
            continue
        path = healer_root / image['path']
        if not path.exists():
            raise SystemExit(f'Photo missing: {path}\nThe photos live in the Healer archive/ folder; pass --healer-root if it moved.')
        photos.append((image.get('sha256') or image['path'], path))
    return photos


def targets_from_args(args, catalog: dict, categories: dict, labels: dict, healer_root: Path) -> list[dict]:
    if args.new:
        spec_path = Path(args.new).expanduser().resolve(strict=True)
        spec = json.loads(spec_path.read_text(encoding='utf-8'))
        missing = [k for k in ('id', 'name', 'category', 'colors', 'material', 'photos') if not spec.get(k)]
        if missing:
            raise SystemExit(f'{spec_path} is missing: {", ".join(missing)}')
        category = next((cid for cid, label in labels.items() if label == spec['category']), None)
        if category is None:
            raise SystemExit(f'Unknown category {spec["category"]!r}. Use one of: {"、".join(sorted(set(labels.values())))}')
        folder = (spec_path.parent / spec['photos']).resolve(strict=True)
        photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_TYPES)
        if not photos:
            raise SystemExit(f'No photos ({", ".join(sorted(PHOTO_TYPES))}) in {folder}')
        return [dict(id=str(spec['id']), name=spec['name'], category=category, colors=list(spec['colors']),
                     material=spec['material'], photos=[(str(p), p) for p in photos])]
    targets = []
    for sid in args.ids:
        if sid not in catalog:
            raise SystemExit(f'{sid} is not in data/catalog.jsonl. For an unpublished product use --new.')
        if not categories.get(sid):
            raise SystemExit(f'{sid} has no category in data/categories.json.')
        product = catalog[sid]
        targets.append(dict(id=sid, name=product['name'], category=categories[sid][0],
                            colors=[c['name'] for c in product['colors']], material=product['material'],
                            photos=catalog_photos(product, healer_root)))
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ids', nargs='*', help='saleid of products already in data/catalog.jsonl')
    parser.add_argument('--new', help='JSON file describing one unpublished product')
    parser.add_argument('--output', required=True, help='new folder for the packets')
    parser.add_argument('--healer-root', default=str(HERE.parent), help='folder that contains archive/ (default: parent of 文案)')
    args = parser.parse_args()
    if bool(args.ids) == bool(args.new):
        parser.error('give product ids or --new, not both')
    out = Path(args.output).resolve()
    if out.exists():
        raise SystemExit(f'{out} already exists; choose a new folder.')
    healer_root = Path(args.healer_root).expanduser().resolve()

    catalog = {r['id']: r for r in load_jsonl(HERE / 'data/catalog.jsonl')}
    category_data = json.loads((HERE / 'data/categories.json').read_text(encoding='utf-8'))
    categories, labels = category_data['products'], category_data['labels']
    pool = json.loads((HERE / 'data/example-pool.json').read_text(encoding='utf-8'))['example_pool_ids']
    rules = (HERE / 'rules/writer-rules.md').read_text(encoding='utf-8') + '\n\n' + \
        (HERE / 'rules/style-profile.md').read_text(encoding='utf-8').replace('## ', '# ', 1)
    targets = targets_from_args(args, catalog, categories, labels, healer_root)

    firsts = [next(l.strip() for l in catalog[p]['description'].splitlines() if l.strip()) for p in pool]
    scene_openers = [f for f in firsts if SCENE_OPENER.search(f)]
    hook_openers = [f for f in firsts if HOOK_OPENER.search(f) and not SCENE_OPENER.search(f)]
    prints = Fingerprints(HERE / 'data/fingerprints.json')
    pool_prints = {p: [prints.get(k, path) for k, path in catalog_photos(catalog[p], healer_root)] for p in pool if categories.get(p)}

    plan = []
    for target in targets:
        sid, category = target['id'], target['category']
        target_prints = [prints.get(k, path) for k, path in target['photos']]
        linked = sorted(p for p, fp in pool_prints.items() if p == sid or shares_photos(target_prints, fp))
        allowed = [p for p in pool_prints if p not in linked]
        candidates = sorted(p for p in allowed if categories[p][0] == category)
        if len(candidates) < EXAMPLES:
            candidates = sorted(p for p in allowed if categories[p][0].split('/')[0] == category.split('/')[0])
        if len(candidates) < EXAMPLES:
            raise SystemExit(f'{sid}: only {len(candidates)} example products in category {labels.get(category, category)}')
        similar = sorted(candidates, key=lambda p: (-similarity(target['name'], catalog[p]['name']), p))[:EXAMPLES]

        folder = out / f'{sid}-S'
        (folder / 'photos').mkdir(parents=True)
        names = []
        for n, (_, path) in enumerate(target['photos'], 1):
            dest = folder / 'photos' / f'{n:02d}{path.suffix}'
            shutil.copyfile(path, dest)
            names.append(dest.name)
        opener_rng = random.Random(f'{SEED}-{sid}')
        openers = (f'\n\n# 本商品的開頭參考（官網其他商品的真實第一行，只學口氣與角度）\n'
                   f'路線 A：{" ／ ".join(opener_rng.sample(hook_openers, OPENERS_SHOWN))}\n'
                   f'路線 C：{" ／ ".join(opener_rng.sample(scene_openers, OPENERS_SHOWN))}\n')
        example_text = '\n\n'.join(
            f'### 範例 {k}：{catalog[p]["name"]}\n材質：{catalog[p]["material"]}\n官網商品資訊：\n{catalog[p]["description"]}'
            for k, p in enumerate(similar, 1))
        label = labels.get(category, category)
        task = (f'# 新商品\n商品名稱：{target["name"]}\n類別：{label}\n'
                f'官方色名：{"、".join(target["colors"])}\n材質：{target["material"]}\n'
                f'照片：photos/ 資料夾內 {len(names)} 張（{names[0]} 到 {names[-1]}）\n\n'
                f'# 寫作規則\n{rules}\n\n# 同店其他商品的真實文案（只學語氣與結構，不可沿用它們的商品事實）\n{example_text}\n{openers}')
        (folder / 'task.md').write_text(task, encoding='utf-8')
        (folder / 'product.json').write_text(json.dumps(
            {k: target[k] for k in ('id', 'name', 'colors', 'material')}, ensure_ascii=False, indent=1), encoding='utf-8')
        plan.append({'id': sid, 'arm': 'S', 'category': label, 'photos': len(names), 'examples': similar,
                     'excluded_shared_photos': [p for p in linked if p != sid]})
    prints.save()
    (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(plan, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
