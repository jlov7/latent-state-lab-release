# Limitations

Correction batches add exact before/after validation against private sync state,
with all-conflict preview and atomic refusal. They still read the full state and
rewrite changed state. After-value replay is not event-ID deduplication: returning
to an earlier before value permits reapplying that batch. Reports include exact
selected data. Review digests are unsigned consistency checks, not authenticated
approval. No database, scan-speed, production or human-usefulness claim follows.
Snapshot/feed byte semantics and empirical NOT RUN qualifications are unchanged.

The practical CSV/JSONL batch command is a local POSIX utility, independent of
the research fixtures. It supports regular files, bounded string groups and
exact decimal sum/mean. Immutable input is the default. Explicit append-only
mode accepts new records between invocations after verifying the saved source
identity and committed prefix. The source must stay fixed during each invocation.
It re-reads the input for integrity, commits
once per successful run by default (or at an explicitly bounded opt-in cadence)
and repeats uncommitted work after interruption. Periodic deadlines are cooperative,
not preemption of a blocking filesystem operation or atomic replacement. Inspection
validates saved state only and always labels the source unverified. Decimal display
is rounded presentation; canonical arithmetic remains rational. Append-only mode
requires newline-terminated records. Bytes after the saved offset may be repaired,
but committed edits, truncation below that offset, path replacement and changed
configuration are refused. It has no cross-host migration, adversarial checkpoint authentication,
power-loss durability guarantee or transaction with external outputs. It does
not restore a pretrained engine or add semantic AI capability. Detailed limits
and caller responsibilities are in [usage](USAGE.md).

- The toy and random-weight numerical probe are authored fixtures. Their success
  demonstrates local serialization/restoration, not pretrained HybridAgent
  behavior, language quality, utility or research novelty.
- Real HybridAgent integration is **HOLD / NOT RUN**. Its inspected engine
  initializes state inside generation. A resumable adapter still needs exact
  engine/cache/configuration custody, sampling state, budgets, batch bookkeeping
  and a completed-effect boundary with its observation feed fully drained.
- Capsule hashes do not authenticate a producer. No security certification or
  absence-of-bugs claim is made.
- Descriptor equality cannot prove external weights or code have the claimed
  identity. Capsules are unencrypted and may contain sensitive observations.
  Keep them local with appropriate filesystem protections.
- Size limits suit small experiments, not real model checkpoints. The probe uses
  CPU float32 and caps sessions at 256 steps. Different Torch/platform/hardware
  versions require new evidence.
- Atomic replacement is not an fsync durability guarantee. Exclusive creation
  requires local hard-link support.
- Tests, examples and verification scripts are source-archive resources. The
  wheel installs runtime modules and the toy, batch, inspection, sync and patch commands. Numerical
  dependencies need a separate installation.
