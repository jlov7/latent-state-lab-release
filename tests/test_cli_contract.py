"""Public command entrypoints must identify the installed release without opening files."""
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from latent_state_lab import __version__


class CliContractTests(unittest.TestCase):
    def test_version_requires_no_job_arguments_or_optional_packages(self):
        for module in ("demo", "batch", "inspect", "sync", "patch"):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", f"latent_state_lab.{module}", "--version"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"latent-state-lab {__version__}\n")
                self.assertEqual(result.stderr, "")

    def test_package_and_source_version_agree(self):
        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        metadata = tomllib.loads(project.read_text())["project"]
        self.assertEqual(metadata["version"], __version__)
