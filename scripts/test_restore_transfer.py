"""Tests for scripts/restore_transfer.py: the pure helpers, the --training-only path, and the whole restore
sequence against an in-process fake panel-api (public /p/… and bot /bot/… routes mirroring handler.ts).
Nothing leaves this machine; data/training.json is never written.

  python3 scripts/test_restore_transfer.py
  python3 -m unittest scripts/test_restore_transfer.py -v
"""
import hashlib
import json
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
sys.path.insert(0, str(SCRIPTS))
import restore_transfer as rt  # noqa: E402
import training  # noqa: E402

TRANSFER = REPO / 'transfer' / '2026-11-05-healer'
PROTECTED = ('data/training.json',)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_record() -> dict:
    return {k: [] for k in training.KEYS}


class RoutesTest(unittest.TestCase):
    def test_strips_ids_keeps_order_and_check(self):
        routes = {'A': [{'id': 'A1', 'text': 'a1', 'check': False}, {'id': 'A2', 'text': '[a2]', 'check': True}],
                  'B': [{'id': 'B1', 'text': 'b1'}], 'C': []}
        out = rt.routes_for_result(routes)
        self.assertEqual(out, {'A': [{'text': 'a1', 'check': False}, {'text': '[a2]', 'check': True}],
                               'B': [{'text': 'b1', 'check': False}], 'C': []})
        for items in out.values():
            for s in items:
                self.assertNotIn('id', s)

    def test_refuses_reordered_ids(self):
        with self.assertRaises(rt.PanelError):
            rt.routes_for_result({'A': [{'id': 'A2', 'text': 'x'}, {'id': 'A1', 'text': 'y'}]})

    def test_refuses_unknown_route_and_empty(self):
        with self.assertRaises(rt.PanelError):
            rt.routes_for_result({'A': [{'id': 'A1', 'text': 'x'}], 'D': []})
        with self.assertRaises(rt.PanelError):
            rt.routes_for_result({'A': [], 'B': [], 'C': []})
        with self.assertRaises(rt.PanelError):
            rt.routes_for_result({'A': [{'id': 'A1', 'text': '  '}]})

    def test_real_export_passes(self):
        export = json.loads((TRANSFER / 'project-export.json').read_text(encoding='utf-8'))
        for p in export['products']:
            out = rt.routes_for_result(p['routes'])
            for r in 'ABC':
                self.assertEqual([s['text'] for s in out[r]], [s['text'] for s in p['routes'][r]])
            # every ♡/✕ id in the state points at a sentence that will exist after the result is re-posted
            ids = {f'{r}{i}' for r in 'ABC' for i in range(1, len(out[r]) + 1)}
            for sid in p['state']['hearts'] + p['state']['dislikes']:
                self.assertIn(sid, ids)


class StateTest(unittest.TestCase):
    def test_only_known_keys_and_final(self):
        state = {'draft': [{'id': 'A1', 'text': 't', 'origin': 'A1'}, 'junk'], 'hearts': ['A1'], 'dislikes': [],
                 'banned_words': ['超級'], 'final': {'text': 'done', 'saved_at': '2026-11-05T02:40:08.792Z'},
                 'something_else': 1}
        out = rt.state_for_restore(state)
        self.assertEqual(set(out), set(rt.STATE_KEYS))
        self.assertEqual(out['draft'], [{'id': 'A1', 'text': 't', 'origin': 'A1'}])
        self.assertEqual(out['final'], {'text': 'done', 'saved_at': '2026-11-05T02:40:08.792Z'})

    def test_no_final_is_null(self):
        self.assertIsNone(rt.state_for_restore({'hearts': ['A1']})['final'])
        self.assertIsNone(rt.state_for_restore({'final': {'text': '  '}})['final'])
        self.assertEqual(rt.state_for_restore({})['hearts'], [])


