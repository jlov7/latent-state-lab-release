import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from latent_state_lab.capsule import write_capsule
from latent_state_lab.fixture import PIN, ToySession


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.zip'

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'latent_state_lab.demo',
                               *map(str, args)], capture_output=True, text=True, timeout=10, check=False)

    def error(self, result, code):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        report = json.loads(result.stderr)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['code'], code)
        self.assertRegex(report['request_id'], r'^[0-9a-f]{32}$')
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn(str(self.path), result.stderr)

    def test_create_requires_explicit_overwrite(self):
        self.assertEqual(self.cli('create', self.path).returncode, 0)
        original = self.path.read_bytes()
        self.error(self.cli('create', self.path), 'destination_exists')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.cli('create', self.path, '--overwrite').returncode, 0)

    def test_missing_input_is_structured(self):
        self.error(self.cli('resume', self.path), 'input_missing')

    def test_uninterrupted_refuses_an_unused_capsule_path(self):
        result = self.cli('uninterrupted', self.path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn('uninterrupted does not accept a capsule path', result.stderr)
        self.assertFalse(self.path.exists())

    def test_fifo_input_refused_without_blocking_or_mutation(self):
        os.mkfifo(self.path)
        before = self.path.stat()
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'latent_state_lab.demo', 'resume', str(self.path)],
                capture_output=True, text=True, timeout=2, check=False)
        except subprocess.TimeoutExpired:
            self.fail('resume blocked on an empty FIFO; child killed and reaped')
        self.error(result, 'capsule_invalid')
        after = self.path.stat()
        self.assertTrue(stat.S_ISFIFO(after.st_mode))
        self.assertEqual((after.st_dev, after.st_ino, after.st_mtime_ns),
                         (before.st_dev, before.st_ino, before.st_mtime_ns))
        self.assertEqual(set(self.path.parent.iterdir()), {self.path})

    def test_corrupt_input_is_structured(self):
        self.path.write_bytes(b'not a zip')
        self.error(self.cli('resume', self.path), 'capsule_invalid')

    def test_incompatible_input_is_structured(self):
        write_capsule(self.path, dict(PIN, runtime_revision='toy-runtime-v1'),
                      ToySession.start().chunks())
        self.error(self.cli('resume', self.path), 'capsule_invalid')

    def test_nested_and_oversized_integer_manifest_errors_are_structured(self):
        for manifest in (b'[' * 2000 + b']' * 2000,
                         b'{"format_version":' + b'9' * 5000 + b'}'):
            with self.subTest(manifest=manifest[:24]):
                with zipfile.ZipFile(self.path, 'w') as archive:
                    archive.writestr('manifest.json', manifest)
                self.error(self.cli('resume', self.path), 'capsule_invalid')
