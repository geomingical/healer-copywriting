"""Tests for scripts/panel_session.py (v2: projects + products) against an in-process fake panel-api.

  python3 -m unittest scripts/test_panel_session.py -v

FakePanel below implements the bot-side routes of spec2 in memory (ThreadingHTTPServer on a free port),
plus /storage/<path> for the signed photo links. The CLI runs as a real subprocess against it.
Training records are written to a temp folder only; data/training.json is never touched.
"""
import datetime
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
CLI = SCRIPTS / 'panel_session.py'
BOT_KEY = 'test-bot-key'
sys.path.insert(0, str(SCRIPTS))
import panel_session  # noqa: E402

OUTPUT_MD = """【目標商品】白色 V 領短洋裝，腰間抽繩。

【照片細節】
- V 領（01）

【路線A】
夏天熱賣款改版 這次補了芥末黃新色！
V領配上胸前抓皺 女人味剛剛好
[整件輕盈不悶熱 夏天穿一整天也舒服]
這句是之前被按過不喜歡的

【路線B】
腰間抽繩 鬆緊自己調！
這件超級好穿

【路線C】
這個夏天想要有度假感 就選這件
白色搭草編包和涼鞋 海邊散步剛剛好
"""
FIELDS = {'name': '測試短洋裝', 'category': '洋裝', 'colors': ['白', '芥末黃'], 'material': '✦材質成分：100%棉',
          'sizes': 'S、M', 'price': '1280', 'background': '補貨', 'feelings': '軟軟的'}


