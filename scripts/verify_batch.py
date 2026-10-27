"""Exercise an installed batch command against an independent integer baseline.

Usage: python scripts/verify_batch.py /path/to/installed/latent-state-batch
All data is locally generated. No providers, network, weights or optional packages.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from math import gcd
from pathlib import Path


def ratio(numerator, denominator):
    divisor = gcd(numerator, denominator)
    numerator, denominator = numerator // divisor, denominator // divisor
    return str(numerator) if denominator == 1 else f'{numerator}/{denominator}'


def main():
    cli = Path(sys.argv[1]).resolve()
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='batch-verification-', dir=Path.cwd()) as tmp:
        work = Path(tmp)
        source = work / 'orders.jsonl'
        with source.open('w', encoding='utf-8', newline='') as stream:
            for i in range(1733):
                mills = (i * 7919 % 200003) - 100001
                number = ('-' if mills < 0 else '') + f'{abs(mills) // 1000}.{abs(mills) % 1000:03d}'
                row = {'department': ['café\nwest', '雪', 'alpha', 'Ω'][i % 4],
                       'amount': 'AMOUNT', 'description': f'row {i}\n' + 'z' * 8192}
                line = json.dumps(row, ensure_ascii=False).replace('"AMOUNT"', number)
                stream.write(line + ('\r\n' if i % 7 == 0 else '\n'))
        # Independent one-pass reference: integer thousandths, no batch imports/Fraction.
        totals = {}
        with source.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line, parse_float=Decimal)
                count, mills = totals.get(row['department'], (0, 0))
                totals[row['department']] = count + 1, mills + int(row['amount'] * 1000)
        expected = [{'key': key, 'count': count, 'sum': ratio(mills, 1000),
                     'mean': ratio(mills, count * 1000)}
                    for key, (count, mills) in sorted(totals.items())]

        def command(checkpoint, *extra):
            return [str(cli), str(source), str(checkpoint), '--group-by', 'department',
                    '--value', 'amount', *map(str, extra)]

        def run(checkpoint, *extra):
            result = subprocess.run(command(checkpoint, *extra), capture_output=True,
                                    text=True, cwd=work, env=env, timeout=30, check=False)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)

        full = run(work / 'full.zip')
        checkpoint = work / 'resume.zip'
        stopped = run(checkpoint, '--max-records', 613)
        assert stopped['records_total'] == 613 and not stopped['complete']
        prior = checkpoint.read_bytes()

        # Kill the actual installed console command while it holds the run lock.
        child = subprocess.Popen(command(checkpoint, '--resume'), cwd=work, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        locked = False
        deadline = time.monotonic() + 10
        try:
            with open(str(checkpoint) + '.lock', 'rb') as lock:
                while child.poll() is None and time.monotonic() < deadline:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    except BlockingIOError:
                        locked = True
                        child.kill()
                        break
                    time.sleep(0.001)
            child.communicate(timeout=10)
            assert locked and child.returncode == -signal.SIGKILL
            assert checkpoint.read_bytes() == prior
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

        # Fault injection stops a new process at the real atomic replace boundary.
        marker = work / 'before-replace'
        helper = '''import os, runpy, signal, sys
from pathlib import Path
import latent_state_lab.capsule as capsule
original = capsule.os.replace
marker_path = Path(sys.argv[1])
def paused(source, destination):
    marker_path.write_text("serialized-before-replace")
    os.kill(os.getpid(), signal.SIGSTOP)
    original(source, destination)
capsule.os.replace = paused
sys.argv = ["latent-state-batch", *sys.argv[2:]]
runpy.run_module("latent_state_lab.batch", run_name="__main__")
'''
        child = subprocess.Popen([str(cli.parent / 'python'), '-c', helper, str(marker),
                                  *command(checkpoint, '--resume')[1:]], cwd=work, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)
            assert marker.exists(), 'write boundary was not reached'
            child.kill()
            child.communicate(timeout=10)
            assert child.returncode == -signal.SIGKILL
            assert checkpoint.read_bytes() == prior
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

        resumed = run(checkpoint, '--resume')
        assert full['groups'] == resumed['groups'] == expected
        assert full['records_total'] == resumed['records_total'] == 1733
        assert resumed['records_this_run'] == 1120 and resumed['complete']
        assert run(checkpoint, '--resume')['records_this_run'] == 0
        print(json.dumps({'status': 'pass', 'rows': 1733, 'checkpoint_rows': 613,
                          'resumed_rows_aggregated': 1120, 'source_bytes': source.stat().st_size,
                          'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                          'independent_integer_baseline': expected,
                          'uninterrupted_equals_resumed_equals_baseline': True,
                          'real_console_sigkill_preserved_checkpoint': True,
                          'sigkill_before_atomic_replace_preserved_checkpoint': True,
                          'input_provenance': 'local synthetic, no provider/model calls'}, indent=2))


if __name__ == '__main__':
    main()
