"""A comparison must not report success merely because every process exited zero."""
import copy
import unittest

from latent_state_lab.matrix_verdict import assess_case


def case(schedule='text', cut=5):
    rows = {name: {'arm': name, 'tokens': [1, 2, 3], 'position': cut + 3,
                   'state_signature': 'a' * 64, 'runtime': {'dtype': 'float32', 'device': 'cpu'}}
            for name in ('uninterrupted', 'restore', 'replay', 'text')}
    if schedule == 'hybrid':
        rows['text']['position'] -= (cut + 1) // 3
    return {'seed': 7, 'cut': cut, 'schedule': schedule, 'continuation_steps': 3, 'arms': rows}


class MatrixVerdictTests(unittest.TestCase):
    def test_complete_matching_controls_pass(self):
        self.assertEqual(assess_case(case())['status'], 'PASS')

    def test_each_required_raw_field_can_fail_despite_green_derived_flags(self):
        for arm in ('restore', 'replay', 'text'):
            for field, value in (('tokens', [1, 2]), ('tokens', [1, 2, 4]),
                                 ('tokens', [True, 2, 3]), ('position', 9),
                                 ('state_signature', 'b' * 64), ('runtime', {'dtype': 'float64'})):
                item = case()
                item['arms'][arm].update(state_match=True, first_token_difference=None)
                item['arms'][arm][field] = value
                with self.subTest(arm=arm, field=field, value=value):
                    self.assertEqual(assess_case(item)['status'], 'FAIL')

    def test_baseline_itself_must_be_complete(self):
        for tokens in ([], [1, 2], [1, 2, 3, 4], [True, 2, 3]):
            item = case()
            item['arms']['uninterrupted']['tokens'] = tokens
            self.assertEqual(assess_case(item)['status'], 'FAIL')

    def test_lossy_control_is_not_required_to_fail(self):
        item = case(schedule='hybrid')
        self.assertEqual(assess_case(item)['status'], 'PASS')
        item['arms']['text'].update(tokens=[4, 5, 6], position=6, state_signature='b' * 64)
        self.assertEqual(assess_case(item)['status'], 'PASS')

    def test_lossy_control_requires_runtime_and_compressed_final_position(self):
        for field, value in (('position', 0), ('position', 8),
                             ('runtime', {'dtype': 'float64', 'device': 'cuda'})):
            item = case(schedule='hybrid')
            item['arms']['text'][field] = value
            with self.subTest(field=field, value=value):
                verdict = assess_case(item)
                self.assertEqual(verdict['status'], 'FAIL')
                self.assertIn({'arm': 'text', 'field': field}, verdict['failures'])

    def test_agreement_on_wrong_runtime_does_not_qualify_cpu_probe(self):
        for runtime in ({'dtype': 'float64', 'device': 'cuda'}, {'dtype': 'float32'}):
            item = case()
            for row in item['arms'].values():
                row['runtime'] = runtime
            with self.subTest(runtime=runtime):
                self.assertEqual(assess_case(item)['status'], 'FAIL')

    def test_worker_runtime_must_match_expected_parent_descriptor(self):
        item = case()
        expected = item['arms']['uninterrupted']['runtime'] | {'runtime_revision': 'expected'}
        self.assertEqual(assess_case(item, expected_runtime=expected)['status'], 'FAIL')

    def test_prefix_without_latent_steps_is_a_lossless_control(self):
        for cut in (0, 1):
            item = case(schedule='hybrid', cut=cut)
            item['arms']['text']['tokens'] = [4, 5, 6]
            with self.subTest(cut=cut):
                self.assertEqual(assess_case(item)['status'], 'FAIL')

    def test_missing_arm_and_wrong_identity_are_failures(self):
        item = case()
        del item['arms']['replay']
        self.assertEqual(assess_case(item)['status'], 'FAIL')
        item = case()
        item['arms']['restore']['arm'] = 'replay'
        self.assertEqual(assess_case(item)['status'], 'FAIL')

    def test_no_mutation_and_no_trust_in_reported_status(self):
        item = case()
        item['status'] = 'FAIL'
        saved = copy.deepcopy(item)
        self.assertEqual(assess_case(item)['status'], 'PASS')
        self.assertEqual(item, saved)

    def test_invalid_case_bounds_are_rejected(self):
        for patch in ({'schedule': 'unknown'}, {'cut': True}, {'continuation_steps': 0},
                      {'continuation_steps': 33}, {'cut': 256}, {'arms': []}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                assess_case(case() | patch)
