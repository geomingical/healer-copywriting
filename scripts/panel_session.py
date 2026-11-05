"""Bot-side CLI for the phone panel v2 (panel/): one long-lived project link per 期, many products in it.

Create a project link, see what the users submitted, claim and download products (one by one or all at
once), push the A/B/C sentences, sync the users' picks and edited copies into data/training.json, and export
a closed project so the users' text preferences can be learned.

Environment:
  HEALER_PANEL_API      panel-api base URL, e.g. https://<project>.supabase.co/functions/v1/panel-api
  HEALER_PANEL_BOT_KEY  bot key (sent as header x-bot-key)
  HEALER_PANEL_URL      phone panel URL on Vercel, used to build the link <URL>/p/<token>
Any of the three that is not set in the environment is read from the settings file $HEALER_PANEL_ENV
(default ~/.config/healer-panel/panel.env): KEY=VALUE lines, `export ` prefix, quotes, # comments allowed.
Only these three keys are read from it. A file readable by group/other gets a warning (chmod 600).

PROJECT = full token or a unique prefix (first 8 characters). PRODUCT = sku, product uuid, or a unique
uuid prefix (first 8 characters).

Usage:
  python3 scripts/panel_session.py create [--title 文字]
  python3 scripts/panel_session.py projects [--status open|closed] [--json]
  python3 scripts/panel_session.py show PROJECT [--json]
  python3 scripts/panel_session.py queue [--project PROJECT] [--status submitted,generating] [--json]
  python3 scripts/panel_session.py pull PROJECT PRODUCT [--out DIR] [--force]     # exit 3 = not claimed
  python3 scripts/panel_session.py pull-all [--project PROJECT] [--out DIR]
  python3 scripts/panel_session.py push PROJECT PRODUCT --file output.md [--summary 文字] [--training PATH]
  python3 scripts/panel_session.py sync PROJECT [PRODUCT] [--training PATH]
  python3 scripts/panel_session.py export PROJECT [--out FILE.json] [--md FILE.md]
  python3 scripts/panel_session.py close PROJECT --yes
  python3 scripts/panel_session.py reopen PROJECT
  python3 scripts/panel_session.py config-webhook (--url URL --key KEY | --clear | --show)
  python3 scripts/panel_session.py delete PROJECT
"""
import argparse
import datetime
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import training  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
TIMEOUT = 30
ROUTE_HEADER = re.compile(r'^\s*【路線\s*([ABC])】\s*$')
ANY_HEADER = re.compile(r'^\s*【[^】]*】')
PHOTO_EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
TOKEN_RE = re.compile(r'[A-Za-z0-9_-]{43}')
UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
STATUS_LABEL = {'draft': '草稿', 'submitted': '已送出', 'generating': '撰寫中', 'ready': '可挑句', 'finalized': '已定稿',
                'open': '進行中', 'closed': '已結束'}
EXIT_NOT_CLAIMED = 3


class PanelError(Exception):
    """A problem to report in plain Chinese (exit 1)."""


class NotClaimed(Exception):
    """pull could not claim the product (exit 3)."""


ENV_NAMES = {'HEALER_PANEL_API': '面板後端網址（panel-api）', 'HEALER_PANEL_BOT_KEY': 'Bot 金鑰',
             'HEALER_PANEL_URL': '手機面板網址（Vercel）'}
ENV_FILE_VAR = 'HEALER_PANEL_ENV'
DEFAULT_ENV_FILE = '~/.config/healer-panel/panel.env'
ENV_LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')


