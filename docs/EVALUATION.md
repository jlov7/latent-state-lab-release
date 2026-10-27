# Evaluation and reproduction

## CSV feed reproduction (0.4.0)

`scripts/verify_batch_feed.py` runs actual CLI processes against a separate
14-nonblank-line stdlib CSV/Decimal restart baseline. It generates 1,291 authored
records and three 113-record appends, with independent integer-thousandth expected
totals at every stage. CSV includes a BOM, Unicode, quoted commas and embedded
newlines. It compares exact counts/sums/means and newly aggregated record counts,
then verifies zero new records at unchanged EOF. The baseline reaggregates 5,842
records across those stages; successful incremental processing aggregates 1,630.
This is a work-accounting observation, not a speed or disk-I/O claim.

The gate kills a real console process after observing a cadence checkpoint,
inspects saved progress and checks exact recovery. A separate injected SIGSTOP
at the real atomic replacement boundary is followed by SIGKILL; prior checkpoint
bytes must remain unchanged. A multiline malformed tail retains its cadence
prefix and resumes after suffix repair. Changed committed bytes must be refused.
Source-independent inspection must leave the checkpoint and directory entries
unchanged. Retained `COMMANDS.json` records argv, exits, outputs and local elapsed
times. `RESULT.json` binds each stage's source hash, expected output and integrity
bytes separately. `--keep-work` preserves these owned artifacts for review.

Run `scripts/verify_batch_feed.py --source`, or supply the installed bin directory.
`scripts/verify_release.py` runs the installed gate outside the checkout and keeps
its inputs/checkpoints inside its own verification directory when `--keep-work`
is selected. The existing CI release-verifier step includes this gate.

Unit tests cover header-only/BOM files, strict CSV failures, complete logical-record
bounds, physical/byte error locations across resume, v1 separation, v2 schema and
runtime refusal, prefix edits/replacement/truncation, write/deadline failures,
in-run mutation and private errors. These local authored journeys do not establish
human usefulness, production savings or pretrained latent-agent restoration.

## Practical batch reproduction

`scripts/verify_release.py` runs `scripts/verify_batch.py` against the installed
wheel outside the checkout. The latter generates 1,733 custom local synthetic
rows with Unicode group keys, escaped newlines, CRLF lines and signed decimal
amounts. A distinct one-pass baseline accumulates integer thousandths and formats
reduced rational results independently of the batch module. It compares all
groups/counts/sums/means with uninterrupted execution and a 613/1,120-row process
handover. It also kills the installed command while locked, restarts, and injects
a process stop just before the actual atomic replacement, then kills/restarts.
Both interruption controls must preserve the previous checkpoint bytes.

Batch tests cover malformed/duplicate/non-finite/invalid-Unicode rows, changed,
truncated, appended and wrong input, configuration/runtime mismatch, corruption,
invalid saved progress, exclusive locking and state/line/group bounds. These are
engineering checks. Avoiding repeated aggregation is useful resumability, not
semantic AI capability, an engine integration or evidence of faster scanning.

These are authored software fixtures. They do not evaluate language competence or
independently validate a research paper.

## Core

Run both README verification commands. Tests cover fresh-process toy restoration;
Unicode/empty/newline roundtrips; runtime mismatch; damaged chunks; unexpected
paths; compression; size limits; unfinished effects; and private POSIX output
permissions. CLI tests cover exclusive creation, explicit replacement and
structured missing/corrupt/incompatible input errors.

The former writer accepted a 131,073-byte manifest and could replace a valid
capsule with one its reader refused. A regression checks refusal and byte-for-byte
destination preservation. Another writes and reads the exact 131,072-byte limit.
Strict manifest regressions reject boolean/float format versions and duplicate
keys at the root, descriptor, chunk-map and chunk-record levels. Chunk sizes
already rejected boolean/float values; that existing boundary is now tested.

The package verifier builds a wheel from the sdist, audits both file lists and
installs with no source import fallback. Tests/examples and optional dependency
pins are in the sdist. The wheel contains runtime modules and console metadata.
The isolated dependency-free wheel check intentionally skips numerical tests.

## Untrained numerical probe

Use Torch 2.13.0, safetensors 0.7.0, packaging 25.0 and NumPy 2.3.5, as pinned in
`requirements-tensor-probe.txt`. Linux CPU Torch identifies as `2.13.0+cpu`;
macOS identifies as `2.13.0`. Capsules bind that runtime identity.

Install the optional probe only when needed. On Linux install the CPU wheel first:

```sh
uv pip install --python .venv/bin/python torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements-tensor-probe.txt
```

macOS arm64 wheels for this pin require macOS 14+. This does not change the
dependency-free core platform contract. The tensor CI job runs the full optional
suite and comparison on this pin; a core pass with 12 skips supplies no numerical
result. Earlier numerical observations below were authored with Torch 2.10.0,
not this newer pin. Fresh same-pin comparison is required before adopting them.

The dependency upgrade addresses the JIT advisory's published fixed version.
See [security](../SECURITY.md) for the separate unresolved pt2-loading caveat.
Old tensor capsules remain unchanged and are refused by the new exact Torch
descriptor. Start a fresh authored fixture under the new runtime; do not rewrite
old descriptors or reinterpret historical signatures as a migration.


```sh
PYTHONPATH=src uv run --no-project --python .venv/bin/python python -m unittest discover -s tests -v
uv run --no-project --python .venv/bin/python python -m latent_state_lab.tensor_probe
```

The protocol seeds weights at 123 and the sampler at 7, performs eight initial
steps (latent when the index modulo 3 equals 1), captures, and continues 12 steps
under the same schedule in four fresh processes.

| Arm | Expected comparison with uninterrupted execution |
| --- | --- |
| Restore KV/hidden/RNG/tape | Same final signature and 12/12 continuation outputs |
| Replay continuous input tape | Same final signature and 12/12 continuation outputs |
| Text-only restart | Different final signature when latent inputs were used |

The text-only control is deliberately lossy. Earlier pinned local runs matched
1/12 outputs; that is a fixture result, not a language-quality estimate. A pure-text
control requires exact signatures, preventing an arbitrary always-failing baseline.
The optional tensor tests also remove RNG, alter tensor shapes/dtypes/finite values,
change runtime pins, exceed step limits and refuse pending effects.

Signatures and integer outputs are stable comparison targets within a pinned
runtime. Error request IDs, ZIP timestamps and timings may vary. Host timings are
descriptive; no speed or memory superiority is claimed. A second clean installed
run must reproduce the comparison before a release verdict. Local evidence and
remote exact-head CI remain separate.

Real HybridAgent continuation, pretrained weights, GPU determinism, live tools,
language benchmarks, independent reproduction and human studies: **NOT RUN**.
An engine adapter needs separate review and approved model/hardware resources.
