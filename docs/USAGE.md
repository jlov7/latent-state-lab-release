# Usage

V1 compatibility and error/report stability are defined in [STABILITY](STABILITY.md).
Every command accepts --help and --version without job arguments. Package version
1.1.0 retains existing artifact pins. Sync row/group limits apply to both the saved
collection and the incoming complete snapshot; a lowered bound refuses either
oversized collection without replacing committed state.

The core package runs on Python 3.11+ with no third-party runtime dependencies.
Commands run from the source archive after the README installation, using its
`.venv` without activation. A wheel can be installed with
`uv pip install --python .venv/bin/python path/to/package.whl`.

## Correction batches (0.6.0)

`latent-state-patch` accepts changed rows against the same private store used by
`latent-state-sync`. First import a snapshot or insert into an absent store.
The portable authored fixture `examples/correction_batch.py NEW_DIRECTORY` writes
an initial CSV, patch JSONL and independent complete corrected CSV. The README
shows installed commands. Patch input is always UTF-8 JSONL, even when
`--input-format csv` selects the existing store's snapshot configuration.

The frozen `latent-state-patch-v1` row schema has exactly three fields:

```json
{"id":"a","before":{"group":"CPU","value":"0.10"},"after":{"group":"GPU","value":"0.15"}}
{"id":"b","before":{"group":"CPU","value":"0.20"},"after":null}
{"id":"d","before":null,"after":{"group":"CPU","value":"0.25"}}
```

`id` is a unique nonempty string up to 1,024 UTF-8 bytes. Each side is null for
absence, or exactly `{group, value}`. A selected group is a string (empty allowed);
an unselected group is null. A selected value is a strict decimal **string**, using
the CSV decimal grammar and existing 1,000-digit/exponent bounds. JSON numbers,
fractions, booleans, nonfinite values, whitespace and extra/missing fields are
refused. An unselected value is null. Decimals compare by exact rational value,
so `"0.1"` equals `"0.100"`. Both sides null is invalid. Repeated IDs are invalid,
even if identical. Distinct rows are unordered simultaneous changes against one
committed state, never a sequential event stream. Empty input is a valid zero
change batch. Preview creates no files. Empty/no-op apply does not create an
absent store or replace an existing store; apply may create its private lock.

`--id`, `--input-format`, `--group-by` and `--value` must match the sync store
configuration. They describe selected snapshot fields, while patch keys remain
literally `id`, `group` and `value`. Defaults match sync. No schema migration or
snapshot/feed byte-semantics change is made.

For each ID, compare its committed selected row (or absence) with `after` first:
matching after is `already_present`; otherwise matching before is `applicable`;
otherwise it is `conflicts`. Equal present before/after is an already-present
no-op only when current state matches. Preview classifies every input row and
reports independent counts under `changes`. `can_apply` is false if any conflict
exists. Then `after` equals `before` and all group deltas are zero, because the
whole batch is refused. Applicable counts still describe those individual rows,
not a partial commit. Without conflicts, `after` is the resulting full collection
with exact group count/sum/mean and `deltas` are exact group count/sum differences.
`--details-limit N` adds the first N IDs in sorted order with status, current,
before and after as `[group, rational_value]` or null. It includes all statuses;
`details_omitted` counts every remaining batch row. Default N is zero (no detail
fields); maximum is 1,000. Group aggregates remain bounded by `--max-groups`.
Reports expose selected data, so keep them private.

Copy `review_sha256` from preview into `--apply --expect-review DIGEST`. The digest
binds patch-v1, exact patch bytes, prior committed state bytes (or absence) and
selected store configuration under the existing exclusive lock. Paths, details
and runtime bounds are not bound. Equivalent decimal spelling or state whitespace
changes require fresh review. This is an unsigned consistency check, not proof of
human approval or authentication. Unguarded `--apply` validates current files.
Any conflict raises `PatchConflict` before writing, with the complete report on
its `report` attribute. CLI conflict apply exits 2, prints that report to stdout
and a private structured `patch_failed` error with request ID to stderr. Preview
with conflicts exits 0. Other refusal errors exit 2 with the same stderr envelope.
`applied` is true only for an explicitly accepted apply; `store_written` says
whether state was replaced. A replay/no-op accepted apply leaves state mtime alone.

