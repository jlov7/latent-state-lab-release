"""Fresh-process numerical restoration matrix with explicit pure-text controls.

This extends the UNTRAINED attention probe only. It neither downloads a model nor
claims HybridAgent integration, language capability, or cross-hardware identity.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from itertools import zip_longest
from pathlib import Path

from .matrix_verdict import assess_case
from .tensor_probe import MAX_STEPS, Probe, descriptor

ARMS = ('uninterrupted', 'restore', 'replay', 'text')


def mode_at(position, schedule):
    return 'latent' if schedule == 'hybrid' and position % 3 == 1 else 'text'


def _arguments(seeds, cuts, steps):
    if (type(seeds) not in (tuple, list) or not 1 <= len(seeds) <= 8 or
            any(type(seed) is not int or not 0 <= seed < 2**63 for seed in seeds) or
            len(set(seeds)) != len(seeds)):
        raise ValueError('provide 1..8 unique integer seeds')
    if (type(cuts) not in (tuple, list) or not 1 <= len(cuts) <= 8 or
            any(type(cut) is not int or not 0 <= cut <= MAX_STEPS for cut in cuts) or
            len(set(cuts)) != len(cuts) or len(seeds) * len(cuts) > 8 or
            type(steps) is not int or not 1 <= steps <= 32 or max(cuts) + steps > MAX_STEPS):
        raise ValueError('provide at most eight seed/cut pairs and 1..32 bounded continuation steps')


def _worker(arm, path, seed, cut, steps, schedule):
    _arguments((seed,), (cut,), steps)
    if arm == 'uninterrupted':
        session = Probe(seed)
        for position in range(cut):
            session.step(mode_at(position, schedule))
    else:
        session = Probe.load(path, arm)
    tokens = [session.step(mode_at(position, schedule)) for position in range(cut, cut + steps)]
    return {'arm': arm, 'tokens': tokens, 'state_signature': session.signature(),
            'position': session.position, 'runtime': descriptor()}


def run_matrix(*, seeds=(7, 19), cuts=(0, 5), continuation_steps=6):
    """Run <=64 isolated child executions; raise rather than drop failed cases.

    Both schedules use absolute event positions. Text-only is expected to be a
    lossless control for a pure-text prefix, not a universally failing straw man.
    One child timeout is 30 seconds. No speed or distributional claim is made.
    """
    _arguments(seeds, cuts, continuation_steps)
    cases = []
    with tempfile.TemporaryDirectory(prefix='latent-matrix-') as directory:
        for seed in seeds:
            for cut in cuts:
                for schedule in ('text', 'hybrid'):
                    source = Probe(seed)
                    for position in range(cut):
                        source.step(mode_at(position, schedule))
                    path = Path(directory) / f'{seed}-{cut}-{schedule}.lsc'
                    source.snapshot(path)
                    rows = {}
                    for arm in ARMS:
                        command = [sys.executable, '-m', 'latent_state_lab.continuation_matrix',
                                   '--worker', arm, '--capsule', str(path), '--seed', str(seed),
                                   '--cut', str(cut), '--steps', str(continuation_steps),
                                   '--schedule', schedule]
                        process = subprocess.run(command, capture_output=True, text=True,
                                                 timeout=30, check=True)
                        rows[arm] = json.loads(process.stdout)
                    case = {'seed': seed, 'cut': cut, 'schedule': schedule,
                            'continuation_steps': continuation_steps,
                            'capsule_bytes': path.stat().st_size, 'arms': rows}
                    case['assessment'] = assess_case(case, expected_runtime=descriptor())
                    baseline = rows['uninterrupted']
                    for row in rows.values():
                        if (type(row) is not dict or type(baseline) is not dict or
                                type(row.get('state_signature')) is not str or
                                type(baseline.get('state_signature')) is not str or
                                type(row.get('tokens')) is not list or
                                type(baseline.get('tokens')) is not list):
                            continue
                        row['state_match'] = row['state_signature'] == baseline['state_signature']
                        row['first_token_difference'] = next(
                            (i for i, (a, b) in enumerate(zip_longest(row['tokens'], baseline['tokens'])) if a != b), None)
                    cases.append(case)
    status = 'PASS' if all(case['assessment']['status'] == 'PASS' for case in cases) else 'FAIL'
    return {'schema': 'latent-numerical-matrix-v1', 'pretrained_model_result': False,
            'status': status,
            'scope': 'UNTRAINED fixed CPU probe; text and hybrid schedules; no statistical generalization.',
            'processes_executed': len(cases) * len(ARMS), 'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=ARMS, help=argparse.SUPPRESS)
    parser.add_argument('--capsule', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--seed', type=int, default=7, help=argparse.SUPPRESS)
    parser.add_argument('--cut', type=int, default=5, help=argparse.SUPPRESS)
    parser.add_argument('--steps', type=int, default=6)
    parser.add_argument('--schedule', choices=('text', 'hybrid'), default='hybrid', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker and not args.capsule:
        parser.error('worker requires a capsule')
    try:
        result = (_worker(args.worker, args.capsule, args.seed, args.cut, args.steps, args.schedule)
                  if args.worker else run_matrix(continuation_steps=args.steps))
    except (ValueError, OSError, subprocess.SubprocessError):
        parser.exit(2, 'Numerical comparison incomplete; check arguments, runtime and retained input.\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if args.worker or result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
