# Security and disclosure posture

This package processes local JSONL files and authored restoration fixtures, with no network client,
telemetry, tool executor or pickle loading. Capsules are unencrypted and their
hashes do not authenticate the producer. Treat observations and decoded tensors
as sensitive; use trusted descriptors and keep files appropriately protected.

Input limits and checks are described in docs/DESIGN.md. These are bounded
engineering checks, not a security certification. Report a reproducing fixture
without credentials, personal data or confidential capsules. Do not upload
untrusted generated executable code or add a tool executor to these fixtures.

## Optional Torch dependency

The core installs no third-party runtime packages. The optional tensor probe
targets Torch 2.13.0. [GHSA-rrmf-rvhw-rf47](https://github.com/advisories/GHSA-rrmf-rvhw-rf47)
lists that version as patched for its JIT memory-corruption issue. The previous
2.10.0 pin also matched [PYSEC-2026-139](https://osv.dev/vulnerability/PYSEC-2026-139),
whose database range ends at 2.10.0. That range alone does not prove a vendor fix:
the [proposed pt2-loading fix](https://github.com/pytorch/pytorch/pull/176791) was
closed, and [the 2.13.0 loader](https://github.com/pytorch/pytorch/blob/v2.13.0/torch/export/__init__.py)
still documents pickle-backed loading. Treat untrusted pt2 loading as unsafe.
The upgrade is not a general Torch security certification or proof that this
separate issue is fixed.

This probe stores raw tensors through Safetensors and bounded JSON capsules.
It does not expose TorchScript, torch.export.load, torch.load or external model
loading. Do not add those paths without a separate security review. Numerical
execution on a new pin requires fresh tests/CI; core tests with optional skips
do not qualify the probe.