This is **after-value replay checking**, not global event-ID deduplication or
exactly-once processing. If later changes return an ID to its before value, the
same batch can apply again. Interruption after atomic replacement may commit
without delivering stdout; rerun without a stale review digest to classify current
values. A successful changed apply invalidates its old review digest. Repreview
and review current state when that guard refuses.

`--max-changes` defaults to 100,000. Resulting and prior states are bounded by
`--max-rows` (100,000), `--max-groups` (10,000), serialized state (16 MiB), input
(128 MiB), logical JSONL line (1 MiB), strings and rational bounds inherited from
sync. CLI bounds can only lower the record/group/line maxima. `--max-seconds`
defaults to 60, positive up to 86,400. Deadline checks are cooperative. A malformed
late row, conflict, bound failure or pre-commit interruption leaves committed state
unchanged. All source bytes are hashed twice; `changes_scanned`, `patch_bytes` and
`integrity_bytes_read` separate patch parsing from integrity I/O. Existing full
state is still read and changed state rewritten in full. This is no database,
sublinear-I/O or speed claim. A simple dictionary/SQL transaction may be preferable.

The same private directory, source/store/lock alias and symlink refusal, nonblocking
advisory lock, private atomic replacement, orphan `.sync-*` handling and lack of
fsync/power-loss/hostile-race guarantees apply as in snapshot sync below. Ancestor
directories must be trusted. Old sync/batch/store schemas and pins are unchanged.
The API is `latent_state_lab.patch.apply_patch(source, store, id_field="run_id",
input_format="csv", group_by="lab", value="cost", apply=False)`; remaining keywords
match CLI names with underscores. `PatchConflict` subclasses `BatchError`; other
refusals raise `BatchError` or `OSError`. `python -m latent_state_lab.patch` matches
the console command. This authored local workflow adds no human-usefulness,
production, real-model or scientific qualification.

## Full-snapshot reconciliation (0.5.0)

```sh
.venv/bin/latent-state-sync billing.csv billing.state --input-format csv --id run_id --group-by lab --value cost --details-limit 20
.venv/bin/latent-state-sync billing.csv billing.state --input-format csv --id run_id --group-by lab --value cost --apply --expect-review DIGEST
# Replace billing.csv with the complete corrected export, then repeat preview/apply.
```

`--id` is required. IDs must be unique nonempty strings of at most 1,024 UTF-8
bytes. JSONL is the default; numeric JSON IDs are refused. CSV uses the same strict
dialect and decimal syntax documented below, including multiline quoted fields,
Unicode and an initial BOM. JSONL numeric values must be JSON numbers. All chosen
fields must exist. Group keys are strings (empty is allowed). Values are parsed
exactly; non-finite values and literals over 1,000 digits/exponent magnitude are
refused. Without `--value`, reports count; without `--group-by`, they have one
group with `key: null`. The optional fields may also select the ID column for
string grouping. A header-only CSV or empty JSONL is an empty collection.

This is a complete replacement snapshot, not an incremental patch. Missing IDs
are deletions. An existing ID is updated when its selected group or exact numeric
value changes. Row order, unselected fields and equivalent decimals (1.0 and
1.00) do not count as changes. ID changes count as a deletion plus an addition.
Input path may change across invocations; format and selected field configuration
must match the existing store. There is no overwrite/migration flag. Choose a
different store for a different job. The older batch commands and v1/v2 capsules
keep their existing semantics and pins.

Version 1.1.0 adds optional `--max-deletions N`, an integer from 0 to 100,000.
Zero permits additions and updates but refuses any deletion. A positive ceiling
allows up to N deleted IDs; an empty export proposes deletion of every saved ID.
Initial imports have zero deletions. Omitting the option preserves the existing
unrestricted deletion behavior and report shape. The ceiling is per invocation,
not saved store configuration, and does not change the snapshot format or pins.

A guarded preview exits 0 even when its proposed deletions exceed the ceiling.
Its additional `deletion_guard` object contains `maximum`, `proposed` and `passed`.
The `after` totals, deltas and details still describe the proposed replacement.
If apply would exceed the ceiling, the entire apply is refused with `applied`
and `store_written` false. CLI exit 2 retains that complete report on stdout and
the structured `sync_failed` error with request ID on stderr. Committed state
bytes and modification time remain unchanged; apply may create its private lock.

Use the same ceiling in preview and apply:

