"""Build one writing packet per product: task.md (rules + 3 similar real copies + opener ideas) and photos/.

This is the round-5 (pilot-v5) logic that produced editor.html, kept as is:
  * rules = rules/writer-rules.md + rules/style-profile.md
  * 3 examples from the example pool, same category, most similar product name
    (falls back to the same top-level group when a sub-category has fewer than 3)
  * pool products that share photos with the target are never used as examples
  * 4 real opening lines per route A and C, drawn per product with a fixed seed
  * official colour names are given; the material line is appended later by the editor
Added after round 5 (both leave task.md byte-identical when empty):
  * --new may carry the user's own 背景資訊 / 自身感受, written in without brackets
  * the team's training record (data/training.json, see scripts/training.py): latest ♡ sentences,
    latest disliked (✕) sentences, all banned words, latest final copies
  * --new may carry optional sizes (尺寸) / price (價格) lines
  * v2: the record's text-style notes (style_notes, 文字偏好) and, for a final copy that has one, the user's
    edited version (edited_text) instead of the original final

Usage:
  python3 scripts/prepare.py --new new-product.json --output packets/2026-11-03     # photos = the user's upload only
  python3 scripts/prepare.py 2507100017 2609070031 --output packets/2026-11-03      # old: re-run existing saleids (needs archive/)
new-product.json: {"id": "new-001", "name": "...", "category": "長褲",
                   "colors": ["深藍"], "material": "...", "photos": "path/to/photo-folder",
                   "background": "optional: 補貨、改版、熱銷…", "feelings": "optional: 試穿心得、身高尺寸、觸感…",
                   "sizes": "optional: S/M/L…", "price": "optional: 1280"}
                  (背景資訊 / 自身感受 are accepted as the key names too)
"""
import argparse
import hashlib
import json
import random
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

import training

HERE = Path(__file__).resolve().parent.parent
SEED = 20261028
EXAMPLES = 3
OPENERS_SHOWN = 4
HEARTS_SHOWN = 40   # latest ♡ sentences put into task.md
FINALS_SHOWN = 3    # latest final copies put into task.md
DISLIKED_SHOWN = 40  # latest ✕ (disliked) sentences put into task.md
STYLE_NOTES_SHOWN = 12  # latest text-style notes (learned from the users' edited copies) put into task.md
# Optional product facts of a --new spec, one line each in "# 新商品" when filled: (key, label in task.md)
PRODUCT_FACT_FIELDS = (('sizes', '尺寸'), ('price', '價格'))
# Optional user-written fields of a --new spec: (English key, Chinese key, label in task.md)
USER_INFO_FIELDS = (('background', '背景資訊', '背景資訊'), ('feelings', '自身感受', '自身感受'))
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
    """Read-only dHash cache in data/fingerprints.json, keyed by the image's sha256.

    The example pool is compared through this cache only, so the photo library (archive/) is never needed.
    Nothing is ever written back: photos a user uploads for a new product are hashed on the fly."""

    def __init__(self, cache: Path):
        self.values = json.loads(cache.read_text())

    def cached(self, key: str) -> int | None:
        return self.values.get(key)

    def pool_print(self, key: str) -> int:
        if key not in self.values:
            raise SystemExit(f'data/fingerprints.json has no fingerprint for example photo {key}. '
                             'The cache is incomplete; rebuild it on a machine that has the photo library.')
        return self.values[key]


def shares_photos(a: list[int], b: list[int]) -> bool:
    matches = sum(1 for x in a if any((x ^ y).bit_count() <= SHARED_DISTANCE for y in b))
    return matches >= SHARED_MIN_IMAGES


def catalog_photos(product: dict, healer_root: Path) -> list[tuple[str, Path]]:
    """(sha256, path) per usable photo of an existing product. Only the old re-run-by-saleid mode needs this,
    and only it needs the photo library (archive/); new-product mode and the example pool never do."""
    photos = []
    for image in product['images']:
        if image['index'] not in product['usable_image_indices']:
            continue
        path = healer_root / image['path']
        if not path.exists():
            raise SystemExit(f'Photo missing: {path}\nRe-running an existing saleid needs the Healer archive/ photo library '
                             '(pass --healer-root). For a new product use --new with the photos you uploaded.')
        photos.append((image.get('sha256') or image['path'], path))
    return photos


