# V1 stability and compatibility

Version 1.0.0 stabilizes the local exact-aggregation utility, not an inference
engine. Package versions use semantic versioning: compatible additions use minor
versions, compatible corrections use patch versions, and breaking public contracts
require a major version with migration notes. Resource-limit, malformed-input and
permission fixes may reject invalid input that earlier versions accidentally accepted.

Version 1.0.1 corrects optional numerical verdicts and malformed-worker reporting.
The stable utility interfaces and saved artifact descriptors remain unchanged.

Version 1.1.0 adds optional snapshot deletion ceilings through `--max-deletions`
and `sync.reconcile(max_deletions=None)`. Zero refuses every deletion; omission
preserves existing behavior and historical review digests. Guarded reviews bind
the ceiling, including its presence, without changing saved store configuration
or artifact pins. An unchanged replay needs no deletion allowance and does not
replace existing state. A changed apply invalidates its old review digest.

The supported platform is Python 3.11+ on macOS/Linux with local POSIX file locking,
atomic replacement and hard links where exclusive capsule creation requires them.
Do not assume Windows, network-filesystem or cross-host checkpoint support.

## Public interfaces

The stable command names are latent-state-batch, latent-state-inspect,
latent-state-sync, latent-state-patch and latent-state-lab. Documented arguments,
default read-only sync/patch behavior, explicit apply authority and exit status
0/2 remain compatible within V1. Every command's --version prints
"latent-state-lab VERSION" without job arguments or filesystem effects.

Documented Python entrypoints are batch.aggregate, batch.inspect_checkpoint,
sync.reconcile, patch.apply_patch and capsule.read_capsule/write_capsule.
BatchError and CapsuleError remain ValueError subclasses; PatchConflict and
SnapshotDeletionLimit are BatchError subclasses with complete report attributes.
Filesystem errors can remain OSError.
Names beginning with an underscore and module implementation constants are private.
ToySession and tensor_probe are research fixtures, outside the stable numerical API.

Successful reports are JSON objects. Existing documented field names, types,
rational-string arithmetic, classification meaning and sorted groups/details are
stable. New optional fields can be added; consumers should select fields rather
than require an exact key set. Request IDs, timings, ZIP timestamps and whitespace
are not reproducibility targets. Error codes and documented location fields are
stable; prose messages may improve. Syntax errors retain argparse text.
Conflicted patch apply returns 2 with its report on stdout and error on stderr.
Snapshot apply exceeding an explicit deletion ceiling also returns 2 with its
proposed report on stdout and error on stderr, preserving committed state.
Guarded previews return 0 and include `deletion_guard` even when its `passed` is
false. Its fields are `maximum`, `proposed` and `passed`; unguarded reports omit it.

## Saved artifacts and upgrades

| Artifact | Retained format/runtime | Upgrade rule |
| --- | --- | --- |
| Capsule container | Integer format version 1 | Exact expected descriptor and bounded chunk schema required |
| Immutable JSONL batch | State v1, jsonl-batch-v1-package-0.2.0 descriptor | Existing pin retained, including its historical package component |
| CSV/append batch | State v2, feed-batch-v2-package-0.4.0 descriptor | Existing pin retained; incompatible with v1 |
| Selected snapshot store | latent-state-sync-v1 | Stable ID/format/group/value configuration must match |
| Before/after patch input | latent-state-patch-v1 | Exact id/before/after schema; decimal strings; simultaneous rows |
| Toy fixture | toy-runtime-v2 | Obsolete v1 rejected; no guessing or migration |
| Optional tensor fixture | Installed Torch/platform descriptor | Separate numerical evidence required for each supported pin |

A package bump alone does not change artifact descriptors. V1 retains the formats
written by 0.6.0, subject to their existing same-host, full Python implementation/
version, source identity, configuration and permission checks. Do not edit saved
pins to force acceptance. No automatic conversion between batch modes, snapshot
stores or Python runtimes is supplied. Preserve original source/state, use a new
job when incompatible, and compare independent totals before adopting a new result.

Review digests bind exact source/patch bytes, prior committed bytes and selected
configuration, plus the optional snapshot deletion ceiling when supplied. They
are unsigned consistency guards, not proof of human approval.
A changed apply invalidates its old digest. Equivalent selected values can still
require a new review when raw bytes change.

Patch after-value replay is not event-ID deduplication. Snapshot omission is
deletion. Append-only byte-prefix semantics do not support correction of committed
history. Full state I/O, cooperative deadlines, private directory assumptions and
lack of fsync durability remain part of the contract. See [usage](USAGE.md).

The V1 lowered sync row/group bounds apply to both prior and incoming collections.
Earlier versions checked only incoming collections. Refusal preserves committed
state; use bounds large enough for both collections or start a different job.

The optional probe's V1 dependency target is Torch 2.13.0. Its exact runtime
descriptor changes naturally with the installed Torch version; historical
2.10.0 capsules and evidence are not rewritten. Regenerate an authored fixture
and run the same-pin numerical comparison. This is separate from the stable
core's preserved v1/v2 artifacts and from real-model continuation.
