import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from latent_state_lab.batch import PIN, BatchError, aggregate
from latent_state_lab.capsule import read_capsule, write_capsule


class BatchAdditions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'private-source.jsonl'
        self.checkpoint = self.root / 'checkpoint.zip'

    def test_private_physical_location_across_resume_utf8_crlf(self):
        prefix = '{"team":"雪","amount":1}\r\n'.encode()
        for bad, limit in [(b'{private-value\r\n', 1024),
                           (b'\xff\r\n', 1024),
                           (b'{"team":"secret"}\r\n', 1024),
                           (b'{"amount":2}\r\n', 1024),
                           (b'{"team":"secret","amount":true}\r\n', 1024),
                           (b'{"team":"secret","amount":2}\r\n', len(prefix))]:
            with self.subTest(bad=bad):
                self.source.write_bytes(prefix + bad)
                path = self.root / ('row-' + str(limit) + '.zip')
                if path.exists():
                    path.unlink()
                aggregate(self.source, path, group_by='team', value='amount',
                          max_records=1, max_line_bytes=limit)
                before = path.read_bytes()
                result = subprocess.run([sys.executable, '-m', 'latent_state_lab.batch',
                    str(self.source), str(path), '--resume', '--group-by', 'team',
                    '--value', 'amount', '--max-line-bytes', str(limit)],
                    capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 2)
                error = json.loads(result.stderr)
                self.assertEqual((error['row'], error['byte_offset']), (2, len(prefix)))
                self.assertNotIn('secret', result.stderr)
                self.assertNotIn('private-', result.stderr)
                self.assertEqual(before, path.read_bytes())

    def test_group_bound_location_and_unterminated_final_row(self):
        prefix = '{"g":"雪"}\r\n'.encode()
        self.source.write_bytes(prefix + b'{"g":"private"}')
        with self.assertRaises(BatchError) as raised:
            aggregate(self.source, self.checkpoint, group_by='g', max_groups=1)
        self.assertEqual((raised.exception.row, raised.exception.byte_offset), (2, len(prefix)))
        self.assertFalse(self.checkpoint.exists())

    def test_periodic_bad_later_row_preserves_exact_boundary(self):
        self.source.write_bytes(b'{"v":1}\n{"v":2}\n{"v":3}\n{bad\n')
        with self.assertRaises(BatchError) as raised:
            aggregate(self.source, self.checkpoint, value='v', checkpoint_every=2,
                      max_records=20, max_seconds=10)
        self.assertEqual((raised.exception.row, raised.exception.byte_offset), (4, 24))
        _, chunks = read_capsule(self.checkpoint, PIN)
        state = json.loads(chunks['batch'])
        self.assertEqual((state['records'], state['offset'], state['groups'][0]['sum']),
                         (2, 16, '3'))

    def test_periodic_bounds_and_deadline_preserve_boundary(self):
        self.source.write_bytes(b'{}\n' * 5)
        for kwargs in ({'max_records': 5}, {'max_seconds': 1},
                       {'max_records': 5, 'max_seconds': float('nan')},
                       {'max_records': 5, 'max_seconds': 0}):
            with self.assertRaises(BatchError):
                aggregate(self.source, self.checkpoint, checkpoint_every=2, **kwargs)
        from latent_state_lab import batch
        original = batch.write_capsule
        clock = [0]
        def commit(*args, **kwargs):
            original(*args, **kwargs)
            clock[0] = 2
        with patch.object(batch.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(batch, 'write_capsule', side_effect=commit), \
                self.assertRaises(BatchError):
            aggregate(self.source, self.checkpoint, checkpoint_every=2,
                      max_records=5, max_seconds=1)
        self.assertEqual(batch.inspect_checkpoint(self.checkpoint)['records_total'], 2)

    def test_inspect_missing_source_is_read_only_and_schema_checked(self):
        from latent_state_lab.batch import inspect_checkpoint
        self.source.write_bytes(b'{"private-key":1}\n' * 3)
        aggregate(self.source, self.checkpoint, max_records=2)
        self.source.unlink()
        Path(str(self.checkpoint) + '.lock').unlink()
        before = self.checkpoint.read_bytes()
        report = inspect_checkpoint(self.checkpoint)
        self.assertEqual(report['records_total'], 2)
        self.assertEqual(report['source_validation'], 'unverified')
        self.assertNotIn('path', json.dumps(report))
        self.assertEqual(before, self.checkpoint.read_bytes())
        self.assertEqual(set(self.root.iterdir()), {self.checkpoint})
        _, chunks = read_capsule(self.checkpoint, PIN)
        state = json.loads(chunks['batch'])
        for field, value in [('config', []), ('source', {}), ('offset', -1),
                             ('groups', [{'key': None, 'count': True}])]:
            invalid = dict(state, **{field: value})
            write_capsule(self.checkpoint, PIN, {'batch': json.dumps(invalid).encode()})
            with self.assertRaises(BatchError):
                inspect_checkpoint(self.checkpoint)

    def test_inspect_cli_nonregular_and_corrupt_are_bounded_private(self):
        os.mkfifo(self.checkpoint)
        for special in (True, False):
            if not special:
                self.checkpoint.unlink()
                self.checkpoint.write_bytes(b'not a capsule private-value')
            before = self.checkpoint.lstat()
            result = subprocess.run([sys.executable, '-m', 'latent_state_lab.inspect',
                                     str(self.checkpoint)], capture_output=True, text=True,
                                    timeout=2, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            error = json.loads(result.stderr)
            self.assertEqual(error['code'], 'inspect_failed')
            self.assertNotIn('private', result.stderr)
            self.assertEqual(before.st_ino, self.checkpoint.lstat().st_ino)
            self.assertFalse(Path(str(self.checkpoint) + '.lock').exists())

    def test_half_even_decimal_reports_never_enter_canonical_state(self):
        self.source.write_bytes(b'{"g":"third","v":1}\n{"g":"third","v":0}\n'
                               b'{"g":"third","v":0}\n{"g":"tie","v":-1.225}\n'
                               b'{"g":"even","v":-1.235}\n{"g":"small","v":1e-1000}\n'
                               b'{"g":"large","v":1e1000}\n')
        displayed = aggregate(self.source, self.checkpoint, group_by='g', value='v',
                              decimal_places=2, max_records=4)
        self.assertEqual(displayed['groups'][-1]['sum_decimal'], '-1.22')
        resumed = aggregate(self.source, self.checkpoint, group_by='g', value='v',
                            decimal_places=2, resume=True)
        groups = {row['key']: row for row in resumed['groups']}
        self.assertEqual(groups['third']['mean_decimal'], '0.33')
        self.assertEqual(groups['even']['sum_decimal'], '-1.24')
        self.assertEqual(groups['small']['sum_decimal'], '0.00')
        self.assertEqual(groups['large']['sum_decimal'], '1' + '0' * 1000 + '.00')
        _, shown = read_capsule(self.checkpoint, PIN)
        plain = aggregate(self.source, self.root / 'plain.zip', group_by='g', value='v')
        _, canonical = read_capsule(self.root / 'plain.zip', PIN)
        self.assertEqual(shown, canonical)
        self.assertEqual([{k: v for k, v in row.items() if not k.endswith('_decimal')}
                          for row in resumed['groups']], plain['groups'])
        for precision in (-1, 19, True):
            with self.assertRaises(BatchError):
                aggregate(self.source, self.root / 'invalid.zip', decimal_places=precision)

    def test_zero_and_maximum_precision_ties(self):
        from fractions import Fraction

        from latent_state_lab.batch import _decimal_display
        for number, places, expected in [('-5/2', 0, '-2'), ('-7/2', 0, '-4'),
                ('1/3', 18, '0.333333333333333333'), ('-1/2000', 2, '0.00')]:
            self.assertEqual(_decimal_display(Fraction(number), places), expected)
