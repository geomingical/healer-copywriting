"""End-to-end test of the v2 phone panel (projects + products) against the mock backend, at phone size (390x844).

  python3 panel/dev/e2e.py [--shots /workspace/panel-shots/v2] [--headed]

Starts panel/dev/mock-server.ts on a free port. The Bot side is simulated with plain HTTP calls (urllib), so this
test does not depend on scripts/. Scenario (spec2):
  old /s/ link + missing/unknown links → create project → add 2 products (invalid sku, duplicate sku on add and on
  rename) → upload photos + fill fields for each → home: tick both → submit shows which product is missing what →
  「去補」→ submit both at once (one webhook) → bot claims both (atomic claim, force) and posts results → pick
  sentences, finalize → paste edited final copy → reload keeps it → offline save shows retry → close-project
  dialog: cancel keeps it open, confirm closes → read-only overview → reload still read-only, writes get 409.
Screenshots (in --shots): 01-home … 09-readonly (.png).
"""
import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[2]
BOT_KEY = 'e2e-bot-key'
WEBHOOK_KEY = 'e2e-webhook-key'
DENO = shutil.which('deno') or str(Path.home() / '.local/bin/deno')
CATEGORIES = ['洋裝', '上衣', '長褲']
RESULT1 = {
    'routes': {
        'A': [{'text': '夏天熱賣款改版 這次補了芥末黃新色！', 'check': False},
              {'text': 'V領配上胸前抓皺 女人味剛剛好', 'check': False},
              {'text': '[整件輕盈不悶熱 夏天穿一整天也舒服]', 'check': True}],
        'B': [{'text': '腰間抽繩 鬆緊自己調！', 'check': False},
              {'text': '三層傘襬自然散開 修飾下半身', 'check': False},
              {'text': '這件超級好穿 每天都想穿', 'check': False}],
        'C': [{'text': '這個夏天想要有度假感 就選這件', 'check': False}],
    },
    'product_summary': '白、芥末黃兩色 V 領短洋裝，腰間抽繩、三層蛋糕裙。',
}
RESULT2 = {
    'routes': {'A': [{'text': '軟軟的針織背心 疊穿最好看', 'check': False}],
               'B': [{'text': '短版剪裁 腿看起來更長', 'check': False}],
               'C': [{'text': '配寬褲去咖啡廳剛剛好', 'check': False}]},
    'product_summary': '短版針織背心。',
}
EDITED = '腰間抽繩，鬆緊自己調\nV領抓皺，溫柔又有女人味\n夏天熱賣款改版，這次補了芥末黃\n✦材質成分：85%棉+15%聚酯'

TARGETS_JS = '''() => [...document.querySelectorAll('button, .tab, select, input:not([type=file]):not([type=checkbox]), summary, .selbox, a')]
  .filter(e => e.checkVisibility()).map(e => [e.textContent.trim() || e.getAttribute('aria-label') || e.id || e.className, e.getBoundingClientRect()])
  .filter(([, r]) => r.height < 47.5 || r.width < 47.5).map(([n, r]) => `${n} ${r.width}x${r.height}`)'''