def env_file_path() -> Path:
    return Path(os.environ.get(ENV_FILE_VAR, '').strip() or DEFAULT_ENV_FILE).expanduser()


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of the settings file (optional `export `, single/double quotes, # comments, blank lines).
    Only the three HEALER_PANEL_* keys are returned; anything else in the file is ignored. Missing file = {}."""
    try:
        text = path.read_text(encoding='utf-8')
        mode = path.stat().st_mode
    except FileNotFoundError:
        return {}
    except OSError as e:
        print(f'警告：讀不到設定檔 {path}：{e.strerror}', file=sys.stderr)
        return {}
    if mode & 0o077:
        print(f'警告：設定檔 {path} 的權限是 {mode & 0o777:o}，其他人可能讀得到金鑰；請執行 chmod 600 {path}',
              file=sys.stderr)
    values = {}
    for line in text.splitlines():
        m = ENV_LINE.match(line)
        if not m or line.lstrip().startswith('#') or m.group(1) not in ENV_NAMES:
            continue
        value = m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        else:
            value = re.sub(r'\s+#.*$', '', value)   # unquoted: a trailing " # comment" is not part of the value
        values[m.group(1)] = value.strip()
    return values


def env() -> tuple[str, str, str]:
    """The three settings: environment variables first; any that are missing come from the settings file
    ($HEALER_PANEL_ENV, default ~/.config/healer-panel/panel.env)."""
    names = ENV_NAMES
    values = {k: os.environ.get(k, '').strip() for k in names}
    if not all(values.values()):
        from_file = read_env_file(env_file_path())
        values = {k: values[k] or from_file.get(k, '') for k in names}
    missing = [f'{k}（{v}）' for k, v in names.items() if not values[k]]
    if missing:
        print('缺少環境變數：' + '、'.join(missing) +
              f'\n請先設定，例如：export HEALER_PANEL_API=https://…/functions/v1/panel-api'
              f'\n或寫進設定檔 {env_file_path()}（每行 KEY=VALUE，權限 600；也可用 {ENV_FILE_VAR} 指定別的檔案）',
              file=sys.stderr)
        sys.exit(2)
    return values['HEALER_PANEL_API'].rstrip('/'), values['HEALER_PANEL_BOT_KEY'], values['HEALER_PANEL_URL'].rstrip('/')


class Client:
    def __init__(self, api: str, key: str):
        self.api, self.key = api, key

    def headers(self) -> dict[str, str]:
        """Request headers; a subclass without a bot key (the public /p/<token> API) overrides this."""
        return {'x-bot-key': self.key, 'content-type': 'application/json', 'accept': 'application/json'}

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            message = detail.get('message') or raw.decode('utf-8', 'replace')[:200]
            if e.code == 401:
                raise PanelError(f'Bot 金鑰不對（401）：請檢查 HEALER_PANEL_BOT_KEY。{message}') from None
            if e.code == 404:
                raise PanelError(f'找不到（404）：專案或商品不存在、已過期或已刪除。{message}') from None
            if e.code == 409:
                if detail.get('error') == 'project_closed':
                    raise PanelError(f'這期專案已結束（409），不能再修改；要補救可用 reopen。{message}') from None
                raise PanelError(f'狀態不允許這個動作（409）：{message}') from None
            raise PanelError(f'面板後端回錯誤 {e.code}：{message}') from None
        except urllib.error.URLError as e:
            raise PanelError(f'連不上面板後端 {self.api}：{e.reason}。請檢查網路或 HEALER_PANEL_API。') from None
        except TimeoutError:
            raise PanelError(f'面板後端 {TIMEOUT} 秒沒有回應，請稍後再試。') from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise PanelError('面板後端回傳的不是 JSON，請確認 HEALER_PANEL_API 是 panel-api 的網址。') from None

    # paths
    @staticmethod
    def project_path(token: str) -> str:
        return f'/bot/projects/{urllib.parse.quote(token, safe="")}'

    def product_path(self, token: str, pid: str) -> str:
        return f'{self.project_path(token)}/products/{urllib.parse.quote(pid, safe="")}'

    def project(self, token: str) -> dict:
        return self.call('GET', self.project_path(token))

    def product(self, token: str, pid: str) -> dict:
        return self.call('GET', self.product_path(token, pid))


# ---------- argument resolution ----------
def resolve_project(client: Client, ref: str) -> str:
    """A full 43-character token is used as is; a shorter prefix must match exactly one project."""
    ref = ref.strip()
    if TOKEN_RE.fullmatch(ref):
        return ref
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,42}', ref):
        raise PanelError(f'專案代碼格式不對：{ref!r}（請給完整 token 或前 8 碼）')
    tokens = {}
    for status in ('open', 'closed'):
        for p in client.call('GET', f'/bot/projects?status={status}') or []:
            tokens[p['token']] = p
    hits = [t for t in tokens if t.startswith(ref)]
    if not hits:
        raise PanelError(f'找不到開頭是 {ref} 的專案（用 projects 看清單）')
    if len(hits) > 1:
        raise PanelError(f'開頭是 {ref} 的專案有 {len(hits)} 個：{"、".join(h[:12] + "…" for h in hits)}，請多給幾碼')
    return hits[0]


def resolve_product(project: dict, ref: str) -> dict:
    """sku first, then the full uuid, then a unique uuid prefix (≥ 8 characters)."""
    products = project.get('products') or []
    ref = ref.strip()
    for p in products:
        if p.get('sku') == ref:
            return p
    low = ref.lower()
    for p in products:
        if str(p.get('id', '')).lower() == low:
            return p
    if len(low) >= 8 and re.fullmatch(r'[0-9a-f-]+', low):
        hits = [p for p in products if str(p.get('id', '')).lower().startswith(low)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise PanelError(f'開頭是 {ref} 的商品有 {len(hits)} 個，請給完整的商品編號或 uuid')
    skus = '、'.join(p.get('sku', '?') for p in products) or '（沒有商品）'
    raise PanelError(f'專案 {project.get("token", "")[:8]} 裡找不到商品 {ref}。現有商品編號：{skus}')


# ---------- small helpers ----------
def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=1))


def label(status: str | None) -> str:
    return f'{STATUS_LABEL.get(status or "", status or "")}（{status}）' if status else ''


def official_categories() -> list[str]:
    labels = json.loads((HERE / 'data/categories.json').read_text(encoding='utf-8'))['labels']
    return list(dict.fromkeys(labels.values()))


def today_taipei() -> str:
    return f'{datetime.datetime.now(TAIPEI):%Y-%m-%d}'


def safe_name(sku: str) -> str:
    """sku as a folder name: anything but letters/digits/CJK/-_. becomes _; never empty, '.' or '..'."""
    name = re.sub(r'[^\w.\-]', '_', sku.strip()).strip('.')
    return name or '_'


def default_project_dir(token: str) -> Path:
    return HERE / 'runs' / f'{today_taipei()}-{token[:8]}'


def default_batch_dir() -> Path:
    return HERE / 'runs' / f'{today_taipei()}-batch'


def table(rows: list[list[str]], header: list[str]) -> str:
    def width(s: str) -> int:
        return sum(2 if ord(c) > 0x2e7f else 1 for c in s)
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(width(r[i]) for r in [header] + rows) for i in range(len(header))]
    fmt = lambda r: '  '.join(c + ' ' * (widths[i] - width(c)) for i, c in enumerate(r)).rstrip()  # noqa: E731
    return '\n'.join([fmt(header), fmt(['-' * w for w in widths])] + [fmt(r) for r in rows])


def short_time(value: str | None) -> str:
    """ISO time → 'YYYY-MM-DD HH:MM' in Asia/Taipei; anything unparsable is shown as is."""
    if not value:
        return ''
    try:
        t = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return f'{t.astimezone(TAIPEI):%Y-%m-%d %H:%M}'


def download(url: str, dest_stem: Path) -> Path:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT * 2) as res:
            ctype = res.headers.get_content_type()
            data = res.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise PanelError(f'照片下載失敗：{getattr(e, "reason", e)}') from None
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in ('.jpg', '.jpeg', '.png', '.webp'):
        suffix = PHOTO_EXT.get(ctype, '.jpg')
    dest = dest_stem.with_suffix(suffix)
    dest.write_bytes(data)
    return dest


# ---------- list commands ----------
def cmd_projects(client: Client, status: str | None, as_json: bool) -> None:
    items = client.call('GET', '/bot/projects' + (f'?status={status}' if status else '')) or []
    if as_json:
        print_json(items)
        return
    if not items:
        print('沒有專案。')
        return
    keys = ('draft', 'submitted', 'generating', 'ready', 'finalized')
    rows = [[p['token'][:8], STATUS_LABEL.get(p.get('status'), p.get('status')), p.get('title') or '',
             *[(p.get('counts') or {}).get(k, 0) for k in keys], short_time(p.get('created_at')), short_time(p.get('closed_at'))]
            for p in items]
    print(table(rows, ['專案', '狀態', '標題', *[STATUS_LABEL[k] for k in keys], '建立', '結束']))


def cmd_show(client: Client, token: str, as_json: bool) -> None:
    p = client.project(token)
    if as_json:
        print_json(p)
        return
    print(f'專案 {p.get("token", token)}  {p.get("title") or "（無標題）"}  {label(p.get("status"))}')
    if p.get('closed_at'):
        print(f'結束於 {short_time(p["closed_at"])}，結束通知：{p.get("close_notify_status") or "—"}')
    products = p.get('products') or []
    if not products:
        print('（還沒有商品）')
        return
    rows = [[x.get('sku', ''), x.get('name') or '', STATUS_LABEL.get(x.get('status'), x.get('status')),
             x.get('photo_count', 0), '有' if x.get('has_edited_final') else '', short_time(x.get('submitted_at')),
             short_time(x.get('claimed_at')), short_time(x.get('synced_at')), str(x.get('id', ''))[:8]]
            for x in sorted(products, key=lambda x: x.get('position', 0))]
    print(table(rows, ['商品編號', '名稱', '狀態', '照片', '修改後文案', '送出', '領取', '同步', 'id']))


def queue_items(client: Client, project: str | None, status: str) -> list[dict]:
    query = {'status': status}
    if project:
        query['project'] = project
    return client.call('GET', '/bot/queue?' + urllib.parse.urlencode(query, safe=',')) or []


def cmd_queue(client: Client, project: str | None, status: str, as_json: bool) -> None:
    items = queue_items(client, project, status)
    if as_json:
        print_json(items)
        return
    if not items:
        print(f'沒有待處理的商品（狀態 {status}）。')
        return
    rows = [[x['project_token'][:8], x.get('project_title') or '', x.get('sku', ''), x.get('name') or '',
             STATUS_LABEL.get(x.get('status'), x.get('status')), short_time(x.get('submitted_at')),
             short_time(x.get('claimed_at'))] for x in items]
    print(table(rows, ['專案', '專案標題', '商品編號', '名稱', '狀態', '送出', '領取']))
    print(f'共 {len(items)} 個。全部領取：python3 scripts/panel_session.py pull-all')


# ---------- pull ----------
def claim(client: Client, token: str, pid: str, sku: str, force: bool) -> dict:
    r = client.call('POST', client.product_path(token, pid) + '/claim', {'force': bool(force)})
    if r.get('claimed'):
        return r
    status = r.get('status')
    if status == 'generating':
        raise NotClaimed(f'{sku}：已被其他流程領走（{short_time(r.get("claimed_at")) or "時間不明"}）；'
                         '若確定要重做加 --force')
    if status == 'draft':
        raise NotClaimed(f'{sku}：使用者還沒送出這個商品（狀態 {label(status)}），現在不能 pull。')
    raise NotClaimed(f'{sku}：目前狀態是 {label(status)}，不需要再 pull。')


def download_product(client: Client, token: str, pid: str, folder: Path) -> tuple[Path, int]:
    s = client.product(token, pid)
    photos = sorted(s.get('photos') or [], key=lambda p: p.get('order', 0))
    if not photos:
        raise PanelError(f'{s.get("sku", pid)} 沒有照片。')
    photos_dir = folder / 'input' / 'photos'
    photos_dir.mkdir(parents=True, exist_ok=True)
    for old in photos_dir.iterdir():   # re-pull replaces the photos instead of mixing two sets
        if old.is_file():
            old.unlink()
    for n, p in enumerate(photos, 1):
        download(p['url'], photos_dir / f'{n:02d}')
    f = s.get('fields') or {}
    spec = {'id': s.get('sku') or pid, 'name': f.get('name', ''), 'category': f.get('category', ''),
            'colors': f.get('colors') or [], 'material': f.get('material', ''),
            'sizes': f.get('sizes', ''), 'price': f.get('price', ''),
            'background': f.get('background', ''), 'feelings': f.get('feelings', ''), 'photos': 'photos'}
    spec_path = folder / 'input' / 'new-product.json'
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return spec_path, len(photos)


def next_steps(token: str, sku: str, folder: Path) -> list[str]:
    return [f'python3 scripts/prepare.py --new {folder / "input/new-product.json"} --output {folder / "packet"}',
            f'（寫好 A/B/C 三版後）python3 scripts/panel_session.py push {token[:8]} {sku} --file {folder / "output.md"}']


def cmd_pull(client: Client, token: str, ref: str, out: str | None, force: bool) -> None:
    product = resolve_product(client.project(token), ref)
    sku, pid = product['sku'], product['id']
    r = claim(client, token, pid, sku, force)
    folder = Path(out).expanduser() if out else default_project_dir(token) / safe_name(sku)
    try:
        spec_path, count = download_product(client, token, pid, folder)
    except PanelError as e:
        raise PanelError(f'{e}（商品已標成撰寫中；修好後用 --force 重新 pull）') from None
    print(f'已領取 {sku}（{label(r.get("status"))}，{short_time(r.get("claimed_at"))}）。'
          f'已下載 {count} 張照片到 {folder / "input/photos"}')
    print(f'描述檔：{spec_path}')
    print('下一步：')
    for line in next_steps(token, sku, folder):
        print(f'  {line}')


def cmd_pull_all(client: Client, project: str | None, out: str | None) -> None:
    items = queue_items(client, project, 'submitted')
    if not items:
        print('沒有待處理的商品（狀態 submitted）。')
        return
    base = Path(out).expanduser() if out else default_batch_dir()
    used: set[str] = set()
    pulled, skipped = [], []
    for x in items:
        token, pid, sku = x['project_token'], x['product_id'], x.get('sku') or x['product_id'][:8]
        name = safe_name(sku)
        if name in used:   # the same sku in two projects: keep both folders apart
            name = f'{name}-{token[:8]}'
        try:
            claim(client, token, pid, sku, force=False)
        except NotClaimed as e:
            skipped.append(str(e))
            continue
        folder = base / name
        try:
            download_product(client, token, pid, folder)
        except PanelError as e:
            skipped.append(f'{sku}：{e}（商品已標成撰寫中；之後用 pull … --force 重新下載）')
            continue
        used.add(name)
        pulled.append((token, sku, folder))
    for line in skipped:
        print(f'略過 {line}', file=sys.stderr)
    if not pulled:
        print('這次沒有領到任何商品。')
        return
    print(f'已領取 {len(pulled)} 個商品，放在 {base}：')
    rows = [[t[:8], sku, str(folder)] for t, sku, folder in pulled]
    print(table(rows, ['專案', '商品編號', '資料夾']))
    print('\n下一步（每個商品各做一次）：')
    for token, sku, folder in pulled:
        print(f'# {sku}')
        for line in next_steps(token, sku, folder):
            print(f'  {line}')


# ---------- push ----------
def parse_output(text: str) -> dict[str, list[dict]]:
    """【路線A】【路線B】【路線C】 sections, one sentence per non-empty line; [bracketed] lines are check=true."""
    routes: dict[str, list[dict]] = {}
    current = None
    for line in text.splitlines():
        header = ROUTE_HEADER.match(line)
        if header:
            current = header.group(1)
            routes.setdefault(current, [])
            continue
        if ANY_HEADER.match(line):
            current = None
            continue
        line = line.strip()
        if current and line and not re.fullmatch(r'[-—=*_]{3,}', line):
            routes[current].append({'text': line, 'check': line.startswith('[') and line.endswith(']')})
    missing = [r for r in 'ABC' if not routes.get(r)]
    if missing:
        raise PanelError(f'找不到這些段落或段落是空的：{"、".join(f"【路線{r}】" for r in missing)}')
    return routes


def filter_routes(routes: dict[str, list[dict]], record: dict) -> tuple[dict, list[str]]:
    disliked = {d['text'].strip() for d in record['disliked']}
    banned = [w for w in record['banned'] if w]
    kept, dropped = {}, []
    for route, items in routes.items():
        kept[route] = []
        for item in items:
            text = item['text'].strip()
            word = next((w for w in banned if w in text), None)
            if text in disliked:
                dropped.append(f'路線{route}（不喜歡過）：{text}')
            elif word:
                dropped.append(f'路線{route}（禁用詞「{word}」）：{text}')
            else:
                kept[route].append(item)
    return kept, dropped


def product_summary(text: str) -> str:
    """The 【目標商品】 paragraph of output.md, if any."""
    m = re.search(r'【目標商品】(.*?)(?=\n\s*【|\Z)', text, re.S)
    return m.group(1).strip()[:500] if m else ''


def cmd_push(client: Client, token: str, ref: str, file: str, summary: str | None, training_path: Path) -> None:
    try:
        text = Path(file).expanduser().read_text(encoding='utf-8')
    except OSError as e:
        raise PanelError(f'讀不到 {file}：{e.strerror}') from None
    routes, dropped = filter_routes(parse_output(text), training.load(training_path))
    product = resolve_product(client.project(token), ref)
    for line in dropped:
        print(f'已過濾 {line}', file=sys.stderr)
    empty = [r for r in 'ABC' if not routes[r]]
    if empty:
        print(f'注意：過濾後 {"、".join(empty)} 路線沒有句子了', file=sys.stderr)
    body = {'routes': routes, 'product_summary': summary if summary is not None else product_summary(text)}
    result = client.call('POST', client.product_path(token, product['id']) + '/result', body)
    counts = '、'.join(f'{r} {len(routes[r])} 句' for r in 'ABC')
    print(f'已上傳 {product["sku"]} 的句子（{counts}），狀態：{result.get("status", "ready")}。使用者重新整理面板就會看到。')


# ---------- sync ----------
def apply_product(record: dict, s: dict) -> dict:
    """Add one product's picks to the record (product_id = sku); returns the counts that changed."""
    state = s.get('state') or {}
    routes = (s.get('result') or {}).get('routes') or {}
    original = {x['id']: x['text'] for items in routes.values() for x in items}
    draft = {d.get('origin') or d.get('id'): d.get('text', '') for d in state.get('draft') or [] if isinstance(d, dict)}
    pid = s.get('sku') or s.get('id')
    name = (s.get('fields') or {}).get('name') or pid
    added = {'hearts': 0, 'disliked': 0, 'banned': 0, 'finals': 0, 'edited': 0}
    for sid in state.get('hearts') or []:
        text = (draft.get(sid) or original.get(sid) or '').strip()
        if text:
            added['hearts'] += training.add_sentence(record, 'hearts', text, sid[:1] or None, pid)
    for sid in state.get('dislikes') or []:
        text = (original.get(sid) or '').strip()
        if text:
            added['disliked'] += training.add_sentence(record, 'disliked', text, sid[:1] or None, pid)
    for word in state.get('banned_words') or []:
        if isinstance(word, str) and word.strip():
            added['banned'] += training.add_banned(record, word)
    final = state.get('final')
    if final and str(final.get('text') or '').strip():
        text = final['text'].strip()
        added['finals'] += training.add_final(record, pid, name, text, replace=True)
        entry = next(f for f in reversed(record['finals']) if f.get('product_id') == pid and f['text'].strip() == text)
        added['edited'] += training.set_edited_text(entry, s.get('edited_final') or '')
    return added


def cmd_sync(client: Client, token: str, ref: str | None, training_path: Path) -> None:
    project = client.project(token)
    if ref:
        targets = [resolve_product(project, ref)]
    else:
        targets = [p for p in project.get('products') or [] if p.get('status') in ('ready', 'finalized')]
        if not targets:
            print('這個專案沒有可同步的商品（狀態要是可挑句或已定稿）。')
            return
    details = [client.product(token, p['id']) for p in targets]
    total = {'hearts': 0, 'disliked': 0, 'banned': 0, 'finals': 0, 'edited': 0}
    lines = []
    with training.locked(training_path):
        record = training.load(training_path)
        for s in details:
            added = apply_product(record, s)
            for k, v in added.items():
                total[k] += v
            note = '' if s.get('status') == 'finalized' else f'（還沒定稿，狀態 {label(s.get("status"))}，之後定稿可再 sync）'
            lines.append(f'  {s.get("sku")}：♡ {added["hearts"]}、不喜歡 {added["disliked"]}、禁用詞 {added["banned"]}、'
                         f'定稿 {added["finals"]}、修改後文案 {added["edited"]}{note}')
        if any(total.values()):
            training.save(training_path, record)
    for s in details:
        client.call('POST', client.product_path(token, s['id']) + '/synced')
    print(f'已同步 {len(details)} 個商品到 {training_path}：♡ {total["hearts"]} 句、不喜歡 {total["disliked"]} 句、'
          f'禁用詞 {total["banned"]} 個、定稿 {total["finals"]} 篇、修改後文案 {total["edited"]} 篇（已存在的不重複加入）')
    print('\n'.join(lines))


# ---------- export ----------
def line_diff(before: str, after: str) -> list[str]:
    """Line-by-line difference of the final vs. the user's edited copy: 保留／改寫／刪除／新增."""
    a = [l.strip() for l in before.splitlines() if l.strip()]
    b = [l.strip() for l in after.splitlines() if l.strip()]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            out += [f'= 保留：{x}' for x in a[i1:i2]]
        elif op == 'delete':
            out += [f'- 刪除：{x}' for x in a[i1:i2]]
        elif op == 'insert':
            out += [f'+ 新增：{x}' for x in b[j1:j2]]
        else:
            old, new = a[i1:i2], b[j1:j2]
            for k in range(max(len(old), len(new))):
                if k < len(old) and k < len(new):
                    out.append(f'~ 改寫：{old[k]}\n    → {new[k]}')
                elif k < len(old):
                    out.append(f'- 刪除：{old[k]}')
                else:
                    out.append(f'+ 新增：{new[k]}')
    return out


def fence(text: str) -> str:
    return f'```text\n{text.rstrip()}\n```'


def export_markdown(data: dict) -> str:
    products = sorted(data.get('products') or [], key=lambda p: p.get('position', 0))
    edited_count = sum(1 for p in products if (p.get('edited_final') or '').strip())
    out = [f'# 專案匯出：{data.get("title") or "（無標題）"}（{str(data.get("token", ""))[:8]}）', '',
           f'- 狀態：{label(data.get("status"))}' + (f'，結束於 {short_time(data.get("closed_at"))}' if data.get('closed_at') else ''),
           f'- 匯出時間：{short_time(data.get("exported_at"))}',
           f'- 商品 {len(products)} 個，其中 {edited_count} 個有「使用者修改後的最終文案」', '',
           '讀法：每個商品依序是 AI 產生版（三路線全部句子）→ 使用者挑選的草稿 → 定稿 → 使用者修改後的最終文案 → '
           '定稿與修改後文案的逐行差異（= 保留、~ 改寫、- 刪除、+ 新增）。歸納文字偏好時，看的是「使用者怎麼改」，不是商品內容。']
    for n, p in enumerate(products, 1):
        fields = p.get('fields') or {}
        state = p.get('state') or {}
        routes = p.get('routes') or {}
        original = {x.get('id'): x.get('text', '') for items in routes.values() for x in items or []}
        out += ['', f'## {n}. {p.get("sku", "")}：{fields.get("name") or "（未命名）"}', '',
                f'狀態：{label(p.get("status"))}' + (f'；類別：{fields["category"]}' if fields.get('category') else '')]
        if p.get('product_summary'):
            out.append(f'商品摘要：{p["product_summary"]}')
        out += ['', '### AI 產生版（三路線全部句子）']
        if routes:
            for r in sorted(routes):
                out.append(f'#### 路線{r}')
                out += [f'- {x.get("id", "")} {x.get("text", "")}' + ('　⚠需確認' if x.get('check') else '') for x in routes[r] or []]
        else:
            out.append('（還沒有產生句子）')
        hearts, dislikes, banned = state.get('hearts') or [], state.get('dislikes') or [], state.get('banned_words') or []
        if hearts or dislikes or banned:
            out += ['', f'♡：{"、".join(hearts) or "—"}；✕ 不喜歡：{"、".join(dislikes) or "—"}；禁用詞：{"、".join(banned) or "—"}']
        out += ['', '### 使用者挑選的草稿（依使用者排序）']
        draft = [d for d in state.get('draft') or [] if isinstance(d, dict)]
        if draft:
            for k, d in enumerate(draft, 1):
                origin = d.get('origin') or d.get('id')
                text = d.get('text', '')
                if origin in original and original[origin].strip() != text.strip():
                    mark = f'（來自 {origin}，有改字；原句：{original[origin]}）'
                elif origin in original:
                    mark = f'（來自 {origin}，未改字）'
                else:
                    mark = '（使用者自己加的句子）'
                out.append(f'{k}. {text} {mark}')
        else:
            out.append('（沒有草稿）')
        final = (state.get('final') or {}).get('text') or ''
        edited = (p.get('edited_final') or '').strip()
        out += ['', '### 定稿', fence(final) if final.strip() else '（還沒定稿）',
                '', '### 使用者修改後的最終文案' + (f'（{short_time(p.get("edited_at"))}）' if edited and p.get('edited_at') else ''),
                fence(edited) if edited else '（沒有提供）']
        if edited and final.strip():
            diff = line_diff(final, edited)
            out += ['', '### 差異（定稿 → 修改後最終文案，逐行）']
            out += diff if any(not d.startswith('= ') for d in diff) else ['（內容與定稿相同，沒有修改）']
    return '\n'.join(out) + '\n'


def cmd_export(client: Client, token: str, out: str | None, md: str | None) -> None:
    data = client.call('GET', client.project_path(token) + '/export')
    json_path = Path(out).expanduser() if out else default_project_dir(token) / 'export.json'
    md_path = Path(md).expanduser() if md else json_path.with_suffix('.md')
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    md_path.write_text(export_markdown(data), encoding='utf-8')
    products = data.get('products') or []
    edited = sum(1 for p in products if (p.get('edited_final') or '').strip())
    print(f'已匯出 {len(products)} 個商品（{edited} 個有修改後文案）：\n  {json_path}\n  {md_path}')
    if data.get('status') != 'closed':
        print('注意：這期專案還沒結束，使用者之後可能還會修改。')


# ---------- main ----------
GUARD = '\x01'


def parse_args(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    """Tokens are base64url and may start with '-'; argparse would read '-AbC1234' as an option. Any argument that
    starts with '-' but is not one of our options is passed through as a value (guarded, then unguarded)."""
    known = {'-h'}
    for action in parser._actions + [a for sp in parser._subparsers._group_actions for p in sp.choices.values()
                                     for a in p._actions]:
        known.update(action.option_strings)
    guarded = [GUARD + a if a.startswith('-') and len(a) >= 4 and a.split('=')[0] not in known else a for a in argv]
    args = parser.parse_args(guarded)
    for k, v in vars(args).items():
        if isinstance(v, str) and v.startswith(GUARD):
            setattr(args, k, v[len(GUARD):])
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help='建立新專案（一期），印出手機連結')
    create.add_argument('--title', default='', help='專案標題（選填）')
    projects = sub.add_parser('projects', help='專案清單與各狀態數量')
    projects.add_argument('--status', choices=['open', 'closed'])
    projects.add_argument('--json', action='store_true')
    show = sub.add_parser('show', help='看專案內所有商品與狀態')
    show.add_argument('project')
    show.add_argument('--json', action='store_true')
    queue = sub.add_parser('queue', help='跨專案的待處理商品')
    queue.add_argument('--project')
    queue.add_argument('--status', default='submitted', help='submitted（預設）、generating 或 submitted,generating')
    queue.add_argument('--json', action='store_true')
    pull = sub.add_parser('pull', help='領取一個商品並下載照片與欄位（沒領到 exit 3）')
    pull.add_argument('project')
    pull.add_argument('product', help='商品編號（sku）或 uuid（前 8 碼也可以）')
    pull.add_argument('--out', help='輸出資料夾（預設 runs/<日期>-<專案前8碼>/<sku>）')
    pull.add_argument('--force', action='store_true', help='已被領走（撰寫中）時強制重領')
    pull_all = sub.add_parser('pull-all', help='領取全部已送出的商品（有空閒就一起處理）')
    pull_all.add_argument('--project')
    pull_all.add_argument('--out', help='輸出資料夾（預設 runs/<日期>-batch），每個商品放 <DIR>/<sku>/')
    push = sub.add_parser('push', help='上傳 A/B/C 句子')
    push.add_argument('project')
    push.add_argument('product')
    push.add_argument('--file', required=True, help='含【路線A】【路線B】【路線C】的 output.md')
    push.add_argument('--summary', help='商品摘要（預設取 output.md 的【目標商品】段）')
    push.add_argument('--training', help='訓練紀錄檔（預設 data/training.json）')
    sync = sub.add_parser('sync', help='把使用者的挑選與修改後文案同步進訓練紀錄')
    sync.add_argument('project')
    sync.add_argument('product', nargs='?', help='省略＝專案內所有可挑句／已定稿的商品')
    sync.add_argument('--training', help='訓練紀錄檔（預設 data/training.json）')
    export = sub.add_parser('export', help='匯出整個專案（JSON＋給 Grok 讀的 Markdown）')
    export.add_argument('project')
    export.add_argument('--out', help='JSON 檔（預設 runs/<日期>-<專案前8碼>/export.json）')
    export.add_argument('--md', help='Markdown 檔（預設與 JSON 同名 .md）')
    close = sub.add_parser('close', help='結束這期專案（連結變唯讀）')
    close.add_argument('project')
    close.add_argument('--yes', action='store_true', help='確認要結束')
    sub.add_parser('reopen', help='重新開啟已結束的專案（測試／補救用）').add_argument('project')
    hook = sub.add_parser('config-webhook', help='設定送出／結束時通知 Bot 的 webhook')
    group = hook.add_mutually_exclusive_group(required=True)
    group.add_argument('--url')
    group.add_argument('--clear', action='store_true')
    group.add_argument('--show', action='store_true')
    hook.add_argument('--key', help='webhook 的 Bearer 金鑰（搭配 --url）')
    sub.add_parser('delete', help='刪除整個專案、全部商品與照片').add_argument('project')
    args = parse_args(parser, sys.argv[1:])
    if args.command == 'config-webhook' and args.url and not args.key:
        parser.error('--url 要搭配 --key')
    if args.command == 'close' and not args.yes:
        parser.error('結束後連結會變唯讀、使用者不能再修改；確定的話加 --yes')
    if args.command == 'queue':
        bad = [s for s in args.status.split(',') if s.strip() not in ('submitted', 'generating')]
        if bad or not args.status.strip():
            parser.error('--status 只能是 submitted、generating 或 submitted,generating')

    api, key, panel_url = env()
    client = Client(api, key)
    try:
        project = resolve_project(client, args.project) if getattr(args, 'project', None) else None
        if args.command == 'create':
            token = client.call('POST', '/bot/projects', {'title': args.title, 'categories': official_categories()})['token']
            print_json({'token': token, 'link': f'{panel_url}/p/{token}'})
        elif args.command == 'projects':
            cmd_projects(client, args.status, args.json)
        elif args.command == 'show':
            cmd_show(client, project, args.json)
        elif args.command == 'queue':
            cmd_queue(client, project, args.status.replace(' ', ''), args.json)
        elif args.command == 'pull':
            cmd_pull(client, project, args.product, args.out, args.force)
        elif args.command == 'pull-all':
            cmd_pull_all(client, project, args.out)
        elif args.command == 'push':
            cmd_push(client, project, args.product, args.file, args.summary, training.record_path(args.training))
        elif args.command == 'sync':
            cmd_sync(client, project, args.product, training.record_path(args.training))
        elif args.command == 'export':
            cmd_export(client, project, args.out, args.md)
        elif args.command == 'close':
            r = client.call('POST', client.project_path(project) + '/close')
            print(f'已結束專案 {project[:8]}（{label(r.get("status", "closed"))}），連結現在是唯讀總覽頁。'
                  f'結束通知：{r.get("close_notify_status") or "—"}')
            print(f'下一步：python3 scripts/panel_session.py sync {project[:8]}，再 export {project[:8]}')
        elif args.command == 'reopen':
            r = client.call('POST', client.project_path(project) + '/reopen')
            print(f'已重新開啟專案 {project[:8]}（{label(r.get("status", "open"))}）')
        elif args.command == 'config-webhook':
            if args.show:
                print_json(client.call('GET', '/bot/config'))
            elif args.clear:
                print_json(client.call('PUT', '/bot/config', {'clear_webhook': True}))
            else:
                print_json(client.call('PUT', '/bot/config', {'webhook_url': args.url, 'webhook_key': args.key}))
        elif args.command == 'delete':
            client.call('DELETE', client.project_path(project))
            print(f'已刪除專案 {project}')
    except NotClaimed as e:
        print(str(e), file=sys.stderr)
        sys.exit(EXIT_NOT_CLAIMED)
    except PanelError as e:
        print(f'錯誤：{e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
