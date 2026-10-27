"""Small, explicit numerical controls; not pretrained-model research evidence."""
import importlib.util
import unittest

HAVE_TENSORS = all(importlib.util.find_spec(name) for name in ('torch', 'safetensors'))


@unittest.skipUnless(HAVE_TENSORS, 'optional tensor dependencies absent')
class ContinuationMatrixTests(unittest.TestCase):
    def test_fresh_process_matrix_includes_lossless_and_lossy_controls(self):
        from latent_state_lab.continuation_matrix import run_matrix
        report = run_matrix(seeds=(7,), cuts=(0, 5), continuation_steps=3)
        self.assertEqual(len(report['cases']), 4)
        self.assertEqual(report['processes_executed'], 16)
        for case in report['cases']:
            with self.subTest(schedule=case['schedule'], cut=case['cut']):
                self.assertTrue(case['arms']['restore']['state_match'])
                self.assertTrue(case['arms']['replay']['state_match'])
                self.assertEqual(case['arms']['restore']['first_token_difference'], None)
                if case['schedule'] == 'text' or case['cut'] == 0:
                    self.assertTrue(case['arms']['text']['state_match'])
                else:
                    self.assertFalse(case['arms']['text']['state_match'])
        self.assertFalse(report['pretrained_model_result'])

    def test_bounded_arguments_fail_before_execution(self):
        from latent_state_lab.continuation_matrix import run_matrix
        for kwargs in ({'seeds': ()}, {'seeds': (True,)}, {'seeds': (7, 7)},
                       {'cuts': (-1,)}, {'cuts': (250,), 'continuation_steps': 12},
                       {'continuation_steps': 0}, {'seeds': tuple(range(9))}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_matrix(**kwargs)
