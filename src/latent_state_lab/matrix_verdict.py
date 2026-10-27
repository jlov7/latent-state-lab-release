"""Judge raw numerical continuation records, not their self-reported match flags.

This is a bounded software check for the untrained probe, not a model-quality
metric, portable checkpoint standard, or reader for arbitrary external reports.
"""
from __future__ import annotations

ARMS = ('uninterrupted', 'restore', 'replay', 'text')


def assess_case(case: dict, *, expected_runtime: dict | None = None) -> dict:
    """Require full restoration/replay and every genuinely lossless text control.

    A text arm after a latent prefix is descriptive: it is permitted either to
    match or differ. A short prefix containing no latent step must match even
    when the continuation schedule is named hybrid. Return detached verdicts.
    """
    if type(case) is not dict:
        raise ValueError('comparison case must be an object')
    cut, steps, schedule = (case.get(name) for name in ('cut', 'continuation_steps', 'schedule'))
    if (type(cut) is not int or not 0 <= cut <= 256 or
            type(steps) is not int or not 1 <= steps <= 32 or cut + steps > 256 or
            schedule not in ('text', 'hybrid') or type(case.get('arms')) is not dict):
        raise ValueError('invalid bounded comparison case')
    rows = case['arms']
    required = list(ARMS[:3])
    if schedule == 'text' or cut < 2:
        required.append('text')
    failures = []
    baseline = rows.get('uninterrupted', {})
    for name in ARMS:
        row = rows.get(name)
        if type(row) is not dict:
            failures.append({'arm': name, 'field': 'record'})
            continue
        tokens = row.get('tokens')
        signature = row.get('state_signature')
        if row.get('arm') != name:
            failures.append({'arm': name, 'field': 'arm'})
        if (type(tokens) is not list or len(tokens) != steps or
                any(type(token) is not int or not 0 <= token < 17 for token in tokens)):
            failures.append({'arm': name, 'field': 'tokens_shape_or_type'})
        if (type(signature) is not str or len(signature) != 64 or
                any(c not in '0123456789abcdef' for c in signature)):
            failures.append({'arm': name, 'field': 'state_signature_shape'})
        if type(row.get('runtime')) is not dict or not row['runtime']:
            failures.append({'arm': name, 'field': 'runtime_shape'})
        if type(row.get('position')) is not int or not 0 <= row['position'] <= 256:
            failures.append({'arm': name, 'field': 'position_shape'})
        position = cut + steps
        if name == 'text' and schedule == 'hybrid':
            position -= (cut + 1) // 3
        if row.get('position') != position:
            failures.append({'arm': name, 'field': 'position'})
        runtime = expected_runtime if expected_runtime is not None else (
            baseline.get('runtime') if type(baseline) is dict else None)
        if (row.get('runtime') != runtime or
                type(row.get('runtime')) is dict and
                (row['runtime'].get('dtype') != 'float32' or row['runtime'].get('device') != 'cpu')):
            failures.append({'arm': name, 'field': 'runtime'})
        if name not in required or type(baseline) is not dict:
            continue
        for field in ('tokens', 'state_signature'):
            if row.get(field) != baseline.get(field):
                failures.append({'arm': name, 'field': field})
    return {'status': 'FAIL' if failures else 'PASS', 'required_arms': required,
            'failures': failures, 'scientific_capability_claim': False}
