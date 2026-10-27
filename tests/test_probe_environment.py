"""The numerical probe's declared float32/CPU contract is not an ambient setting."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HAVE_TENSORS = all(importlib.util.find_spec(name) for name in ('torch', 'safetensors'))


@unittest.skipUnless(HAVE_TENSORS, 'optional tensor dependencies absent')
class ProbeEnvironmentTests(unittest.TestCase):
    def run_probe(self):
        from latent_state_lab.tensor_probe import Probe
        probe = Probe(13)
        for mode in ('text', 'latent', 'text', 'latent', 'latent', 'text'):
            probe.step(mode)
        return probe

    def test_default_dtype_does_not_change_the_declared_probe(self):
        import torch
        expected = self.run_probe()
        previous = torch.get_default_dtype()
        try:
            torch.set_default_dtype(torch.float64)
            actual = self.run_probe()
            self.assertEqual(torch.get_default_dtype(), torch.float64)
        finally:
            torch.set_default_dtype(previous)
        self.assertEqual(actual.signature(), expected.signature())
        self.assertEqual(actual.hidden.dtype, torch.float32)

    def test_cpu_autocast_does_not_silently_change_the_probe(self):
        import torch
        expected = self.run_probe()
        with torch.autocast('cpu', dtype=torch.bfloat16):
            actual = self.run_probe()
            self.assertTrue(torch.is_autocast_enabled('cpu'))
        self.assertEqual(actual.signature(), expected.signature())

    def test_created_under_other_defaults_can_be_restored(self):
        import torch

        from latent_state_lab.tensor_probe import Probe
        previous = torch.get_default_dtype()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'state.lsc'
            try:
                torch.set_default_dtype(torch.float64)
                source = self.run_probe()
                source.snapshot(path)
            finally:
                torch.set_default_dtype(previous)
            for arm in ('restore', 'replay'):
                with self.subTest(arm=arm):
                    self.assertEqual(Probe.load(path, arm).signature(), source.signature())

    def test_restore_under_autocast_matches_normal_continuation(self):
        import torch

        from latent_state_lab.tensor_probe import Probe
        source = self.run_probe()
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'state.lsc'
            source.snapshot(path)
            source.step('latent')
            with torch.autocast('cpu', dtype=torch.bfloat16):
                restored = Probe.load(path)
                restored.step('latent')
            self.assertEqual(source.signature(), restored.signature())
