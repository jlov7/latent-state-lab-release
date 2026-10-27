import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from latent_state_lab.batch import BatchError, aggregate, inspect_checkpoint


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'private-ledger.csv'
        self.checkpoint = self.root / 'feed.zip'

    def csv_bytes(self, rows):
        text = io.StringIO(newline='')
        csv.writer(text).writerows(rows)
        return text.getvalue().encode()

    def run_feed(self, **kwargs):
        return aggregate(self.source, self.checkpoint, input_format='csv', append_only=True,
                         group_by='team', value='amount', **kwargs)

    def test_csv_fresh_process_append_exact_multiline_and_no_reaggregation(self):
        initial = self.csv_bytes([['team', 'amount', 'note'], ['雪,one', '0.1', 'a\nb'],
                                  ['雪,one', '0.2', 'quote " value']])
        self.source.write_bytes(initial)
        first = self.run_feed()
        self.assertEqual(first['records_total'], 2)
        self.assertEqual(first['physical_lines'], 4)
        self.assertEqual(first['groups'][0]['sum'], '3/10')
        self.assertEqual(first['integrity_bytes_read'], 2 * len(initial))
        with self.source.open('ab') as stream:
            stream.write(self.csv_bytes([['β', '-1.25', 'new']]))
        result = subprocess.run([sys.executable, '-m', 'latent_state_lab.batch',
            str(self.source), str(self.checkpoint), '--input-format', 'csv', '--append-only',
            '--resume', '--group-by', 'team', '--value', 'amount'],
            capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report['records_this_run'], report['records_total']), (1, 3))
        self.assertEqual(report['integrity_bytes_read'], 2 * self.source.stat().st_size + len(initial))
        again = self.run_feed(resume=True)
        self.assertEqual(again['records_this_run'], 0)
        self.assertEqual(again['groups'], report['groups'])
        full = aggregate(self.source, self.root / 'full.zip', input_format='csv',
                         group_by='team', value='amount')
        self.assertEqual(full['groups'], report['groups'])

    def test_jsonl_append_and_legacy_checkpoint_remain_separate(self):
        self.source.write_bytes(b'{"v":0.1}\n')
        legacy = aggregate(self.source, self.checkpoint, value='v')
        before = self.checkpoint.read_bytes()
        with self.assertRaises(BatchError):
            aggregate(self.source, self.checkpoint, value='v', append_only=True, resume=True)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        path = self.root / 'append.zip'
        aggregate(self.source, path, value='v', append_only=True)
        with self.source.open('ab') as stream:
            stream.write(b'{"v":0.2}\n')
        with self.assertRaises(BatchError):
            aggregate(self.source, self.checkpoint, value='v', resume=True)
        result = aggregate(self.source, path, value='v', append_only=True, resume=True)
        self.assertEqual((result['records_this_run'], result['groups'][0]['sum']), (1, '3/10'))
        self.assertEqual(legacy['groups'][0]['sum'], '1/10')

    def test_bom_header_only_csv_inspects_and_later_appends(self):
        header = b'\xef\xbb\xbf"team",amount\r\n'
        self.source.write_bytes(header)
        result = self.run_feed()
        self.assertEqual((result['records_total'], result['physical_lines']), (0, 1))
        self.assertEqual(inspect_checkpoint(self.checkpoint)['records_total'], 0)
        self.assertEqual(self.run_feed(resume=True)['records_this_run'], 0)
        with self.source.open('ab') as stream:
            stream.write(b'a,1\r\n')
        self.assertEqual(self.run_feed(resume=True)['records_total'], 1)

    def test_prefix_edits_truncation_replacement_and_config_preserve_checkpoint(self):
        original = self.csv_bytes([['team', 'amount'], ['private-group', '1'], ['b', '2']])
        self.source.write_bytes(original)
        self.run_feed(max_records=1)
        before = self.checkpoint.read_bytes()
        offset = inspect_checkpoint(self.checkpoint)['offset']
        for changed in (original.replace(b',1', b',9'), original[:offset - 1]):
            self.source.write_bytes(changed)
            with self.assertRaises(BatchError):
                self.run_feed(resume=True)
            self.assertEqual(before, self.checkpoint.read_bytes())
        self.source.write_bytes(original)
        replacement = self.root / 'other.csv'
        replacement.write_bytes(original)
        replacement.replace(self.source)
        with self.assertRaises(BatchError):
            self.run_feed(resume=True)
        self.assertEqual(before, self.checkpoint.read_bytes())
        with self.assertRaises(BatchError):
            aggregate(self.source, self.checkpoint, input_format='csv', append_only=True,
                      resume=True, group_by='team')

    def test_periodic_multiline_error_location_and_uncommitted_suffix_repair(self):
        prefix = self.csv_bytes([['team', 'amount', 'note'], ['雪', '1', 'a\nb'],
                                 ['雪', '2', 'ok']])
        self.source.write_bytes(prefix + b'private,invalid,"unterminated\n')
        with self.assertRaises(BatchError) as raised:
            self.run_feed(checkpoint_every=2, max_records=10, max_seconds=10)
        self.assertEqual((raised.exception.row, raised.exception.byte_offset), (5, len(prefix)))
        self.assertNotIn('private', str(raised.exception))
        saved = inspect_checkpoint(self.checkpoint)
        self.assertEqual((saved['records_total'], saved['physical_lines']), (2, 4))
        self.source.write_bytes(prefix + self.csv_bytes([['b', '3', 'fixed']]))
        report = self.run_feed(resume=True)
        self.assertEqual((report['records_total'], report['records_this_run']), (3, 1))

    def test_strict_csv_headers_numbers_width_unicode_and_record_bound(self):
        invalid = [b'team,team\r\na,1\r\n', b'team,\r\na,1\r\n',
                   b'team,amount\r\na,1,extra\r\n', b'team,amount\r\na,NaN\r\n',
                   b'team,amount\r\na,Infinity\r\n', b'team,amount\r\na, 1\r\n',
                   b'team,amount\r\na,1e1001\r\n', b'team,amount\r\n\xff,1\r\n',
                   b'team,amount\r\n"x"junk,1\r\n', b'team,amount\r\na,1', b'']
        for raw in invalid:
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                with self.assertRaises(BatchError):
                    self.run_feed()
                self.assertFalse(self.checkpoint.exists())
        self.source.write_bytes(b'team,amount,note\n"a\na\na\na\na\na\na\na\na",1,x\n')
        with self.assertRaises(BatchError) as raised:
            self.run_feed(max_line_bytes=18)
        self.assertEqual((raised.exception.row, raised.exception.byte_offset), (2, 17))

    def test_mutation_during_commit_keeps_prior_boundary(self):
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['a', '1'], ['b', '2']]))
        self.run_feed(max_records=1)
        before = self.checkpoint.read_bytes()
        from latent_state_lab import feed
        original = feed._identity
        calls = [0]
        def changing(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                with self.source.open('ab') as stream:
                    stream.write(b'c,3\r\n')
            return original(*args, **kwargs)
        with patch.object(feed, '_identity', side_effect=changing), self.assertRaises(BatchError):
            self.run_feed(resume=True)
        self.assertEqual(before, self.checkpoint.read_bytes())

    def test_v2_schema_prefix_hash_and_read_only_private_inspection(self):
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['private', '1']]))
        self.run_feed()
        from latent_state_lab.capsule import read_capsule, write_capsule
        from latent_state_lab.feed import PIN
        _, chunks = read_capsule(self.checkpoint, PIN)
        state = json.loads(chunks['batch'])
        before = self.checkpoint.read_bytes()
        self.source.unlink()
        Path(str(self.checkpoint) + '.lock').unlink()
        report = inspect_checkpoint(self.checkpoint)
        self.assertEqual(report['source_validation'], 'unverified')
        self.assertEqual((report['state_version'], report['physical_lines']), (2, 2))
        self.assertNotIn('private', json.dumps(report))
        self.assertEqual(before, self.checkpoint.read_bytes())
        self.assertEqual(set(self.root.iterdir()), {self.checkpoint})
        for key, value in [('version', True), ('prefix_sha256', 'bad'), ('physical_lines', 0),
                           ('header', ['team', 'team']), ('offset', -1), ('records', True),
                           ('header', ['team', 'amount', 'x' * (128 * 1024 + 1)]),
                           ('groups', [{'key': 'x', 'count': 1, 'sum': '1/0'}])]:
            with self.subTest(key=key):
                write_capsule(self.checkpoint, PIN,
                              {'batch': json.dumps(dict(state, **{key: value})).encode()})
                with self.assertRaises(BatchError):
                    inspect_checkpoint(self.checkpoint)

    def test_csv_eof_without_newline_only_in_immutable_mode_and_empty_jsonl_append(self):
        self.source.write_bytes(b'team,amount\na,1.25')
        result = aggregate(self.source, self.checkpoint, input_format='csv',
                           group_by='team', value='amount')
        self.assertEqual((result['records_total'], result['physical_lines']), (1, 2))
        again = aggregate(self.source, self.checkpoint, input_format='csv', resume=True,
                          group_by='team', value='amount')
        self.assertEqual(again['records_this_run'], 0)
        self.source.write_bytes(b'')
        path = self.root / 'empty-jsonl.zip'
        self.assertEqual(aggregate(self.source, path, append_only=True)['groups'], [])
        with self.source.open('ab') as stream:
            stream.write(b'{}\n')
        self.assertEqual(aggregate(self.source, path, append_only=True,
                                   resume=True)['records_total'], 1)

    def test_feed_commit_failure_and_deadline_keep_previous_boundary(self):
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['a', '1'], ['b', '2']]))
        self.run_feed(max_records=1)
        before = self.checkpoint.read_bytes()
        from latent_state_lab import feed
        with patch.object(feed, 'write_capsule', side_effect=OSError('write interrupted')), self.assertRaises(OSError):
            self.run_feed(resume=True)
        self.assertEqual(before, self.checkpoint.read_bytes())
        with patch.object(feed.time, 'monotonic', side_effect=[0, 2]), self.assertRaises(BatchError):
            self.run_feed(resume=True, checkpoint_every=1, max_records=2, max_seconds=1)
        self.assertEqual(before, self.checkpoint.read_bytes())

    def test_csv_bounds_and_new_state_pin_are_enforced(self):
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['a', '1'], ['b', '2']]))
        with self.assertRaises(BatchError):
            self.run_feed(max_groups=1)
        self.assertFalse(self.checkpoint.exists())
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['x' * 1025, '1']]))
        with self.assertRaises(BatchError):
            self.run_feed()
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['a', '1']]))
        self.run_feed()
        from latent_state_lab.capsule import read_capsule, write_capsule
        from latent_state_lab.feed import PIN
        _, chunks = read_capsule(self.checkpoint, PIN)
        write_capsule(self.checkpoint, dict(PIN, runtime_revision='wrong'), chunks)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(BatchError):
            self.run_feed(resume=True)
        with self.assertRaises(BatchError):
            inspect_checkpoint(self.checkpoint)
        self.assertEqual(before, self.checkpoint.read_bytes())

    def test_no_overwrite_private_cli_errors_and_no_parse_of_prefix(self):
        self.source.write_bytes(self.csv_bytes([['team', 'amount'], ['private', '1']]))
        self.run_feed()
        before = self.checkpoint.read_bytes()
        with self.assertRaises(BatchError):
            self.run_feed()
        self.assertEqual(before, self.checkpoint.read_bytes())
        from latent_state_lab import feed
        with patch.object(feed, '_record', side_effect=AssertionError('old rows parsed')):
            report = self.run_feed(resume=True)
        self.assertEqual(report['records_this_run'], 0)
        with self.source.open('ab') as stream:
            stream.write(b'secret,not-a-number\r\n')
        result = subprocess.run([sys.executable, '-m', 'latent_state_lab.batch',
            str(self.source), str(self.checkpoint), '--input-format', 'csv', '--append-only',
            '--resume', '--group-by', 'team', '--value', 'amount'],
            capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 2)
        error = json.loads(result.stderr)
        self.assertEqual((error['row'], error['byte_offset']), (3, len(self.source.read_bytes().split(b'secret')[0])))
        self.assertEqual(error['code'], 'batch_failed')
        self.assertRegex(error['request_id'], r'^[0-9a-f]{32}$')
        self.assertNotIn('secret', result.stderr)
        self.assertNotIn('private', result.stderr)
        self.assertEqual(before, self.checkpoint.read_bytes())


if __name__ == '__main__':
    unittest.main()
