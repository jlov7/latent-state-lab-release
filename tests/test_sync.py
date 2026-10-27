"""ID-aware full snapshots, independent exact answers and atomic failure boundaries."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'snapshot.csv'
        self.store = self.root / 'state.json'
        self.source.write_text('id,team,amount\na,East,1.25\nb,East,2.50\nc,West,-0.50\n')

    def sync(self, **kwargs):
        from latent_state_lab.sync import reconcile
        return reconcile(self.source, self.store, id_field='id', group_by='team',
                         value='amount', input_format='csv', **kwargs)

    def test_preview_and_correction_cycle(self):
        first = self.sync()
        self.assertFalse(self.store.exists())
        self.assertEqual(first['changes'], {'added': 3, 'updated': 0, 'deleted': 0, 'unchanged': 0})
        self.assertFalse(first['applied'])
        self.sync(apply=True)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)
        original = self.store.read_bytes()
        self.source.write_text('id,team,amount\na,West,1.50\nc,West,-0.50\nd,East,3.25\n')
        preview = self.sync()
        self.assertEqual(preview['changes'], {'added': 1, 'updated': 1, 'deleted': 1, 'unchanged': 1})
        self.assertEqual(preview['before'], {'records': 3, 'groups': [
            {'key': 'East', 'count': 2, 'sum': '15/4', 'mean': '15/8'},
            {'key': 'West', 'count': 1, 'sum': '-1/2', 'mean': '-1/2'}]})
        self.assertEqual(preview['after'], {'records': 3, 'groups': [
            {'key': 'East', 'count': 1, 'sum': '13/4', 'mean': '13/4'},
            {'key': 'West', 'count': 2, 'sum': '1', 'mean': '1/2'}]})
        self.assertEqual(preview['deltas'], [
            {'key': 'East', 'count': -1, 'sum': '-1/2'},
            {'key': 'West', 'count': 1, 'sum': '3/2'}])
        self.assertEqual(self.store.read_bytes(), original)
        self.assertTrue(self.sync(apply=True)['applied'])
        self.assertEqual(self.sync()['changes'], {'added': 0, 'updated': 0, 'deleted': 0, 'unchanged': 3})

    def test_reordering_and_unselected_fields_are_unchanged(self):
        self.sync(apply=True)
        self.source.write_text('id,team,amount,ignored\nc,West,-0.5,x\na,East,1.250,y\nb,East,2.50,z\n')
        self.assertEqual(self.sync()['changes']['unchanged'], 3)

    def test_review_guard_rejects_changed_source_or_committed_state(self):
        from latent_state_lab.batch import BatchError
        self.sync(apply=True)
        original = self.store.read_bytes()
        review = self.sync()['review_sha256']
        self.source.write_text('id,team,amount\na,West,99\n')
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.sync(apply=True, expect_review=review)
        self.assertEqual(self.store.read_bytes(), original)
        current_review = self.sync()['review_sha256']
        self.sync(apply=True)
        committed = self.store.read_bytes()
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.sync(apply=True, expect_review=current_review)
        self.assertEqual(self.store.read_bytes(), committed)

    def test_review_guard_initial_apply_noop_and_configuration_binding(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.sync import reconcile
        review = self.sync(details_limit=1)['review_sha256']
        self.assertEqual(self.sync(details_limit=3)['review_sha256'], review)
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            reconcile(self.source, self.store, id_field='id', input_format='csv',
                      group_by='team', apply=True, expect_review=review)
        self.assertFalse(self.store.exists())
        self.assertTrue(self.sync(apply=True, expect_review=review)['store_written'])
        review = self.sync()['review_sha256']
        self.assertFalse(self.sync(apply=True, expect_review=review)['store_written'])

    def test_review_guard_rejects_new_store_and_semantically_equal_byte_edits(self):
        from latent_state_lab.batch import BatchError
        review = self.sync()['review_sha256']
        self.sync(apply=True)
        original = self.store.read_bytes()
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.sync(apply=True, expect_review=review)
        self.assertEqual(self.store.read_bytes(), original)
        review = self.sync()['review_sha256']
        self.source.write_text(self.source.read_text().replace('1.25', '1.250'))
        self.assertEqual(self.sync()['changes']['unchanged'], 3)
        with self.assertRaisesRegex(BatchError, 'review no longer matches'):
            self.sync(apply=True, expect_review=review)
        self.assertEqual(self.store.read_bytes(), original)

    def test_review_guard_requires_apply_and_valid_digest(self):
        from latent_state_lab.batch import BatchError
        for kwargs in ({'expect_review': 'a' * 64},
                       {'apply': True, 'expect_review': ''},
                       {'apply': True, 'expect_review': 'g' * 64},
                       {'apply': True, 'expect_review': '0' * 64},
                       {'apply': True, 'expect_review': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BatchError):
                self.sync(**kwargs)
        self.assertFalse(self.store.exists())

    def test_bounded_opt_in_change_details_and_noop_apply(self):
        first = self.sync(details_limit=2)
        self.assertEqual(first['details'], [
            {'id': 'a', 'change': 'added', 'before': None, 'after': ['East', '5/4']},
            {'id': 'b', 'change': 'added', 'before': None, 'after': ['East', '5/2']}])
        self.assertEqual(first['details_omitted'], 1)
        self.sync(apply=True)
        original = self.store.stat()
        self.assertFalse(self.sync(apply=True)['store_written'])
        self.assertEqual(self.store.stat().st_mtime_ns, original.st_mtime_ns)

    def test_state_bound_and_unsafe_directory(self):
        from latent_state_lab.batch import BatchError
        self.sync(apply=True)
        original = self.store.read_bytes()
        with patch('latent_state_lab.sync.MAX_STATE', 100), self.assertRaises(BatchError):
            self.sync(apply=True)
        self.assertEqual(self.store.read_bytes(), original)
        self.store.unlink()
        with patch('latent_state_lab.sync.MAX_STATE', 100), self.assertRaises(BatchError):
            self.sync(apply=True)
        self.assertFalse(self.store.exists())
        self.root.chmod(0o777)
        try:
            with self.assertRaises(BatchError):
                self.sync(apply=True)
        finally:
            self.root.chmod(0o700)

    def test_empty_snapshot_deletes_all(self):
        self.sync(apply=True)
        self.source.write_text('id,team,amount\n')
        result = self.sync(apply=True)
        self.assertEqual(result['changes']['deleted'], 3)
        self.assertEqual(result['after'], {'records': 0, 'groups': []})

    def test_jsonl_exact_numbers_and_count_only(self):
        from latent_state_lab.sync import reconcile
        self.source.write_text('{"id":"a","v":0.1}\n{"id":"b","v":0.2}\n')
        result = reconcile(self.source, self.store, id_field='id', value='v')
        self.assertEqual(result['after']['groups'], [
            {'key': None, 'count': 2, 'sum': '3/10', 'mean': '3/20'}])
        result = reconcile(self.source, self.store, id_field='id')
        self.assertEqual(result['after']['groups'], [{'key': None, 'count': 2}])

    def test_late_invalid_records_preserve_state(self):
        from latent_state_lab.batch import BatchError
        self.sync(apply=True)
        original = self.store.read_bytes()
        for bad in ('a,West,3\na,East,4\n', ',East,4\n', 'q,East,NaN\n',
                    'q,East, 1\n', 'q,East,+1\n', 'q,East,01\n', 'q,East,1e1001\n',
                    'q,East,\n', 'q,East,1,extra\n'):
            with self.subTest(bad=bad):
                self.source.write_text('id,team,amount\nfresh,East,10\n' + bad)
                with self.assertRaises(BatchError):
                    self.sync(apply=True)
                self.assertEqual(self.store.read_bytes(), original)

    def test_invalid_json_ids_and_fields(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.sync import reconcile
        for row in ({'id': 12}, {'id': True}, {}, {'id': ''}, {'id': None}):
            self.source.write_text(json.dumps(row) + '\n')
            with self.subTest(row=row), self.assertRaises(BatchError):
                reconcile(self.source, self.store, id_field='id', apply=True)
            self.assertFalse(self.store.exists())
        for text in ('{"id":"a","v":"0.1"}\n', '{"id":"a","id":"b"}\n',
                     '{"id":"\\ud800"}\n'):
            self.source.write_text(text)
            with self.assertRaises(BatchError):
                reconcile(self.source, self.store, id_field='id', value='v', apply=True)

    def test_configuration_mismatch_and_foreign_store(self):
        from latent_state_lab.batch import BatchError
        from latent_state_lab.sync import reconcile
        self.sync(apply=True)
        original = self.store.read_bytes()
        with self.assertRaises(BatchError):
            reconcile(self.source, self.store, id_field='team', input_format='csv')
        self.assertEqual(self.store.read_bytes(), original)
        for text in ('{}', '{"format":"foreign"}', 'not json'):
            self.store.write_text(text)
            with self.assertRaises(BatchError):
                self.sync(apply=True)
            self.assertEqual(self.store.read_text(), text)

    def test_symlinks_hardlinks_and_public_state_refused(self):
        from latent_state_lab.batch import BatchError
        target = self.root / 'target'
        target.write_text('private')
        self.store.symlink_to(target)
        with self.assertRaises((BatchError, OSError)):
            self.sync(apply=True)
        self.assertEqual(target.read_text(), 'private')
        self.store.unlink()
        self.sync(apply=True)
        os.link(self.store, self.root / 'alias')
        with self.assertRaises(BatchError):
            self.sync(apply=True)
        (self.root / 'alias').unlink()
        self.store.chmod(0o644)
        with self.assertRaises(BatchError):
            self.sync(apply=True)
        self.store.chmod(0o600)
        other = self.root / 'input-link'
        self.source.rename(other)
        self.source.symlink_to(other)
        with self.assertRaises((BatchError, OSError)):
            self.sync(apply=True)

    def test_row_group_byte_and_time_bounds(self):
        from latent_state_lab.batch import BatchError
        self.sync(apply=True)
        original = self.store.read_bytes()
        for kwargs in ({'max_rows': 2}, {'max_groups': 1}, {'max_line_bytes': 8},
                       {'max_seconds': 0.000001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BatchError):
                self.sync(apply=True, **kwargs)
            self.assertEqual(self.store.read_bytes(), original)


    def test_lowered_bounds_also_refuse_oversized_saved_collections(self):
        from latent_state_lab.batch import BatchError

        self.sync(apply=True)
        original = self.store.read_bytes()
        self.source.write_text('id,team,amount\na,East,1.25\n')
        for apply in (False, True):
            for bounds in ({'max_rows': 1}, {'max_groups': 1}):
                with self.subTest(apply=apply, bounds=bounds):
                    self.store.write_bytes(original)
                    stamp = self.store.stat().st_mtime_ns
                    with self.assertRaisesRegex(BatchError, 'saved.*bound'):
                        self.sync(apply=apply, **bounds)
                    self.assertEqual(self.store.read_bytes(), original)
                    self.assertEqual(self.store.stat().st_mtime_ns, stamp)

    def test_failed_replace_preserves_state_and_removes_temporary(self):
        self.sync(apply=True)
        original = self.store.read_bytes()
        self.source.write_text('id,team,amount\na,West,99\n')
        with patch('latent_state_lab.sync.os.replace', side_effect=OSError('injected')), self.assertRaises(OSError):
            self.sync(apply=True)
        self.assertEqual(self.store.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.sync-*')), [])

    def test_initial_creation_sigkill_recovers_before_and_after_commit(self):
        worker = '''import sys,time
from pathlib import Path
from latent_state_lab import sync
marker=Path(sys.argv[3])
def wrap(original):
    def boundary(src,dst):
        if sys.argv[4]=='after': original(src,dst)
        marker.write_text('ready')
        time.sleep(60)
        if sys.argv[4]=='before': original(src,dst)
    return boundary
sync.os.link=wrap(sync.os.link)
sync.os.replace=wrap(sync.os.replace)
sync.reconcile(Path(sys.argv[1]),Path(sys.argv[2]),id_field='id',input_format='csv',group_by='team',value='amount',apply=True)
'''
        for boundary in ('before', 'after'):
            with self.subTest(boundary=boundary):
                self.store = self.root / f'initial-{boundary}.state'
                marker = self.root / f'{boundary}.ready'
                proc = subprocess.Popen([sys.executable, '-c', worker, str(self.source),
                                         str(self.store), str(marker), boundary],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    end = time.monotonic() + 10
                    while not marker.exists() and proc.poll() is None and time.monotonic() < end:
                        time.sleep(0.01)
                    self.assertTrue(marker.exists(), 'worker did not reach commit boundary')
                    proc.send_signal(signal.SIGKILL)
                    stdout, stderr = proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, -signal.SIGKILL, (stdout, stderr))
                    # Do not clean orphan temporaries before verifying ordinary recovery.
                    preview = self.sync()
                    if boundary == 'before':
                        self.assertFalse(self.store.exists())
                        self.assertEqual(preview['changes']['added'], 3)
                    else:
                        self.assertEqual(self.store.stat().st_nlink, 1)
                        self.assertEqual(preview['changes']['unchanged'], 3)
                    applied = self.sync(apply=True)
                    self.assertEqual(applied['after'], preview['after'])
                    self.assertEqual(self.sync()['changes']['unchanged'], 3)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate(timeout=10)

    def test_preview_opened_store_survives_cooperating_atomic_replacement(self):
        self.sync(apply=True)
        old = self.sync()['before']
        previous = self.store.stat()
        candidate = self.root / 'next.state'
        current_store = self.store
        self.source.write_text('id,team,amount\na,West,99\n')
        self.store = candidate
        new = self.sync(apply=True)['after']
        self.store = current_store
        original = os.fstat
        swapped = False

        def replace_before_stat(descriptor):
            nonlocal swapped
            info = original(descriptor)
            if not swapped and (info.st_dev, info.st_ino) == (previous.st_dev, previous.st_ino):
                os.replace(candidate, self.store)
                swapped = True
            return original(descriptor)

        with patch('latent_state_lab.sync.os.fstat', side_effect=replace_before_stat):
            report = self.sync()
        self.assertTrue(swapped)
        self.assertEqual(report['before'], old)
        self.assertEqual(report['after'], new)
        self.assertEqual(self.sync()['before'], new)

    def test_input_mutation_preserves_state(self):
        from latent_state_lab import sync
        from latent_state_lab.batch import BatchError
        self.sync(apply=True)
        original = self.store.read_bytes()
        record = sync._record
        mutated = False

        def mutate(*args, **kwargs):
            nonlocal mutated
            result = record(*args, **kwargs)
            if not mutated:
                mutated = True
                with self.source.open('ab') as stream:
                    stream.write(b'new,East,7\n')
            return result

        with patch('latent_state_lab.sync._record', side_effect=mutate), self.assertRaises(BatchError):
            self.sync(apply=True)
        self.assertEqual(self.store.read_bytes(), original)

    def test_cli_errors_are_private_structured_and_preview_is_default(self):
        command = [sys.executable, '-m', 'latent_state_lab.sync', str(self.source),
                   str(self.store), '--id', 'id', '--input-format', 'csv',
                   '--group-by', 'team', '--value', 'amount']
        first = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(json.loads(first.stdout)['applied'])
        self.assertFalse(self.store.exists())
        self.source.write_text('id,team,amount\nSECRET_ROW,East,SECRET_VALUE\n')
        failed = subprocess.run(command + ['--apply'], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(failed.returncode, 2)
        error = json.loads(failed.stderr)
        self.assertEqual(error['code'], 'sync_failed')
        self.assertTrue(error['request_id'])
        self.assertNotIn('SECRET', failed.stderr)
        self.assertNotIn(str(self.root), failed.stderr)
        self.assertFalse(self.store.exists())


if __name__ == '__main__':
    unittest.main()