```sh
.venv/bin/latent-state-sync billing.csv billing.state --input-format csv --id run_id --group-by lab --value cost --max-deletions 1 --details-limit 20
.venv/bin/latent-state-sync billing.csv billing.state --input-format csv --id run_id --group-by lab --value cost --max-deletions 1 --apply --expect-review DIGEST
```

Replace `DIGEST` with that preview's `review_sha256`. A guarded digest also binds
the ceiling. Changing, adding or removing it requires a new preview and review;
unguarded digests retain their historical values. Other runtime bounds and detail
limits remain outside the digest. The ceiling does not infer whether an omitted
ID was intended to be deleted, and it permits every deletion within its limit.
Use a correction batch when the input only describes changed rows.

Preview is the default and creates no file or lock. Reports include a
`review_sha256` digest. In version 0.5.1, copy that digest from the reviewed preview
into `--apply --expect-review DIGEST` to require the same exact source bytes,
committed state bytes (or absent state) and selected field configuration. A stale
or incorrect digest refuses the apply without changing committed state. The guard
is checked while holding the same cooperating-writer lock used for commit. A new
store created after an initial preview also invalidates its digest. Repreview after
a refusal and review the new differences before applying. A successful changed
apply invalidates the old digest because the prior state has changed.

The guard binds exact bytes: reordered rows, equivalent decimal spellings and
unselected-field edits require a fresh preview even when selected values match.
Input paths may change if the bytes, configuration and optional ceiling are identical.
Detail limits and other runtime bounds are not part of the digest. The digest is
an unsigned consistency check, not proof of who reviewed the report or what they
approved. It does not authenticate state or protect against noncooperating writers. `--expect-review`
requires `--apply` and a lowercase 64-character SHA-256 hex digest.

For scripted imports, `--apply` without `--expect-review` retains its existing
semantics: it validates and commits the current files, which may differ from an
earlier preview. Reports have `changes` (added, updated, deleted, unchanged counts),
`before` and `after` totals and sorted `deltas` with each group's count and exact
sum difference, including disappeared groups. Means and sums are rational
strings; there is no mean-difference field. `records_scanned` counts the full
incoming collection. `integrity_bytes_read` counts the two full hash passes,
separately from parsing I/O. `snapshot_sha256` identifies the bytes validated.
`applied` declares explicit commit authority; `store_written` is false for
preview or an unchanged existing collection. An unchanged apply leaves state
bytes and modification time alone.

`--details-limit N` (0 default, at most 1,000) adds sorted changed IDs with
`before`/`after` as `[group, exact_value]` or null for absence. For count-only jobs,
the exact value is null. `details_omitted` counts further changed records.
Reports intentionally contain group values and, when requested, IDs/selected
values. Store/report files are sensitive. Errors never echo row contents, field
names or paths: exit 2 returns JSON `code`, `message`, `request_id` and optional
one-based physical `row`/zero-based row-start `byte_offset`. Argparse syntax
failures retain its text usage. No telemetry or external effects occur.

Bounds: 100,000 rows (`--max-rows`), 10,000 groups (`--max-groups`), 1 MiB per
logical record (`--max-line-bytes`, including delimiters), 128 MiB per input and
16 MiB of serialized selected state. Row/group/line options can lower these
limits per invocation; they do not change saved configuration. Strings use the
1,024-byte bound, CSV fields the existing 128 KiB bound and exact saved fractions
the existing 4,096-character bound. A run defaults to a 60-second deadline
(`--max-seconds`, positive and at most 86,400). Deadline checks occur between
records, hash blocks and stages; they cannot interrupt a blocking filesystem
operation, bounded parse/serialization/sort or atomic replacement. Memory holds
bounded before/after selected collections. This workflow reads the entire input
and existing state, hashes input before/after and rewrites the whole changed
state. It is not resumable within a snapshot and makes no sublinear-I/O claim.

Keep inputs and stores in trusted local directories. The existing store directory
must be owned by the caller and not writable by group/others. State and lock files
must be caller-owned regular files, have no group/other permissions and no hard
links. New files are mode 0600 (or more restrictive under umask). Final-component
symlinks for input/state/lock and path aliases are refused. Directory ancestors
must be trusted; this is not isolation against hostile filesystem races.

