"""Explicit deletion ceilings protect full-snapshot imports, not partial updates."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from latent_state_lab import sync
from latent_state_lab.batch import BatchError

CONFIG = {'id_field': 'id', 'group_by': 'team', 'value': 'amount'}
INITIAL = [{'id': 'a', 'team': 'cpu', 'amount': 0.1},
           {'id': 'b', 'team': 'gpu', 'amount': 0.2},
           {'id': 'c', 'team': 'gpu', 'amount': -0.05}]


def write(path, rows):
    path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))


class SnapshotDeletionGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / 'snapshot.jsonl'
        self.state = self.root / 'snapshot.state'
        write(self.source, INITIAL)
        sync.reconcile(self.source, self.state, apply=True, **CONFIG)
        self.original = self.state.read_bytes()

    def tearDown(self):
        self.directory.cleanup()

    def test_partial_export_is_inspectable_but_cannot_be_applied_above_ceiling(self):
        write(self.source, INITIAL[:1])
        report = sync.reconcile(self.source, self.state, max_deletions=0, **CONFIG)
        self.assertEqual(report['changes']['deleted'], 2)
        self.assertEqual(report['deletion_guard'], {'maximum': 0, 'proposed': 2, 'passed': False})
        self.assertFalse(report['applied'])
        with self.assertRaises(BatchError) as caught:
            sync.reconcile(self.source, self.state, max_deletions=0, apply=True, **CONFIG)
        self.assertEqual(caught.exception.report['deletion_guard'], report['deletion_guard'])
        self.assertFalse(caught.exception.report['applied'])
        self.assertFalse(caught.exception.report['store_written'])
        self.assertEqual(self.state.read_bytes(), self.original)

    def test_exact_ceiling_applies_reviewed_batch_and_repeat_does_not_write(self):
        changed = [INITIAL[0], {'id': 'c', 'team': 'cpu', 'amount': -0.15},
                   {'id': 'd', 'team': 'gpu', 'amount': 0.25}]
        write(self.source, changed)
        review = sync.reconcile(self.source, self.state, max_deletions=1, **CONFIG)
        actual = sync.reconcile(self.source, self.state, max_deletions=1, apply=True,
                                expect_review=review['review_sha256'], **CONFIG)
        self.assertEqual(actual['changes'], {'added': 1, 'deleted': 1, 'updated': 1, 'unchanged': 1})
        self.assertEqual(actual['after']['groups'], [
            {'key': 'cpu', 'count': 2, 'sum': '-1/20', 'mean': '-1/40'},
            {'key': 'gpu', 'count': 1, 'sum': '1/4', 'mean': '1/4'}])
        self.assertTrue(actual['store_written'])
        saved = self.state.read_bytes()
        repeated = sync.reconcile(self.source, self.state, max_deletions=0, apply=True, **CONFIG)
        self.assertFalse(repeated['store_written'])
        self.assertEqual(self.state.read_bytes(), saved)

    def test_review_binds_the_guard_and_cannot_be_reused_after_removing_it(self):
        write(self.source, INITIAL[:2])
        review = sync.reconcile(self.source, self.state, max_deletions=1, **CONFIG)
        for options in ({}, {'max_deletions': 2}, {'max_deletions': 0}):
            with self.subTest(options=options), self.assertRaises(BatchError):
                sync.reconcile(self.source, self.state, apply=True,
                               expect_review=review['review_sha256'], **options, **CONFIG)
            self.assertEqual(self.state.read_bytes(), self.original)
        legacy = sync.reconcile(self.source, self.state, **CONFIG)
        with self.assertRaises(BatchError):
            sync.reconcile(self.source, self.state, max_deletions=1, apply=True,
                           expect_review=legacy['review_sha256'], **CONFIG)

    def test_legacy_review_bytes_and_default_semantics_remain_unchanged(self):
        write(self.source, INITIAL[:1])
        report = sync.reconcile(self.source, self.state, **CONFIG)
        config = {'id_field': 'id', 'group_by': 'team', 'value': 'amount', 'input_format': 'jsonl'}
        expected = hashlib.sha256(sync._encode({
            'config': config, 'store_sha256': hashlib.sha256(self.original).hexdigest(),
            'snapshot_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()})).hexdigest()
        self.assertEqual(report['review_sha256'], expected)
        self.assertNotIn('deletion_guard', report)
        applied = sync.reconcile(self.source, self.state, apply=True, **CONFIG)
        self.assertEqual(applied['changes']['deleted'], 2)
        self.assertTrue(applied['store_written'])

    def test_bad_ceiling_is_rejected_before_files_are_read(self):
        for maximum in (True, False, -1, 1.0, '1', sync.MAX_ROWS + 1):
            with self.subTest(maximum=maximum), \
                    patch.object(sync, '_read_store', side_effect=AssertionError('I/O occurred')), \
                    self.assertRaises(BatchError):
                sync.reconcile(self.source, self.state, max_deletions=maximum, **CONFIG)

    def test_empty_export_is_a_deletion_not_an_empty_noop(self):
        write(self.source, [])
        with self.assertRaises(BatchError):
            sync.reconcile(self.source, self.state, max_deletions=2, apply=True, **CONFIG)
        report = sync.reconcile(self.source, self.state, max_deletions=3, apply=True, **CONFIG)
        self.assertEqual(report['after']['records'], 0)
        self.assertEqual(report['changes']['deleted'], 3)

    def test_initial_import_does_not_count_missing_previous_records(self):
        self.state.unlink()
        report = sync.reconcile(self.source, self.state, max_deletions=0, apply=True, **CONFIG)
        self.assertTrue(report['deletion_guard']['passed'])
        self.assertEqual(report['changes']['added'], 3)

    def test_guard_is_not_persisted_as_a_store_configuration_change(self):
        guarded = sync.reconcile(self.source, self.state, max_deletions=0, apply=True, **CONFIG)
        self.assertFalse(guarded['store_written'])
        raw = json.loads(self.state.read_bytes())
        self.assertNotIn('max_deletions', raw['config'])
        self.assertNotIn('deletion_guard', raw)

    def test_actual_cli_retains_blocked_report_without_changing_state(self):
        write(self.source, INITIAL[:1])
        cmd = [sys.executable, '-m', 'latent_state_lab.sync', str(self.source), str(self.state),
               '--id', 'id', '--group-by', 'team', '--value', 'amount', '--max-deletions', '0']
        for apply in ([], ['--apply']):
            result = subprocess.run(cmd + apply, capture_output=True, text=True, timeout=10, check=False,
                                    env=os.environ.copy())
            self.assertEqual(result.returncode, 2 if apply else 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['deletion_guard']['passed'])
            self.assertFalse(report['applied'])
            if apply:
                self.assertEqual(json.loads(result.stderr)['code'], 'sync_failed')
            else:
                self.assertEqual(result.stderr, '')
            self.assertEqual(self.state.read_bytes(), self.original)
