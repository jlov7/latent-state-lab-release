"""Exact ID-aware reconciliation of bounded local full snapshots (POSIX)."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from . import __version__
from .batch import (
    MAX_GROUPS,
    MAX_KEY_BYTES,
    MAX_LINE,
    BatchError,
    _check_deadline,
    _constant,
    _encode,
    _fraction,
    _groups,
    _identity,
    _number,
    _object,
)
from .feed import NUMBER, _header, _record

FORMAT = 'latent-state-sync-v1'
MAX_ROWS = 100000
MAX_STATE = 16 * 1024 * 1024
MAX_INPUT = 128 * 1024 * 1024


class SnapshotDeletionLimit(BatchError):
    """A complete snapshot would remove more IDs than explicitly allowed."""

    def __init__(self, report):
        super().__init__('snapshot deletion ceiling exceeded; inspect the preview or use a correction batch')
        self.report = report


def _text(value, *, empty=False):
    if (type(value) is not str or (not empty and not value) or
            len(value.encode('utf-8')) > MAX_KEY_BYTES):
        raise BatchError('selected string is missing, invalid or exceeds byte bound')
    return value


def _private(info, *, opened_store=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink not in ((0, 1) if opened_store else (1,)) or info.st_mode & 0o077):
        raise BatchError('state and lock must be private owned regular files without hard links')


@contextmanager
def _locked(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private(os.fstat(descriptor))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchError('state is locked by another process') from exc
        yield
    finally:
        os.close(descriptor)


def _read_store(path, config, deadline):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}, None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        # A cooperating replacement can unlink this already-open, complete old state.
        _private(info, opened_store=True)
        if info.st_size > MAX_STATE:
            raise BatchError('state exceeds 16 MiB bound')
        raw = stream.read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise BatchError('state exceeds 16 MiB bound')
    try:
        state = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        if (type(state) is not dict or set(state) != {'format', 'config', 'rows'} or
                state['format'] != FORMAT or state['config'] != config or
                type(state['rows']) is not list or len(state['rows']) > MAX_ROWS):
            raise BatchError('foreign state or incompatible snapshot configuration')
        result = {}
        for row in state['rows']:
            _check_deadline(deadline)
            if type(row) is not list or len(row) != 3:
                raise BatchError('invalid saved record schema')
            key, group, value = row
            _text(key)
            if key in result:
                raise BatchError('duplicate saved ID')
            if config['group_by'] is None:
                if group is not None:
                    raise BatchError('invalid saved group')
            else:
                _text(group, empty=True)
            if config['value'] is None:
                if value is not None:
                    raise BatchError('invalid saved value')
            else:
                _fraction(value)
            result[key] = (group, value)
        return result, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise BatchError('invalid, foreign or incompatible snapshot state') from exc


def _snapshot(stream, config, deadline, max_rows, max_groups, max_line_bytes):
    stream.seek(0)
    header, rows, groups, physical = None, {}, set(), 0
    state_bytes = len(_encode({'format': FORMAT, 'config': config, 'rows': []}))
    while True:
        _check_deadline(deadline)
        offset = stream.tell()
        start = physical + 1
        try:
            record = _record(stream, config['input_format'], max_line_bytes, deadline)
            if record is None:
                if config['input_format'] == 'csv' and header is None:
                    raise BatchError('CSV snapshot requires a header')
                break
            row, _, lines = record
            physical += lines
            if config['input_format'] == 'csv':
                if header is None:
                    header = _header(row)
                    if any(field is not None and field not in header for field in
                           (config['id_field'], config['group_by'], config['value'])):
                        raise BatchError('configured column missing from CSV header')
                    continue
                if len(row) != len(header):
                    raise BatchError('CSV record has incorrect field count')
                row = dict(zip(header, row))
            if len(rows) >= max_rows:
                raise BatchError('snapshot row bound exceeded')
            key = _text(row.get(config['id_field']))
            if key in rows:
                raise BatchError('duplicate snapshot ID')
            group = (_text(row.get(config['group_by']), empty=True)
                     if config['group_by'] is not None else None)
            value = None
            if config['value'] is not None:
                number = row.get(config['value'])
                if config['input_format'] == 'csv':
                    if type(number) is not str or NUMBER.fullmatch(number) is None:
                        raise BatchError('selected value must be a strict decimal')
                    number = _number(number)
                if type(number) is not Decimal:
                    raise BatchError('selected value must be a decimal number')
                value = str(Fraction(number))
                _fraction(value)
            groups.add(group)
            if len(groups) > max_groups:
                raise BatchError('snapshot group bound exceeded')
            state_bytes += len(_encode([key, group, value])) + bool(rows)
            if state_bytes > MAX_STATE:
                raise BatchError('state exceeds 16 MiB bound')
            rows[key] = (group, value)
        except BatchError as exc:
            raise BatchError(str(exc), row=start, byte_offset=offset) from exc
    return rows


def _aggregate(rows, value, deadline):
    groups = {}
    for group, number in rows.values():
        _check_deadline(deadline)
        count, total = groups.get(group, (0, Fraction(0)))
        total += Fraction(number) if value is not None else 0
        _fraction(str(total))
        groups[group] = (count + 1, total)
    return groups


def _write_store(path, raw, previous, config, deadline):
    descriptor, name = tempfile.mkstemp(prefix='.sync-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
        # Cooperating writers hold the lock. Check the destination immediately before commit.
        _, current = _read_store(path, config, deadline)
        if current != previous:
            raise BatchError('state changed before commit')
        _check_deadline(deadline)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile(source, store, *, id_field, input_format='jsonl', group_by=None, value=None,
              apply=False, max_rows=MAX_ROWS, max_groups=MAX_GROUPS,
              max_line_bytes=MAX_LINE, max_seconds=60, details_limit=0, expect_review=None,
              max_deletions=None):
    """Preview or atomically apply a full snapshot. See docs/USAGE.md for bounds/trust."""
    source, store = Path(source), Path(store)
    try:
        for field in (id_field, group_by, value):
            if field is not None:
                _text(field)
        if (id_field is None or input_format not in ('csv', 'jsonl') or type(apply) is not bool or
                type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS or
                type(max_groups) is not int or not 1 <= max_groups <= MAX_GROUPS or
                type(max_line_bytes) is not int or not 1 <= max_line_bytes <= MAX_LINE or
                type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or
                not 0 < max_seconds <= 86400 or type(details_limit) is not int or
                not 0 <= details_limit <= 1000 or
                (max_deletions is not None and
                 (type(max_deletions) is not int or not 0 <= max_deletions <= MAX_ROWS)) or
                (expect_review is not None and
                 (not apply or type(expect_review) is not str or len(expect_review) != 64 or
                  any(c not in '0123456789abcdef' for c in expect_review)))):
            raise BatchError('invalid snapshot configuration or bounds')
    except UnicodeError as exc:
        raise BatchError('invalid snapshot configuration') from exc
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
        if len(before) > max_rows:
            raise BatchError('saved snapshot row bound exceeded')
        old_groups = _aggregate(before, value, deadline)
        if len(old_groups) > max_groups:
            raise BatchError('saved snapshot group bound exceeded')
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW),
                       'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
                raise BatchError('input must be a regular file at most 128 MiB')
            identity = _identity(stream, source, deadline)
            binding = {'config': config, 'store_sha256': previous,
                       'snapshot_sha256': identity['sha256']}
            # Omitting the optional guard retains every historical review digest.
            # A guarded review cannot be applied after changing/removing the guard.
            if max_deletions is not None:
                binding['max_deletions'] = max_deletions
            review = hashlib.sha256(_encode(binding)).hexdigest()
            if expect_review is not None and expect_review != review:
                raise BatchError('review no longer matches source, state or configuration')
            after = _snapshot(stream, config, deadline, max_rows, max_groups, max_line_bytes)
            new_groups = _aggregate(after, value, deadline)
            changes = {'added': 0, 'updated': 0, 'deleted': 0, 'unchanged': 0}
            details = []
            for key in sorted(before.keys() | after.keys()):
                _check_deadline(deadline)
                kind = ('added' if key not in before else 'deleted' if key not in after else
                        'unchanged' if before[key] == after[key] else 'updated')
                changes[kind] += 1
                if kind != 'unchanged' and len(details) < details_limit:
                    details.append({'id': key, 'change': kind,
                                    'before': list(before[key]) if key in before else None,
                                    'after': list(after[key]) if key in after else None})
            raw = _encode({'format': FORMAT, 'config': config,
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
            report = {'applied': apply, 'store_written': False, 'changes': changes,
                      'before': {'records': len(before),
                                 'groups': _groups(old_groups, value, means=True)},
                      'after': {'records': len(after),
                                'groups': _groups(new_groups, value, means=True)},
                      'deltas': deltas, 'snapshot_sha256': identity['sha256'],
                      'review_sha256': review,
                      'snapshot_bytes': identity['size'], 'records_scanned': len(after),
                      'integrity_bytes_read': identity['size'] * 2}
            if details_limit:
                report['details'] = details
                report['details_omitted'] = sum(changes[k] for k in
                                                ('added', 'updated', 'deleted')) - len(details)
            if _identity(stream, source, deadline) != identity:
                raise BatchError('input changed during snapshot reconciliation')
        if max_deletions is not None:
            allowed = changes['deleted'] <= max_deletions
            report['deletion_guard'] = {'maximum': max_deletions,
                                        'proposed': changes['deleted'], 'passed': allowed}
            if apply and not allowed:
                report['applied'] = False
                raise SnapshotDeletionLimit(report)
        if apply and (previous is None or before != after):
            _write_store(store, raw, previous, config, deadline)
            report['store_written'] = True
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'latent-state-lab {__version__}')
    parser.add_argument('source', type=Path,
                        help='complete replacement CSV/JSONL snapshot; missing IDs are deletions')
    parser.add_argument('store', type=Path)
    parser.add_argument('--id', dest='id_field', required=True)
    parser.add_argument('--input-format', choices=('csv', 'jsonl'), default='jsonl')
    parser.add_argument('--group-by')
    parser.add_argument('--value')
    parser.add_argument('--apply', action='store_true', help='commit after full validation')
    parser.add_argument('--expect-review',
                        help='apply only if source, state and configuration match this review SHA-256')
    parser.add_argument('--max-deletions', type=int,
                        help='refuse the entire apply if this many deleted IDs would be exceeded')
    parser.add_argument('--max-rows', type=int, default=MAX_ROWS,
                        help='maximum rows in both saved and incoming collections')
    parser.add_argument('--max-groups', type=int, default=MAX_GROUPS,
                        help='maximum groups in both saved and incoming collections')
    parser.add_argument('--max-line-bytes', type=int, default=MAX_LINE)
    parser.add_argument('--max-seconds', type=float, default=60)
    parser.add_argument('--details-limit', type=int, default=0,
                        help='include up to 1000 changed IDs and selected before/after values')
    args = vars(parser.parse_args(argv))
    try:
        report = reconcile(**args)
    except (BatchError, OSError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SnapshotDeletionLimit):
            print(json.dumps(exc.report, ensure_ascii=True, sort_keys=True))
        error = {'code': 'sync_failed',
                 'message': str(exc) if isinstance(exc, BatchError) else 'snapshot I/O refused',
                 'request_id': uuid.uuid4().hex}
        if isinstance(exc, BatchError) and exc.row is not None:
            error.update(row=exc.row, byte_offset=exc.byte_offset)
        print(json.dumps(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