The store is a separate versioned JSON format containing stable IDs and selected
group/value fields. Foreign, incompatible and malformed stores are refused
without overwrite. Stores are not authenticated; a person able to rewrite a
valid store can forge its contents. No arbitrary code or SQL is loaded from it.
Apply takes an exclusive nonblocking advisory lock, validates the full incoming
snapshot, checks input identity/digest again and writes private temporary state.
Initial creation and updates atomically replace the destination under the same
exclusive cooperating-writer lock, after a final check that the saved state has
not changed. Preexisting foreign state is refused. A noncooperating process racing
the destination after that check is outside this trusted-directory contract.
Duplicate IDs, malformed
late records, limits and pre-commit interruption preserve committed logical state.
An interruption after replacement may commit even if stdout was never received;
rerunning the same snapshot reports unchanged records, including initial creation.
An unchanged repeat can use `--max-deletions 0`. Omit the old review digest or
preview again, since a changed apply invalidates the digest for its prior state.
Preview can observe either whole state during a concurrent apply. An already-open
private regular store descriptor remains readable after replacement unlinks its
old inode; stores with multiple hard links and nonprivate files are still refused.
Cooperating writers serialize; concurrent
external editors are unsupported. No power-loss or network-filesystem guarantee
is claimed. SIGKILL may leave `.sync-*` temporary files. Remove such orphans only
after all writers stop; the persistent empty `.lock` file is normal.

The Python entrypoint is `latent_state_lab.sync.reconcile(source, store,
id_field="run_id", input_format="csv", group_by="lab", value="cost", apply=False,
max_deletions=None)`; remaining keyword names match CLI names with underscores.
It raises `BatchError` or `OSError` for refused operations.
`SnapshotDeletionLimit` subclasses `BatchError`
and exposes the complete proposed report on its `report` attribute. The API accepts
only an integer ceiling in the stated range or `None`; booleans are refused.
`python -m latent_state_lab.sync` matches the console command. Prefer a restart
script if you only need a small export's totals; prefer direct SQLite for an
existing SQL workflow, larger stores, rich
queries or row-level changes. This command packages bounded preview and atomic
replacement for full file exports, with no performance superiority claim.

## Recurring CSV and JSONL feeds (0.4.0)

```sh
.venv/bin/latent-state-batch usage.csv usage.zip --input-format csv --append-only --group-by meter --value usage
# Append records to usage.csv while no invocation is running, then:
.venv/bin/latent-state-batch usage.csv usage.zip --input-format csv --append-only --group-by meter --value usage --resume
.venv/bin/latent-state-inspect usage.zip
```

CSV is read directly, with no conversion step. The first logical record is a
header with unique nonempty names; configured columns must exist. Every data
record must have the same number of columns. The fixed dialect uses commas,
double quotes and doubled quote escapes, with Python's `csv.reader(strict=True)`
plus refusal of quotes in unquoted fields. Quoted commas and embedded LF/CRLF,
UTF-8 Unicode and LF/CRLF record endings are supported. Bare CR record endings,
blank records, invalid UTF-8 and malformed quoting are refused. This is a defined
stdlib-based dialect, not a universal CSV or RFC-conformance claim. An optional
UTF-8 BOM is stripped only at byte zero for header parsing. All original bytes,
including that BOM and both bytes of CRLF, remain in offsets and integrity hashes.

Group columns are strings. Selected values use strict decimal number syntax
(for example `-1.25`, `0`, `1e-3`), parsed exactly without floats. Blank values,
leading/trailing whitespace, leading plus signs, leading zeroes, thousands
separators and non-finite numbers are refused. The existing 1,000-digit/exponent
bounds apply. Limits on groups, keys, checkpoint size and exact rational output
are shared with immutable JSONL. `--max-line-bytes` bounds an entire logical CSV
record including all its physical lines and delimiter, at most 1 MiB. Each CSV
field additionally has a 128 KiB UTF-8 bound. There is no dialect or schema inference.

In Python, pass `input_format="csv", append_only=True` to `aggregate(...)` with
the same remaining settings shown below. `--input-format csv` without
`--append-only` requires an immutable CSV snapshot. `--append-only` with the
default JSONL format uses the same prefix contract. These modes use a separate
v2 state and runtime descriptor. They cannot resume or convert a v1 checkpoint.
The default immutable JSONL v1 state/runtime remains compatible on the same Python
implementation and full version; existing commands keep their behavior.

### Committed-prefix contract

The input and checkpoint must be private regular files on a trusted local POSIX
filesystem. Resume requires identical format, append flag, fields and bounds,
plus the same resolved path, device and inode. It reads and hashes the exact saved
byte prefix, verifies physical-line progress and refuses any mismatch or truncation
below the saved offset. Someone able to rewrite a valid capsule can forge saved
state and hashes. This is corruption detection, not producer authentication.