def user_info(spec: dict, spec_path: Path) -> list[tuple[str, str]]:
    """(label, text) for each optional field the user actually filled in; blank fields are left out."""
    filled = []
    for english, chinese, label in USER_INFO_FIELDS:
        values = []
        for key in (english, chinese):
            value = spec.get(key)
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                value = '\n'.join(value)
            if value is not None and not isinstance(value, str):
                raise SystemExit(f'{spec_path}: "{key}" must be text')
            if value and value.strip():
                values.append(value.strip())
        if len(values) == 2:
            raise SystemExit(f'{spec_path}: fill in "{english}" or "{chinese}", not both')
        if values:
            filled.append((label, values[0]))
    return filled


def user_info_section(info: list[tuple[str, str]]) -> str:
    if not info:
        return ''
    listed = '\n\n'.join(f'{label}：\n{text}' for label, text in info)
    return ('\n\n# 小編提供的資訊\n'
            '以下是使用者（小編本人）這次親自提供的資訊：\n\n'
            f'{listed}\n\n'
            '使用方式：\n'
            '- 這是使用者本人提供的資訊，可以直接用小編口吻寫進文案，不用加方括號。'
            '這一條優先於寫作規則裡「小編試穿心得、銷量或回購故事（熱銷、補貨、改版）要放方括號」的規定。\n'
            '- 但只限上面實際寫出的內容：不可以延伸、誇大或自己編。例如寫了「摸起來軟軟的」，不能變成「整天穿都不悶熱」；'
            '寫了「補貨」，不能變成「回購率破表」或「秒殺」。\n'
            '- 照片和材質仍證明不了、上面也沒寫到的內容，照舊用 [方括號]。')


def product_facts(spec: dict, spec_path: Path) -> list[tuple[str, str]]:
    """(label, text) for the optional 尺寸 / 價格 lines of a --new spec; blank ones are left out."""
    facts = []
    for key, label in PRODUCT_FACT_FIELDS:
        value = spec.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if value is not None and not isinstance(value, str):
            raise SystemExit(f'{spec_path}: "{key}" must be text')
        if value and value.strip():
            facts.append((label, value.strip()))
    return facts


