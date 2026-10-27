import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from latent_state_lab.batch import BatchError, aggregate


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'input.jsonl'
        self.checkpoint = self.root / 'checkpoint.zip'
        self.source.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in [
            {'team': 'café\nα', 'amount': 0.1}, {'team': '雪', 'amount': 2},
            {'team': 'café\nα', 'amount': 0.2}]), encoding='utf-8')

    def run_batch(self, **kwargs):
        return aggregate(self.source, self.checkpoint, group_by='team', value='amount', **kwargs)

    def test_exact_resume_matches_one_pass_in_another_process(self):
        stopped = self.run_batch(max_records=1)
        self.assertFalse(stopped['complete'])
        command = [sys.executable, '-m', 'latent_state_lab.batch', str(self.source),
                   str(self.checkpoint), '--resume', '--group-by', 'team', '--value', 'amount']
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        resumed = json.loads(result.stdout)
        full = aggregate(self.source, self.root / 'full.zip', group_by='team', value='amount')
        self.assertEqual(resumed['groups'], full['groups'])
        self.assertEqual(resumed['records_this_run'], 2)
        self.assertEqual(resumed['records_total'], 3)
        self.assertTrue(resumed['complete'])
        self.assertEqual(resumed['groups'][0]['sum'], '3/10')
        self.assertEqual(resumed['groups'][0]['mean'], '3/20')

    def test_count_without_numeric_field(self):
        result = aggregate(self.source, self.checkpoint)
        self.assertEqual(result['groups'], [{'key': None, 'count': 3}])

    def test_cli_refuses_fifo_checkpoint_without_blocking_or_mutation(self):
        os.mkfifo(self.checkpoint)
        original = self.checkpoint.stat()
        for resume in ([], ['--resume']):
            with self.subTest(resume=resume):
                result = subprocess.run(
                    [sys.executable, '-m', 'latent_state_lab.batch', str(self.source),
                     str(self.checkpoint), '--value', 'amount', *resume],
                    capture_output=True, text=True, timeout=2, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                error = json.loads(result.stderr)
                self.assertEqual(error['code'], 'batch_failed')
                self.assertRegex(error['request_id'], r'^[0-9a-f]{32}$')
                self.assertTrue(stat.S_ISFIFO(self.checkpoint.stat().st_mode))
                self.assertEqual(self.checkpoint.stat().st_ino, original.st_ino)

    def test_fifo_lock_refused_as_invalid_input(self):
        os.mkfifo(Path(str(self.checkpoint) + '.lock'))
        with self.assertRaises(BatchError):
            self.run_batch()

    def test_cli_refuses_fifo_lock_without_blocking_or_mutation(self):
        for input_format, append_only in (('jsonl', False), ('jsonl', True), ('csv', True)):
            with self.subTest(input_format=input_format, append_only=append_only):
                source = self.root / f'lock-input.{input_format}'
                source.write_bytes(b'amount\n1\n' if input_format == 'csv' else b'{"amount":1}\n')
                checkpoint = self.root / f'{input_format}-{append_only}.zip'
                lock = Path(str(checkpoint) + '.lock')
                os.mkfifo(lock)
                before = lock.stat()
                args = ['--append-only'] if append_only else []
                result = subprocess.run(
                    [sys.executable, '-m', 'latent_state_lab.batch', str(source),
                     str(checkpoint), '--input-format', input_format, '--value', 'amount', *args],
                    capture_output=True, text=True, timeout=2, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                error = json.loads(result.stderr)
                self.assertEqual(error['code'], 'batch_failed')
                self.assertRegex(error['request_id'], r'^[0-9a-f]{32}$')
                self.assertNotIn(str(source), result.stderr)
                self.assertNotIn(str(lock), result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                after = lock.stat()
                self.assertTrue(stat.S_ISFIFO(after.st_mode))
                self.assertEqual((after.st_dev, after.st_ino, after.st_mtime_ns),
                                 (before.st_dev, before.st_ino, before.st_mtime_ns))
                self.assertFalse(checkpoint.exists())

    def test_resume_does_not_parse_committed_rows(self):
        from latent_state_lab.batch import _load_row
        self.run_batch(max_records=1)
        with patch('latent_state_lab.batch._load_row', wraps=_load_row) as parse:
            result = self.run_batch(resume=True)
        self.assertEqual(parse.call_count, 2)
        self.assertEqual(result['records_this_run'], 2)

    def test_empty_input_final_unterminated_row_and_complete_resume(self):
        self.source.write_bytes(b'')
        empty = aggregate(self.source, self.checkpoint)
        self.assertTrue(empty['complete'])
        self.assertEqual(empty['groups'], [])
        self.source.write_bytes(b'{"amount":-1.25}')
        path = self.root / 'unterminated.zip'
        result = aggregate(self.source, path, value='amount', max_records=1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['groups'][0]['sum'], '-5/4')
        again = aggregate(self.source, path, value='amount', resume=True)
        self.assertEqual(again['records_this_run'], 0)
        self.assertEqual(again['groups'], result['groups'])

    def test_changed_during_run_does_not_commit(self):
        from latent_state_lab.batch import _load_row
        def changing(raw):
            with self.source.open('ab') as stream:
                stream.write(b'{}\n')
            return _load_row(raw)
        with patch('latent_state_lab.batch._load_row', side_effect=changing), self.assertRaises(BatchError):
            self.run_batch(max_records=1)
        self.assertFalse(self.checkpoint.exists())

    def test_runtime_mismatch_and_key_limit(self):
        from latent_state_lab.batch import PIN
        from latent_state_lab.capsule import read_capsule, write_capsule
        self.run_batch(max_records=1)
        _, chunks = read_capsule(self.checkpoint, PIN)
        write_capsule(self.checkpoint, dict(PIN, runtime_revision='wrong'), chunks)
        with self.assertRaises(BatchError):
            self.run_batch(resume=True)
        self.source.write_text(json.dumps({'team': 'x' * 1025, 'amount': 1}) + '\n')
        with self.assertRaises(BatchError):
            aggregate(self.source, self.root / 'key.zip', group_by='team', value='amount')

    def test_changed_truncated_wrong_input_and_config_preserve_checkpoint(self):
        self.run_batch(max_records=1)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(BatchError):
            aggregate(self.source, self.checkpoint, resume=True)
        other = self.root / 'other.jsonl'
        other.write_bytes(self.source.read_bytes())
        with self.assertRaises(BatchError):
            aggregate(other, self.checkpoint, resume=True, group_by='team', value='amount')
        original = self.source.read_bytes()
        for changed in (original.replace(b'0.1', b'0.9'), original[:-1], original + b'{}\n'):
            self.source.write_bytes(changed)
            with self.assertRaises(BatchError):
                self.run_batch(resume=True)
            self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_invalid_rows_and_bounds_preserve_checkpoint(self):
        self.run_batch(max_records=1)
        before = self.checkpoint.read_bytes()
        for bad in ('{', '{"team":"x","amount":NaN}', '{"a":1,"a":2}',
                    '{"team":"x","amount":1e1000000}',
                    '{"team":"x","amount":1e999999999999999999999999999}',
                    '{"team":"x","amount":true}', '{"team":"\\ud800","amount":1}'):
            self.source.write_text(bad + '\n', encoding='utf-8')
            with self.assertRaises(BatchError):
                aggregate(self.source, self.root / 'new.zip', group_by='team', value='amount')
            self.assertFalse((self.root / 'new.zip').exists())
            self.assertEqual(self.checkpoint.read_bytes(), before)
        self.source.write_text('{"team":"a"}\n{"team":"b"}\n')
        with self.assertRaises(BatchError):
            aggregate(self.source, self.root / 'groups.zip', group_by='team', max_groups=1)
        with self.assertRaises(BatchError):
            aggregate(self.source, self.root / 'line.zip', max_line_bytes=4)

    def test_tamper_existing_destination_and_interrupted_write(self):
        self.run_batch(max_records=1)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(BatchError):
            self.run_batch()
        with patch('latent_state_lab.batch.write_capsule', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.run_batch(resume=True)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.checkpoint.write_bytes(before[:-20])
        with self.assertRaises(BatchError):
            self.run_batch(resume=True)

    def test_checkpoint_fraction_syntax_is_checked_before_numeric_construction(self):
        from latent_state_lab.batch import _fraction
        with patch('latent_state_lab.batch.Fraction', side_effect=AssertionError('parser reached')), self.assertRaises(BatchError):
            _fraction('1e1000000000')

    def test_exclusive_lock_and_source_destination_collision(self):
        import fcntl
        with open(str(self.checkpoint) + '.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BatchError):
                self.run_batch()
        original = self.source.read_bytes()
        with self.assertRaises(BatchError):
            aggregate(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), original)

    def test_state_schema_tamper_is_rejected_even_with_valid_capsule_hashes(self):
        from latent_state_lab.batch import PIN
        from latent_state_lab.capsule import read_capsule, write_capsule
        self.run_batch(max_records=1)
        _, chunks = read_capsule(self.checkpoint, PIN)
        state = json.loads(chunks['batch'])
        state['offset'] = -1
        write_capsule(self.checkpoint, PIN, {'batch': json.dumps(state).encode()})
        with self.assertRaises(BatchError):
            self.run_batch(resume=True)
