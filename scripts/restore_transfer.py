"""Restore a transfer folder (transfer/<date>-<name>/) into YOUR OWN phone panel.

A transfer folder is a snapshot of one project made with `panel_session.py export` plus the photos and the
training record at that time:
  project-export.json   the project: products, A/B/C sentences, the users' picks, finals, edited finals
  photos/<sku>/*.jpg    the photos of each product (also .jpeg/.png/.webp), uploaded in file-name order
  training.json         data/training.json as it was when the snapshot was taken

What it does:
  1. merges TRANSFER/training.json into data/training.json (dedupe; nothing is removed)   [--no-merge-training]
  2. creates a project on your panel (or reuses one with --project) and, for every product in the export,
     replays the real user/bot flow through the existing panel-api only:
       create product → fields → photos → submit → claim → result (A/B/C) → state (picks + final) → edited final
     so the phone link ends up showing the products as 已定稿 with the users' 修改後最終文案.
  A product whose sku is already finalized in the project is skipped; one left half-way (draft/submitted/
  ready) by an earlier run is resumed from where it stopped, so the command can simply be run again.

Environment (same as panel_session.py): HEALER_PANEL_API, HEALER_PANEL_BOT_KEY, HEALER_PANEL_URL, from the
environment or ~/.config/healer-panel/panel.env ($HEALER_PANEL_ENV). Nothing else is read; no key is printed.

Usage:
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --title "11月上架（轉移）"
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --project <token-or-prefix>
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --training-only
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --skip-photos
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --no-merge-training
  python3 scripts/restore_transfer.py transfer/2026-11-05-healer --training path/to/training.json
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel_session as ps  # noqa: E402
import training  # noqa: E402

PanelError = ps.PanelError
CONTENT_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
ROUTES = ('A', 'B', 'C')
STATE_KEYS = ('draft', 'hearts', 'dislikes', 'banned_words', 'final')
EXPORT_FILE = 'project-export.json'
TRAINING_FILE = 'training.json'
PHOTOS_DIR = 'photos'
TITLE_SUFFIX = '（轉移）'
label = ps.label


# ---------- pure helpers (unit-tested, no network) ----------
def routes_for_result(routes: dict) -> dict[str, list[dict]]:
    """The export's routes as the body of POST …/result: {text, check} only, ids dropped.

    The panel regenerates ids as A1, A2… in array order, so the export order is kept as is. If the export's
    ids are not already A1…An in order, the users' ♡/✕ ids would point at the wrong sentences: refuse."""
    out: dict[str, list[dict]] = {}
    for r in ROUTES:
        items = routes.get(r) or []
        if not isinstance(items, list):
            raise PanelError(f'匯出檔的 routes.{r} 不是清單')
        out[r] = []
        for i, s in enumerate(items, 1):
            if not isinstance(s, dict) or not str(s.get('text') or '').strip():
                raise PanelError(f'匯出檔的 routes.{r}[{i - 1}] 沒有句子')
            sid = s.get('id')
            if sid is not None and sid != f'{r}{i}':
                raise PanelError(f'匯出檔的 routes.{r} 第 {i} 句 id 是 {sid}，不是 {r}{i}；順序被動過，♡／✕ 會對不上，不能還原')
            out[r].append({'text': s['text'], 'check': bool(s.get('check'))})
    extra = set(routes) - set(ROUTES)
    if extra:
        raise PanelError(f'匯出檔有不認得的路線：{"、".join(sorted(extra))}')
    if not any(out.values()):
        raise PanelError('匯出檔的 A/B/C 三路線都是空的')
    return out


def state_for_restore(state: dict) -> dict:
    """The export's state as the body of PUT …/state (only the five known keys; missing ones empty)."""
    state = state or {}
    out = {'draft': [d for d in state.get('draft') or [] if isinstance(d, dict)],
           'hearts': list(state.get('hearts') or []), 'dislikes': list(state.get('dislikes') or []),
           'banned_words': list(state.get('banned_words') or [])}
    final = state.get('final')
    if isinstance(final, dict) and str(final.get('text') or '').strip():
        out['final'] = {'text': final['text']}
        if isinstance(final.get('saved_at'), str):
            out['final']['saved_at'] = final['saved_at']
    else:
        out['final'] = None
    return out