FONTS_JS = '''() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=file]), textarea, select')]
  .filter(e => e.checkVisibility()).map(e => parseFloat(getComputedStyle(e).fontSize))'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def make_photos(folder: Path) -> list[Path]:
    """Three fake JPEGs; the first is a 3000x2000 landscape with EXIF orientation 6 (= portrait once rotated)."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, (size, color) in enumerate((((3000, 2000), (230, 200, 120)), ((1200, 1600), (240, 240, 235)),
                                       ((800, 800), (90, 120, 160))), 1):
        img = Image.new('RGB', size, color)
        for x in range(0, size[0], 200):   # a few stripes so the JPEG is not trivially small
            img.paste((color[0] // 2, color[1] // 2, color[2] // 2), (x, 0, x + 40, size[1]))
        path = folder / f'photo{n}.jpg'
        exif = Image.Exif()
        if n == 1:
            exif[0x0112] = 6
        img.save(path, 'JPEG', quality=90, exif=exif)
        paths.append(path)
    return paths


class Http:
    def __init__(self, api: str):
        self.api = api

    def call(self, method: str, path: str, body=None, bot=True) -> tuple[int, object]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.api + path, data=data, method=method)
        if bot:
            req.add_header('x-bot-key', BOT_KEY)
        if data is not None:
            req.add_header('content-type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'null')

    def ok(self, method: str, path: str, body=None, bot=True):
        status, data = self.call(method, path, body, bot)
        assert 200 <= status < 300, (method, path, status, data)
        return data


def start_server(port: int, delay_ms: int = 0) -> subprocess.Popen:
    base = f'http://127.0.0.1:{port}'
    server = subprocess.Popen([DENO, 'run', '-A', str(REPO / 'panel/dev/mock-server.ts')],
                              env={**os.environ, 'PORT': str(port), 'MOCK_BOT_KEY': BOT_KEY, 'MOCK_DELAY_MS': str(delay_ms)},
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            urllib.request.urlopen(base + '/config.js', timeout=1).read()
            break
        except OSError:
            time.sleep(0.1)
    return server


# 在頁面裡記錄：state／edited-final 請求的先後與結果、#edited-status 每次變化（以及當下 edited-final 是否已存成功）
WATCH_JS = '''() => {
  const log = window.__log = [];
  window.__status = [];
  window.__pasted = false;
  let stateInflight = 0, editedOk = false;
  const orig = window.fetch;
  window.fetch = async (url, init) => {
    const u = String(url), m = (init && init.method) || 'GET';
    const kind = m === 'PUT' && u.endsWith('/state') ? 'state' : m === 'PUT' && u.endsWith('/edited-final') ? 'edited' : null;
    if (kind === 'state') stateInflight += 1;
    if (kind) log.push({ kind, ev: 'start', stateInflight: kind === 'edited' ? stateInflight : undefined, final: kind === 'state' ? !!JSON.parse(init.body).state.final : undefined });
    try {
      const r = await orig(url, init);
      if (kind) log.push({ kind, ev: 'end', status: r.status });
      if (kind === 'edited' && r.ok) editedOk = true;
      return r;
    } catch (e) {
      if (kind) log.push({ kind, ev: 'fail' });
      throw e;
    } finally {
      if (kind === 'state') stateInflight -= 1;
    }
  };
  const box = document.getElementById('edited-status');
  new MutationObserver(() => window.__status.push({ text: box.textContent, saved: editedOk, pasted: window.__pasted }))
    .observe(box, { childList: true, characterData: true, subtree: true });
}'''
DIRTY_JS = '''() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }'''


def race_scenario(browser, step) -> None:
    """MOCK_DELAY_MS=1500：按定稿後立刻貼修改後文案，edited-final 必須等定稿的 state 存好才送、最後一定存到。"""
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    http = Http(f'{base}/functions/v1/panel-api')
    server = start_server(port, delay_ms=1500)
    try:
        token = http.ok('POST', '/bot/projects', {'title': '延遲測試', 'categories': CATEGORIES})['token']
        jpeg = io.BytesIO()
        Image.new('RGB', (40, 40), (200, 100, 100)).save(jpeg, 'JPEG')

        def make(sku: str, out: list):   # 使用者端 API 建立一個填好的商品（每個請求都慢 1.5 秒，所以兩個並行）
            pid = http.ok('POST', f'/p/{token}/products', {'sku': sku}, bot=False)['id']
            http.ok('PUT', f'/p/{token}/products/{pid}/fields', {'fields': {
                'name': f'延遲測試 {sku}', 'category': '洋裝', 'colors': ['白'], 'material': '棉'}}, bot=False)
            up = http.ok('POST', f'/p/{token}/products/{pid}/upload-url',
                         {'name': 'a.jpg', 'type': 'image/jpeg', 'size': len(jpeg.getvalue())}, bot=False)
            urllib.request.urlopen(urllib.request.Request(up['signed_url'], data=jpeg.getvalue(), method='PUT'))
            http.ok('POST', f'/p/{token}/products/{pid}/photos', {'path': up['path'], 'name': 'a.jpg'}, bot=False)
            out.append((sku, pid))

        made: list = []
        threads = [threading.Thread(target=make, args=(sku, made)) for sku in ('LAG-1', 'LAG-2')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        pids = dict(made)
        assert len(pids) == 2, made
        http.ok('POST', f'/p/{token}/submit', {'product_ids': list(pids.values())}, bot=False)
        for pid in pids.values():
            http.ok('POST', f'/bot/projects/{token}/products/{pid}/claim', {})
            http.ok('POST', f'/bot/projects/{token}/products/{pid}/result', RESULT1)
        export = lambda pid: next(p for p in http.ok('GET', f'/bot/projects/{token}/export')['products'] if p['id'] == pid)

        ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, locale='zh-TW')
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: errors.append(m.text)
                if m.type == 'error' and not m.text.startswith('Failed to load resource') else None)

        def open_and_finalize(pid: str):
            page.goto(f'{base}/p/{token}?item={pid}')
            expect(page.locator('#pane-pick')).to_be_visible(timeout=15000)
            page.evaluate(WATCH_JS)
            page.locator('[data-id=A1] .heart').click()
            page.locator('[data-id=A2] .heart').click()   # 兩個 ♡ 的 state 存檔也還在飛，就直接去定稿
            page.locator('#fab').click()
            page.locator('#finalize-btn').click()
            page.locator('#confirm-yes').click()
            expect(page.locator('#final-box')).to_be_visible()
            page.evaluate('window.__pasted = true')
            page.locator('#edited-text').fill(EDITED)   # 不等定稿存好，立刻貼上

        def check_status_log():
            log = page.evaluate('window.__status')
            bad = [s for s in log if s['pasted'] and (s['text'] == '' or (s['text'] == '已儲存' and not s['saved']))]
            assert not bad, f'貼上後出現空白或「已儲存」但其實沒存：{log}'
            return log

        # A. 1.5 秒延遲下，按定稿後立刻貼上：edited-final 等到定稿的 state 存好才送出，一次就成功
        pid = pids['LAG-1']
        open_and_finalize(pid)
        expect(page.locator('#edited-status')).to_have_text('尚未儲存')
        assert page.evaluate(DIRTY_JS), '修改後文案還沒存時，離開頁面應該要提示'
        expect(page.locator('#edited-status')).to_have_text('已儲存', timeout=20000)
        reqs = page.evaluate('window.__log')
        edited = [r for r in reqs if r['kind'] == 'edited']
        assert edited and all(r['stateInflight'] == 0 for r in edited if r['ev'] == 'start'), f'edited-final 在 state 還沒存完時就送出：{reqs}'
        assert [r['status'] for r in edited if r['ev'] == 'end'] == [200], reqs
        states = [r for r in reqs if r['kind'] == 'state']
        assert all(r['status'] == 200 for r in states if r['ev'] == 'end'), f'state 請求不該互相打架（stale）：{reqs}'
        log = check_status_log()
        assert any(s['text'] == '已儲存' for s in log), log
        e = export(pid)
        assert e['status'] == 'finalized' and e['edited_final'] == EDITED and e['edited_at'], e
        assert e['state']['hearts'] == ['A1', 'A2'], e['state']
        expect(page.locator('#save-status')).to_have_text('已自動儲存', timeout=10000)
        assert not page.evaluate(DIRTY_JS), '都存好了不該再提示'
        page.reload()
        expect(page.locator('#edited-text')).to_have_value(EDITED, timeout=15000)
        step('延遲 1.5 秒：定稿後立刻貼上修改後文案，等定稿存好才送 edited-final，最後有存到（export 驗證），過程沒有顯示空白')

        # B. 定稿的 state 一直存不到：edited-final 回 409 → 等 state 重存後重試一次仍失敗 → 維持「尚未儲存」、不丟字、可重試
        pid = pids['LAG-2']
        blocked = lambda route: route.abort() if route.request.method == 'PUT' and '"final":{' in (route.request.post_data or '') else route.continue_()
        page.route('**/state', blocked)
        open_and_finalize(pid)
        expect(page.locator('#banner')).to_contain_text('修改後的文案還沒存到', timeout=20000)
        expect(page.locator('#edited-status')).to_have_text('尚未儲存')
        expect(page.locator('#edited-text')).to_have_value(EDITED)
        ends = [r.get('status') for r in page.evaluate('window.__log') if r['kind'] == 'edited' and r['ev'] == 'end']
        assert ends == [409, 409], f'應該是 409 → 重存 state → 再試一次 409：{ends}'
        assert export(pid)['edited_final'] is None
        assert page.evaluate(DIRTY_JS), '沒存到時離開頁面應該要提示'
        page.unroute('**/state', blocked)
        page.locator('#banner-retry').click()
        expect(page.locator('#edited-status')).to_have_text('已儲存', timeout=20000)
        check_status_log()
        e = export(pid)
        assert e['status'] == 'finalized' and e['edited_final'] == EDITED, e
        step('定稿一直存不到時：edited-final 409 會先重存定稿再重試，仍失敗就維持「尚未儲存」並可重試；恢復後重試成功')
        assert not errors, errors
        ctx.close()
    finally:
        server.terminate()
        server.wait(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', default='/workspace/panel-shots/v2')
    ap.add_argument('--headed', action='store_true')
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='panel-e2e-'))
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    http = Http(f'{base}/functions/v1/panel-api')
    server = start_server(port)
    step = lambda msg: print(f'✓ {msg}', flush=True)
    try:
        http.ok('PUT', '/bot/config', {'webhook_url': f'{base}/dev/webhook', 'webhook_key': WEBHOOK_KEY})
        token = http.ok('POST', '/bot/projects', {'title': '11 月第一週新品', 'categories': CATEGORIES})['token']
        assert len(token) == 43, token
        link = f'{base}/p/{token}'
        webhooks = lambda: json.loads(urllib.request.urlopen(base + '/dev/webhooks').read())
        photos = make_photos(tmp / 'photos')
        step('Bot 建立專案、設定 webhook')

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            ctx = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, locale='zh-TW')
            ctx.grant_permissions(['clipboard-read', 'clipboard-write'], origin=base)
            page = ctx.new_page()
            errors, foreign = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            # 404、409、斷線是測試故意造成的（瀏覽器會印 Failed to load resource）；其他 console 錯誤（含 CSP 違規）都算失敗
            page.on('console', lambda m: errors.append(m.text)
                    if m.type == 'error' and not m.text.startswith('Failed to load resource') else None)
            page.on('request', lambda r: foreign.append(r.url) if not r.url.startswith((base, 'blob:', 'data:')) else None)

            def check_targets(where: str):
                small = page.evaluate(TARGETS_JS)
                assert not small, f'{where}：點擊目標小於 48px：{small}'
                fonts = page.evaluate(FONTS_JS)
                assert all(f >= 16 for f in fonts), f'{where}：輸入框字級 {fonts}'

            def back_home():
                page.locator('#back-home').click()
                expect(page.locator('#view-home')).to_be_visible()

            # 0. 沒有 token、錯的 token、舊版 /s/ 連結
            page.goto(base + '/')
            expect(page.locator('#view-missing')).to_be_visible()
            page.goto(base + '/p/' + 'x' * 43)
            expect(page.locator('#view-missing')).to_be_visible()
            expect(page.locator('#missing-title')).to_have_text('找不到這個專案連結')
            page.goto(base + '/s/' + 'y' * 43)
            expect(page.locator('#view-missing')).to_be_visible()
            expect(page.locator('#missing-text')).to_contain_text('這是舊版連結，請回聊天室跟小編要新的專案連結')
            step('沒有／錯的 token 顯示說明；舊 /s/ 連結顯示「這是舊版連結」')

            # 1. 專案首頁（空）
            page.goto(link)
            expect(page.locator('#view-home')).to_be_visible()
            expect(page.locator('#view-home h2').first).to_have_text('這期專案')
            expect(page.locator('#product-empty')).to_be_visible()
            page.screenshot(path=str(shots / '01-home.png'))
            check_targets('首頁')

            # 2. 新增商品對話框：格式錯誤 → 正確
            page.locator('#add-product').click()
            expect(page.locator('#add-dialog')).to_be_visible()
            page.locator('#add-sku').fill('DR 2405')
            page.locator('#add-ok').click()
            expect(page.locator('#add-error')).to_contain_text('不能有空白或斜線')
            page.locator('#add-sku').fill('DR-2405')
            page.screenshot(path=str(shots / '02-add-dialog.png'))
            check_targets('新增商品對話框')
            page.locator('#add-ok').click()
            expect(page.locator('#view-product')).to_be_visible()
            expect(page.locator('#pane-form')).to_be_visible()
            assert '?item=' in page.url, page.url
            pid1 = page.url.split('?item=')[1]
            expect(page.locator('#product-head')).to_contain_text('DR-2405')
            expect(page.locator('#sku-input')).to_have_value('DR-2405')
            step('新增商品：格式錯誤會擋下，新增後直接進入商品畫面')

            # 3. 商品 1：先按送出 → 指出缺什麼；上傳照片、填欄位
            page.locator('#submit-btn').click()
            expect(page.locator('#form-missing')).to_contain_text('照片')
            expect(page.locator('#form-missing')).to_contain_text('商品名稱')
            page.locator('#file-input').set_input_files([str(p) for p in photos[:2]])
            expect(page.locator('.photo.done')).to_have_count(2, timeout=20000)
            f = page.locator('#fields-form')
            f.locator('[name=name]').fill('馬賽小鎮 V領抓皺腰抽繩短洋裝')
            f.locator('[name=category]').select_option('洋裝')
            page.locator('#color-input').fill('白')
            page.locator('#color-input').press('Enter')
            page.locator('#color-input').fill('芥末黃')
            page.locator('#color-add').click()
            expect(page.locator('#color-tags .tag')).to_have_count(2)
            f.locator('[name=material]').fill('✦材質成分：85%棉+15%聚酯')
            f.locator('[name=sizes]').fill('S、M')
            f.locator('[name=price]').fill('1280')
            f.locator('[name=background]').fill('夏天熱賣款改版，這次補了芥末黃新色。')
            f.locator('[name=feelings]').fill('小編 160 公分穿 S 剛好。')
            expect(page.locator('#form-missing')).to_be_empty()
            expect(page.locator('#save-status')).to_have_text('已自動儲存', timeout=5000)
            expect(page.locator('#product-head')).to_contain_text('馬賽小鎮')
            page.screenshot(path=str(shots / '03-product-form.png'), full_page=True)
            check_targets('商品填寫')
            s = http.ok('GET', f'/bot/projects/{token}/products/{pid1}')
            assert s['fields']['colors'] == ['白', '芥末黃'] and s['fields']['price'] == '1280', s['fields']
            sizes = []
            for p in s['photos']:
                assert p['path'].startswith(f'{token}/{pid1}/'), p['path']
                with Image.open(io.BytesIO(urllib.request.urlopen(p['url']).read())) as im:
                    sizes.append(im.size)
            assert sizes[0] == (1067, 1600), f'EXIF rotate + resize failed: {sizes}'
            step(f'商品 1：上傳 2 張照片（{sizes}，EXIF 方向已套用）、欄位自動儲存')

            # 4. 商品 2：新增時重複編號被擋；改編號時伺服器回重複
            back_home()
            expect(page.locator('#product-list .pcard')).to_have_count(1)
            page.locator('#add-product').click()
            page.locator('#add-sku').fill('DR-2405')
            page.locator('#add-ok').click()
            expect(page.locator('#add-error')).to_contain_text('已經有了')
            status, body = http.call('POST', f'/p/{token}/products', {'sku': 'DR-2405'}, bot=False)
            assert status == 409 and body['error'] == 'duplicate_sku', (status, body)
            page.locator('#add-sku').fill('TP-0611')
            page.locator('#add-ok').click()
            expect(page.locator('#pane-form')).to_be_visible()
            pid2 = page.url.split('?item=')[1]
            assert pid2 != pid1
            page.locator('#sku-input').fill('DR-2405')
            expect(page.locator('#sku-error')).to_contain_text('已經有了', timeout=5000)
            page.locator('#sku-input').fill('TP-0612')
            expect(page.locator('#product-head')).to_contain_text('TP-0612', timeout=5000)
            expect(page.locator('#sku-error')).to_be_empty()
            page.locator('#file-input').set_input_files([str(photos[2])])
            expect(page.locator('.photo.done')).to_have_count(1, timeout=20000)
            f.locator('[name=name]').fill('短版針織背心')
            f.locator('[name=category]').select_option('上衣')
            page.locator('#color-input').fill('燕麥')
            page.locator('#color-input').press('Enter')
            expect(page.locator('#save-status')).to_have_text('已自動儲存', timeout=5000)
            assert http.ok('GET', f'/bot/projects/{token}/products/{pid2}')['sku'] == 'TP-0612'
            step('重複商品編號：新增時與改編號時都會擋下；商品 2 故意不填材質')

            # 5. 首頁：勾選兩個一起送出 → 指出商品 2 缺材質（全部不送出）→ 去補 → 再送
            back_home()
            cards = page.locator('#product-list .pcard')
            expect(cards).to_have_count(2)
            page.locator('#submit-selected').click()
            expect(page.locator('#home-missing')).to_contain_text('先勾選')
            for sku in ('DR-2405', 'TP-0612'):
                page.locator(f'.pcard[data-sku="{sku}"] .selbox').click()
            expect(page.locator('#submit-selected')).to_have_text('送出選取的商品（2）')
            page.screenshot(path=str(shots / '04-home-selected.png'))
            check_targets('首頁已勾選')
            page.locator('#submit-selected').click()
            expect(page.locator('#home-missing')).to_contain_text('「TP-0612」還缺：材質')
            assert 'DR-2405' not in page.locator('#home-missing').inner_text()
            info = http.ok('GET', f'/bot/projects/{token}')
            assert [p['status'] for p in info['products']] == ['draft', 'draft'], info
            page.get_by_role('button', name='去補 TP-0612').click()
            expect(page.locator('#pane-form')).to_be_visible()
            assert page.url.endswith(pid2)
            f.locator('[name=material]').fill('✦材質成分：100%羊毛')
            expect(page.locator('#save-status')).to_have_text('已自動儲存', timeout=5000)
            page.go_back()   # 瀏覽器返回鍵回首頁
            expect(page.locator('#view-home')).to_be_visible()
            expect(page.locator('#submit-selected')).to_have_text('送出選取的商品（2）')
            page.locator('#submit-selected').click()
            expect(page.locator('#toast')).to_contain_text('已送出 2 個商品')
            expect(page.locator('.pcard .st-submitted')).to_have_count(2)
            expect(page.locator('.pcard input[type=checkbox]')).to_have_count(0)
            hooks = [w for w in webhooks() if w['body']['event'] == 'panel.products.submitted']
            assert len(hooks) == 1, hooks
            assert hooks[0]['authorization'] == f'Bearer {WEBHOOK_KEY}'
            assert hooks[0]['body']['product_ids'] == [pid1, pid2] and hooks[0]['body']['skus'] == ['DR-2405', 'TP-0612'], hooks
            assert hooks[0]['body']['project_token'] == token
            step('缺欄位時指出「哪個商品缺什麼」且全部不送出；補齊後兩個一起送出，只發一次 webhook')

            # 6. 等待畫面
            page.locator('.pcard[data-sku="DR-2405"] .pbody').click()
            expect(page.locator('#pane-wait')).to_be_visible()
            page.wait_for_timeout(1200)
            expect(page.locator('#wait-time')).not_to_have_text('0 秒')
            page.screenshot(path=str(shots / '05-wait.png'))
            check_targets('等待')

            # Bot：queue → 原子 claim（兩個同時領只有一個成功）→ force → result
            q = http.ok('GET', f'/bot/queue?project={token}')
            assert [x['product_id'] for x in q] == [pid1, pid2], q
            claims = []
            threads = [threading.Thread(target=lambda: claims.append(
                http.ok('POST', f'/bot/projects/{token}/products/{pid1}/claim', {}))) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert sorted(c['claimed'] for c in claims) == [False, True], claims
            again = http.ok('POST', f'/bot/projects/{token}/products/{pid1}/claim', {})
            assert again['claimed'] is False and again['status'] == 'generating' and again['claimed_at'], again
            assert http.ok('POST', f'/bot/projects/{token}/products/{pid1}/claim', {'force': True})['claimed'] is True
            assert http.ok('POST', f'/bot/projects/{token}/products/{pid2}/claim', {})['claimed'] is True
            assert http.ok('GET', '/bot/queue?status=submitted,generating') and not http.ok('GET', '/bot/queue')
            expect(page.locator('#wait-title')).to_have_text('小編正在寫文案', timeout=8000)
            http.ok('POST', f'/bot/projects/{token}/products/{pid1}/result', RESULT1)
            http.ok('POST', f'/bot/projects/{token}/products/{pid2}/result', RESULT2)
            expect(page.locator('#pane-pick')).to_be_visible(timeout=8000)
            step('等待畫面；Bot claim 是原子的（同時兩次只有一次成功、force 可重領）；上傳句子後自動切到挑句子')

            # 7. 首頁高亮「可挑句」
            back_home()
            expect(page.locator('.pcard.hot')).to_have_count(2)
            expect(page.locator('.pcard.hot .st').first).to_have_text('✨ 可挑句')
            page.locator('.pcard[data-sku="DR-2405"] .pbody').click()

            # 8. 挑句子
            expect(page.locator('#pane-pick')).to_be_visible()
            expect(page.locator('#cards .cardx')).to_have_count(3)
            expect(page.locator('#cards .cardx').nth(2).locator('.badge')).to_have_text('⚠ 需確認')
            page.locator('[data-id=A2] .heart').click()
            page.locator('[data-id=A1] .heart').click()
            page.locator('.tab[data-route=B]').click()
            page.locator('[data-id=B1] .heart').click()
            page.locator('[data-id=B2] .dislike').click()
            expect(page.locator('#hidden-summary')).to_have_text('已隱藏 1 句（可復原）')
            expect(page.locator('#fab')).to_have_text('我的文案 (3)')
            page.screenshot(path=str(shots / '06-pick.png'))
            check_targets('挑句子')
            page.locator('#fab').click()
            page.locator('#banned-input').fill('超級')
            page.locator('#banned-input').press('Enter')
            expect(page.locator('#hidden-summary')).to_have_text('已隱藏 2 句（可復原）')
            page.get_by_role('button', name='第 3 句往上移').click()
            page.get_by_role('button', name='第 2 句往上移').click()
            check_targets('我的文案面板')
            page.locator('#finalize-btn').click()
            expect(page.locator('#confirm')).to_contain_text('定稿後會記錄成團隊的喜好')
            page.locator('#confirm-yes').click()
            final = '腰間抽繩 鬆緊自己調！\nV領配上胸前抓皺 女人味剛剛好\n夏天熱賣款改版 這次補了芥末黃新色！\n✦材質成分：85%棉+15%聚酯'
            expect(page.locator('#final-text')).to_have_text(final)
            expect(page.locator('#save-status')).to_have_text('已自動儲存', timeout=5000)
            assert http.ok('GET', f'/bot/projects/{token}/products/{pid1}')['status'] == 'finalized'
            step('挑句子：♡／✕／禁用詞／排序／定稿都作用在這個商品')

            # 9. 我修改後的最終文案
            expect(page.locator('#edited-text')).to_have_value('')
            expect(page.locator('#edited-text')).to_have_attribute('placeholder', '把你實際使用、修改後的版本貼在這裡，結束專案後小編會學習你的文字偏好')
            page.locator('#edited-seed').click()
            expect(page.locator('#edited-text')).to_have_value(final)
            page.locator('#edited-text').fill(EDITED)
            expect(page.locator('#edited-status')).to_have_text('已儲存', timeout=5000)
            page.screenshot(path=str(shots / '07-final-edited.png'), full_page=True)
            check_targets('定稿＋修改後文案')
            page.locator('#edited-copy').click()
            expect(page.locator('#toast')).to_have_text('已複製')
            assert page.evaluate('navigator.clipboard.readText()') == EDITED
            s = http.ok('GET', f'/bot/projects/{token}/products/{pid1}')
            assert s['edited_final'] == EDITED and s['edited_at'], s
            rev_before = s['state_rev']
            page.reload()
            expect(page.locator('#final-text')).to_have_text(final)
            expect(page.locator('#edited-text')).to_have_value(EDITED)
            expect(page.locator('#edited-status')).to_have_text('已儲存')
            # 「用定稿全文當起點」在已有內容時會先確認，取消就不動
            page.locator('#edited-seed').click()
            expect(page.locator('#confirm')).to_be_visible()
            page.locator('#confirm-no').click()
            expect(page.locator('#edited-text')).to_have_value(EDITED)
            assert http.ok('GET', f'/bot/projects/{token}/products/{pid1}')['state_rev'] == rev_before
            step('定稿後貼修改後文案：自動儲存、重新整理仍在、不影響 state_rev')

            # 10. 斷線重試
            ctx.set_offline(True)
            page.locator('#edited-text').fill(EDITED + '\n（斷線時補的一行）')
            expect(page.locator('#banner')).to_be_visible(timeout=5000)
            expect(page.locator('#edited-status')).to_have_text('尚未儲存')
            ctx.set_offline(False)
            page.locator('#banner-retry').click()
            expect(page.locator('#edited-status')).to_have_text('已儲存', timeout=5000)
            assert http.ok('GET', f'/bot/projects/{token}/products/{pid1}')['edited_final'].endswith('（斷線時補的一行）')
            page.locator('#edited-text').fill(EDITED)
            expect(page.locator('#edited-status')).to_have_text('已儲存', timeout=5000)
            step('斷線時顯示重試提示、文字不丟，恢復後重試成功')

            # 11. 結束專案：取消不結束 → 確定結束
            back_home()
            page.locator('#close-project').click()
            expect(page.locator('#confirm')).to_be_visible()
            expect(page.locator('#confirm')).to_contain_text('結束後這個網址會變成唯讀，不能再新增或修改。確定要結束嗎？')
            expect(page.locator('#confirm-extra')).to_contain_text('還有 1 個商品尚未定稿')
            page.screenshot(path=str(shots / '08-close-confirm.png'))
            check_targets('結束專案確認')
            page.locator('#confirm-no').click()
            expect(page.locator('#confirm')).to_be_hidden()
            assert http.ok('GET', f'/bot/projects/{token}')['status'] == 'open'
            expect(page.locator('#view-home')).to_be_visible()
            page.locator('#close-project').click()
            page.locator('#confirm-yes').click()
            expect(page.locator('#view-closed')).to_be_visible()
            step('結束專案確認對話框：取消不結束；確定後切到唯讀總覽')

            # 12. 唯讀總覽
            expect(page.locator('#view-closed h2')).to_have_text('這期專案已結束')
            c1 = page.locator('.ccard[data-sku="DR-2405"]')
            expect(c1.locator('.source')).to_have_text('我修改後的最終文案')
            expect(c1.locator('pre')).to_have_text(EDITED)
            expect(page.locator('.ccard[data-sku="TP-0612"] .source')).to_contain_text('沒有定稿')
            assert page.locator('#view-closed').locator('input, textarea, select').count() == 0
            page.screenshot(path=str(shots / '09-readonly.png'), full_page=True)
            check_targets('唯讀總覽')
            c1.get_by_role('button', name='複製 DR-2405 的文案').click()
            expect(page.locator('#toast')).to_have_text('已複製')
            assert page.evaluate('navigator.clipboard.readText()') == EDITED
            page.reload()
            expect(page.locator('#view-closed')).to_be_visible()
            page.goto(f'{link}?item={pid1}')
            expect(page.locator('#view-closed')).to_be_visible()
            assert '?item=' not in page.url
            status, body = http.call('PUT', f'/p/{token}/products/{pid1}/edited-final', {'text': 'x'}, bot=False)
            assert status == 409 and body['error'] == 'project_closed', (status, body)
            status, body = http.call('POST', f'/p/{token}/products', {'sku': 'NEW-1'}, bot=False)
            assert status == 409 and body['error'] == 'project_closed', (status, body)
            closed = [w for w in webhooks() if w['body']['event'] == 'panel.project.closed']
            assert len(closed) == 1 and closed[0]['body']['skus'] == ['DR-2405', 'TP-0612'], closed
            exp = http.ok('GET', f'/bot/projects/{token}/export')
            e1 = exp['products'][0]
            assert exp['status'] == 'closed' and e1['edited_final'] == EDITED and e1['state']['final']['text'] == final, e1
            assert e1['routes']['A'][0] == {'id': 'A1', 'text': RESULT1['routes']['A'][0]['text'], 'check': False}, e1['routes']
            assert 'photos' not in json.dumps(exp), '匯出不該含照片'
            step('唯讀總覽：顯示修改後文案（沒有就標示來源）、可複製、沒有編輯控制項；重新整理仍唯讀；寫入回 409；export 正確')

            # 不存在的商品 id → 回首頁；（重開專案後測）
            http.ok('POST', f'/bot/projects/{token}/reopen')
            page.goto(f'{link}?item=00000000-0000-4000-8000-000000000000')
            expect(page.locator('#view-home')).to_be_visible()
            assert '?item=' not in page.url
            step('不存在的商品 id 會回到首頁')

            assert not errors, errors
            assert not foreign, f'有外部請求：{foreign}'
            step('沒有 JS／console 錯誤（含 CSP），也沒有任何外部請求；各畫面點擊目標 ≥48px、輸入框字級 ≥16px')

            race_scenario(browser, step)
            browser.close()
        print(f'\n全部通過。截圖在 {shots}')
    finally:
        server.terminate()
        server.wait(5)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
