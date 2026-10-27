"""Exercise additive batch features with fresh synthetic ledger data and an integer oracle.

Run with --source, or supply the installed bin directory. No optional dependencies/network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from decimal import ROUND_HALF_EVEN, Decimal
from math import gcd
from pathlib import Path


def ratio(numerator, denominator):
    divisor = gcd(numerator, denominator)
    numerator, denominator = numerator // divisor, denominator // divisor
    return str(numerator) if denominator == 1 else f'{numerator}/{denominator}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin', nargs='?', type=Path)
    parser.add_argument('--source', action='store_true')
    args = parser.parse_args()
    env = dict(os.environ)
    if args.source:
        batch = [sys.executable, '-m', 'latent_state_lab.batch']
        inspect = [sys.executable, '-m', 'latent_state_lab.inspect']
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    else:
        if args.bin is None:
            parser.error('supply an installed bin directory or --source')
        batch, inspect = [str(args.bin / 'latent-state-batch')], [str(args.bin / 'latent-state-inspect')]
        env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='additive-batch-', dir=Path.cwd()) as tmp:
        root = Path(tmp)
        source = root / 'settlements.jsonl'
        totals, lines = {}, []
        for i in range(4099):
            units = (i * 3571 % 90011) - 45005
            key = ['north', '南', 'café', 'east', 'Ω', 'west', '雪'][i % 7]
            count, total = totals.get(key, (0, 0))
            totals[key] = count + 1, total + units
            number = ('-' if units < 0 else '') + f'{abs(units) // 100}.{abs(units) % 100:02d}'
            line = json.dumps({'region': key, 'net': 'NUMBER', 'note': 'x' * 2048},
                              ensure_ascii=False).replace('"NUMBER"', number)
            lines.append(line.encode() + (b'\r\n' if i % 3 == 0 else b'\n'))
        source.write_bytes(b''.join(lines))
        expected = [{'key': key, 'count': count, 'sum': ratio(total, 100),
                     'mean': ratio(total, 100 * count)}
                    for key, (count, total) in sorted(totals.items())]

        def command(checkpoint, *extra, input_path=source):
            return [*batch, str(input_path), str(checkpoint), '--group-by', 'region',
                    '--value', 'net', *map(str, extra)]

        def run(command, code=0):
            result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    text=True, timeout=30, check=False)
            assert result.returncode == code, (result.returncode, result.stderr)
            return json.loads(result.stdout if code == 0 else result.stderr)

        full = run(command(root / 'full.zip'))
        assert full['groups'] == expected
        checkpoint = root / 'periodic.zip'
        partial = run(command(checkpoint, '--checkpoint-every', 137, '--max-records', 967,
                              '--max-seconds', 30, '--decimal-places', 4))
        assert partial['records_total'] == 967 and partial['periodic']['commits_this_run'] == 8
        resumed = run(command(checkpoint, '--resume', '--checkpoint-every', 211,
                              '--max-records', 5000, '--max-seconds', 30, '--decimal-places', 4))
        assert resumed['records_this_run'] == 4099 - 967
        exact = [{k: v for k, v in row.items() if not k.endswith('_decimal')}
                 for row in resumed['groups']]
        assert exact == expected
        for row in resumed['groups']:
            count, cents = totals[row['key']]
            for name, denominator in [('sum', 100), ('mean', count * 100)]:
                decimal = (Decimal(cents) / Decimal(denominator)).quantize(
                    Decimal('0.0001'), rounding=ROUND_HALF_EVEN)
                assert row[name + '_decimal'] == format(decimal, '.4f')

        # Actual console process: observe a durable periodic boundary, kill, then resume.
        killed = root / 'killed.zip'
        child = subprocess.Popen(command(killed, '--checkpoint-every', 137,
            '--max-records', 5000, '--max-seconds', 30), cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not killed.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.001)
            assert killed.exists() and child.poll() is None, 'live periodic boundary not observed'
            child.kill()
            child.communicate(timeout=5)
            assert child.returncode == -signal.SIGKILL
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        progress = run([*inspect, str(killed)])
        boundary = progress['records_total']
        assert 137 <= boundary < 4099 and boundary % 137 == 0
        recovered = run(command(killed, '--resume'))
        assert recovered['records_this_run'] == 4099 - boundary
        assert recovered['records_total'] == 4099 and recovered['groups'] == expected

        # Inspection requires no source and creates no lock or other directory entry.
        Path(str(checkpoint) + '.lock').unlink()
        held = source.with_suffix('.held')
        source.rename(held)
        before = checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns, set(root.iterdir())
        inspection = run([*inspect, str(checkpoint)])
        assert inspection['source_validation'] == 'unverified'
        assert 'path' not in json.dumps(inspection)
        assert before == (checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns, set(root.iterdir()))
        held.rename(source)

        bad_source = root / 'private-bad.jsonl'
        bad_source.write_bytes(b''.join(lines[:301]) + b'{private-record\r\n')
        bad_checkpoint = root / 'bad.zip'
        error = run(command(bad_checkpoint, '--checkpoint-every', 137, '--max-records', 500,
                    '--max-seconds', 30, input_path=bad_source), code=2)
        assert (error['row'], error['byte_offset']) == (302, sum(map(len, lines[:301])))
        assert 'private' not in json.dumps(error)
        saved = run([*inspect, str(bad_checkpoint)])
        assert saved['records_total'] == 274
        fifo = root / 'fifo.zip'
        os.mkfifo(fifo)
        run([*inspect, str(fifo)], code=2)
        print(json.dumps({'status': 'pass', 'mode': 'source' if args.source else 'installed',
            'rows': 4099, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'periodic_partial_rows': 967, 'sigkill_committed_rows': boundary,
            'sigkill_resumed_rows': recovered['records_this_run'],
            'bad_later_row': error['row'], 'bad_saved_rows': saved['records_total'],
            'independent_integer_oracle': expected,
            'decimal_oracle': 'stdlib Decimal ROUND_HALF_EVEN from independent integer cents',
            'missing_source_inspect_immutable': True, 'fifo_refused': True,
            'provenance': 'fresh synthetic settlements; no provider/model calls'}, indent=2))


if __name__ == '__main__':
    main()