def merge_training(record: dict, incoming: dict) -> dict[str, int]:
    """Add everything in `incoming` that `record` does not have yet; returns how many were newly added.

    hearts/disliked: same stripped text = same sentence.   banned: same word.
    finals: same (product_id, text) = same final; then an incoming edited_text updates the existing entry.
    style_notes: same note text. Dates (added/saved) of new entries are taken from the incoming file."""
    added = {'hearts': 0, 'disliked': 0, 'banned': 0, 'finals': 0, 'edited': 0, 'style_notes': 0}
    for key in ('hearts', 'disliked'):
        for s in incoming.get(key) or []:
            text = str(s.get('text') or '').strip()
            if text and training.add_sentence(record, key, text, s.get('route'), s.get('product_id')):
                record[key][-1]['added'] = s.get('added') or record[key][-1]['added']
                added[key] += 1
    for word in incoming.get('banned') or []:
        if isinstance(word, str) and word.strip():
            added['banned'] += training.add_banned(record, word)
    for f in incoming.get('finals') or []:
        text = str(f.get('text') or '').strip()
        if not text:
            continue
        pid, name = f.get('product_id'), f.get('name') or ''
        before = len(record['finals'])
        existing = next((e for e in record['finals'] if e.get('product_id') == pid and e['text'].strip() == text), None)
        had_edited = (existing or {}).get('edited_text')
        edited = f.get('edited_text') if isinstance(f.get('edited_text'), str) and f['edited_text'].strip() else None
        training.add_final(record, pid, name, text, replace=False, edited_text=edited)
        if len(record['finals']) > before:
            record['finals'][-1]['saved'] = f.get('saved') or record['finals'][-1]['saved']
            added['finals'] += 1
        elif edited and had_edited != edited.strip():
            added['edited'] += 1
    for n in incoming.get('style_notes') or []:
        note = str(n.get('note') or '').strip()
        if not note:
            continue
        try:
            added['style_notes'] += training.add_note(record, note, n.get('source') or '', n.get('added') or None)
        except SystemExit as e:   # too many notes / bad date: a warning, not a failed restore
            print(f'警告：文字偏好沒有加入「{note}」：{e}', file=sys.stderr)
    return added


