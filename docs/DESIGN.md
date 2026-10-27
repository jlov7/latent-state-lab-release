# Design

The practical batch workload (`latent_state_lab.batch`) stores a single bounded
JSON chunk in the existing capsule. Its descriptor pins the batch runtime,
package version and Python implementation/version. The state binds immutable
input identity and SHA-256, configuration, completed row count, byte offset and
exact rational group totals. Lock paths open nonblocking and must identify regular
files before a POSIX advisory lock serializes cooperating writers;
atomic capsule replacement commits one completed run. Resume validates the
capsule/schema, full source digest and prefix row boundaries before aggregating
remaining rows. See [usage](USAGE.md) for limits and interruption semantics.
This workload uses no inference engine and does not establish latent continuity.

CSV and opt-in append-only feeds use `latent_state_lab.feed` with an explicit v2
descriptor and state. Default immutable JSONL retains its v1 descriptor/schema.
The v2 state adds a committed-prefix SHA-256, bounded parsed header and physical
line count alongside logical data-record progress. CSV readers retain one bounded
logical record, including quoted newlines; offsets refer to original bytes. BOM
handling affects only the first header parse, not byte identity. Resume hashes
the saved prefix without parsing or aggregating committed records. Append mode
allows changes after the committed offset between invocations; immutable CSV
requires the complete saved identity. Full snapshot scans before processing and
each commit retain the unchanged-source-during-run contract. Reports count those
integrity bytes separately from logical aggregation work. Inspection validates
either state without opening the source and never returns its header or groups.

The capsule is a ZIP_STORED container holding canonical UTF-8 JSON
`manifest.json` and raw byte chunks under `tensors/`. Reading never extracts
ZIP paths, imports supplied modules or loads pickle. SHA-256 verifies each
chunk's integrity; it does not authenticate the producer.

| Boundary | Limit |
| --- | --- |
| Serialized archive | 16 MiB |
| Manifest, writer and reader | 128 KiB, inclusive |
| Chunks | 1 to 32 |
| Each chunk | 1 byte to 4 MiB |
| Chunk name | 1 to 96 ASCII letters/digits/dot/underscore/hyphen, no leading dot |

The writer validates the descriptor and chunks, serializes the manifest once and
refuses an oversized manifest before touching the destination. It bounds the
completed archive including ZIP overhead before replacing or exclusively creating
the destination according to the caller's overwrite policy.

The reader opens paths nonblocking and requires a regular file on the opened
descriptor before parsing, including shared already-open reads. It then checks
file size, member count, duplicate ZIP entries, storage method,
encryption flag, member sizes, exact manifest fields, pinned descriptor, chunk
metadata, lengths, SHA-256 and the exact member set. It reads bounded members.
Duplicate JSON object keys are rejected at every nesting level. The container
format version must be the integer 1; booleans and floats are refused. Chunk byte
sizes also require integers, excluding booleans. These checks detect structural errors and
accidental corruption; a malicious producer can recompute hashes. Use an expected
descriptor you trust.

## Runtime state

The authored toy uses `toy-runtime-v2`. Its transcript is a bounded JSON list of
Unicode strings, preserving empty observations, newlines and the literal `EMPTY`.
Its decoder rejects malformed/nonstring data, lone surrogate escapes, count
mismatches and obsolete runtime pins. It derives a synthetic next-action hash.

The optional probe uses locally generated fixed random weights, float32 CPU
tensors and a dedicated seeded sampler RNG. Safetensors stores KV cache, hidden
state, RNG bytes and input tape. JSON metadata records position, mode history and
integer outputs. Loading validates names, shapes, dtypes, finite values and history
relationships. Sessions have a 256-step cap. The runtime descriptor includes the
installed Torch version; a different version is rejected.

Full restore retains state; replay reconstructs it from continuous inputs;
text-only restart discards latent inputs. The text control teacher-forces recorded
visible outputs, and pure-text tests require exact restoration. There is no tool
executor, network client or arbitrary generated-code execution.