def training_section(record: dict, has_user_info: bool) -> str:
    hearts = record['hearts'][-HEARTS_SHOWN:]
    finals = record['finals'][-FINALS_SHOWN:]
    banned = record['banned']
    disliked = record.get('disliked', [])[-DISLIKED_SHOWN:]
    notes = record.get('style_notes', [])[-STYLE_NOTES_SHOWN:]
    if not (hearts or finals or banned or disliked or notes):
        return ''
    exception = '（「小編提供的資訊」裡實際寫到的除外）' if has_user_info else ''
    parts = ['\n\n# 團隊喜好（訓練紀錄）\n'
             '以下是團隊挑文案時留下的紀錄，用來對齊大家喜歡的口氣與用詞。使用方式：\n'
             '- 這些只決定口氣與用詞，不改變寫作規則。尤其不能推翻「照片和材質證明不了的句子要加 [方括號]」：'
             f'♡ 句子或定稿裡沒加括號的穿著感受、小編心得、熱銷故事，用到本商品時照樣要放方括號{exception}。\n'
             '- ♡ 句子不可整句照抄，只學它的節奏、語氣和用字。\n'
             '- ♡ 句子和定稿寫的是別的商品，不可沿用其中的商品事實。']
    if disliked:
        parts[0] += '\n- 「不喜歡的句子」是團隊按 ✕ 刪掉的句子：不要寫出相同或很像的句子與說法。'
    if notes:
        parts[0] += ('\n- 「文字偏好」是從使用者實際使用、修改後的文案歸納出的習慣：只用來調整口氣、句型、用字習慣，'
                     '**不能推翻** 方括號規則、官方色名、「小編提供的資訊」（背景資訊／自身感受）的使用限制、行數與字數規則；'
                     '跟這些規則衝突時，以規則為準。')
        parts.append(f'## 文字偏好（從使用者修改後的文案歸納，最近 {len(notes)} 條）\n' + '\n'.join(
            f'- {n["note"]}' for n in notes))
    if hearts:
        parts.append(f'## 團隊按 ♡ 的句子（最近 {len(hearts)} 句）\n' + '\n'.join(
            f'- {h["text"]}' + (f'（路線 {h["route"]}）' if h.get('route') else '') for h in hearts))
    if disliked:
        parts.append(f'## 不喜歡的句子（不要寫出相同或很像的句子與說法；最近 {len(disliked)} 句）\n' + '\n'.join(
            f'- {d["text"]}' for d in disliked))
    if banned:
        parts.append('## 禁用詞（絕對不可出現在文案任何地方，包括方括號內）\n' + '、'.join(banned))
    if finals:
        parts.append(f'## 團隊最近的定稿（最近 {len(finals)} 篇）\n' + '\n\n'.join(
            f'### 定稿 {k}：{f.get("name") or f.get("product_id") or ""}' +
            (f'（使用者實際使用的修改後版本）\n{f["edited_text"]}' if (f.get('edited_text') or '').strip() else f'\n{f["text"]}')
            for k, f in enumerate(finals, 1)))
    return '\n\n'.join(parts)


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
                     material=spec['material'], user_info=user_info(spec, spec_path),
                     facts=product_facts(spec, spec_path),
                     photos=[(hashlib.sha256(p.read_bytes()).hexdigest(), p) for p in photos])]
    targets = []
    for sid in args.ids:
        if sid not in catalog:
            raise SystemExit(f'{sid} is not in data/catalog.jsonl. For an unpublished product use --new.')
        if not categories.get(sid):
            raise SystemExit(f'{sid} has no category in data/categories.json.')
        product = catalog[sid]
        targets.append(dict(id=sid, name=product['name'], category=categories[sid][0],
                            colors=[c['name'] for c in product['colors']], material=product['material'], user_info=[], facts=[],
                            photos=catalog_photos(product, healer_root)))
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ids', nargs='*', help='saleid of products already in data/catalog.jsonl')
    parser.add_argument('--new', help='JSON file describing one unpublished product')
    parser.add_argument('--output', required=True, help='new folder for the packets')
    parser.add_argument('--healer-root', default=str(HERE.parent),
                        help='only for re-running existing saleids: folder that contains archive/. Not used with --new')
    parser.add_argument('--training', default=None,
                        help='team training record (default data/training.json); a missing file means no record')
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
    record = training.load(training.record_path(args.training))

    firsts = [next(l.strip() for l in catalog[p]['description'].splitlines() if l.strip()) for p in pool]
    scene_openers = [f for f in firsts if SCENE_OPENER.search(f)]
    hook_openers = [f for f in firsts if HOOK_OPENER.search(f) and not SCENE_OPENER.search(f)]
    prints = Fingerprints(HERE / 'data/fingerprints.json')
    pool_prints = {p: [prints.pool_print(i['sha256']) for i in catalog[p]['images'] if i['index'] in catalog[p]['usable_image_indices']]
                   for p in pool if categories.get(p)}

    plan = []
    for target in targets:
        sid, category = target['id'], target['category']
        target_prints = [prints.cached(k) if prints.cached(k) is not None else fingerprint(path) for k, path in target['photos']]
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
        facts = ''.join(f'{fact}：{text}\n' for fact, text in target['facts'])
        task = (f'# 新商品\n商品名稱：{target["name"]}\n類別：{label}\n'
                f'官方色名：{"、".join(target["colors"])}\n材質：{target["material"]}\n'
                f'{facts}'
                f'照片：photos/ 資料夾內 {len(names)} 張（{names[0]} 到 {names[-1]}）'
                f'{user_info_section(target["user_info"])}\n\n'
                f'# 寫作規則\n{rules}{training_section(record, bool(target["user_info"]))}\n\n# 同店其他商品的真實文案（只學語氣與結構，不可沿用它們的商品事實）\n{example_text}\n{openers}')
        (folder / 'task.md').write_text(task, encoding='utf-8')
        (folder / 'product.json').write_text(json.dumps(
            {k: target[k] for k in ('id', 'name', 'colors', 'material')}, ensure_ascii=False, indent=1), encoding='utf-8')
        plan.append({'id': sid, 'arm': 'S', 'category': label, 'photos': len(names), 'examples': similar,
                     'excluded_shared_photos': [p for p in linked if p != sid]})
    (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(plan, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