def photo_files(folder: Path) -> list[Path]:
    """The photos of one product, in file-name order; only jpg/jpeg/png/webp."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CONTENT_TYPES),
                  key=lambda p: p.name)


def default_title(export: dict) -> str:
    title = (export.get('title') or '').strip()
    return (title or '轉移') + TITLE_SUFFIX


# ---------- transfer folder ----------
def load_transfer(folder: Path) -> tuple[dict, dict | None]:
    """(project export, training snapshot or None)."""
    if not folder.is_dir():
        raise PanelError(f'找不到轉移資料夾 {folder}')
    export_path = folder / EXPORT_FILE
    try:
        export = json.loads(export_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise PanelError(f'資料夾裡沒有 {EXPORT_FILE}：{folder}') from None
    except ValueError as e:
        raise PanelError(f'{export_path} 不是有效的 JSON：{e}') from None
    if not isinstance(export, dict) or not isinstance(export.get('products'), list):
        raise PanelError(f'{export_path} 格式不對：要有 products 清單')
    snapshot_path = folder / TRAINING_FILE
    snapshot = None
    if snapshot_path.exists():
        snapshot = training.load(snapshot_path)
    return export, snapshot


# ---------- public (phone-side) API: same base URL, no bot key, paths /p/<token>/… ----------
class PublicClient(ps.Client):
    def __init__(self, api: str, token: str):
        super().__init__(api, '')
        self.token = token
        self.base = f'/p/{urllib.parse.quote(token, safe="")}'

    def headers(self) -> dict[str, str]:
        return {'content-type': 'application/json', 'accept': 'application/json'}

    def pcall(self, method: str, path: str = '', body=None):
        return self.call(method, self.base + path, body)

    def product_view(self, pid: str) -> dict:
        return self.pcall('GET', f'/products/{urllib.parse.quote(pid, safe="")}')


def put_binary(url: str, data: bytes, content_type: str) -> None:
    """PUT a photo to the signed upload URL (what the phone panel does)."""
    req = urllib.request.Request(url, data=data, method='PUT', headers={'content-type': content_type})
    try:
        with urllib.request.urlopen(req, timeout=ps.TIMEOUT * 4) as res:
            res.read()
    except urllib.error.HTTPError as e:
        raise PanelError(f'照片上傳到儲存空間失敗（{e.code}）：{e.read().decode("utf-8", "replace")[:200]}') from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise PanelError(f'照片上傳到儲存空間失敗：{getattr(e, "reason", e)}') from None


# ---------- restore steps ----------
def upload_photos(pub: PublicClient, pid: str, files: list[Path]) -> int:
    for f in files:
        data = f.read_bytes()
        ctype = CONTENT_TYPES[f.suffix.lower()]
        up = pub.pcall('POST', f'/products/{pid}/upload-url', {'name': f.name, 'type': ctype, 'size': len(data)})
        put_binary(up['signed_url'], data, ctype)
        pub.pcall('POST', f'/products/{pid}/photos', {'path': up['path'], 'name': f.name, 'size': len(data)})
    return len(files)


def finish_product(bot: ps.Client, pub: PublicClient, token: str, product: dict, current: dict) -> None:
    """From submitted/generating/ready to finalized: result → state → edited final."""
    pid, sku = current['id'], product.get('sku', '')
    status = current.get('status')
    if status in ('submitted', 'generating'):
        r = bot.call('POST', bot.product_path(token, pid) + '/claim', {'force': True})
        if not r.get('claimed') and r.get('status') not in ('ready',):
            raise PanelError(f'{sku}：領取失敗（狀態 {label(r.get("status"))}）')
    body = {'routes': routes_for_result(product.get('routes') or {}),
            'product_summary': str(product.get('product_summary') or '')}
    r = bot.call('POST', bot.product_path(token, pid) + '/result', body)
    counts = r.get('counts') or {}
    print(f'  已上傳 A/B/C 句子（A {counts.get("A", "?")}、B {counts.get("B", "?")}、C {counts.get("C", "?")} 句）')
    rev = r.get('state_rev')
    if not isinstance(rev, int):
        rev = pub.product_view(pid).get('state_rev')
    state = state_for_restore(product.get('state') or {})
    r = pub.pcall('PUT', f'/products/{pid}/state', {'rev': rev, 'state': state})
    print(f'  已還原挑選內容：♡ {len(state["hearts"])}、✕ {len(state["dislikes"])}、草稿 {len(state["draft"])} 句，'
          f'狀態 {label(r.get("status"))}')
    edited = product.get('edited_final')
    if isinstance(edited, str) and edited.strip():
        if r.get('status') != 'finalized':
            print(f'  注意：{sku} 的匯出檔沒有定稿，修改後最終文案無法寫入（面板只在定稿後接受）', file=sys.stderr)
        else:
            pub.pcall('PUT', f'/products/{pid}/edited-final', {'text': edited})
            print(f'  已寫入修改後最終文案（{len(edited)} 字）')


def restore_product(bot: ps.Client, pub: PublicClient, token: str, product: dict, existing: dict | None,
                    photos_dir: Path, skip_photos: bool) -> str:
    """One product end to end; returns 'done' | 'skipped' | 'waiting' (left as draft, needs photos)."""
    sku = str(product.get('sku') or '').strip()
    if not sku:
        raise PanelError('匯出檔裡有商品沒有 sku')
    files = photo_files(photos_dir / ps.safe_name(sku)) or photo_files(photos_dir / sku)
    if existing is None:
        r = pub.pcall('POST', '/products', {'sku': sku})
        pid = r['id']
        print(f'  已建立商品（id {pid[:8]}）')
        current = {'id': pid, 'status': 'draft', 'photo_count': 0}
    else:
        current = existing
        pid = current['id']
        if current.get('status') == 'finalized':
            print(f'  已在專案裡且已定稿，略過')
            return 'skipped'
        print(f'  已在專案裡（狀態 {label(current.get("status"))}），從這裡接著做')
    if current.get('status') == 'draft':
        pub.pcall('PUT', f'/products/{pid}/fields', {'fields': product.get('fields') or {}})
        print('  已填欄位')
        have = int(current.get('photo_count') or 0)
        if have == 0 and not skip_photos:
            if not files:
                print(f'  {sku} 在 {photos_dir / ps.safe_name(sku)} 沒有照片。面板沒有照片不能送出，這個商品先留在草稿；'
                      f'請在手機面板補上照片後再執行一次本指令（會從送出接著做）。', file=sys.stderr)
                return 'waiting'
            have = upload_photos(pub, pid, files)
            print(f'  已上傳 {have} 張照片')
        if have == 0:
            print(f'  已略過照片（--skip-photos）。面板沒有照片不能送出，{sku} 先留在草稿；'
                  f'請在手機面板補上照片後再執行一次本指令（不加 --skip-photos 或補照片後都可以）。', file=sys.stderr)
            return 'waiting'
        pub.pcall('POST', '/submit', {'product_ids': [pid]})
        print('  已送出（若你的後端設了 webhook，Bot 會收到一次「送出」通知，可忽略）')
        current = {'id': pid, 'status': 'submitted'}
    finish_product(bot, pub, token, product, current)
    return 'done'


def checklist(panel_url: str, token: str, export: dict, results: dict[str, str]) -> str:
    link = f'{panel_url}/p/{token}'
    done = [s for s, r in results.items() if r == 'done']
    waiting = [s for s, r in results.items() if r == 'waiting']
    lines = ['', f'手機連結：{link}', '', '檢查清單：',
             f'  1. 用手機打開連結，專案標題應該是「{export.get("title") or ""}」的轉移版，'
             f'商品 {len(results)} 個。',
             '  2. 每個商品的狀態是「已定稿」，點進去看得到定稿與「修改後最終文案」。' if done else
             '  2. （這次沒有完成任何商品）',
             f'  3. python3 scripts/panel_session.py show {token[:8]}  → 每列的「修改後文案」欄應該是「有」。',
             '  4. 專案目前是「進行中」；確認沒問題後可用 close 結束（連結變唯讀）。']
    if waiting:
        lines.append(f'  5. 還留在草稿、等照片的商品：{"、".join(waiting)}。補上照片後再執行一次同樣的指令。')
    return '\n'.join(lines)


# ---------- main ----------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('transfer', help='轉移資料夾，例如 transfer/2026-11-05-healer')
    parser.add_argument('--title', help=f'新專案標題（預設：匯出檔的標題＋{TITLE_SUFFIX}）')
    parser.add_argument('--project', help='還原到已存在的專案（完整 token 或前 8 碼；token 以 - 開頭時用 --project=TOKEN）')
    parser.add_argument('--training', help='訓練紀錄檔（預設 data/training.json）')
    parser.add_argument('--training-only', action='store_true', help='只合併 training.json，不碰面板')
    parser.add_argument('--no-merge-training', action='store_true', help='不合併 training.json')
    parser.add_argument('--skip-photos', action='store_true', help='不上傳照片（商品會留在草稿，要在手機補照片後再跑一次）')
    args = parser.parse_args(argv)
    if args.training_only and args.no_merge_training:
        parser.error('--training-only 和 --no-merge-training 不能同時用')

    folder = Path(args.transfer).expanduser()
    export, snapshot = load_transfer(folder)
    products = export['products']
    print(f'轉移資料夾：{folder}（專案「{export.get("title") or ""}」，商品 {len(products)} 個，'
          f'匯出時間 {ps.short_time(export.get("exported_at"))}）')

    # 1. training
    if not args.no_merge_training:
        if snapshot is None:
            print(f'資料夾裡沒有 {TRAINING_FILE}，略過合併訓練紀錄。')
        else:
            path = training.record_path(args.training)
            with training.locked(path):
                record = training.load(path)
                added = merge_training(record, snapshot)
                if any(added.values()):
                    training.save(path, record)
            print(f'已合併訓練紀錄到 {path}：新增 ♡ {added["hearts"]} 句、不喜歡 {added["disliked"]} 句、'
                  f'禁用詞 {added["banned"]} 個、定稿 {added["finals"]} 篇、修改後文案 {added["edited"]} 篇、'
                  f'文字偏好 {added["style_notes"]} 條（已存在的不重複加入）')
    if args.training_only:
        return 0

    # 2. panel
    api, key, panel_url = ps.env()
    bot = ps.Client(api, key)
    if args.project:
        token = ps.resolve_project(bot, args.project)
        print(f'使用既有專案 {token[:8]}…')
    else:
        categories = list(dict.fromkeys(ps.official_categories() +
                                        [str((p.get('fields') or {}).get('category') or '').strip()
                                         for p in products if (p.get('fields') or {}).get('category')]))
        title = args.title if args.title is not None else default_title(export)
        r = bot.call('POST', '/bot/projects', {'title': title, 'categories': categories})
        token = r['token']
        print(f'已建立專案「{title}」（{token[:8]}…）')
    pub = PublicClient(api, token)
    overview = pub.pcall('GET')
    if overview.get('status') != 'open':
        raise PanelError(f'專案 {token[:8]} 已結束，不能還原；先 reopen 或換一個專案')
    by_sku = {p.get('sku'): p for p in overview.get('products') or []}

    results: dict[str, str] = {}
    failed: list[str] = []
    for n, product in enumerate(sorted(products, key=lambda p: p.get('position', 0)), 1):
        sku = str(product.get('sku') or '')
        name = (product.get('fields') or {}).get('name') or ''
        print(f'[{n}/{len(products)}] {sku} {name}')
        try:
            results[sku] = restore_product(bot, pub, token, product, by_sku.get(sku), folder / PHOTOS_DIR, args.skip_photos)
        except PanelError as e:
            failed.append(sku)
            print(f'  錯誤：{e}', file=sys.stderr)
            print(f'  {sku} 沒有完成；修好原因後再執行一次同樣的指令會從中斷處接著做。', file=sys.stderr)
    done = sum(1 for r in results.values() if r == 'done')
    skipped = sum(1 for r in results.values() if r == 'skipped')
    waiting = sum(1 for r in results.values() if r == 'waiting')
    print(f'\n完成 {done} 個、略過（已定稿）{skipped} 個、等照片 {waiting} 個、失敗 {len(failed)} 個')
    print(checklist(panel_url, token, export, results))
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except PanelError as e:
        print(f'錯誤：{e}', file=sys.stderr)
        sys.exit(1)
