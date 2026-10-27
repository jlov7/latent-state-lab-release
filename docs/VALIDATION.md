# Validation

The dependency-free utility and optional numerical probe have separate checks.
Run the [README verification commands](../README.md#contracts-and-verification) and
[release procedure](RELEASING.md) on the exact source being evaluated.

| Contract | Discriminating checks | Limit |
| --- | --- | --- |
| Exact correction and replay | [Batch cases](../tests/test_batch.py), [sync cases](../tests/test_sync.py), [patch cases](../tests/test_patch.py) | Local bounded files and trusted directory ancestry |
| A deletion ceiling is bound to the reviewed replacement | [Deletion guard](../tests/test_snapshot_deletion_guard.py), [guarded example](../examples/guarded_snapshot.py) | Unsigned consistency checks do not authenticate a reviewer |
| Append resumption verifies the committed prefix | [Feed cases](../tests/test_batch_feed.py) | Committed input must remain immutable |
| Numerical comparisons distinguish mismatch from execution failure | [Verdict cases](../tests/test_matrix_verdict.py), [command cases](../tests/test_matrix_cli.py) | Pinned random-weight probes, not real-engine language results |

The package gate checks a strict source-file allowlist, wheel membership,
isolated installation, examples and every utility command family. Core runs
skip the explicitly optional tensor tests when their requirements are absent.
Use the separate pinned tensor environment for numerical claims.
[Evaluation](EVALUATION.md) gives the reproduction commands and controls.
Real-engine, pretrained-model, GPU, human-benefit and production results remain
outside these software checks.
