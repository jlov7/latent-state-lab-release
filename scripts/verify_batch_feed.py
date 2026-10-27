"""Check actual CSV feed commands against an independent exact restart baseline.

Use --source or supply an installed bin directory. All inputs are authored local data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from math import gcd
from pathlib import Path


def ratio(numerator, denominator):
    divisor = gcd(numerator, denominator)
    numerator, denominator = numerator // divisor, denominator // divisor
    return str(numerator) if denominator == 1 else f'{numerator}/{denominator}'


BASELINE = '''import csv, json, sys
from decimal import Decimal
from math import gcd
def ratio(n, d):
    factor = gcd(n, d)
    n, d = n // factor, d // factor
    return str(n) if d == 1 else str(n) + "/" + str(d)
totals = {}
with open(sys.argv[1], encoding="utf-8-sig", newline="") as stream:
    for row in csv.DictReader(stream):
        count, total = totals.get(row["meter"], (0, 0))
        totals[row["meter"]] = count + 1, total + int(Decimal(row["usage"]) * 1000)
print(json.dumps([{"key": k, "count": c, "sum": ratio(t, 1000),
                   "mean": ratio(t, 1000*c)} for k, (c, t) in sorted(totals.items())]))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin', nargs='?', type=Path)
    parser.add_argument('--source', action='store_true')
    parser.add_argument('--keep-work', type=Path)
    args = parser.parse_args()
    env = dict(os.environ)
    if args.source:
        batch = [sys.executable, '-m', 'latent_state_lab.batch']
        inspect = [sys.executable, '-m', 'latent_state_lab.inspect']
        python = sys.executable
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    else:
        if args.bin is None:
            parser.error('supply an installed bin directory or --source')
        batch = [str(args.bin.resolve() / 'latent-state-batch')]
        inspect = [str(args.bin.resolve() / 'latent-state-inspect')]
        python = str(args.bin.resolve() / 'python')
        env.pop('PYTHONPATH', None)
    if args.keep_work:
        args.keep_work.mkdir(parents=True, exist_ok=False)
    context = (nullcontext(str(args.keep_work.resolve())) if args.keep_work else
               tempfile.TemporaryDirectory(prefix='feed-verification-', dir=Path.cwd()))
    with context as tmp:
        root = Path(tmp)
        source, checkpoint = root / 'meters.csv', root / 'feed.zip'
        baseline = root / 'restart_baseline.py'
        baseline.write_text(BASELINE)
        commands, stages, totals = [], [], {}

        def run(argv, code=0):
            started = time.perf_counter()
            result = subprocess.run(list(map(str, argv)), cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30, check=False)
            commands.append({'argv': list(map(str, argv)), 'exit': result.returncode,
                             'elapsed_seconds': time.perf_counter() - started,
                             'stdout': result.stdout, 'stderr': result.stderr})
            (root / 'COMMANDS.json').write_text(json.dumps(commands, indent=2))
            assert result.returncode == code, (result.returncode, result.stderr)
            return json.loads(result.stdout if code == 0 else result.stderr)

        def command(path, *extra, input_path=source):
            return [*batch, str(input_path), str(path), '--input-format', 'csv', '--append-only',
                    '--group-by', 'meter', '--value', 'usage', *map(str, extra)]

        def append(start, count, initial=False):
            with source.open('w' if initial else 'a', encoding='utf-8-sig' if initial else 'utf-8',
                             newline='') as stream:
                writer = csv.writer(stream)
                if initial:
                    writer.writerow(['meter', 'usage', 'note'])
                for index in range(start, start + count):
                    units = (index * 1877 % 70001) - 35000
                    key = ['north,one', '雪', 'café', 'Ω', 'west'][index % 5]
                    number = ('-' if units < 0 else '') + f'{abs(units)//1000}.{abs(units)%1000:03d}'
                    writer.writerow([key, number, ('quoted " note\n' if index % 17 == 0 else '') +
                                     'x' * 4096])
                    old_count, old_total = totals.get(key, (0, 0))
                    totals[key] = old_count + 1, old_total + units

        def expected():
            return [{'key': key, 'count': count, 'sum': ratio(total, 1000),
                     'mean': ratio(total, count * 1000)}
                    for key, (count, total) in sorted(totals.items())]

        append(0, 1291, initial=True)
        for stage in range(4):
            if stage:
                append(1291 + (stage - 1) * 113, 113)
            oracle = expected()
            reference = run([python, baseline, source])
            report = run(command(checkpoint, *(['--resume'] if stage else [])))
            assert report['groups'] == reference == oracle
            assert report['records_this_run'] == (113 if stage else 1291)
            assert report['records_total'] == 1291 + stage * 113
            assert report['complete']
            stages.append({'stage': stage, 'source_bytes': source.stat().st_size,
                           'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                           'expected': oracle, 'candidate': report,
                           'baseline_records_aggregated': 1291 + stage * 113})
        assert run(command(checkpoint, '--resume'))['records_this_run'] == 0

        # Kill the actual console process after observing a committed cadence.
        killed = root / 'killed.zip'
        argv = command(killed, '--checkpoint-every', 71, '--max-records', 2000, '--max-seconds', 30)
        child = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not killed.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.001)
            assert killed.exists() and child.poll() is None, 'live cadence boundary was not observed'
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            assert child.returncode == -signal.SIGKILL
            commands.append({'argv': argv, 'exit': child.returncode,
                             'stdout': stdout.decode(), 'stderr': stderr.decode(),
                             'termination': 'SIGKILL after observing checkpoint creation'})
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        progress = run([*inspect, str(killed)])
        boundary = progress['records_total']
        assert 71 <= boundary < 1630 and boundary % 71 == 0
        recovered = run(command(killed, '--resume'))
        assert recovered['groups'] == expected()
        assert recovered['records_this_run'] == 1630 - boundary

        # Stop after serialization but before the real atomic replacement.
        atomic = root / 'atomic.zip'
        run(command(atomic, '--max-records', 73))
        prior = atomic.read_bytes()
        marker = root / 'before-replace'
        helper = '''import os, runpy, signal, sys
from pathlib import Path
import latent_state_lab.capsule as capsule
original = capsule.os.replace
marker = Path(sys.argv[1])
def paused(source, destination):
    marker.write_text("serialized-before-replace")
    os.kill(os.getpid(), signal.SIGSTOP)
    original(source, destination)
capsule.os.replace = paused
sys.argv = ["latent-state-batch", *sys.argv[2:]]
runpy.run_module("latent_state_lab.batch", run_name="__main__")
'''
        argv = [python, '-c', helper, str(marker), *command(atomic, '--resume')[len(batch):]]
        child = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.002)
            assert marker.exists(), 'atomic boundary was not reached'
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            assert child.returncode == -signal.SIGKILL and atomic.read_bytes() == prior
            commands.append({'argv': argv, 'exit': child.returncode,
                             'stdout': stdout.decode(), 'stderr': stderr.decode(),
                             'termination': 'SIGKILL after serialization before replace'})
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        assert run(command(atomic, '--resume'))['groups'] == expected()

        # Cadence survives a private malformed suffix, which can then be repaired.
        bad = root / 'private-bad.csv'
        prefix = b'meter,usage,note\r\n"a\nb",0.1,x\r\na,0.2,x\r\n'
        bad.write_bytes(prefix + b'private,NaN,"unfinished\r\n')
        bad_checkpoint = root / 'bad.zip'
        error = run(command(bad_checkpoint, '--checkpoint-every', 2, '--max-records', 10,
                            '--max-seconds', 10, input_path=bad), code=2)
        assert (error['row'], error['byte_offset']) == (5, len(prefix))
        assert 'private' not in json.dumps(error) and 'NaN' not in json.dumps(error)
        assert run([*inspect, str(bad_checkpoint)])['records_total'] == 2
        bad.write_bytes(prefix + b'b,0.3,fixed\r\n')
        repaired = run(command(bad_checkpoint, '--resume', input_path=bad))
        assert (repaired['records_total'], repaired['records_this_run']) == (3, 1)
        prior = bad_checkpoint.read_bytes()
        bad.write_bytes(bad.read_bytes().replace(b'0.1', b'0.9'))
        run(command(bad_checkpoint, '--resume', input_path=bad), code=2)
        assert bad_checkpoint.read_bytes() == prior

        # Source-independent inspection writes neither a lock nor any other entry.
        Path(str(checkpoint) + '.lock').unlink()
        held = source.with_suffix('.held')
        source.rename(held)
        before = checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns, set(root.iterdir())
        # run() writes the pre-existing command log, so compare all directory entries
        # plus the checkpoint bytes/mtime, rather than claiming all logs are immutable.
        inspection = run([*inspect, str(checkpoint)])
        assert inspection['source_validation'] == 'unverified'
        assert 'path' not in json.dumps(inspection) and 'groups' not in inspection
        assert before == (checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns, set(root.iterdir()))
        held.rename(source)
        result = {'status': 'pass', 'mode': 'source' if args.source else 'installed',
                  'provenance': 'authored synthetic CSV; agent-operated, no human/model/provider evidence',
                  'stages': stages, 'sigkill_committed_rows': boundary,
                  'sigkill_resumed_rows': recovered['records_this_run'],
                  'atomic_replace_sigkill_preserved_checkpoint': True,
                  'malformed_suffix_repaired_after_committed_prefix': True,
                  'inspection_source_independent_and_checkpoint_unchanged': True,
                  'baseline_nonblank_lines': sum(bool(line.strip()) for line in BASELINE.splitlines()),
                  'timing_qualification': 'local observations only; inspect COMMANDS.json, no speed claim'}
        (root / 'RESULT.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
