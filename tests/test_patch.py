"""Declarative correction batches against existing exact sync state."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'changes.jsonl'
        self.store = self.root / 'state.json'
        snapshot = self.root / 'snapshot.csv'
        snapshot.write_text('id,team,amount\na,East,0.10\nb,East,0.20\nc,West,-0.50\n')
        from latent_state_lab.sync import reconcile
        reconcile(snapshot, self.store, id_field='id', input_format='csv',
                  group_by='team', value='amount', apply=True)
        self.original = self.store.read_bytes()

    def write(self, rows):
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def row(self, key, before, after):
        def side(pair):
            return {'group': pair[0], 'value': pair[1]} if pair is not None else None
        return {'id': key, 'before': side(before), 'after': side(after)}

    def run_patch(self, **kwargs):
        from latent_state_lab.patch import apply_patch
        return apply_patch(self.source, self.store, id_field='id', input_format='csv',
                           group_by='team', value='amount', **kwargs)

    def corrections(self):
        self.write([self.row('a', ('East', '0.1'), ('West', '0.15')),
                    self.row('b', ('East', '0.2'), None),
                    self.row('d', None, ('East', '0.25'))])

    def test_correction_move_insert_delete_and_exact_aggregates(self):
        self.corrections()
        result = self.run_patch(details_limit=2)
        self.assertEqual(result['changes'], {'applicable': 3, 'already_present': 0, 'conflicts': 0})
        self.assertEqual(result['after'], {'records': 3, 'groups': [
            {'key': 'East', 'count': 1, 'sum': '1/4', 'mean': '1/4'},
            {'key': 'West', 'count': 2, 'sum': '-7/20', 'mean': '-7/40'}]})
        self.assertEqual(result['details_omitted'], 1)
        self.assertEqual(self.store.read_bytes(), self.original)
        result = self.run_patch(apply=True, expect_review=result['review_sha256'])
        self.assertTrue(result['store_written'])
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)

    def test_all_conflicts_reported_and_apply_is_all_or_nothing(self):
        from latent_state_lab.patch import PatchConflict
        self.write([self.row('a', ('East', '9'), ('West', '8')),
                    self.row('b', ('East', '0.2'), None),
                    self.row('c', None, ('West', '8'))])
        result = self.run_patch(details_limit=1)
        self.assertEqual(result['changes'], {'applicable': 1, 'already_present': 0, 'conflicts': 2})
        self.assertEqual(result['after'], result['before'])
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['details_omitted'], 2)
        with self.assertRaises(PatchConflict) as raised:
            self.run_patch(apply=True)
        self.assertEqual(raised.exception.report['changes']['conflicts'], 2)
        self.assertEqual(self.store.read_bytes(), self.original)

    def test_after_replay_is_noop_including_absent_delete(self):
        self.corrections()
        self.run_patch(apply=True)
        original = self.store.stat()
        result = self.run_patch(apply=True)
        self.assertEqual(result['changes'], {'applicable': 0, 'already_present': 3, 'conflicts': 0})
        self.assertFalse(result['store_written'])
        self.assertEqual(self.store.stat().st_mtime_ns, original.st_mtime_ns)
        self.write([self.row('a', ('West', '0.1500'), ('West', '0.15'))])
        self.assertEqual(self.run_patch()['changes']['already_present'], 1)

    def test_review_rejects_changed_bytes_state_config_and_preview_guard(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.patch import apply_patch
        self.corrections()
        review = self.run_patch()['review_sha256']
        self.source.write_text(self.source.read_text().replace('0.15', '0.150'))
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.run_patch(apply=True, expect_review=review)
        review = self.run_patch()['review_sha256']
        self.run_patch(apply=True)
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.run_patch(apply=True, expect_review=review)
        with self.assertRaises(BatchError):
            apply_patch(self.source, self.store, id_field='different', input_format='csv',
                        group_by='team', value='amount', apply=True, expect_review=review)
        with self.assertRaises(BatchError):
            self.run_patch(expect_review=review)

    def test_empty_absent_insert_delete_and_count_only(self):
        from latent_state_lab.patch import apply_patch
        self.store.unlink()
        self.write([self.row('absent', ('East', '1'), None)])
        self.assertEqual(self.run_patch(apply=True)['changes']['already_present'], 1)
        self.assertFalse(self.store.exists())
        self.write([self.row('a', None, ('East', '0.1'))])
        self.assertTrue(self.run_patch(apply=True)['store_written'])
        self.store.unlink()
        self.write([self.row('a', None, (None, None))])
        result = apply_patch(self.source, self.store, id_field='id', apply=True)
        self.assertEqual(result['after'], {'records': 1, 'groups': [{'key': None, 'count': 1}]})

    def test_malformed_duplicates_bounds_and_private_errors_preserve_state(self):
        from latent_state_lab.batch import BatchError
        good = self.row('fresh', None, ('West', '1'))
        bad_rows = [dict(good, extra=True), dict(good, id=2),
                    dict(good, before={}), dict(good, after={'group': 'West', 'value': 1}),
                    self.row('a', None, ('West', 'NaN')),
                    self.row('a', None, ('West', '01')),
                    self.row('a', None, ('West', '1e1001'))]
        for bad in bad_rows:
            self.write([good, bad])
            with self.subTest(bad=bad), self.assertRaises(BatchError):
                self.run_patch(apply=True)
            self.assertEqual(self.store.read_bytes(), self.original)
        self.write([good, good])
        with self.assertRaisesRegex(BatchError, 'duplicate'):
            self.run_patch(apply=True)
        for raw in ('{"id":"a","id":"b","before":null,"after":null}\n',
                    '{"id":"\\ud800","before":null,"after":null}\n'):
            self.source.write_text(raw)
            with self.assertRaises(BatchError):
                self.run_patch(apply=True)
        self.corrections()
        for bounds in ({'max_changes': 2}, {'max_rows': 2}, {'max_groups': 1},
                       {'max_line_bytes': 8}, {'max_seconds': 0.000001}):
            with self.subTest(bounds=bounds), self.assertRaises(BatchError):
                self.run_patch(apply=True, **bounds)
        self.assertEqual(self.store.read_bytes(), self.original)

    def test_aliases_symlinks_permissions_and_lock_refused(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.patch import apply_patch
        self.corrections()
        with self.assertRaises(BatchError):
            apply_patch(self.store, self.store, id_field='id', input_format='csv',
                        group_by='team', value='amount', apply=True)
        self.source.unlink()
        self.source.symlink_to(self.store)
        with self.assertRaises((BatchError, OSError)):
            self.run_patch(apply=True)
        self.source.unlink()
        os.link(self.store, self.source)
        with self.assertRaises(BatchError):
            self.run_patch(apply=True)
        self.source.unlink()
        self.corrections()
        self.store.chmod(0o644)
        with self.assertRaises(BatchError):
            self.run_patch(apply=True)
        self.store.chmod(0o600)
        from latent_state_lab.sync import _locked
        with _locked(Path(str(self.store) + '.lock')), self.assertRaisesRegex(BatchError, 'locked'):
            self.run_patch(apply=True)
        self.assertEqual(self.store.read_bytes(), self.original)

    def test_atomic_replace_failure_and_source_mutation(self):
        from latent_state_lab import patch
        from latent_state_lab.batch import BatchError
        self.corrections()
        with mock_patch('latent_state_lab.sync.os.replace', side_effect=OSError('injected')), self.assertRaises(OSError):
            self.run_patch(apply=True)
        self.assertEqual(list(self.root.glob('.sync-*')), [])
        original = patch._record
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            if result is None:
                with self.source.open('ab') as stream:
                    stream.write(b'\n')
            return result
        with mock_patch('latent_state_lab.patch._record', side_effect=mutate), self.assertRaises(BatchError):
            self.run_patch(apply=True)
        self.assertEqual(self.store.read_bytes(), self.original)

    def test_cli_conflict_report_and_private_malformed_error(self):
        self.write([self.row('a', None, ('West', '99'))])
        args = [sys.executable, '-m', 'latent_state_lab.patch', str(self.source),
                str(self.store), '--id', 'id', '--input-format', 'csv',
                '--group-by', 'team', '--value', 'amount']
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['changes']['conflicts'], 1)
        result = subprocess.run(args + ['--apply'], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['changes']['conflicts'], 1)
        self.source.write_text('PRIVATE_INPUT\n')
        result = subprocess.run(args + ['--apply'], capture_output=True, text=True, check=False)
        error = json.loads(result.stderr)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(error['request_id'])
        self.assertNotIn('PRIVATE_INPUT', result.stderr)
        self.assertNotIn(str(self.root), result.stderr)

    def test_empty_batch_and_no_operation_schema(self):
        from latent_state_lab.batch import BatchError
        self.source.write_bytes(b'')
        result = self.run_patch(apply=True)
        self.assertEqual(result['changes'], {'applicable': 0, 'already_present': 0, 'conflicts': 0})
        self.assertFalse(result['store_written'])
        self.assertEqual(self.store.read_bytes(), self.original)
        self.store.unlink()
        self.assertFalse(self.run_patch(apply=True)['store_written'])
        self.assertFalse(self.store.exists())
        self.write([self.row('a', None, None)])
        with self.assertRaisesRegex(BatchError, 'both be absent'):
            self.run_patch(apply=True)

    def test_later_return_to_before_permits_reapply(self):
        self.corrections()
        self.run_patch(apply=True)
        self.assertEqual(self.run_patch()['changes']['already_present'], 3)
        self.store.write_bytes(self.original)
        self.assertEqual(self.run_patch(apply=True)['changes']['applicable'], 3)

    def test_lock_aliases_permissions_and_unsafe_directory(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.patch import apply_patch
        self.corrections()
        lock = Path(str(self.store) + '.lock')
        with self.assertRaises(BatchError):
            apply_patch(lock, self.store, id_field='id', apply=True)
        lock.chmod(0o644)
        with self.assertRaises(BatchError):
            self.run_patch(apply=True)
        lock.chmod(0o600)
        self.root.chmod(0o777)
        try:
            with self.assertRaises(BatchError):
                self.run_patch(apply=True)
        finally:
            self.root.chmod(0o700)
        self.assertEqual(self.store.read_bytes(), self.original)

    def test_review_binds_equivalent_committed_state_bytes(self):
        from latent_state_lab.batch import BatchError
        self.corrections()
        review = self.run_patch()['review_sha256']
        self.store.write_bytes(self.original + b'\n')
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.run_patch(apply=True, expect_review=review)
        self.assertEqual(self.store.read_bytes(), self.original + b'\n')