Between invocations, append-only mode permits new data or repair of the uncommitted
suffix. That suffix can become shorter, provided the committed prefix is unchanged.
The source must remain unchanged during each invocation: the full snapshot is
hashed at startup and again before each commit, including size/mtime/inode checks.
Changes during a run fail and leave its last successful cadence commit available.
These checks are not isolation from a process actively racing writes. No live
tailing, daemon, concurrent source writer or automatic repair is supported.

Every committed append-only record must end in LF (including CRLF). An unfinished
record fails with a private error; a prior cadence checkpoint remains available.
After completing or correcting the uncommitted tail, resume repeats only work after
the saved offset. An immutable CSV may have an unterminated final record. An empty
CSV is refused because it has no header; a header-only CSV has zero logical data
records and can be inspected and later appended. An empty append-only JSONL is valid.

Successful v2 reports include `state_version: 2`, `physical_lines` and
`integrity_bytes_read`. `records_total`, `records_this_run` and `--max-records`
count logical data records, excluding the CSV header. Physical lines include the
header and quoted newlines. Errors retain `row` as the one-based physical start
line and `byte_offset` as the zero-based first byte of the failing logical record,
including all committed progress. Raw values and paths are not echoed.

`complete` means the current snapshot reached EOF; another invocation may consume
an appended suffix. Repeating resume without new data returns zero new records.
`integrity_bytes_read` counts source bytes read for the startup full scan, committed
prefix verification on resume, and each full commit scan. It excludes record parsing
and capsule I/O. It can exceed the source size several times, especially with a
short cadence. Avoiding repeated aggregation does not imply reduced disk I/O or
faster completion. A small one-shot restart script remains a valid simpler choice.

Periodic commits, deadlines, output-only decimal formatting, exclusive checkpoint
creation, locks and interruption boundaries below apply to v2 too. Inspection reads
only the checkpoint and omits the saved header, group values and path identity;
v2 adds state version and physical progress. It always marks source validation
unverified. No empirical human benefit or real latent-agent restoration is established.

## Immutable JSONL batch

The batch CLI requires macOS or Linux (POSIX `flock`, atomic rename and hard links
on a trusted local filesystem). Each input line is a UTF-8 JSON object. A final
line without a newline is accepted; blank lines, a BOM, duplicate object fields,
invalid Unicode and non-finite numbers are rejected. Escaped newlines in strings,
Unicode keys and CRLF delimiters are supported. Fields are top-level field names,
not expressions or executable code.

```sh
.venv/bin/latent-state-batch orders.jsonl counts.zip --max-records 10000
.venv/bin/latent-state-batch orders.jsonl counts.zip --resume
.venv/bin/latent-state-batch orders.jsonl departments.zip --group-by department --value amount --max-records 10000
.venv/bin/latent-state-batch orders.jsonl departments.zip --group-by department --value amount --resume
```

The first job counts all rows. The second job counts rows per string-valued
`department` and computes exact sum and mean of `amount`. Every selected field
must exist; numeric values exclude booleans, null and strings. Numbers are parsed
from their JSON decimal representation without binary floating-point conversion.
Sum and mean use reduced rational strings (`"1"`, `"-5/4"`, `"0"`). Groups are
sorted by Unicode string key. Without grouping, the one result has `key: null`.
An empty file has zero records and an empty groups array.

Successful runs return 0 and one JSON report. `records_this_run` counts newly
aggregated rows, `records_total` counts all committed rows, `offset` is the next
byte offset, and `complete` means EOF was reached. `--max-records` limits only
newly aggregated rows in this process. It is not a time limit or a limit on
integrity I/O. Repeating resume at EOF returns the same results and zero new rows.
Input/checkpoint failures return 2 with JSON `code`, `message`, `request_id` on
stderr. Argument syntax failures use argparse text. No telemetry is sent.
Row parsing, missing/invalid selected fields and row/group bounds add `row`
(one-based physical line) and `byte_offset` (zero-based first byte of that row).
Offsets count the original UTF-8 bytes and both bytes of CRLF; a final
unterminated row is one physical row. Locations continue from saved progress
after resume. Messages never echo raw row values, selected field names or paths.

