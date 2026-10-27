# Release checks

Run from the source distribution on a supported POSIX host. Install the package
and requirements-dev.txt into a local environment. The lint tool is pinned; it is
not a runtime dependency. No lint rule or test should be disabled to obtain a pass.

1. Run the README lint and unittest commands.
2. Run scripts/verify_release.py. It builds an sdist, checks its allowlist, builds
   its wheel, checks license/README metadata, installs outside source and runs
   tests, examples, all command families and the README quickstart.
3. Run scripts/verify_docs.py to validate local image/document links, anchors and
   the supplied SVG's self-contained accessible structure.
4. Retain commands, exits, pass/skip counts, package hashes and any failed attempts.
   Do not turn core passes into a numerical or scientific claim.

Use --keep-work NEW_DIRECTORY on the package gate to retain inspected artifacts.
Its .release-scratch location is disposable; remove only resources created by
your run after recording receipts. Public build dependencies can be fetched by
the normal build. Offline maintainers can use already installed exact build tools
with UV_NO_BUILD_ISOLATION=1 and UV_OFFLINE=1.

The core suite intentionally skips optional tensor tests when their separate
requirements are absent. Changes to that probe require its pinned numerical suite
and fresh-process comparison as well. A core utility release does not certify an
unrun optional probe, real model, human benefit or production deployment.

Check exact-head CI and the final source/package surface before distributing:
README, docs, examples, tests, scripts, workflow, license, metadata and linked
assets. Exclude private planning, handoffs, source data, checkpoints and reports.
A source distribution includes reproduction resources. A wheel installs runtime
modules, license metadata and command entrypoints only.

Apply the [stability policy](STABILITY.md), publish versioned release notes and
record compatibility changes. Verify the licensing and provenance of every
distributed file. A local gate pass establishes only its documented test coverage.

## Changelog

### 1.1.0

Snapshot reconciliation accepts an optional deletion ceiling with `--max-deletions`
or `sync.reconcile(max_deletions=...)`. Zero refuses deletions while allowing
additions and updates. A guarded preview reports proposed deletions; apply exceeding
the ceiling refuses the whole replacement and retains the report without changing
committed state. Guarded review digests bind the ceiling, so changing or removing
it requires fresh review. An unchanged repeat needs no deletion allowance and
does not replace existing state. Omission preserves previous behavior, report
shape and review digests; saved configuration and artifact pins are unchanged.

The guarded CSV example checks refusal, reviewed corrections and repeat use against
exact script and SQLite alternatives. Equal outputs are a bounded software check.
No speed advantage, human-effort improvement or real-model result is claimed.

### 1.0.1

The optional numerical matrix validates every arm's runtime and final position,
including the compressed prefix of the lossy text control. Worker records are
assessed before computing derived flags, so missing fields and non-object records
remain visible in a failed report with exit 1. The parent descriptor also prevents
all workers from qualifying by agreeing on an unexpected runtime. Tokens and state
signatures may still match or differ for a genuinely lossy control.

Precision isolation and bounded fresh-process comparisons remain untrained probe
checks. Stable utility commands, exact arithmetic and capsule pins are preserved.
CI runners use Ubuntu 24.04 explicitly, with the existing jobs and timeouts.

### 1.0.0

Version 1.0.0 adds saved-collection bound enforcement, additive --version commands,
a supplied-fixture quickstart, workflow illustration and reproducible lint/docs/
installed-example checks. Existing artifact pins and exact arithmetic remain.
