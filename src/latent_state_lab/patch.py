"""Bounded declarative before/after corrections against private sync state (POSIX)."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import time
import uuid
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path

from . import __version__
from .batch import (
    MAX_GROUPS,
    MAX_LINE,
    BatchError,
    _check_deadline,
    _encode,
    _fraction,
    _groups,
    _identity,
    _number,
)
from .feed import NUMBER, _record
from .sync import FORMAT as STORE_FORMAT
from .sync import (
    MAX_INPUT,
    MAX_ROWS,
    MAX_STATE,
    _aggregate,
    _locked,
    _read_store,
    _text,
    _write_store,
)

FORMAT = 'latent-state-patch-v1'
MAX_CHANGES = MAX_ROWS


class PatchConflict(BatchError):
    """An apply was refused in full; report contains every conflict count."""

    def __init__(self, report):
        super().__init__('patch conflicts prevent the entire apply')
        self.report = report


def _side(row, config):
    if row is None:
        return None
    if type(row) is not dict or set(row) != {'group', 'value'}:
        raise BatchError('patch side must be null or exactly group and value')
    group, value = row['group'], row['value']
    if config['group_by'] is None:
        if group is not None:
            raise BatchError('unselected patch group must be null')
    else:
        _text(group, empty=True)
    if config['value'] is None:
        if value is not None:
            raise BatchError('unselected patch value must be null')
    else:
        if type(value) is not str or NUMBER.fullmatch(value) is None:
            raise BatchError('patch value must be a strict decimal string')
        value = str(Fraction(_number(value)))
        _fraction(value)
    return group, value


def _changes(stream, config, deadline, max_changes, max_line_bytes):
    stream.seek(0)
    rows = {}
    while True:
        _check_deadline(deadline)
        offset, start = stream.tell(), len(rows) + 1
        try:
            record = _record(stream, 'jsonl', max_line_bytes, deadline)
            if record is None:
                return rows
            row, _, _ = record
            if len(rows) >= max_changes:
                raise BatchError('patch change bound exceeded')
            if set(row) != {'id', 'before', 'after'}:
                raise BatchError('patch row must contain exactly id, before and after')
            key = _text(row['id'])
            if key in rows:
                raise BatchError('duplicate patch ID')
            before, after = _side(row['before'], config), _side(row['after'], config)
            if before is None and after is None:
                raise BatchError('patch before and after cannot both be absent')
            rows[key] = before, after
        except BatchError as exc:
            raise BatchError(str(exc), row=start, byte_offset=offset) from exc


def apply_patch(source, store, *, id_field, input_format='jsonl', group_by=None, value=None,
                apply=False, max_changes=MAX_CHANGES, max_rows=MAX_ROWS, max_groups=MAX_GROUPS,
                max_line_bytes=MAX_LINE, max_seconds=60, details_limit=0, expect_review=None):
    """Preview or atomically apply patch-v1 JSONL. Conflicted apply raises PatchConflict."""
    source, store = Path(source), Path(store)
    try:
        for field in (id_field, group_by, value):
            if field is not None:
                _text(field)
        if (id_field is None or input_format not in ('csv', 'jsonl') or type(apply) is not bool or
                type(max_changes) is not int or not 1 <= max_changes <= MAX_CHANGES or
                type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS or
                type(max_groups) is not int or not 1 <= max_groups <= MAX_GROUPS or
                type(max_line_bytes) is not int or not 1 <= max_line_bytes <= MAX_LINE or
                type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or
                not 0 < max_seconds <= 86400 or type(details_limit) is not int or
                not 0 <= details_limit <= 1000 or
                (expect_review is not None and
                 (not apply or type(expect_review) is not str or len(expect_review) != 64 or
                  any(c not in '0123456789abcdef' for c in expect_review)))):
            raise BatchError('invalid patch configuration or bounds')
    except UnicodeError as exc:
        raise BatchError('invalid patch configuration') from exc
    deadline = time.monotonic() + max_seconds
    config = {'id_field': id_field, 'group_by': group_by, 'value': value, 'input_format': input_format}
    lock = Path(str(store) + '.lock')
    parent = store.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise BatchError('state directory must be owned and not writable by other users')
    paths = (source, store, lock)
    for index, path in enumerate(paths):
        if path.is_symlink():
            raise BatchError('symlink inputs, state and locks are refused')
        for other in paths[:index]:
            if path.resolve() == other.resolve() or (
                    path.exists() and other.exists() and os.path.samefile(path, other)):
                raise BatchError('input, state and lock must be separate files')
    with _locked(lock) if apply else nullcontext():
        before, previous = _read_store(store, config, deadline)
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW),
                       'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
                raise BatchError('input must be a regular file at most 128 MiB')
            identity = _identity(stream, source, deadline)
            review = hashlib.sha256(_encode({'format': FORMAT, 'config': config,
                                             'store_sha256': previous,
                                             'patch_sha256': identity['sha256']})).hexdigest()
            if expect_review is not None and expect_review != review:
                raise BatchError('review no longer matches source, state or configuration')
            changes = _changes(stream, config, deadline, max_changes, max_line_bytes)
            after, counts, details = before.copy(), {'applicable': 0, 'already_present': 0,
                                                         'conflicts': 0}, []
            for key in sorted(changes):
                _check_deadline(deadline)
                expected, proposed = changes[key]
                current = before.get(key)
                kind = ('already_present' if current == proposed else
                        'applicable' if current == expected else 'conflicts')
                counts[kind] += 1
                if kind == 'applicable':
                    if proposed is None:
                        del after[key]
                    else:
                        after[key] = proposed
                if len(details) < details_limit:
                    details.append({'id': key, 'status': kind,
                                    'current': list(current) if current is not None else None,
                                    'before': list(expected) if expected is not None else None,
                                    'after': list(proposed) if proposed is not None else None})
            # A conflicted batch has no resulting changes, including its applicable rows.
            if counts['conflicts']:
                after = before
            if len(before) > max_rows or len(after) > max_rows:
                raise BatchError('state row bound exceeded')
            old_groups, new_groups = (_aggregate(rows, value, deadline) for rows in (before, after))
            if len(old_groups) > max_groups or len(new_groups) > max_groups:
                raise BatchError('state group bound exceeded')
            raw = _encode({'format': STORE_FORMAT, 'config': config,
                           'rows': [[key, *after[key]] for key in sorted(after)]})
            if len(raw) > MAX_STATE:
                raise BatchError('state exceeds 16 MiB bound')
            deltas = []
            for key in sorted(old_groups.keys() | new_groups.keys(), key=lambda k: k or ''):
                old_count, old_sum = old_groups.get(key, (0, Fraction(0)))
                new_count, new_sum = new_groups.get(key, (0, Fraction(0)))
                delta = {'key': key, 'count': new_count - old_count}
                if value is not None:
                    delta['sum'] = str(new_sum - old_sum)
                deltas.append(delta)
            report = {'format': FORMAT, 'applied': apply and not counts['conflicts'],
                      'can_apply': not counts['conflicts'], 'store_written': False,
                      'changes': counts,
                      'before': {'records': len(before),
                                 'groups': _groups(old_groups, value, means=True)},
                      'after': {'records': len(after),
                                'groups': _groups(new_groups, value, means=True)},
                      'deltas': deltas, 'patch_sha256': identity['sha256'],
                      'review_sha256': review, 'patch_bytes': identity['size'],
                      'changes_scanned': len(changes),
                      'integrity_bytes_read': identity['size'] * 2}
            if details_limit:
                report.update(details=details, details_omitted=len(changes) - len(details))
            if _identity(stream, source, deadline) != identity:
                raise BatchError('input changed during patch reconciliation')
        if apply and counts['conflicts']:
            raise PatchConflict(report)
        if apply and before != after:
            _write_store(store, raw, previous, config, deadline)
            report['store_written'] = True
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'latent-state-lab {__version__}')
    parser.add_argument('source', type=Path, help='patch-v1 JSONL, containing changed IDs only')
    parser.add_argument('store', type=Path)
    parser.add_argument('--id', dest='id_field', required=True,
                        help='existing sync ID field configuration; patch rows always use id')
    parser.add_argument('--input-format', choices=('csv', 'jsonl'), default='jsonl',
                        help='existing sync snapshot format; patch input is always JSONL')
    parser.add_argument('--group-by')
    parser.add_argument('--value')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expect-review')
    parser.add_argument('--max-changes', type=int, default=MAX_CHANGES)
    parser.add_argument('--max-rows', type=int, default=MAX_ROWS)
    parser.add_argument('--max-groups', type=int, default=MAX_GROUPS)
    parser.add_argument('--max-line-bytes', type=int, default=MAX_LINE)
    parser.add_argument('--max-seconds', type=float, default=60)
    parser.add_argument('--details-limit', type=int, default=0)
    try:
        report = apply_patch(**vars(parser.parse_args(argv)))
    except (BatchError, OSError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PatchConflict):
            print(json.dumps(exc.report, ensure_ascii=True, sort_keys=True))
        error = {'code': 'patch_failed',
                 'message': str(exc) if isinstance(exc, BatchError) else 'patch I/O refused',
                 'request_id': uuid.uuid4().hex}
        if isinstance(exc, BatchError) and exc.row is not None:
            error.update(row=exc.row, byte_offset=exc.byte_offset)
        print(json.dumps(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