Default/maximum limits: 10,000 groups (`--max-groups`), 1 MiB per line including
its delimiter (`--max-line-bytes`), 1,024 UTF-8 bytes per group key, 1,000 digits
per numeric literal with decimal exponent magnitude at most 1,000, and 4 MiB of
serialized aggregate state. Lower group/line bounds can be chosen when starting
a job and become part of its configuration. The parser and aggregation retain
one bounded row plus bounded groups, not the complete input. Nested JSON that
exceeds Python's parser depth is refused. Inputs need to be regular files.

### Python API and process handover

```python
from pathlib import Path
from latent_state_lab.batch import aggregate

report = aggregate(Path("orders.jsonl"), Path("orders.zip"),
                   group_by="department", value="amount", max_records=10000)
# A different process, using the same installed version/Python and unchanged source:
report = aggregate(Path("orders.jsonl"), Path("orders.zip"),
                   group_by="department", value="amount", resume=True)
```

`BatchError` is a `ValueError`; filesystem errors remain `OSError` subclasses.
Retain the source at the same resolved path with the same device/inode, size,
modification time and SHA-256. Hand the next process the checkpoint path, source
path and identical field/group/line settings. The Python implementation and full
Python version and batch state runtime are pinned. Packages 0.3.0 and 0.4.0 retain
the default immutable JSONL 0.2.0 batch descriptor and schema, so existing checkpoints on the same
Python runtime remain compatible. Moving or
copying the source requires a new job, even for identical bytes. The checkpoint
can be handed to another process on the same host; cross-host migration and
automatic version migrations are unsupported. No raw rows are saved, but group
keys, totals, counts and input identity are sensitive local data.

In this default immutable JSONL mode, appending, truncating, replacing or changing a source,
or changing configuration, rejects resume before committing. Each run hashes all
source bytes before and after aggregation. Resume additionally counts delimiters
up to the saved offset to validate progress; it does not parse or aggregate those
rows. This costs full-file integrity reads and does not establish speed superiority.
The caller must keep the input immutable during the run. These checks are not
an isolation boundary against a process actively racing file modifications.

### Commit and interruption boundaries

By default one run makes one checkpoint commit after all selected rows and integrity checks
pass. No partial progress is committed on malformed rows, bounds failures or I/O
errors before replacement. Existing jobs require `--resume`; there is no batch
overwrite flag. Checkpoint updates use the existing safe bounded capsule format,
an exclusive advisory lock, a private temporary archive and atomic replacement.
The checkpoint's parent directory must already exist. Existing checkpoints must
be regular files. FIFOs and other special files are
refused before capsule loading. A persistent empty
`<checkpoint>.lock` file is normal; the OS releases its lock when a process dies.
Do not delete it while any job may be running.

After interruption, resume from the last successful bounded run. Uncommitted rows
are processed again. If termination occurs after atomic replacement but before
the stdout report, the new checkpoint is already authoritative. Aggregate state
counts committed rows once; this is not an exactly-once transaction for external
effects or redirected stdout files. The batch command only writes its checkpoint
and lock and emits a report. It does not create a separate output transaction.
No fsync/power-loss guarantee or network-filesystem locking guarantee is claimed.
A SIGKILL during serialization can leave a private `.tmp-capsule-*` file in the
checkpoint directory; remove such an orphan only after the job has stopped.

Capsule hashes detect accidental corruption. They do not authenticate who wrote
the state: someone able to rewrite the checkpoint and hashes can forge totals.
Use trusted private directories. Never treat a checkpoint as proof of data
provenance, a signed result or a safe container for arbitrary executable objects.


### Optional periodic commits

```sh
.venv/bin/latent-state-batch orders.jsonl periodic.zip --group-by department --value amount --checkpoint-every 1000 --max-records 50000 --max-seconds 60
.venv/bin/latent-state-batch orders.jsonl periodic.zip --group-by department --value amount --resume --checkpoint-every 1000 --max-records 50000 --max-seconds 60
```

`checkpoint_every` / `--checkpoint-every` must be a positive integer. This opt-in
mode requires positive `max_records` and finite `max_seconds` / `--max-seconds`
at most 86,400. These are per-invocation limits, independent of saved configuration.
The deadline starts before initial integrity I/O and is checked between rows,
integrity blocks and commits. It cannot preempt a blocking filesystem operation,
one bounded row parse or atomic replacement. Reaching it returns error 2 and
retains the last successful commit; it does not commit a partial cadence.

