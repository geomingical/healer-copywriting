"""Tests for panel/build.mjs (writes public/config.js from PANEL_API_URL or --api).

  python3 -m unittest panel/dev/test_build.py -v

Each test copies build.mjs into a temp folder and runs it there, so the real panel/public/ is never written.
Skipped when node is not installed.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PANEL = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
GOOD = 'https://abcdefghijklmnop.supabase.co/functions/v1/panel-api'
CJK = re.compile(r'[一-鿿]')


@unittest.skipUnless(NODE, 'node is not installed')
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='panel-build-'))
        shutil.copy(PANEL / 'build.mjs', self.tmp / 'build.mjs')
        (self.tmp / 'public').mkdir()
        self.config = self.tmp / 'public' / 'config.js'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_build(self, *args, api=None):
        env = {k: v for k, v in os.environ.items() if k != 'PANEL_API_URL'}
        if api is not None:
            env['PANEL_API_URL'] = api
        return subprocess.run([NODE, str(self.tmp / 'build.mjs'), *args], cwd=self.tmp, env=env,
                              capture_output=True, text=True, timeout=30)

    def assert_written(self, r, api):
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.config.read_text(encoding='utf-8')
        self.assertIn(f'window.PANEL_CONFIG = {{ api: "{api}" }};', text)
        self.assertIn('不放任何金鑰', text)

    def assert_rejected(self, r):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stderr, CJK, 'error message should be in Chinese')
        self.assertFalse(self.config.exists(), 'config.js must not be written on error')

    def test_env_url(self):
        self.assert_written(self.run_build(api=GOOD), GOOD)

    def test_cli_url_wins_over_env(self):
        other = 'https://zzzzzzzzzzzzzzzzzzzz.supabase.co/functions/v1/panel-api'
        self.assert_written(self.run_build('--api', other, api=GOOD), other)
        self.config.unlink()
        self.assert_written(self.run_build(f'--api={GOOD}'), GOOD)

    def test_trailing_slash_removed(self):
        self.assert_written(self.run_build(api=GOOD + '/'), GOOD)
        self.assert_written(self.run_build(api='  ' + GOOD + '//  '), GOOD)

    def test_missing(self):
        self.assert_rejected(self.run_build())
        self.assert_rejected(self.run_build(api='   '))
        self.assert_rejected(self.run_build('--api'))

    def test_http_rejected(self):
        self.assert_rejected(self.run_build(api=GOOD.replace('https://', 'http://')))

    def test_bad_path_rejected(self):
        for bad in ('https://abcdefghijklmnop.supabase.co',
                    'https://abcdefghijklmnop.supabase.co/functions/v1/other-api',
                    'https://abcdefghijklmnop.supabase.co/functions/v1/panel-api/bot',
                    GOOD + '?x=1',
                    'not a url'):
            with self.subTest(bad=bad):
                self.assert_rejected(self.run_build(api=bad))


if __name__ == '__main__':
    unittest.main()