class MergeTest(unittest.TestCase):
    def test_dedupe_and_counts(self):
        record = empty_record()
        incoming = {'hearts': [{'text': ' 句子一 ', 'route': 'A', 'product_id': 'p1', 'added': '2026-11-01'},
                               {'text': '句子一', 'route': 'B'}],
                    'disliked': [{'text': '不要', 'route': 'C', 'product_id': 'p1', 'added': '2026-11-02'}],
                    'banned': ['超級', '超級', ' '],
                    'finals': [{'product_id': 'p1', 'name': 'N', 'text': '定稿', 'saved': '2026-11-03',
                                'edited_text': '改過'}],
                    'style_notes': [{'note': '短句', 'source': '來源', 'added': '2026-11-04'}]}
        added = rt.merge_training(record, incoming)
        self.assertEqual(added, {'hearts': 1, 'disliked': 1, 'banned': 1, 'finals': 1, 'edited': 0, 'style_notes': 1})
        self.assertEqual(record['hearts'][0]['added'], '2026-11-01')
        self.assertEqual(record['finals'][0]['saved'], '2026-11-03')
        self.assertEqual(record['finals'][0]['edited_text'], '改過')
        self.assertEqual(record['banned'], ['超級'])
        # second merge of the same thing adds nothing
        again = rt.merge_training(record, incoming)
        self.assertEqual(sum(again.values()), 0)
        self.assertEqual(len(record['hearts']), 1)

    def test_existing_final_gets_edited_text(self):
        record = empty_record()
        training.add_final(record, 'p1', 'N', '定稿')
        added = rt.merge_training(record, {'finals': [{'product_id': 'p1', 'name': 'N', 'text': '定稿',
                                                       'edited_text': '使用者改的'}]})
        self.assertEqual(added['finals'], 0)
        self.assertEqual(added['edited'], 1)
        self.assertEqual(len(record['finals']), 1)
        self.assertEqual(record['finals'][0]['edited_text'], '使用者改的')
        # the same product with a different text is a new final, not an overwrite
        added = rt.merge_training(record, {'finals': [{'product_id': 'p1', 'name': 'N', 'text': '另一篇'}]})
        self.assertEqual(added['finals'], 1)
        self.assertEqual(len(record['finals']), 2)

    def test_existing_edited_text_is_kept_when_incoming_has_none(self):
        record = empty_record()
        training.add_final(record, 'p1', 'N', '定稿', edited_text='原本的')
        rt.merge_training(record, {'finals': [{'product_id': 'p1', 'name': 'N', 'text': '定稿'}]})
        self.assertEqual(record['finals'][0]['edited_text'], '原本的')

    def test_snapshot_into_itself_is_a_no_op(self):
        snapshot = training.load(TRANSFER / 'training.json')
        record = training.load(TRANSFER / 'training.json')
        self.assertEqual(sum(rt.merge_training(record, snapshot).values()), 0)
        self.assertEqual(record, snapshot)

    def test_snapshot_into_empty_record(self):
        snapshot = training.load(TRANSFER / 'training.json')
        record = empty_record()
        added = rt.merge_training(record, snapshot)
        self.assertEqual(added['hearts'], len(snapshot['hearts']))
        self.assertEqual(added['disliked'], len(snapshot['disliked']))
        self.assertEqual(added['finals'], len(snapshot['finals']))
        self.assertEqual(added['style_notes'], len(snapshot['style_notes']))