Every cadence of newly processed rows commits under the same exclusive lock,
after a fresh full-source integrity scan. A successful EOF/record-limit boundary
also commits a final partial cadence. The report's `periodic` object declares
the cadence, maximum records/seconds and commits made. `records_this_run` always
counts all newly aggregated rows in that process once. A bad later row preserves
the last cadence boundary. Resume repeats only uncommitted work. Each commit
adds a full-file integrity scan, so choose cadence with that I/O cost in mind.

### Read-only checkpoint inspection

```sh
.venv/bin/latent-state-inspect periodic.zip
.venv/bin/python -m latent_state_lab.inspect periodic.zip
```

`latent_state_lab.batch.inspect_checkpoint(path)` is the matching Python API.
Inspection opens only the checkpoint, validates capsule hashes, the pinned
descriptor and saved batch schema, and returns progress, configuration, group
count, saved source byte size and SHA-256. It takes no lock and writes nothing.
The source may be absent. `source_validation: "unverified"` always applies:
inspection does not establish current-source identity or actual row boundaries.
Path hashes, device/inode identity, group keys and totals are omitted. Configured
field names are limited to 256 characters each with an explicit truncation mark.
Special files, symlinks and corrupt/incompatible capsules are refused with exit
2 and a private JSON `inspect_failed` error. Concurrent replacement may yield
either complete valid checkpoint; no current-writer freshness guarantee is made.

### Optional decimal presentation

`decimal_places=2` / `--decimal-places 2` adds `sum_decimal` and `mean_decimal`
strings beside the exact rationals and declares `decimal_display` with `places`
and `rounding: "half-even"`. Precision means fixed decimal places, 0 through 18.
Rounding is nearest, with ties to the even last retained digit: -1.225 becomes
-1.22 and -1.235 becomes -1.24 at two places. Rounded zero has no minus sign.
Integer quotient/remainder arithmetic avoids decimal-context or float limits,
including the supported extreme input exponents. Counting without `--value`
has no sums or means to display. Display choices can change on resume without
changing configuration, saved rationals, chunk bytes or state digests.

## Toy commands

```sh
.venv/bin/latent-state-lab create toy.zip
.venv/bin/latent-state-lab resume toy.zip
.venv/bin/latent-state-lab uninterrupted
.venv/bin/latent-state-lab create toy.zip --overwrite
```

`create` captures after two completed authored observations. `resume` validates
the descriptor and chunk hashes, restores the toy and advances the third
observation. Compare its `step` and `action` with `uninterrupted`.
`uninterrupted` runs the fixed authored observations from scratch and accepts no
capsule path. It does not inspect or compare a supplied capsule. Custom state and
observations use the Python API below.
Success writes one JSON object to stdout and returns 0. Missing files, invalid
capsules, permission failures and refused replacement return 2 with a JSON error
on stderr. Its random request ID identifies an error occurrence, with no telemetry.
Argument syntax errors use argparse's usage text and return 2.

Creation uses an atomic exclusive filesystem link to refuse any existing
destination, including one created during capture. It refuses existing symlinks.
`--overwrite` atomically replaces the destination directory entry only after
serialization succeeds. Local filesystems must support hard links for exclusive
creation. Temporary files are removed on failure. The writer creates missing
parent directories and exports with mode 0600 on POSIX.

## Python API

```python
from pathlib import Path
from latent_state_lab.capsule import read_capsule, write_capsule
from latent_state_lab.fixture import PIN, ToySession

path = Path("toy-api.zip")
session = ToySession.start().advance("observation")
write_capsule(path, PIN, session.chunks(), overwrite=False)
descriptor, chunks = read_capsule(path, PIN)
assert ToySession.from_chunks(chunks) == session
```

The API retains `overwrite=True` by default for existing callers. Choose
`overwrite=False` for exclusive creation. `CapsuleError` is a `ValueError`
for invalid or incompatible content. Filesystem errors remain `OSError`
subclasses. The caller supplies the expected pinned descriptor, including model,
tokenizer, runtime, dtype, device, capture point and completed-tool flag.
There is no automatic migration of old runtime versions.

## Numerical comparison

After installing `requirements-tensor-probe.txt`, run
`python -m latent_state_lab.tensor_probe`. The module creates a temporary capsule
and launches each comparison arm in a fresh process, with a 30-second timeout per
subprocess. It removes temporary files afterward. The `prepare_ns` fields are
local timings, not performance claims.