def jpeg_bytes(color=(200, 80, 60)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (60, 80), color).save(buf, 'JPEG')
    return buf.getvalue()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FakePanel:
    """In-memory v2 panel-api (bot routes only) — field names and shapes follow spec2.md."""

    def __init__(self):
        self.projects: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.config = {'webhook_url': None, 'webhook_key': None}
        self.log: list[tuple[str, str, object]] = []
        self.lock = threading.Lock()
        self.on_queue = None   # test hook: called after /bot/queue has built its answer
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header('content-type', ctype)
                self.send_header('content-length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _any(self):
                url = urllib.parse.urlparse(self.path)
                length = int(self.headers.get('content-length') or 0)
                raw = self.rfile.read(length) if length else b''
                body = json.loads(raw) if raw else None
                if url.path.startswith('/storage/'):
                    data = panel.files.get(urllib.parse.unquote(url.path[len('/storage/'):]))
                    return self._send(200, data, 'image/jpeg') if data else self._send(404, {'error': 'not_found'})
                path = url.path.removeprefix('/functions/v1/panel-api')
                panel.log.append((self.command, path + ('?' + url.query if url.query else ''), body))
                if self.headers.get('x-bot-key') != BOT_KEY:
                    return self._send(401, {'error': 'unauthorized', 'message': 'bad bot key'})
                with panel.lock:
                    code, out = panel.route(self.command, path, urllib.parse.parse_qs(url.query), body or {})
                self._send(code, out)

            do_GET = do_POST = do_PUT = do_DELETE = _any

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.api = self.base + '/functions/v1/panel-api'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # ---- test helpers (direct state changes = what the user does on the phone) ----
    def add_project(self, title='', status='open') -> str:
        token = secrets.token_urlsafe(32)
        self.projects[token] = {'token': token, 'title': title, 'status': status, 'options': {'categories': ['洋裝']},
                                'created_at': now(), 'closed_at': None, 'close_notify_status': None, 'products': []}
        return token

    def add_product(self, token, sku, status='submitted', fields=None, photos=((10, 20, 30), (200, 210, 220))) -> dict:
        project = self.projects[token]
        pid = str(uuid.uuid4())
        items = []
        for n, color in enumerate(photos):
            path = f'{token}/{pid}/{n}-{secrets.token_hex(3)}.jpg'
            self.files[path] = jpeg_bytes(color)
            items.append({'path': path, 'name': f'p{n}.jpg', 'size': len(self.files[path]), 'order': n})
        p = {'id': pid, 'sku': sku, 'position': len(project['products']), 'status': status,
             'fields': dict(FIELDS if fields is None else fields), 'photos': items, 'result': None, 'state': {},
             'state_rev': 0, 'edited_final': None, 'edited_at': None, 'notify_status': 'unset',
             'submitted_at': now() if status != 'draft' else None, 'claimed_at': None, 'synced_at': None,
             'created_at': now(), 'updated_at': now()}
        project['products'].append(p)
        return p

    def find(self, token, sku) -> dict:
        return next(p for p in self.projects[token]['products'] if p['sku'] == sku)

    def pick(self, token, sku, state, edited=None):
        p = self.find(token, sku)
        p['state'] = state
        p['state_rev'] += 1
        p['status'] = 'finalized' if state.get('final') else 'ready'
        if edited is not None:
            p['edited_final'], p['edited_at'] = edited or None, now()

    # ---- routes ----
    def summary(self, p):
        return {'id': p['id'], 'sku': p['sku'], 'name': p['fields'].get('name'), 'status': p['status'],
                'position': p['position'], 'photo_count': len(p['photos']), 'submitted_at': p['submitted_at'],
                'claimed_at': p['claimed_at'], 'synced_at': p['synced_at'], 'notify_status': p['notify_status'],
                'has_edited_final': bool(p['edited_final']), 'updated_at': p['updated_at']}

    def detail(self, project, p):
        return {'id': p['id'], 'sku': p['sku'], 'status': p['status'], 'fields': p['fields'],
                'photos': [dict(x, url=f'{self.base}/storage/{x["path"]}') for x in p['photos']],
                'result': p['result'], 'state': p['state'], 'state_rev': p['state_rev'],
                'edited_final': p['edited_final'], 'edited_at': p['edited_at'], 'updated_at': p['updated_at'],
                'server_time': now(), 'project_status': project['status'], 'notify_status': p['notify_status'],
                'synced_at': p['synced_at'], 'claimed_at': p['claimed_at'], 'submitted_at': p['submitted_at']}

    def counts(self, project):
        c = dict.fromkeys(('draft', 'submitted', 'generating', 'ready', 'finalized'), 0)
        for p in project['products']:
            c[p['status']] += 1
        return c

    def route(self, method, path, query, body):
        err = lambda code, e, m='': (code, {'error': e, 'message': m or e})  # noqa: E731
        parts = [urllib.parse.unquote(x) for x in path.strip('/').split('/')]
        if parts == ['bot', 'config']:
            if method == 'PUT':
                if body.get('clear_webhook'):
                    self.config = {'webhook_url': None, 'webhook_key': None}
                else:
                    self.config.update({k: body[k] for k in ('webhook_url', 'webhook_key') if k in body})
            return 200, {'webhook_url': self.config['webhook_url'], 'webhook_key_set': bool(self.config['webhook_key'])}
        if parts == ['bot', 'projects']:
            if method == 'POST':
                token = self.add_project(body.get('title') or '')
                self.projects[token]['options'] = {'categories': body.get('categories') or []}
                return 200, {'token': token}
            status = (query.get('status') or [None])[0]
            return 200, [{'token': t, 'title': x['title'], 'status': x['status'], 'created_at': x['created_at'],
                          'closed_at': x['closed_at'], 'counts': self.counts(x)}
                         for t, x in self.projects.items() if status in (None, x['status'])]
        if parts == ['bot', 'queue']:
            statuses = (query.get('status') or ['submitted'])[0].split(',')
            only = (query.get('project') or [None])[0]
            items = [{'project_token': t, 'project_title': x['title'], 'project_status': x['status'],
                      'product_id': p['id'], 'sku': p['sku'], 'name': p['fields'].get('name'), 'status': p['status'],
                      'submitted_at': p['submitted_at'], 'claimed_at': p['claimed_at'], 'updated_at': p['updated_at']}
                     for t, x in self.projects.items() if only in (None, t)
                     for p in x['products'] if p['status'] in statuses]
            items.sort(key=lambda i: i['submitted_at'] or '')
            if self.on_queue:
                self.on_queue(items)
            return 200, items
        if len(parts) < 3 or parts[:2] != ['bot', 'projects'] or parts[2] not in self.projects:
            return err(404, 'not_found')
        token, project, rest = parts[2], self.projects[parts[2]], parts[3:]
        if rest == [] and method == 'GET':
            return 200, {k: project[k] for k in ('token', 'title', 'status', 'closed_at', 'close_notify_status',
                                                   'options', 'created_at')} | {
                'products': [self.summary(p) for p in project['products']]}
        if rest == [] and method == 'DELETE':
            del self.projects[token]
            return 200, {'deleted': True}
        if rest == ['close'] and method == 'POST':
            if project['status'] != 'closed':
                project.update(status='closed', closed_at=now(), close_notify_status='unset')
            return 200, {'status': 'closed', 'closed_at': project['closed_at'],
                         'close_notify_status': project['close_notify_status']}
        if rest == ['reopen'] and method == 'POST':
            project.update(status='open', closed_at=None)
            return 200, {'status': 'open'}
        if rest == ['export'] and method == 'GET':
            return 200, {'token': token, 'title': project['title'], 'status': project['status'],
                         'closed_at': project['closed_at'], 'exported_at': now(), 'products': [
                {'id': p['id'], 'sku': p['sku'], 'position': p['position'], 'status': p['status'], 'fields': p['fields'],
                 'product_summary': (p['result'] or {}).get('product_summary'), 'routes': (p['result'] or {}).get('routes') or {},
                 'state': {k: p['state'].get(k) for k in ('draft', 'hearts', 'dislikes', 'banned_words', 'final')},
                 'edited_final': p['edited_final'], 'edited_at': p['edited_at']} for p in project['products']]}
        if len(rest) < 2 or rest[0] != 'products':
            return err(404, 'not_found')
        p = next((x for x in project['products'] if x['id'] == rest[1]), None)
        if p is None:
            return err(404, 'not_found')
        action = rest[2:]
        if action == [] and method == 'GET':
            return 200, self.detail(project, p)
        if action == ['claim'] and method == 'POST':
            if p['status'] == 'submitted' or (p['status'] == 'generating' and body.get('force')):
                p.update(status='generating', claimed_at=now())
                return 200, {'claimed': True, 'status': 'generating', 'claimed_at': p['claimed_at']}
            if p['status'] == 'generating':
                return 200, {'claimed': False, 'status': 'generating', 'claimed_at': p['claimed_at']}
            return 200, {'claimed': False, 'status': p['status']}
        if action == ['result'] and method == 'POST':
            if project['status'] == 'closed':
                return err(409, 'project_closed', '專案已結束')
            if p['status'] not in ('generating', 'ready', 'submitted'):
                return err(409, 'bad_status', p['status'])
            routes = {r: [{'id': f'{r}{n}', 'text': x['text'], 'check': bool(x.get('check'))}
                          for n, x in enumerate(items, 1)] for r, items in body['routes'].items()}
            p.update(result={'routes': routes, 'product_summary': body.get('product_summary', '')}, state={},
                     state_rev=p['state_rev'] + 1, edited_final=None, edited_at=None, status='ready')
            return 200, {'status': 'ready'}
        if action == ['synced'] and method == 'POST':
            p['synced_at'] = now()
            return 200, {'synced_at': p['synced_at']}
        return err(404, 'not_found')


class PanelSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected = hashlib.sha256((REPO / 'data/training.json').read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256((REPO / 'data/training.json').read_bytes()).hexdigest() == cls.protected, \
            'data/training.json was modified'

    def setUp(self):
        self.srv = FakePanel()
        self.tmp = Path(tempfile.mkdtemp(prefix='panel-test-'))
        self.training = self.tmp / 'training.json'

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, env=None, check=True, code=0):
        e = {**os.environ, 'HEALER_PANEL_API': self.srv.api, 'HEALER_PANEL_BOT_KEY': BOT_KEY,
             'HEALER_PANEL_URL': 'https://panel.example.app/',
             'HEALER_PANEL_ENV': str(self.tmp / 'no-such-panel.env')}   # never read the machine's real settings file
        e.update(env or {})
        p = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=e, timeout=60)
        if check and p.returncode != code:
            self.fail(f'{args} exited {p.returncode}, expected {code}:\n{p.stdout}\n{p.stderr}')
        self.assertNotIn('Traceback', p.stderr)
        return p

    def calls(self, method, suffix):
        return [x for x in self.srv.log if x[0] == method and x[1].endswith(suffix)]

    # ---------- env / errors ----------
    def test_missing_env_exits_2(self):
        p = self.cli('projects', env={'HEALER_PANEL_BOT_KEY': '', 'HEALER_PANEL_URL': ''}, code=2)
        self.assertIn('缺少環境變數', p.stderr)
        self.assertIn('HEALER_PANEL_BOT_KEY', p.stderr)
        self.assertIn('HEALER_PANEL_URL', p.stderr)

    # ---------- settings file (panel.env) ----------
    def write_env_file(self, text: str, mode=0o600) -> Path:
        path = self.tmp / 'panel.env'
        path.write_text(text, encoding='utf-8')
        path.chmod(mode)
        return path

    def test_env_file_fills_missing(self):
        path = self.write_env_file(
            '# Healer 面板設定\n\n'
            f'export HEALER_PANEL_API="{self.srv.api}"\n'
            f"HEALER_PANEL_BOT_KEY='{BOT_KEY}'\n"
            'HEALER_PANEL_URL=https://panel.from-file.app/   # 面板網址\n'
            'OTHER_SECRET=should-not-be-loaded\n')
        unset = {'HEALER_PANEL_API': '', 'HEALER_PANEL_BOT_KEY': '', 'HEALER_PANEL_URL': '', 'HEALER_PANEL_ENV': str(path)}
        p = self.cli('create', '--title', 't', env=unset)
        self.assertEqual(p.stderr, '')   # 600 → no warning
        self.assertTrue(json.loads(p.stdout)['link'].startswith('https://panel.from-file.app/p/'))
        # environment variables win over the file
        p = self.cli('create', env={'HEALER_PANEL_URL': 'https://panel.from-env.app', 'HEALER_PANEL_ENV': str(path)})
        self.assertTrue(json.loads(p.stdout)['link'].startswith('https://panel.from-env.app/p/'))
        self.assertIn('401', self.cli('projects', env={**unset, 'HEALER_PANEL_BOT_KEY': 'wrong'}, code=1).stderr)
        # only the three keys are read, nothing else is put into the environment
        self.assertEqual(panel_session.read_env_file(path), {
            'HEALER_PANEL_API': self.srv.api, 'HEALER_PANEL_BOT_KEY': BOT_KEY,
            'HEALER_PANEL_URL': 'https://panel.from-file.app/'})
        self.assertNotIn('OTHER_SECRET', os.environ)

    def test_env_file_default_location(self):
        home = self.tmp / 'home'
        folder = home / '.config/healer-panel'
        folder.mkdir(parents=True)
        (folder / 'panel.env').write_text(f'HEALER_PANEL_API={self.srv.api}\nHEALER_PANEL_BOT_KEY={BOT_KEY}\n'
                                          'HEALER_PANEL_URL=https://panel.home.app\n')
        (folder / 'panel.env').chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith('HEALER_PANEL')}
        p = subprocess.run([sys.executable, str(CLI), 'create'], capture_output=True, text=True, timeout=60,
                           env={**env, 'HOME': str(home)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)['link'].startswith('https://panel.home.app/p/'))

    def test_env_file_permission_warning(self):
        path = self.write_env_file(f'HEALER_PANEL_BOT_KEY={BOT_KEY}\n', mode=0o644)
        p = self.cli('projects', env={'HEALER_PANEL_BOT_KEY': '', 'HEALER_PANEL_ENV': str(path)})
        self.assertIn('警告', p.stderr)
        self.assertIn('chmod 600', p.stderr)
        self.assertNotIn(BOT_KEY, p.stderr)

    def test_env_file_still_missing_exits_2(self):
        path = self.write_env_file(f'HEALER_PANEL_API={self.srv.api}\n')
        p = self.cli('projects', env={'HEALER_PANEL_API': '', 'HEALER_PANEL_BOT_KEY': '', 'HEALER_PANEL_URL': '',
                                      'HEALER_PANEL_ENV': str(path)}, code=2)
        self.assertIn('缺少環境變數', p.stderr)
        self.assertIn('HEALER_PANEL_BOT_KEY', p.stderr)
        self.assertIn('HEALER_PANEL_URL', p.stderr)
        self.assertNotIn('HEALER_PANEL_API（', p.stderr)

    def test_errors_are_chinese(self):
        self.assertIn('Bot 金鑰不對（401）', self.cli('projects', env={'HEALER_PANEL_BOT_KEY': 'wrong'}, code=1).stderr)
        self.assertIn('找不到（404）', self.cli('show', 'A' * 43, code=1).stderr)
        self.assertIn('找不到開頭是 Zzzz9999 的專案', self.cli('show', 'Zzzz9999', code=1).stderr)
        port = self.srv.server.server_address[1]
        self.srv.stop()
        self.srv = FakePanel()   # tearDown stops this one
        p = self.cli('projects', env={'HEALER_PANEL_API': f'http://127.0.0.1:{port}/x'}, code=1)
        self.assertIn('連不上面板後端', p.stderr)

    # ---------- create / projects / show ----------
    def test_create_projects_show(self):
        out = json.loads(self.cli('create', '--title', '十一月第一期').stdout)
        token = out['token']
        self.assertEqual(len(token), 43)
        self.assertEqual(out['link'], f'https://panel.example.app/p/{token}')
        body = self.calls('POST', '/bot/projects')[0][2]
        labels = json.loads((REPO / 'data/categories.json').read_text())['labels'].values()
        self.assertEqual(body, {'title': '十一月第一期', 'categories': list(dict.fromkeys(labels))})

        self.srv.add_product(token, 'HL-001')
        self.srv.add_product(token, '洋裝.02', status='draft')
        listed = self.cli('projects').stdout
        self.assertIn(token[:8], listed)
        self.assertIn('十一月第一期', listed)
        data = json.loads(self.cli('projects', '--status', 'open', '--json').stdout)
        self.assertEqual(data[0]['counts']['submitted'], 1)
        self.assertEqual(json.loads(self.cli('projects', '--status', 'closed', '--json').stdout), [])

        shown = self.cli('show', token[:8]).stdout   # 8-character prefix resolves
        self.assertIn('HL-001', shown)
        self.assertIn('洋裝.02', shown)
        self.assertIn('已送出', shown)
        self.assertIn('草稿', shown)
        self.assertEqual([p['sku'] for p in json.loads(self.cli('show', token, '--json').stdout)['products']],
                         ['HL-001', '洋裝.02'])

    def test_token_starting_with_dash(self):
        old = self.srv.add_project()
        token = '-' + old[1:]
        self.srv.projects[token] = dict(self.srv.projects.pop(old), token=token)
        self.srv.add_product(token, '-SKU1')
        self.assertIn('-SKU1', self.cli('show', token[:8]).stdout)
        self.assertIn('-SKU1', self.cli('show', token).stdout)
        p = self.cli('pull', token[:8], '-SKU1', '--out', str(self.tmp / 'r'))
        self.assertIn(f'push {token[:8]} -SKU1', p.stdout)
        out = self.tmp / 'o.md'
        out.write_text(OUTPUT_MD)
        self.cli('push', token[:8], '-SKU1', '--file', str(out), '--summary', '-短摘要-', '--training', str(self.training))
        self.assertEqual(self.calls('POST', '/result')[0][2]['product_summary'], '-短摘要-')

    def test_ambiguous_prefix(self):
        a, b = self.srv.add_project(), self.srv.add_project()
        self.srv.projects['abcd' + a[4:]] = dict(self.srv.projects.pop(a), token='abcd' + a[4:])
        self.srv.projects['abcd' + b[4:]] = dict(self.srv.projects.pop(b), token='abcd' + b[4:])
        self.assertIn('有 2 個', self.cli('show', 'abcd', code=1).stderr)

    # ---------- queue ----------
    def test_queue(self):
        t1, t2 = self.srv.add_project('一期'), self.srv.add_project('二期')
        self.srv.add_product(t1, 'A-1')
        self.srv.add_product(t2, 'B-1')
        self.srv.add_product(t2, 'B-2', status='generating')
        self.srv.add_product(t2, 'B-3', status='draft')
        self.assertEqual([x['sku'] for x in json.loads(self.cli('queue', '--json').stdout)], ['A-1', 'B-1'])
        self.assertEqual([x['sku'] for x in json.loads(self.cli('queue', '--project', t2[:8], '--json').stdout)], ['B-1'])
        both = json.loads(self.cli('queue', '--status', 'submitted,generating', '--json').stdout)
        self.assertEqual(sorted(x['sku'] for x in both), ['A-1', 'B-1', 'B-2'])
        self.assertEqual(self.calls('GET', '/bot/queue?status=submitted,generating')[0][0], 'GET')
        text = self.cli('queue').stdout
        self.assertIn('A-1', text)
        self.assertIn('共 2 個', text)
        self.assertIn('--status', self.cli('queue', '--status', 'ready', code=2).stderr)

    # ---------- pull ----------
    def test_pull_claim_and_exit_3(self):
        token = self.srv.add_project()
        prod = self.srv.add_product(token, 'HL-001')
        run = self.tmp / 'run'
        p = self.cli('pull', token[:8], 'HL-001', '--out', str(run))
        self.assertIn('已領取 HL-001', p.stdout)
        self.assertIn('prepare.py --new', p.stdout)
        self.assertIn(f'push {token[:8]} HL-001', p.stdout)
        self.assertEqual(sorted(x.name for x in (run / 'input/photos').iterdir()), ['01.jpg', '02.jpg'])
        self.assertEqual((run / 'input/photos/01.jpg').read_bytes(), jpeg_bytes((10, 20, 30)))
        spec = json.loads((run / 'input/new-product.json').read_text())
        self.assertEqual(spec, {'id': 'HL-001', **FIELDS, 'photos': 'photos'})
        self.assertEqual(prod['status'], 'generating')
        self.assertEqual(self.calls('POST', '/claim')[0][2], {'force': False})
        first_claim = prod['claimed_at']

        # already claimed → exit 3, nothing downloaded
        other = self.tmp / 'other'
        p = self.cli('pull', token, prod['id'][:8], '--out', str(other), code=3)
        self.assertIn('已被其他流程領走', p.stderr)
        self.assertIn('--force', p.stderr)
        self.assertFalse(other.exists())
        self.assertEqual(prod['claimed_at'], first_claim)

        # --force re-claims
        p = self.cli('pull', token, prod['id'], '--out', str(other), '--force')
        self.assertEqual(self.calls('POST', '/claim')[-1][2], {'force': True})
        self.assertNotEqual(prod['claimed_at'], first_claim)
        self.assertTrue((other / 'input/new-product.json').exists())

        # ready / draft → exit 3 with the status
        prod['status'] = 'ready'
        self.assertIn('可挑句（ready）', self.cli('pull', token, 'HL-001', '--out', str(self.tmp / 'x'), code=3).stderr)
        self.assertIn('可挑句（ready）', self.cli('pull', token, 'HL-001', '--force', '--out', str(self.tmp / 'x'), code=3).stderr)
        self.srv.add_product(token, 'D-1', status='draft')
        self.assertIn('還沒送出', self.cli('pull', token, 'D-1', '--out', str(self.tmp / 'y'), code=3).stderr)
        self.assertFalse((self.tmp / 'x').exists() or (self.tmp / 'y').exists())
        self.assertIn('找不到商品 NOPE', self.cli('pull', token, 'NOPE', code=1).stderr)

        # the pulled folder feeds prepare.py directly (id = sku)
        subprocess.run([sys.executable, str(SCRIPTS / 'prepare.py'), '--new', str(run / 'input/new-product.json'),
                        '--output', str(run / 'packet'), '--training', str(self.training)], check=True, capture_output=True)
        task = (run / 'packet/HL-001-S/task.md').read_text()
        self.assertIn('尺寸：S、M\n價格：1280\n', task)
        self.assertIn('# 小編提供的資訊', task)

    def test_pull_race_only_one_wins(self):
        token = self.srv.add_project()
        self.srv.add_product(token, 'HL-001')
        procs = [subprocess.Popen([sys.executable, str(CLI), 'pull', token, 'HL-001', '--out', str(self.tmp / f'r{i}')],
                                  env={**os.environ, 'HEALER_PANEL_API': self.srv.api, 'HEALER_PANEL_BOT_KEY': BOT_KEY,
                                       'HEALER_PANEL_URL': 'https://x'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for i in range(4)]
        self.assertEqual(sorted(p.wait(60) for p in procs), [0, 3, 3, 3])

    def test_default_folders(self):
        token = 'Abcdefgh' + 'x' * 35
        day = datetime.datetime.now(panel_session.TAIPEI).strftime('%Y-%m-%d')
        self.assertEqual(panel_session.default_project_dir(token), REPO / 'runs' / f'{day}-Abcdefgh')
        self.assertEqual(panel_session.default_batch_dir(), REPO / 'runs' / f'{day}-batch')
        self.assertEqual(panel_session.safe_name('洋裝-01.A_b'), '洋裝-01.A_b')
        self.assertEqual(panel_session.safe_name('a/b c:d'), 'a_b_c_d')
        self.assertEqual(panel_session.safe_name('..'), '_')

    # ---------- pull-all ----------
    def test_pull_all(self):
        t1, t2 = self.srv.add_project(), self.srv.add_project()
        self.srv.add_product(t1, 'A-1')
        self.srv.add_product(t2, 'SAME')
        self.srv.add_product(t1, 'SAME')
        self.srv.add_product(t2, 'TAKEN')
        self.srv.add_product(t2, 'G-1', status='generating')

        def someone_else_claims(items):   # between the queue listing and our claim, another bot takes TAKEN
            self.srv.find(t2, 'TAKEN').update(status='generating', claimed_at=now())
            self.srv.on_queue = None
        self.srv.on_queue = someone_else_claims

        batch = self.tmp / 'batch'
        p = self.cli('pull-all', '--out', str(batch))
        self.assertIn('已領取 3 個商品', p.stdout)
        self.assertIn('略過 TAKEN：已被其他流程領走', p.stderr)
        folders = sorted(x.name for x in batch.iterdir())
        self.assertEqual(folders, sorted(['A-1', 'SAME', f'SAME-{t1[:8]}']))
        for f in folders:
            self.assertTrue((batch / f / 'input/new-product.json').exists())
            self.assertIn(f'--output {batch / f / "packet"}', p.stdout)
        self.assertEqual(json.loads((batch / 'SAME/input/new-product.json').read_text())['id'], 'SAME')
        self.assertEqual(self.srv.find(t1, 'A-1')['status'], 'generating')
        self.assertNotIn('G-1', p.stdout)
        self.assertIn('沒有待處理的商品', self.cli('pull-all', '--out', str(batch)).stdout)

    def test_pull_all_one_project(self):
        t1, t2 = self.srv.add_project(), self.srv.add_project()
        self.srv.add_product(t1, 'A-1')
        self.srv.add_product(t2, 'B-1')
        self.cli('pull-all', '--project', t1[:8], '--out', str(self.tmp / 'b'))
        self.assertEqual([x.name for x in (self.tmp / 'b').iterdir()], ['A-1'])
        self.assertEqual(self.srv.find(t2, 'B-1')['status'], 'submitted')

    # ---------- push ----------
    def test_push_filters(self):
        token = self.srv.add_project()
        prod = self.srv.add_product(token, 'HL-001', status='generating')
        self.training.write_text(json.dumps({'hearts': [], 'banned': ['超級'], 'finals': [],
                                             'disliked': [{'text': '這句是之前被按過不喜歡的'}]}, ensure_ascii=False))
        out = self.tmp / 'output.md'
        out.write_text(OUTPUT_MD)
        p = self.cli('push', token[:8], 'HL-001', '--file', str(out), '--training', str(self.training))
        self.assertIn('已過濾 路線A（不喜歡過）：這句是之前被按過不喜歡的', p.stderr)
        self.assertIn('禁用詞「超級」', p.stderr)
        self.assertIn('A 3 句、B 1 句、C 2 句', p.stdout)
        body = self.calls('POST', '/result')[0][2]
        self.assertEqual(body['product_summary'], '白色 V 領短洋裝，腰間抽繩。')
        self.assertEqual(body['routes']['A'][2], {'text': '[整件輕盈不悶熱 夏天穿一整天也舒服]', 'check': True})
        self.assertNotIn('這件超級好穿', json.dumps(body, ensure_ascii=False))
        self.assertEqual(prod['status'], 'ready')
        self.assertEqual([x['id'] for x in prod['result']['routes']['A']], ['A1', 'A2', 'A3'])

        out.write_text('【路線A】\n一句\n【路線B】\n')
        self.assertIn('【路線B】、【路線C】', self.cli('push', token, 'HL-001', '--file', str(out), code=1).stderr)
        self.srv.projects[token]['status'] = 'closed'
        out.write_text(OUTPUT_MD)
        self.assertIn('專案已結束（409）', self.cli('push', token, 'HL-001', '--file', str(out),
                                                 '--training', str(self.training), code=1).stderr)

    # ---------- sync ----------
    def pushed(self, token, sku):
        p = self.srv.find(token, sku)
        p['status'] = 'ready'
        p['result'] = {'product_summary': '', 'routes': {
            'A': [{'id': 'A1', 'text': f'{sku} 夏天熱賣款改版！', 'check': False}, {'id': 'A2', 'text': f'{sku} 女人味', 'check': False}],
            'B': [{'id': 'B1', 'text': f'{sku} 腰間抽繩 鬆緊自己調！', 'check': False}],
            'C': [{'id': 'C1', 'text': f'{sku} 海邊散步剛剛好', 'check': False}]}}
        return p

    def test_sync_all_idempotent_with_edited_text(self):
        token = self.srv.add_project()
        for sku in ('S-1', 'S-2'):
            self.srv.add_product(token, sku)
            self.pushed(token, sku)
        self.srv.add_product(token, 'S-3')   # still submitted → not synced
        self.srv.pick(token, 'S-1', {'draft': [{'id': 'B1', 'text': 'S-1 腰間抽繩 鬆緊隨你調！', 'origin': 'B1'}],
                                     'hearts': ['B1', 'A1'], 'dislikes': ['C1'], 'banned_words': ['爆款'],
                                     'final': {'text': 'S-1 腰間抽繩 鬆緊隨你調！\n✦材質成分：100%棉'}},
                      edited='S-1 腰間抽繩 想鬆想緊自己調\n✦材質成分：100%棉')
        self.srv.pick(token, 'S-2', {'hearts': ['A2'], 'dislikes': [], 'banned_words': ['爆款'], 'final': None})

        p1 = self.cli('sync', token[:8], '--training', str(self.training))
        self.assertIn('已同步 2 個商品', p1.stdout)
        self.assertIn('♡ 3 句、不喜歡 1 句、禁用詞 1 個、定稿 1 篇、修改後文案 1 篇', p1.stdout)
        self.assertIn('S-2：', p1.stdout)
        self.assertIn('還沒定稿', p1.stdout)
        rec = json.loads(self.training.read_text())
        self.assertEqual([(h['text'], h['route'], h['product_id']) for h in rec['hearts']],
                         [('S-1 腰間抽繩 鬆緊隨你調！', 'B', 'S-1'), ('S-1 夏天熱賣款改版！', 'A', 'S-1'), ('S-2 女人味', 'A', 'S-2')])
        self.assertEqual([d['text'] for d in rec['disliked']], ['S-1 海邊散步剛剛好'])
        self.assertEqual(rec['banned'], ['爆款'])
        self.assertEqual(rec['style_notes'], [])
        self.assertEqual(len(rec['finals']), 1)
        self.assertEqual(rec['finals'][0]['product_id'], 'S-1')
        self.assertEqual(rec['finals'][0]['name'], '測試短洋裝')
        self.assertEqual(rec['finals'][0]['edited_text'], 'S-1 腰間抽繩 想鬆想緊自己調\n✦材質成分：100%棉')
        synced = {x[1].split('/')[-2] for x in self.calls('POST', '/synced')}
        self.assertEqual(synced, {self.srv.find(token, s)['id'] for s in ('S-1', 'S-2')})

        first = self.training.read_text()
        p2 = self.cli('sync', token, '--training', str(self.training))
        self.assertIn('♡ 0 句、不喜歡 0 句、禁用詞 0 個、定稿 0 篇、修改後文案 0 篇', p2.stdout)
        self.assertEqual(self.training.read_text(), first)

        # the user edits the edited copy again → edited_text is updated, no second final
        self.srv.find(token, 'S-1')['edited_final'] = '第三版'
        p3 = self.cli('sync', token, 'S-1', '--training', str(self.training))
        self.assertIn('已同步 1 個商品', p3.stdout)
        self.assertIn('修改後文案 1 篇', p3.stdout)
        rec = json.loads(self.training.read_text())
        self.assertEqual([(f['product_id'], f['edited_text']) for f in rec['finals']], [('S-1', '第三版')])
        # cleared on the phone → removed from the record
        self.srv.find(token, 'S-1')['edited_final'] = None
        self.cli('sync', token, 'S-1', '--training', str(self.training))
        self.assertNotIn('edited_text', json.loads(self.training.read_text())['finals'][0])

    def test_sync_nothing(self):
        token = self.srv.add_project()
        self.srv.add_product(token, 'S-1')
        self.assertIn('沒有可同步的商品', self.cli('sync', token, '--training', str(self.training)).stdout)
        self.assertFalse(self.training.exists())

    # ---------- export ----------
    def test_export_md(self):
        token = self.srv.add_project('十一月')
        self.srv.add_product(token, 'E-1')
        self.pushed(token, 'E-1')
        self.srv.pick(token, 'E-1', {
            'draft': [{'id': 'A1', 'text': 'E-1 夏天熱賣款改版！', 'origin': 'A1'},
                      {'id': 'B1', 'text': 'E-1 腰間抽繩 想鬆想緊自己調', 'origin': 'B1'},
                      {'id': 'x1', 'text': '自己加的一句'}],
            'hearts': ['A1'], 'dislikes': ['C1'], 'banned_words': ['爆款'],
            'final': {'text': '第一行不變\n第二行原本\n要刪的行\n✦材質成分：100%棉'}},
            edited='第一行不變\n第二行改過了\n✦材質成分：100%棉\n最後加一句')
        self.srv.add_product(token, 'E-2', status='draft')
        self.cli('close', token, '--yes')
        js, md = self.tmp / 'out/export.json', self.tmp / 'out/export.md'
        p = self.cli('export', token[:8], '--out', str(js))
        self.assertIn('已匯出 2 個商品（1 個有修改後文案）', p.stdout)
        data = json.loads(js.read_text())
        self.assertEqual(data['status'], 'closed')
        text = md.read_text()
        for s in ('# 專案匯出：十一月', '## 1. E-1：測試短洋裝', '### AI 產生版（三路線全部句子）', '#### 路線A',
                  '- A1 E-1 夏天熱賣款改版！', '- C1 E-1 海邊散步剛剛好', '### 使用者挑選的草稿',
                  '1. E-1 夏天熱賣款改版！ （來自 A1，未改字）',
                  '2. E-1 腰間抽繩 想鬆想緊自己調 （來自 B1，有改字；原句：E-1 腰間抽繩 鬆緊自己調！）',
                  '3. 自己加的一句 （使用者自己加的句子）', '### 定稿', '### 使用者修改後的最終文案',
                  '### 差異（定稿 → 修改後最終文案，逐行）', '= 保留：第一行不變', '~ 改寫：第二行原本\n    → 第二行改過了',
                  '- 刪除：要刪的行', '= 保留：✦材質成分：100%棉', '+ 新增：最後加一句', '## 2. E-2', '（還沒有產生句子）',
                  '♡：A1；✕ 不喜歡：C1；禁用詞：爆款'):
            self.assertIn(s, text)
        self.assertEqual(text.count('### 差異'), 1)   # E-2 has no edited copy → no diff

        # md option + default md path next to json; no edited copy → diff skipped; open project warns
        self.cli('reopen', token)
        self.srv.find(token, 'E-1')['edited_final'] = None
        p = self.cli('export', token, '--out', str(self.tmp / 'a.json'), '--md', str(self.tmp / 'b/x.md'))
        self.assertIn('還沒結束', p.stdout)
        self.assertNotIn('### 差異', (self.tmp / 'b/x.md').read_text())

    def test_line_diff_same(self):
        self.assertEqual(panel_session.line_diff('a\n\nb', 'a\nb\n'), ['= 保留：a', '= 保留：b'])

    # ---------- close / reopen / delete / config ----------
    def test_close_reopen_delete(self):
        token = self.srv.add_project()
        self.assertIn('--yes', self.cli('close', token, code=2).stderr)
        self.assertEqual(self.srv.projects[token]['status'], 'open')
        p = self.cli('close', token[:8], '--yes')
        self.assertIn('已結束專案', p.stdout)
        self.assertEqual(self.srv.projects[token]['status'], 'closed')
        self.assertEqual(len(self.calls('POST', f'/bot/projects/{token}/close')), 1)
        self.assertEqual(json.loads(self.cli('projects', '--status', 'closed', '--json').stdout)[0]['token'], token)
        self.cli('close', token[:8], '--yes')   # closed projects still resolve by prefix; idempotent
        self.assertIn('已重新開啟', self.cli('reopen', token[:8]).stdout)
        self.assertEqual(self.srv.projects[token]['status'], 'open')
        self.cli('delete', token)
        self.assertNotIn(token, self.srv.projects)

    def test_webhook_config(self):
        hook = 'https://bot.example/hook'
        self.assertEqual(json.loads(self.cli('config-webhook', '--url', hook, '--key', 'k123').stdout),
                         {'webhook_url': hook, 'webhook_key_set': True})
        self.assertEqual(json.loads(self.cli('config-webhook', '--show').stdout)['webhook_key_set'], True)
        self.assertEqual(json.loads(self.cli('config-webhook', '--clear').stdout),
                         {'webhook_url': None, 'webhook_key_set': False})
        self.assertIn('--key', self.cli('config-webhook', '--url', hook, code=2).stderr)

    def test_no_old_routes(self):
        token = self.srv.add_project()
        self.srv.add_product(token, 'HL-1')
        self.cli('show', token)
        self.cli('queue')
        self.assertFalse([x for x in self.srv.log if re.search(r'/sessions|/s/', x[1])])


if __name__ == '__main__':
    unittest.main()
