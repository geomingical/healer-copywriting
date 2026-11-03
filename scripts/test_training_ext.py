"""Tests for the training-record extension: disliked sentences, sizes/price lines, v2 edited_text and
style_notes (文字偏好), and the byte-identical regressions.

  python3 -m unittest scripts/test_training_ext.py -v

The regression tests compare against task.md / plan.json written by the pre-change prepare.py:
  * Regression   — before disliked / sizes / price (env HEALER_BASELINE, default /tmp/reg/baseline,
                   made by /tmp/reg/setup_baseline.py)
  * RegressionV2 — before style_notes / edited_text (env HEALER_BASELINE_V2, default /tmp/reg2/baseline,
                   made by /tmp/reg2/setup_baseline.py)
Each is skipped when its baseline is absent. Inputs and outputs live in a temp folder; data/ is never written.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import training  # noqa: E402

BASELINE = Path(os.environ.get('HEALER_BASELINE', '/tmp/reg/baseline'))
INPUTS = BASELINE.parent / 'in'
# v2 baseline: written by prepare.py as it was right before style_notes / edited_text (/tmp/reg2/setup_baseline.py)
BASELINE_V2 = Path(os.environ.get('HEALER_BASELINE_V2', '/tmp/reg2/baseline'))
INPUTS_V2 = BASELINE_V2.parent / 'in'
PROTECTED = ('data/fingerprints.json', 'data/training.json')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='healer-test-'))
        self.protected = {p: digest(REPO / p) for p in PROTECTED}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for p, h in self.protected.items():
            self.assertEqual(digest(REPO / p), h, f'{p} was modified')

    def prepare(self, spec: Path, record: Path, name: str) -> Path:
        out = self.tmp / name
        subprocess.run([sys.executable, str(SCRIPTS / 'prepare.py'), '--new', str(spec), '--output', str(out),
                        '--training', str(record)], check=True, stdout=subprocess.DEVNULL)
        return out

    def training_cli(self, record: Path, *args) -> str:
        return subprocess.run([sys.executable, str(SCRIPTS / 'training.py'), '--training', str(record), *args],
                              check=True, capture_output=True, text=True).stdout


@unittest.skipUnless((BASELINE / 'plain__missing').is_dir(), f'no baseline at {BASELINE}')
class Regression(Base):
    """Empty record (incl. old files without 'disliked') and blank sizes/price → output identical to before."""

    def check(self, spec: str, record: str, baseline: str):
        rec_path = INPUTS / f'rec-{record}.json' if record != 'missing' else self.tmp / 'missing.json'
        out = self.prepare(INPUTS / f'{spec}.json', rec_path, f'{spec}__{record}')
        before = BASELINE / baseline
        for rel in ('plan.json', 'test-2609070031-S/task.md', 'test-2609070031-S/product.json'):
            self.assertEqual((out / rel).read_bytes(), (before / rel).read_bytes(), f'{spec}/{record}: {rel} differs')

    def test_identical(self):
        for spec, base_spec in (('plain', 'plain'), ('plain-blank-size', 'plain'), ('info', 'info')):
            for record in ('missing', 'old-empty', 'new-empty', 'old-full'):
                with self.subTest(spec=spec, record=record):
                    self.check(spec, record, f'{base_spec}__{record}')


class Features(Base):
    def spec(self, **extra) -> Path:
        src = REPO / 'runs/2026-11-03-test-2609070031/input'
        photos = self.tmp / 'photos'
        if not photos.exists():
            shutil.copytree(INPUTS / 'photos' if (INPUTS / 'photos').is_dir() else src / 'photos', photos)
        spec = {'id': 'new-001', 'name': '馬賽小鎮 V領抓皺腰抽繩流蘇短洋裝', 'category': '洋裝', 'colors': ['白'],
                'material': '✦材質成分：85%棉+15%聚酯', 'photos': 'photos', **extra}
        path = self.tmp / f'spec-{len(list(self.tmp.glob("spec-*")))}.json'
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        return path

    def task(self, out: Path) -> str:
        return (out / 'new-001-S/task.md').read_text(encoding='utf-8')

    def test_sizes_and_price_lines(self):
        rec = self.tmp / 'rec.json'
        task = self.task(self.prepare(self.spec(sizes='S、M', price=1280), rec, 'a'))
        self.assertIn('材質：✦材質成分：85%棉+15%聚酯\n尺寸：S、M\n價格：1280\n照片：', task)
        task = self.task(self.prepare(self.spec(price='NT$990'), rec, 'b'))
        self.assertIn('15%聚酯\n價格：NT$990\n照片：', task)
        self.assertNotIn('尺寸：', task.split('# 寫作規則')[0])

    def test_old_record_without_disliked_loads(self):
        rec = self.tmp / 'rec.json'
        rec.write_text('{"hearts": [], "banned": [], "finals": []}')
        self.assertEqual(training.load(rec)['disliked'], [])
        self.training_cli(rec, 'ban', '超級')
        self.assertEqual(json.loads(rec.read_text())['disliked'], [])   # written back with the key

    def test_dislike_cli(self):
        rec = self.tmp / 'rec.json'
        self.assertIn('已加入不喜歡', self.training_cli(rec, 'dislike', ' 好看到不行 ', '--route', 'b', '--product', 'p1'))
        self.assertIn('沒有重複加入', self.training_cli(rec, 'dislike', '好看到不行'))
        data = json.loads(rec.read_text())
        self.assertEqual([(d['text'], d['route'], d['product_id']) for d in data['disliked']], [('好看到不行', 'B', 'p1')])
        self.assertIn('不喜歡 1 句', self.training_cli(rec, 'show'))
        self.assertIn('✕ 好看到不行（路線 B）', self.training_cli(rec, 'show'))
        self.assertIn('已取消不喜歡', self.training_cli(rec, 'undislike', '好看到不行'))
        self.assertIn('沒有這句', self.training_cli(rec, 'undislike', '好看到不行'))
        self.assertEqual(json.loads(rec.read_text())['disliked'], [])
        self.assertIn('已加入 ♡：', self.training_cli(rec, 'heart', '一穿就愛上'))
        self.assertIn('已經有這句 ♡，沒有重複加入', self.training_cli(rec, 'heart', '一穿就愛上'))

    def test_disliked_in_task(self):
        rec = self.tmp / 'rec.json'
        rec.write_text(json.dumps({'hearts': [], 'banned': [], 'finals': [],
                                   'disliked': [{'text': f'不喜歡第{i}句'} for i in range(45)]}, ensure_ascii=False))
        task = self.task(self.prepare(self.spec(), rec, 'd'))
        self.assertIn('# 團隊喜好（訓練紀錄）', task)
        self.assertIn('## 不喜歡的句子（不要寫出相同或很像的句子與說法；最近 40 句）\n- 不喜歡第5句\n', task)
        self.assertNotIn('不喜歡第4句\n', task)
        self.assertIn('- 不喜歡第44句\n', task)

    def test_final_helper_idempotent(self):
        record = {k: [] for k in training.KEYS}
        self.assertTrue(training.add_final(record, 'p', 'n', '定稿一'))
        self.assertFalse(training.add_final(record, 'p', 'n', '定稿一'))
        self.assertTrue(training.add_final(record, 'p', 'n', '定稿二', replace=True))
        self.assertEqual([f['text'] for f in record['finals']], ['定稿二'])

    def test_final_edited_text(self):
        record = {k: [] for k in training.KEYS}
        self.assertTrue(training.add_final(record, 'p', 'n', '定稿', replace=True, edited_text='改過'))
        self.assertFalse(training.add_final(record, 'p', 'n', '定稿', replace=True, edited_text=' 改過 '))
        self.assertTrue(training.add_final(record, 'p', 'n', '定稿', replace=True, edited_text='又改了'))
        self.assertFalse(training.add_final(record, 'p', 'n', '定稿', replace=True))   # None = leave as is
        self.assertEqual(record['finals'], [{'product_id': 'p', 'name': 'n', 'text': '定稿', 'saved': training.today(),
                                             'edited_text': '又改了'}])
        self.assertTrue(training.add_final(record, 'p', 'n', '定稿', replace=True, edited_text=''))
        self.assertNotIn('edited_text', record['finals'][0])
        self.assertFalse(training.add_final(record, 'p', 'n', '定稿', replace=True, edited_text=''))
        # CLI: final --edited-file
        rec, final, edited = self.tmp / 'rec.json', self.tmp / 'f.txt', self.tmp / 'e.txt'
        final.write_text('定稿全文\n')
        edited.write_text('修改後全文\n')
        self.training_cli(rec, 'final', '--product', 'p1', '--name', '商品', '--file', str(final), '--edited-file', str(edited))
        self.assertEqual(json.loads(rec.read_text())['finals'][0]['edited_text'], '修改後全文')
        self.assertIn('含修改後版本 1 篇', self.training_cli(rec, 'show'))
        self.assertIn('（修改後版本 5 字）', self.training_cli(rec, 'show'))

    def test_style_notes_helpers(self):
        record = {k: [] for k in training.KEYS}
        self.assertTrue(training.add_note(record, '  句尾少用驚嘆號  ', '專案 abcd1234', '2026-11-03'))
        self.assertFalse(training.add_note(record, '句尾少用驚嘆號', '別的來源'))   # exact duplicate
        self.assertEqual(record['style_notes'], [{'note': '句尾少用驚嘆號', 'source': '專案 abcd1234', 'added': '2026-11-03'}])
        with self.assertRaises(SystemExit) as e:
            training.add_note(record, '長' * 81, 'x')
        self.assertIn('上限 80 字', str(e.exception))
        training.add_note(record, '長' * 80, 'x')   # exactly 80 is fine
        with self.assertRaises(SystemExit):
            training.add_note(record, '規則', '來' * 81)
        with self.assertRaises(SystemExit):
            training.add_note(record, '規則', 'x', '2026/11/03')
        with self.assertRaises(SystemExit):
            training.add_note(record, '規則', 'x', '2026-02-30')
        for i in range(training.STYLE_NOTES_MAX - 2):
            training.add_note(record, f'規則{i}', 'x')
        self.assertEqual(len(record['style_notes']), training.STYLE_NOTES_MAX)
        with self.assertRaises(SystemExit) as e:
            training.add_note(record, '第三十一條', 'x')
        self.assertIn('note remove', str(e.exception))
        self.assertFalse(training.add_note(record, '規則0', 'x'))   # a duplicate at the limit is just "already there"
        self.assertEqual(training.remove_note(record, 1)['note'], '句尾少用驚嘆號')
        with self.assertRaises(SystemExit):
            training.remove_note(record, 30)

    def test_note_cli(self):
        rec = self.tmp / 'rec.json'
        rec.write_text('{"hearts": [], "banned": [], "finals": [], "disliked": []}')   # file without style_notes
        self.assertEqual(training.load(rec)['style_notes'], [])
        self.assertIn('還沒有文字偏好', self.training_cli(rec, 'note', 'list'))
        self.assertIn('已加入文字偏好（第 1 條）：開頭直接講穿上的畫面',
                      self.training_cli(rec, 'note', 'add', '開頭直接講穿上的畫面', '--source', '專案 abcd1234 3 件', '--date', '2026-11-01'))
        self.assertIn('沒有重複加入', self.training_cli(rec, 'note', 'add', '開頭直接講穿上的畫面', '--source', 'y'))
        self.training_cli(rec, 'note', 'add', '少用「超」「很」等程度副詞', '--source', '專案 abcd1234')
        listed = self.training_cli(rec, 'note', 'list')
        self.assertIn('1. 開頭直接講穿上的畫面（來源：專案 abcd1234 3 件）  2026-11-01', listed)
        self.assertIn('2. 少用「超」「很」等程度副詞', listed)
        self.assertIn('共 2 條（上限 30）', listed)
        self.assertIn('文字偏好 2 條', self.training_cli(rec, 'show'))
        self.assertIn('已刪除第 1 條：開頭直接講穿上的畫面', self.training_cli(rec, 'note', 'remove', '1'))
        data = json.loads(rec.read_text())
        self.assertEqual([n['note'] for n in data['style_notes']], ['少用「超」「很」等程度副詞'])
        self.assertEqual(data['style_notes'][0]['added'], training.today())
        # refusals exit non-zero with a Chinese message, record untouched
        for args in (('note', 'add', '長' * 81, '--source', 'x'), ('note', 'remove', '5'), ('note', 'add', '規則')):
            p = subprocess.run([sys.executable, str(SCRIPTS / 'training.py'), '--training', str(rec), *args],
                               capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, args)
        self.assertEqual(json.loads(rec.read_text()), data)

    def test_style_notes_and_edited_text_in_task(self):
        rec = self.tmp / 'rec.json'
        notes = [{'note': f'規則{i}', 'source': 's', 'added': '2026-11-01'} for i in range(15)]
        finals = [{'product_id': 'p1', 'name': '舊商品', 'text': '原本的定稿'},
                  {'product_id': 'p2', 'name': '改過的商品', 'text': '原定稿二', 'edited_text': '使用者改過的版本二'}]
        rec.write_text(json.dumps({'hearts': [], 'banned': [], 'finals': finals, 'disliked': [], 'style_notes': notes},
                                  ensure_ascii=False))
        task = self.task(self.prepare(self.spec(), rec, 'n'))
        section = task.split('# 團隊喜好（訓練紀錄）')[1].split('# 同店其他商品的真實文案')[0]
        self.assertIn('## 文字偏好（從使用者修改後的文案歸納，最近 12 條）\n- 規則3\n', section)
        self.assertNotIn('- 規則2\n', section)
        self.assertIn('- 規則14', section)
        self.assertIn('只用來調整口氣、句型、用字習慣，**不能推翻** 方括號規則、官方色名、「小編提供的資訊」（背景資訊／自身感受）的使用限制、行數與字數規則', section)
        self.assertIn('### 定稿 1：舊商品\n原本的定稿', section)
        self.assertIn('### 定稿 2：改過的商品（使用者實際使用的修改後版本）\n使用者改過的版本二', section)
        self.assertNotIn('原定稿二', task)

    def test_new_content_only_in_its_places(self):
        """Adding style notes / edited_text to a full v1 record only inserts text in those two places."""
        base = {'hearts': [{'text': '一穿就愛上', 'route': 'A'}], 'banned': ['超級'], 'disliked': [{'text': '好看到不行'}],
                'finals': [{'product_id': 'p1', 'name': '舊商品', 'text': '原本的定稿'}]}
        a, b = self.tmp / 'a.json', self.tmp / 'b.json'
        a.write_text(json.dumps(base, ensure_ascii=False))
        extra = json.loads(json.dumps(base))
        extra['style_notes'] = [{'note': '句子短一點', 'source': 's'}]
        extra['finals'][0]['edited_text'] = '改過的定稿'
        b.write_text(json.dumps(extra, ensure_ascii=False))
        spec = self.spec()
        before, after = self.task(self.prepare(spec, a, 'a')), self.task(self.prepare(spec, b, 'b'))
        import difflib
        changes = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l[:1] in '+-']
        self.assertEqual(changes, [
            '+ - 「文字偏好」是從使用者實際使用、修改後的文案歸納出的習慣：只用來調整口氣、句型、用字習慣，**不能推翻** 方括號規則、'
            '官方色名、「小編提供的資訊」（背景資訊／自身感受）的使用限制、行數與字數規則；跟這些規則衝突時，以規則為準。',
            '+ ', '+ ## 文字偏好（從使用者修改後的文案歸納，最近 1 條）', '+ - 句子短一點',
            '- ### 定稿 1：舊商品', '- 原本的定稿', '+ ### 定稿 1：舊商品（使用者實際使用的修改後版本）', '+ 改過的定稿'])


class DefaultRecord(Base):
    """No --training = data/training.json; a missing record file reads as empty and only a writing command creates it.
    training.DEFAULT_PATH is patched to a temp folder, so the real data/training.json is never touched."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        self.default = self.tmp / 'data/training.json'
        patcher = mock.patch.object(training, 'DEFAULT_PATH', self.default)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, module, *argv) -> str:
        import contextlib
        import io
        from unittest import mock
        with mock.patch.object(sys, 'argv', [f'{module.__name__}.py', *argv]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            module.main()
        return out.getvalue()

    def photo_spec(self) -> Path:
        from PIL import Image
        (self.tmp / 'photos').mkdir()
        for n, color in enumerate(((230, 200, 120), (90, 120, 160)), 1):
            Image.new('RGB', (90, 120), color).save(self.tmp / f'photos/{n:02d}.jpg', 'JPEG')
        spec = self.tmp / 'spec.json'
        spec.write_text(json.dumps({'id': 'new-001', 'name': '測試 V領短洋裝', 'category': '洋裝', 'colors': ['白'],
                                    'material': '✦材質成分：100%棉', 'photos': 'photos'}, ensure_ascii=False))
        return spec

    def test_existing_default_untouched(self):
        self.default.parent.mkdir(parents=True)
        self.default.write_text('{"hearts": [], "banned": ["超級"], "finals": []}')
        training.record_path(None)
        self.assertEqual(json.loads(self.default.read_text())['banned'], ['超級'])

    def test_explicit_path_not_created(self):
        explicit = self.tmp / 'mine/training.json'
        self.assertEqual(training.record_path(str(explicit)), explicit)
        self.assertFalse(explicit.exists())
        self.assertIn('♡ 句子 0 句', self.training_cli(explicit, 'show'))
        self.assertIn('還沒有文字偏好', self.training_cli(explicit, 'note', 'list'))
        self.assertFalse(explicit.exists())
        self.assertFalse(self.default.exists())

    def test_training_cli_default_path(self):
        self.assertEqual(training.record_path(None), self.default)
        self.assertIn('♡ 句子 0 句', self.run_main(training, 'show'))
        self.assertFalse(self.default.exists())
        self.assertIn('已禁用「超級」', self.run_main(training, 'ban', '超級'))
        self.assertEqual(json.loads(self.default.read_text())['banned'], ['超級'])

    def test_prepare_default_path_same_as_empty_record(self):
        import prepare
        spec = self.photo_spec()
        missing = self.tmp / 'explicit-missing.json'
        explicit = self.prepare(spec, missing, 'explicit')
        self.assertFalse(missing.exists())
        default = self.tmp / 'default'
        self.run_main(prepare, '--new', str(spec), '--output', str(default))
        self.assertFalse(self.default.exists())
        for rel in ('plan.json', 'new-001-S/task.md', 'new-001-S/product.json'):
            self.assertEqual((default / rel).read_bytes(), (explicit / rel).read_bytes(), rel)
        self.assertNotIn('# 團隊喜好', (default / 'new-001-S/task.md').read_text(encoding='utf-8'))


@unittest.skipUnless((BASELINE_V2 / 'plain__missing').is_dir(), f'no v2 baseline at {BASELINE_V2}')
class RegressionV2(Base):
    """Task.md written before style_notes / edited_text existed (/tmp/reg2/setup_baseline.py) must not change
    when the record has no style notes and no edited_text — whether the new key is missing or empty."""

    def test_identical(self):
        for spec in ('plain', 'full'):
            for record in ('missing', 'v1-empty', 'v1-full'):
                variants = {'as-is': None}
                if record != 'missing':
                    data = json.loads((INPUTS_V2 / f'rec-{record}.json').read_text(encoding='utf-8'))
                    variants['empty-notes'] = dict(data, style_notes=[])
                    variants['blank-edited'] = dict(data, style_notes=[],
                                                    finals=[dict(f, edited_text='  ') for f in data['finals']])
                for variant, data in variants.items():
                    with self.subTest(spec=spec, record=record, variant=variant):
                        if record == 'missing':
                            rec = self.tmp / 'missing.json'
                        elif data is None:
                            rec = INPUTS_V2 / f'rec-{record}.json'
                        else:
                            rec = self.tmp / f'{record}-{variant}.json'
                            rec.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                        out = self.prepare(INPUTS_V2 / f'{spec}.json', rec, f'{spec}__{record}__{variant}')
                        before = BASELINE_V2 / f'{spec}__{record}'
                        for rel in ('plan.json', 'test-2609070031-S/task.md', 'test-2609070031-S/product.json'):
                            self.assertEqual((out / rel).read_bytes(), (before / rel).read_bytes(), rel)
        # the record files themselves were not rewritten by prepare.py
        self.assertNotIn('style_notes', (INPUTS_V2 / 'rec-v1-full.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
