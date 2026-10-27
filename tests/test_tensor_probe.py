import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from latent_state_lab.capsule import CapsuleError, read_capsule, write_capsule

HAVE_TENSORS = all(importlib.util.find_spec(x) for x in ('torch', 'safetensors'))


@unittest.skipUnless(HAVE_TENSORS, 'optional torch/safetensors packages are not installed')
class TensorProbeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('latent_state_lab.tensor_probe'),
                             'Missing real tensor/RNG continuation probe')
        from latent_state_lab.tensor_probe import Probe, descriptor
        self.Probe, self.descriptor = Probe, descriptor
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.lsc'
        self.session = Probe(seed=7)
        for mode in ['text', 'latent', 'text', 'latent', 'text']:
            self.session.step(mode)
        self.session.snapshot(self.path)

    def test_snapshot_restore_and_tape_replay_match_exact_state(self):
        for arm in ['restore', 'replay']:
            other = self.Probe.load(self.path, arm)
            self.assertEqual(other.signature(), self.session.signature())
            self.assertEqual(other.step('latent'), self.session.step('latent'))
            self.assertEqual(other.signature(), self.session.signature())
            self.session = self.Probe.load(self.path)

    def test_fresh_process_continuation_matches_parent(self):
        result = subprocess.run([sys.executable, '-m', 'latent_state_lab.tensor_probe',
                                 '--arm', 'restore', '--capsule', str(self.path), '--steps', '7'],
                                capture_output=True, text=True, timeout=30, check=True)
        report = json.loads(result.stdout)
        expected = [self.session.step('latent' if i % 3 == 1 else 'text') for i in range(7)]
        self.assertEqual(report['tokens'], expected)
        self.assertEqual(report['signature'], self.session.signature())

    def test_text_restart_is_explicitly_lossy(self):
        restored = self.Probe.load(self.path, 'text')
        self.assertNotEqual(restored.signature(), self.session.signature())
        self.assertLess(restored.position, self.session.position)

    def test_rng_is_required_not_silently_reseeded(self):
        from safetensors.torch import load, save
        pin, chunks = read_capsule(self.path, self.descriptor())
        tensors = load(chunks['state'])
        del tensors['rng']
        chunks['state'] = save(tensors)
        write_capsule(self.path, pin, chunks)
        with self.assertRaises(CapsuleError):
            self.Probe.load(self.path)

    def test_shape_and_dtype_mismatch_refused(self):
        from safetensors.torch import load, save
        for mutation in ['shape', 'dtype', 'nan']:
            self.session.snapshot(self.path)
            pin, chunks = read_capsule(self.path, self.descriptor())
            tensors = load(chunks['state'])
            if mutation == 'shape':
                tensors['k'] = tensors['k'][:1]
            elif mutation == 'dtype':
                tensors['hidden'] = tensors['hidden'].double()
            else:
                tensors['hidden'][0] = float('nan')
            chunks['state'] = save(tensors)
            write_capsule(self.path, pin, chunks)
            with self.subTest(mutation=mutation), self.assertRaises(CapsuleError):
                self.Probe.load(self.path)

    def test_pending_operation_cannot_be_captured(self):
        self.session.pending_tool = True
        with self.assertRaises(CapsuleError):
            self.session.snapshot(self.path)

    def test_runtime_mismatch_is_rejected(self):
        pin, chunks = read_capsule(self.path, self.descriptor())
        pin['runtime_revision'] = 'some-other-runtime'
        write_capsule(self.path, pin, chunks)
        with self.assertRaises(CapsuleError):
            self.Probe.load(self.path)

    def test_mutating_saved_state_does_not_mutate_original_session(self):
        restored = self.Probe.load(self.path)
        restored.hidden.zero_()
        self.assertNotEqual(restored.signature(), self.session.signature())

    def test_empty_session_round_trip(self):
        empty = self.Probe(7)
        empty.snapshot(self.path)
        self.assertEqual(empty.signature(), self.Probe.load(self.path).signature())

    def test_wrong_mode_and_unbounded_steps_refused(self):
        with self.assertRaises(CapsuleError):
            self.session.step('execute-python')
        for _ in range(251):
            self.session.step('latent')
        with self.assertRaises(CapsuleError):
            self.session.step('latent')

    def test_actual_tensor_codec_not_pickle(self):
        pin, chunks = read_capsule(self.path, self.descriptor())
        from safetensors.torch import load
        self.assertEqual(set(load(chunks['state'])), {'k', 'v', 'hidden', 'rng', 'tape'})
        self.assertIn('UNTRAINED', pin['model_revision'])

    def test_text_control_matches_when_no_latent_inputs_were_used(self):
        plain = self.Probe(7)
        for _ in range(8):
            plain.step('text')
        plain.snapshot(self.path)
        restored = self.Probe.load(self.path, 'text')
        self.assertEqual(restored.signature(), plain.signature())
