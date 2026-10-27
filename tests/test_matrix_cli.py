"""Command success means required controls matched, not merely successful execution."""
import importlib.util
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

HAVE_TENSORS = all(importlib.util.find_spec(n) for n in ('torch', 'safetensors'))


@unittest.skipUnless(HAVE_TENSORS, 'optional tensor dependencies absent')
class MatrixCommandTests(unittest.TestCase):
    def test_failed_control_returns_nonzero_and_keeps_report(self):
        from latent_state_lab import continuation_matrix as matrix
        report = {'status': 'FAIL', 'cases': [{'failure': 'retained'}]}
        out = io.StringIO()
        with patch.object(matrix, 'run_matrix', return_value=report), \
                patch('sys.argv', ['matrix']), redirect_stdout(out):
            code = matrix.main()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue()), report)

    def test_success_returns_zero_without_suppressing_details(self):
        from latent_state_lab import continuation_matrix as matrix
        report = {'status': 'PASS', 'cases': [{'raw': 'retained'}]}
        out = io.StringIO()
        with patch.object(matrix, 'run_matrix', return_value=report), \
                patch('sys.argv', ['matrix']), redirect_stdout(out):
            code = matrix.main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), report)

    def test_truncated_successful_worker_is_reported_as_failure(self):
        from latent_state_lab import continuation_matrix as matrix

        def response(command, **_kwargs):
            arm = command[command.index('--worker') + 1]
            record = {'arm': arm, 'tokens': [1, 2] if arm == 'restore' else [1, 2, 3],
                      'state_signature': 'a' * 64, 'position': 3, 'runtime': {'dtype': 'float32'}}
            return subprocess.CompletedProcess(command, 0, json.dumps(record), '')

        with patch.object(matrix.subprocess, 'run', side_effect=response):
            report = matrix.run_matrix(seeds=(7,), cuts=(0,), continuation_steps=3)
        self.assertEqual(report.get('status'), 'FAIL')
        self.assertEqual(len(report['cases']), 2)
        self.assertEqual(report['processes_executed'], 8)
        for case in report['cases']:
            self.assertEqual(case['arms']['restore']['tokens'], [1, 2])
            self.assertEqual(case['arms']['restore']['first_token_difference'], 2)
            self.assertEqual(case['assessment']['status'], 'FAIL')

    def test_malformed_successful_worker_is_retained_in_failed_cli_report(self):
        from latent_state_lab import continuation_matrix as matrix

        for malformed in ({'arm': 'restore', 'tokens': [1, 2, 3]},
                          {'arm': 'restore', 'state_signature': 'a' * 64},
                          {'arm': 'restore', 'tokens': None, 'state_signature': 'a' * 64},
                          None, []):
            def response(command, malformed=malformed, **_kwargs):
                arm = command[command.index('--worker') + 1]
                record = (malformed if arm == 'restore' else
                          {'arm': arm, 'tokens': [1, 2, 3], 'state_signature': 'a' * 64,
                           'position': 3, 'runtime': {'dtype': 'float32'}})
                return subprocess.CompletedProcess(command, 0, json.dumps(record), '')

            out = io.StringIO()
            with self.subTest(malformed=malformed), \
                    patch.object(matrix.subprocess, 'run', side_effect=response), \
                    patch('sys.argv', ['matrix', '--steps', '3']), redirect_stdout(out):
                code = matrix.main()
                report = json.loads(out.getvalue())
                self.assertEqual(code, 1)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['processes_executed'], 32)
                for case in report['cases']:
                    self.assertEqual(case['arms']['restore'], malformed)
                    self.assertEqual(case['assessment']['status'], 'FAIL')