class SmallHelpersTest(unittest.TestCase):
    def test_photo_files_sorted_and_filtered(self):
        tmp = Path(tempfile.mkdtemp(prefix='restore-test-'))
        try:
            for name in ('10.jpg', '02.JPG', '01.png', 'spec.json', 'x.webp', 'y.gif'):
                (tmp / name).write_bytes(b'x')
            self.assertEqual([p.name for p in rt.photo_files(tmp)], ['01.png', '02.JPG', '10.jpg', 'x.webp'])
            self.assertEqual(rt.photo_files(tmp / 'missing'), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_content_types(self):
        self.assertEqual(rt.CONTENT_TYPES['.jpg'], 'image/jpeg')
        self.assertEqual(rt.CONTENT_TYPES['.jpeg'], 'image/jpeg')
        self.assertEqual(rt.CONTENT_TYPES['.png'], 'image/png')
        self.assertEqual(rt.CONTENT_TYPES['.webp'], 'image/webp')

    def test_default_title(self):
        self.assertEqual(rt.default_title({'title': '11月上架（新）'}), '11月上架（新）（轉移）')
        self.assertEqual(rt.default_title({}), '轉移（轉移）')

    def test_public_client_has_no_bot_key(self):
        pub = rt.PublicClient('https://x.supabase.co/functions/v1/panel-api', 'tok-en')
        self.assertNotIn('x-bot-key', pub.headers())
        self.assertEqual(pub.base, '/p/tok-en')

    def test_transfer_folder_photos_exist_for_every_sku(self):
        export, snapshot = rt.load_transfer(TRANSFER)
        self.assertIsNotNone(snapshot)
        for p in export['products']:
            self.assertTrue(rt.photo_files(TRANSFER / 'photos' / p['sku']), p['sku'])


class CliTrainingOnlyTest(unittest.TestCase):
    """--training-only must merge into the given file and never touch the network or data/training.json."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='restore-cli-'))
        self.protected = {p: digest(REPO / p) for p in PROTECTED}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p, h in self.protected.items():
            self.assertEqual(digest(REPO / p), h, f'{p} was modified')

    def run_cli(self, *args):
        env = {'PATH': '/usr/bin:/bin', 'HEALER_PANEL_ENV': str(self.tmp / 'no-such-panel.env'),
               'PYTHONIOENCODING': 'utf-8'}
        return subprocess.run([sys.executable, str(SCRIPTS / 'restore_transfer.py'), *args],
                              capture_output=True, text=True, env=env)

    def test_training_only_merges_into_fresh_file(self):
        target = self.tmp / 'training.json'
        r = self.run_cli(str(TRANSFER), '--training-only', '--training', str(target))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('已合併訓練紀錄', r.stdout)
        merged = training.load(target)
        snapshot = training.load(TRANSFER / 'training.json')
        self.assertEqual(len(merged['hearts']), len(snapshot['hearts']))
        self.assertEqual(len(merged['finals']), len(snapshot['finals']))
        r = self.run_cli(str(TRANSFER), '--training-only', '--training', str(target))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('新增 ♡ 0 句', r.stdout)

    def test_without_env_stops_before_panel_with_exit_2(self):
        target = self.tmp / 'training.json'
        r = self.run_cli(str(TRANSFER), '--training', str(target))
        self.assertEqual(r.returncode, 2)
        self.assertIn('缺少環境變數', r.stderr)
        self.assertTrue(target.exists(), 'training merge happens before the panel is contacted')

    def test_bad_folder(self):
        r = self.run_cli(str(self.tmp / 'nope'), '--training-only')
        self.assertEqual(r.returncode, 1)
        self.assertIn('找不到轉移資料夾', r.stderr)


class FakePanel:
    """In-memory panel-api: the routes restore_transfer.py uses, with the same status rules as handler.ts."""
    BOT_KEY = 'test-bot-key'
    API_PREFIX = '/functions/v1/panel-api'

    def __init__(self):
        self.projects: dict[str, dict] = {}
        self.storage: dict[str, bytes] = {}
        self.webhooks = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get('content-length') or 0)
                return self.rfile.read(n) if n else b''

            def _send(self, code, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                url = urllib.parse.urlparse(self.path)
                raw = self._body()
                if url.path.startswith('/storage/upload/'):
                    fake.storage[urllib.parse.unquote(url.path[len('/storage/upload/'):])] = raw
                    return self._send(200, {'Key': 'ok'})
                body = json.loads(raw) if raw else {}
                parts = [urllib.parse.unquote(p) for p in url.path[len(fake.API_PREFIX):].split('/') if p]
                try:
                    code, payload = fake.route(self.command, parts, urllib.parse.parse_qs(url.query), body,
                                               self.headers.get('x-bot-key'))
                except Fail as e:
                    code, payload = e.code, {'error': e.error, 'message': e.message, **e.extra}
                self._send(code, payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = f'http://127.0.0.1:{self.server.server_port}{self.API_PREFIX}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # --- routes ---
    def route(self, m, parts, query, body, bot_key):
        if parts[:1] == ['bot']:
            if bot_key != self.BOT_KEY:
                raise Fail(401, 'unauthorized', 'Bot 金鑰錯誤')
            return self.bot(m, parts[1:], query, body)
        if parts[:1] == ['p'] and len(parts) >= 2:
            return self.public(m, parts[1], parts[2:], body)
        raise Fail(404, 'no_route', '沒有這個功能')

    def project(self, token):
        p = self.projects.get(token)
        if not p:
            raise Fail(404, 'not_found', '找不到這個專案連結')
        return p

    def product(self, project, pid):
        p = project['products'].get(pid.lower())
        if not p:
            raise Fail(404, 'product_not_found', '找不到這個商品')
        return p

    def bot(self, m, rest, query, body):
        if rest == ['projects'] and m == 'POST':
            token = secrets.token_urlsafe(32)
            self.projects[token] = {'token': token, 'title': body.get('title', ''), 'status': 'open',
                                    'categories': body.get('categories') or [], 'products': {}}
            return 201, {'token': token, 'status': 'open'}
        if rest == ['projects'] and m == 'GET':
            status = (query.get('status') or [None])[0]
            return 200, [{'token': t, 'title': p['title'], 'status': p['status']}
                         for t, p in self.projects.items() if not status or p['status'] == status]
        if len(rest) == 2 and rest[0] == 'projects' and m == 'GET':
            p = self.project(rest[1])
            return 200, {**{k: v for k, v in p.items() if k != 'products'}, 'products': list(p['products'].values())}
        if len(rest) == 5 and rest[0] == 'projects' and rest[2] == 'products' and m == 'POST':
            project = self.project(rest[1])
            prod = self.product(project, rest[3])
            if rest[4] == 'claim':
                allowed = ['submitted', 'generating'] if body.get('force') is True else ['submitted']
                if prod['status'] in allowed:
                    prod['status'] = 'generating'
                    return 200, {'claimed': True, 'status': 'generating', 'claimed_at': 'now'}
                return 200, {'claimed': False, 'status': prod['status']}
            if rest[4] == 'result':
                if prod['status'] not in ('submitted', 'generating', 'ready'):
                    raise Fail(409, 'bad_status', '這個狀態不能上傳文案', status=prod['status'])
                routes = {}
                for r in 'ABC':
                    items = body['routes'].get(r) or []
                    for s in items:
                        assert 'id' not in s, 'restore must not post ids'
                        assert isinstance(s['text'], str) and s['text'].strip()
                    routes[r] = [{'id': f'{r}{i + 1}', 'text': s['text'], 'check': s.get('check') is True}
                                 for i, s in enumerate(items)]
                prod.update(result={'routes': routes, 'product_summary': body.get('product_summary', '')},
                            status='ready', state={'draft': [], 'hearts': [], 'dislikes': [], 'banned_words': [],
                                                   'final': None}, state_rev=prod['state_rev'] + 1,
                            edited_final=None)
                return 200, {'status': 'ready', 'counts': {r: len(routes[r]) for r in 'ABC'}, 'state_rev': prod['state_rev']}
        raise Fail(404, 'no_route', '沒有這個功能')

    def public(self, m, token, rest, body):
        project = self.project(token)
        if not rest and m == 'GET':
            return 200, {'title': project['title'], 'status': project['status'],
                         'products': [{'id': p['id'], 'sku': p['sku'], 'status': p['status'],
                                       'photo_count': len(p['photos']), 'has_edited_final': bool(p['edited_final'])}
                                      for p in project['products'].values()]}
        if project['status'] != 'open':
            raise Fail(409, 'project_closed', '這期專案已結束')
        if rest == ['products'] and m == 'POST':
            sku = str(body.get('sku') or '').strip()
            if any(p['sku'] == sku for p in project['products'].values()):
                raise Fail(409, 'duplicate_sku', f'商品編號「{sku}」已經在這期專案裡了')
            pid = str(uuid.uuid4())
            project['products'][pid] = {'id': pid, 'sku': sku, 'status': 'draft', 'fields': {}, 'photos': [],
                                        'result': None, 'state': {}, 'state_rev': 0, 'edited_final': None,
                                        'position': len(project['products']) + 1}
            return 201, {'id': pid, 'sku': sku, 'status': 'draft'}
        if rest == ['submit'] and m == 'POST':
            for pid in body['product_ids']:
                prod = self.product(project, pid)
                if prod['status'] in ('ready', 'finalized'):
                    raise Fail(409, 'bad_status', '有商品的文案已經產生，不能再送出')
                f = prod['fields']
                missing = [k for k in ('name', 'category', 'material') if not str(f.get(k) or '').strip()]
                if not f.get('colors'):
                    missing.append('colors')
                if not prod['photos']:
                    missing.append('photos')
                if missing:
                    raise Fail(400, 'incomplete', '還有欄位沒填：' + '、'.join(missing))
                prod['status'] = 'submitted'
            self.webhooks += 1
            return 200, {'submitted': body['product_ids']}
        if rest[:1] != ['products'] or len(rest) < 2:
            raise Fail(404, 'no_route', '沒有這個功能')
        prod = self.product(project, rest[1])
        action = rest[2] if len(rest) == 3 else None
        if action is None and m == 'GET':
            return 200, {k: v for k, v in prod.items()}
        if action in ('fields', 'upload-url', 'photos') and prod['status'] != 'draft':
            raise Fail(409, 'not_draft', '這個商品已經送出，不能再修改', status=prod['status'])
        if action == 'fields' and m == 'PUT':
            fields = body['fields']
            if fields.get('category') and fields['category'] not in project['categories']:
                raise Fail(400, 'invalid_category', '類別不在可選清單內')
            prod['fields'] = fields
            return 200, {'fields': fields}
        if action == 'upload-url' and m == 'POST':
            if body.get('type') not in ('image/jpeg', 'image/png', 'image/webp'):
                raise Fail(400, 'bad_type', '只能上傳 JPEG、PNG 或 WebP 照片')
            if not isinstance(body.get('size'), int) or body['size'] <= 0:
                raise Fail(400, 'bad_size', '檔案大小不正確')
            path = f'{token}/{prod["id"]}/{secrets.token_hex(4)}.jpg'
            base = self.api[:self.api.index(self.API_PREFIX)]
            return 200, {'path': path, 'signed_url': f'{base}/storage/upload/{path}', 'token': 'x'}
        if action == 'photos' and m == 'POST':
            if body['path'] not in self.storage:
                raise Fail(400, 'not_uploaded', '找不到已上傳的照片')
            if len(self.storage[body['path']]) != body['size']:
                raise Fail(400, 'bad_size', 'size 與實際上傳不同')
            prod['photos'].append({'path': body['path'], 'name': body['name'], 'size': body['size'],
                                   'order': len(prod['photos'])})
            return 200, {'photos': prod['photos']}
        if action == 'state' and m == 'PUT':
            if prod['status'] not in ('ready', 'finalized'):
                raise Fail(409, 'not_ready', '文案還沒產生', status=prod['status'])
            if body.get('rev') != prod['state_rev']:
                raise Fail(409, 'stale', '內容在別處被修改過', state=prod['state'], state_rev=prod['state_rev'])
            state = body['state']
            assert set(state) <= {'draft', 'hearts', 'dislikes', 'banned_words', 'final'}
            prod['state'] = state
            prod['state_rev'] += 1
            prod['status'] = 'finalized' if state.get('final') else 'ready'
            return 200, {'state_rev': prod['state_rev'], 'status': prod['status']}
        if action == 'edited-final' and m == 'PUT':
            if prod['status'] != 'finalized':
                raise Fail(409, 'not_finalized', '定稿之後才能貼上修改後的最終文案', status=prod['status'])
            prod['edited_final'] = body['text'] or None
            return 200, {'edited_final': prod['edited_final']}
        raise Fail(404, 'no_route', '沒有這個功能')


class Fail(Exception):
    def __init__(self, code, error, message, **extra):
        super().__init__(message)
        self.code, self.error, self.message, self.extra = code, error, message, extra


class EndToEndTest(unittest.TestCase):
    """The CLI as a subprocess against FakePanel: the whole sequence, a rerun, and the --skip-photos resume."""

    def setUp(self):
        self.fake = FakePanel()
        self.tmp = Path(tempfile.mkdtemp(prefix='restore-e2e-'))
        self.protected = {p: digest(REPO / p) for p in PROTECTED}
        self.export = json.loads((TRANSFER / 'project-export.json').read_text(encoding='utf-8'))

    def tearDown(self):
        self.fake.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p, h in self.protected.items():
            self.assertEqual(digest(REPO / p), h, f'{p} was modified')

    def run_cli(self, *args):
        env = {'PATH': '/usr/bin:/bin', 'PYTHONIOENCODING': 'utf-8', 'HEALER_PANEL_API': self.fake.api,
               'HEALER_PANEL_BOT_KEY': FakePanel.BOT_KEY, 'HEALER_PANEL_URL': 'https://panel.example.app'}
        return subprocess.run([sys.executable, str(SCRIPTS / 'restore_transfer.py'), str(TRANSFER),
                               '--training', str(self.tmp / 'training.json'), *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def only_project(self) -> dict:
        self.assertEqual(len(self.fake.projects), 1)
        return next(iter(self.fake.projects.values()))

    def assert_restored(self, project):
        by_sku = {p['sku']: p for p in project['products'].values()}
        self.assertEqual(set(by_sku), {p['sku'] for p in self.export['products']})
        for src in self.export['products']:
            got = by_sku[src['sku']]
            self.assertEqual(got['status'], 'finalized', src['sku'])
            self.assertEqual(got['fields'], src['fields'])
            self.assertEqual(len(got['photos']), len(rt.photo_files(TRANSFER / 'photos' / src['sku'])))
            self.assertEqual([p['name'] for p in got['photos']],
                             [p.name for p in rt.photo_files(TRANSFER / 'photos' / src['sku'])])
            self.assertEqual(got['result']['routes'], src['routes'])          # ids regenerated identically
            self.assertEqual(got['result']['product_summary'], src['product_summary'])
            self.assertEqual(got['state']['hearts'], src['state']['hearts'])
            self.assertEqual(got['state']['dislikes'], src['state']['dislikes'])
            self.assertEqual(got['state']['draft'], src['state']['draft'])
            self.assertEqual(got['state']['final']['text'], src['state']['final']['text'])
            self.assertEqual(got['edited_final'], src['edited_final'])

    def test_full_restore_then_rerun_skips(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        project = self.only_project()
        self.assertEqual(project['title'], '11月上架（新）（轉移）')
        self.assertIn('裙子', project['categories'])
        self.assert_restored(project)
        self.assertIn(f'https://panel.example.app/p/{project["token"]}', r.stdout)
        self.assertIn('完成 2 個', r.stdout)
        self.assertEqual(self.fake.webhooks, 2)
        self.assertNotIn(FakePanel.BOT_KEY, r.stdout + r.stderr)
        # training merged into the temp record, not data/
        self.assertEqual(len(training.load(self.tmp / 'training.json')['hearts']),
                         len(training.load(TRANSFER / 'training.json')['hearts']))
        # same project again: both already finalized → skipped, nothing changed
        before = json.dumps(project, sort_keys=True)
        r = self.run_cli('--project', project['token'], '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('略過（已定稿）2 個', r.stdout)
        self.assertEqual(json.dumps(self.only_project(), sort_keys=True), before)

    def test_custom_title(self):
        r = self.run_cli('--title', '自訂標題', '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.only_project()['title'], '自訂標題')

    def test_skip_photos_leaves_drafts_then_resume_finishes(self):
        r = self.run_cli('--skip-photos', '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        project = self.only_project()
        self.assertEqual({p['status'] for p in project['products'].values()}, {'draft'})
        self.assertEqual(self.fake.webhooks, 0)
        self.assertIn('等照片 2 個', r.stdout)
        self.assertIn('--skip-photos', r.stderr)
        # second run without --skip-photos picks the drafts up: photos → submit → … → finalized
        r = self.run_cli('--project', project['token'][:8], '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn('從這裡接著做', r.stdout)
        self.assert_restored(self.only_project())

    def test_resume_from_submitted(self):
        """A product left at 'submitted' (e.g. the bot half failed) is finished without re-creating it."""
        r = self.run_cli('--skip-photos', '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr)
        project = self.only_project()
        for p in project['products'].values():                     # pretend the phone user added a photo and submitted
            p['photos'].append({'path': 'x', 'name': 'manual.jpg', 'size': 1, 'order': 0})
            p['status'] = 'submitted'
        r = self.run_cli('--project', project['token'], '--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(len(self.only_project()['products']), 2)
        for p in self.only_project()['products'].values():
            self.assertEqual(p['status'], 'finalized')
            self.assertTrue(p['edited_final'])
            self.assertEqual(len(p['photos']), 1)                   # the manual photo was kept, none re-uploaded

    def test_closed_project_is_refused(self):
        r = self.run_cli('--no-merge-training')
        self.assertEqual(r.returncode, 0, r.stderr)
        project = self.only_project()
        project['status'] = 'closed'
        r = self.run_cli('--project', project['token'], '--no-merge-training')
        self.assertEqual(r.returncode, 1)
        self.assertIn('已結束', r.stderr)


if __name__ == '__main__':
    unittest.main()
