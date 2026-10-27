"""Resumable, exact aggregation of local CSV/JSONL files (POSIX)."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from . import __version__
from .capsule import MAX_CHUNK, CapsuleError, _read_capsule, write_capsule

PIN = {
    'model_revision': 'none', 'tokenizer_revision': 'none',
    'runtime_revision': 'jsonl-batch-v1-package-0.2.0-' + sys.implementation.name + '-' +
                        '.'.join(map(str, sys.version_info[:3])),
    'dtype': 'exact-rational', 'device': 'cpu', 'capture_point': 'completed-jsonl-row',
    'pending_tool': False,
}
# Default immutable JSONL retains its existing v1 schema and runtime pin.
MAX_GROUPS = 10000
MAX_LINE = 1024 * 1024
MAX_KEY_BYTES = 1024


class BatchError(ValueError):
    """Invalid input, checkpoint, configuration or concurrent operation."""

    def __init__(self, message, *, row=None, byte_offset=None):
        super().__init__(message)
        self.row = row
        self.byte_offset = byte_offset


def _encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BatchError('duplicate JSON field')
        result[key] = value
    return result


def _number(value):
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BatchError('numeric literal exceeds decimal representation bounds') from exc
    if len(number.as_tuple().digits) > 1000 or abs(number.as_tuple().exponent) > 1000:
        raise BatchError('numeric literal exceeds 1000 digits or exponent bound')
    return number


def _constant(value):
    raise BatchError('non-finite JSON number')


def _load_row(raw):
    try:
        text = raw.decode('utf-8')
        row = json.loads(text, object_pairs_hook=_object, parse_int=_number,
                         parse_float=_number, parse_constant=_constant)
        # Reject unpaired surrogate escapes, including in fields not aggregated.
        json.dumps(row, ensure_ascii=False, default=str).encode('utf-8')
        if type(row) is not dict:
            raise BatchError('each JSONL row must be an object')
        return row
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BatchError('invalid UTF-8 JSON object row') from exc


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise BatchError('maximum run duration reached; resume the last committed checkpoint')


def _identity(stream, source, deadline=None):
    before = os.fstat(stream.fileno())
    if not stat.S_ISREG(before.st_mode):
        raise BatchError('input must be a regular file')
    digest = hashlib.sha256()
    stream.seek(0)
    while block := stream.read(1024 * 1024):
        _check_deadline(deadline)
        digest.update(block)
    after = os.fstat(stream.fileno())
    named = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise BatchError('input changed during integrity scan')
    if (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino):
        raise BatchError('input path was replaced during integrity scan')
    return {'path_sha256': hashlib.sha256(os.fsencode(source.resolve())).hexdigest(),
            'device': after.st_dev, 'inode': after.st_ino, 'size': after.st_size,
            'mtime_ns': after.st_mtime_ns, 'sha256': digest.hexdigest()}


@contextmanager
def _lock(checkpoint, source):
    lock = Path(str(checkpoint) + '.lock')
    if lock.resolve() == source.resolve() or (
            lock.exists() and os.path.samefile(lock, source)):
        raise BatchError('lock and input must be separate files')
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BatchError('lock must be a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchError('checkpoint is locked by another process') from exc
        yield
    finally:
        os.close(descriptor)


def _fraction(value):
    if (type(value) is not str or len(value) > 4096 or
            re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?', value) is None):
        raise BatchError('invalid checkpoint sum')
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise BatchError('invalid checkpoint sum') from exc
    if str(result) != value:
        raise BatchError('noncanonical checkpoint sum')
    return result


def _config_valid(config):
    return (type(config) is dict and set(config) == {
        'group_by', 'value', 'max_groups', 'max_line_bytes'} and
        all(field is None or type(field) is str and bool(field)
            for field in (config['group_by'], config['value'])) and
        type(config['max_groups']) is int and 1 <= config['max_groups'] <= MAX_GROUPS and
        type(config['max_line_bytes']) is int and 1 <= config['max_line_bytes'] <= MAX_LINE)


def _read_state(checkpoint):
    try:
        # Nonblocking open and fstat refuse FIFOs/devices even after a path replacement.
        with os.fdopen(os.open(checkpoint, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW),
                       'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BatchError('checkpoint must be a regular file')
            _, chunks = _read_capsule(stream, PIN)
        if set(chunks) != {'batch'}:
            raise BatchError('unexpected checkpoint chunks')
        state = json.loads(chunks['batch'], object_pairs_hook=_object,
                           parse_constant=_constant)
        if type(state) is not dict or set(state) != {
                'version', 'config', 'source', 'offset', 'records', 'complete', 'groups'}:
            raise BatchError('invalid checkpoint schema')
        if type(state['version']) is not int or state['version'] != 1:
            raise BatchError('unsupported batch state version')
        config, identity = state['config'], state['source']
        if not _config_valid(config):
            raise BatchError('invalid checkpoint configuration')
        if (type(identity) is not dict or set(identity) != {
                'path_sha256', 'device', 'inode', 'size', 'mtime_ns', 'sha256'} or
                any(type(identity[name]) is not str or
                    re.fullmatch('[0-9a-f]{64}', identity[name]) is None
                    for name in ('sha256', 'path_sha256')) or
                any(type(identity[name]) is not int or identity[name] < 0
                    for name in ('device', 'inode', 'size')) or
                type(identity['mtime_ns']) is not int):
            raise BatchError('invalid checkpoint source identity')
        offset, records = state['offset'], state['records']
        if (type(offset) is not int or not 0 <= offset <= identity['size'] or
                type(records) is not int or not 0 <= records <= offset or
                (records == 0) != (offset == 0) or
                type(state['complete']) is not bool or
                state['complete'] != (offset == identity['size'])):
            raise BatchError('invalid checkpoint progress')
        groups = state['groups']
        if type(groups) is not list or len(groups) > config['max_groups']:
            raise BatchError('invalid checkpoint groups')
        result = {}
        for group in groups:
            expected = {'key', 'count', 'sum'} if config['value'] else {'key', 'count'}
            if type(group) is not dict or set(group) != expected:
                raise BatchError('invalid checkpoint group schema')
            key, count = group['key'], group['count']
            if ((config['group_by'] is None and key is not None) or
                    (config['group_by'] is not None and type(key) is not str) or
                    key in result or type(count) is not int or count < 1):
                raise BatchError('invalid checkpoint group')
            if type(key) is str and len(key.encode('utf-8')) > MAX_KEY_BYTES:
                raise BatchError('checkpoint group key exceeds bound')
            result[key] = [count, _fraction(group['sum']) if config['value'] else Fraction(0)]
        if sum(group[0] for group in result.values()) != records:
            raise BatchError('checkpoint group counts differ from progress')
        return state, result
    except (CapsuleError, ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise BatchError('invalid or incompatible batch checkpoint') from exc


def _restore(checkpoint, config, identity, stream, deadline=None):
    state, result = _read_state(checkpoint)
    if state['config'] != config or state['source'] != identity:
        raise BatchError('input or aggregation configuration changed')
    offset, records = state['offset'], state['records']
    if offset and offset != identity['size']:
        stream.seek(offset - 1)
        if stream.read(1) != b'\n':
            raise BatchError('checkpoint offset is not a row boundary')
    stream.seek(0)
    remaining, lines, last = offset, 0, b''
    while remaining:
        _check_deadline(deadline)
        block = stream.read(min(remaining, 1024 * 1024))
        if not block:
            raise BatchError('checkpoint offset exceeds source')
        lines += block.count(b'\n')
        last = block[-1:]
        remaining -= len(block)
    if records != lines + int(bool(last) and last != b'\n'):
        raise BatchError('checkpoint records differ from source row boundaries')
    return state, result


def inspect_checkpoint(checkpoint: Path):
    """Validate saved state without opening its source, writing or taking a lock."""
    try:
        state, groups = _read_state(Path(checkpoint))
    except BatchError:
        from .feed import read_state
        state, groups = read_state(Path(checkpoint))
    config = dict(state['config'])
    for field in ('group_by', 'value'):
        if config[field] is not None and len(config[field]) > 256:
            config[field] = config[field][:256] + '[truncated]'
    report = {'status': 'valid_checkpoint', 'source_validation': 'unverified',
            'records_total': state['records'], 'offset': state['offset'],
            'complete': state['complete'], 'group_count': len(groups), 'config': config,
            'source': {name: state['source'][name] for name in ('size', 'sha256')}}
    if state['version'] == 2:
        report.update(state_version=2, physical_lines=state['physical_lines'])
    return report


def _decimal_display(number, places):
    scaled, remainder = divmod(abs(number.numerator) * 10 ** places, number.denominator)
    if 2 * remainder > number.denominator or (
            2 * remainder == number.denominator and scaled % 2):
        scaled += 1
    sign = '-' if number < 0 and scaled else ''
    digits = str(scaled).zfill(places + 1)
    return sign + (digits[:-places] + '.' + digits[-places:] if places else digits)


def _groups(groups, value, *, means=False, decimal_places=None):
    rows = []
    for key, (count, total) in sorted(groups.items(), key=lambda item: item[0] or ''):
        row = {'key': key, 'count': count}
        if value:
            row['sum'] = str(total)
            if means:
                row['mean'] = str(total / count)
            if decimal_places is not None:
                row['sum_decimal'] = _decimal_display(total, decimal_places)
                row['mean_decimal'] = _decimal_display(total / count, decimal_places)
        rows.append(row)
    return rows


def aggregate(source: Path, checkpoint: Path, *, resume=False, group_by=None, value=None,
              max_records=None, max_groups=MAX_GROUPS, max_line_bytes=MAX_LINE,
              checkpoint_every=None, max_seconds=None, decimal_places=None,
              input_format='jsonl', append_only=False):
    """Commit one bounded run; return exact results. See docs/USAGE.md for the contract."""
    source, checkpoint = Path(source), Path(checkpoint)
    if (any(field is not None and (type(field) is not str or not field)
            for field in (group_by, value)) or
            type(max_groups) is not int or not 1 <= max_groups <= MAX_GROUPS or
            type(max_line_bytes) is not int or not 1 <= max_line_bytes <= MAX_LINE or
            (max_records is not None and (type(max_records) is not int or max_records < 1)) or
            type(resume) is not bool or input_format not in ('jsonl', 'csv') or
            type(append_only) is not bool or
            (decimal_places is not None and
             (type(decimal_places) is not int or not 0 <= decimal_places <= 18)) or
            (checkpoint_every is not None and (
             type(checkpoint_every) is not int or checkpoint_every < 1 or
             max_records is None or type(max_seconds) not in (int, float) or
             not 0 < max_seconds <= 86400 or not math.isfinite(max_seconds))) or
            (checkpoint_every is None and max_seconds is not None)):
        raise BatchError('invalid aggregation configuration or run limit')
    if input_format != 'jsonl' or append_only:
        from .feed import aggregate_feed
        config = {'group_by': group_by, 'value': value, 'max_groups': max_groups,
                  'max_line_bytes': max_line_bytes, 'input_format': input_format,
                  'append_only': append_only}
        return aggregate_feed(source, checkpoint, config=config, resume=resume,
                              max_records=max_records, checkpoint_every=checkpoint_every,
                              max_seconds=max_seconds, decimal_places=decimal_places)
    deadline = time.monotonic() + max_seconds if checkpoint_every is not None else None
    if checkpoint.is_symlink() or source.resolve() == checkpoint.resolve() or (
            checkpoint.exists() and os.path.samefile(source, checkpoint)):
        raise BatchError('input and checkpoint must be separate regular paths')
    config = {'group_by': group_by, 'value': value, 'max_groups': max_groups,
              'max_line_bytes': max_line_bytes}
    with _lock(checkpoint, source), os.fdopen(
            os.open(source, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        if checkpoint.exists() and not checkpoint.is_file():
            raise BatchError('checkpoint must be a regular file')
        if not resume and checkpoint.exists():
            raise BatchError('checkpoint exists; choose a new path or use --resume')
        identity = _identity(stream, source, deadline)
        if resume:
            state, groups = _restore(checkpoint, config, identity, stream, deadline)
        else:
            state = {'version': 1, 'config': config, 'source': identity, 'offset': 0,
                     'records': 0, 'complete': identity['size'] == 0, 'groups': []}
            groups = {}
        stream.seek(state['offset'])
        processed = 0
        initial_records = state['records']
        commits = 0

        def commit():
            nonlocal commits
            offset = stream.tell()
            if _identity(stream, source, deadline) != identity:
                raise BatchError('input changed during aggregation')
            stream.seek(offset)
            _check_deadline(deadline)
            state.update(offset=offset, records=initial_records + processed,
                         complete=offset == identity['size'], groups=_groups(groups, value))
            data = _encode(state)
            if len(data) > MAX_CHUNK:
                raise BatchError('checkpoint state exceeds 4 MiB bound')
            write_capsule(checkpoint, PIN, {'batch': data}, overwrite=resume or commits > 0)
            commits += 1

        while max_records is None or processed < max_records:
            _check_deadline(deadline)
            offset = stream.tell()
            raw = stream.readline(max_line_bytes + 1)
            if not raw:
                break
            try:
                if len(raw) > max_line_bytes:
                    raise BatchError('JSONL line exceeds configured byte bound')
                row = _load_row(raw)
                key = row[group_by] if group_by else None
                if group_by and type(key) is not str:
                    raise BatchError('group field must be a string')
                if group_by and len(key.encode('utf-8')) > MAX_KEY_BYTES:
                    raise BatchError('group key exceeds 1024 UTF-8 bytes')
                number = row[value] if value else Decimal(0)
                if type(number) is not Decimal:
                    raise BatchError('value field must be a finite JSON number')
                if key not in groups:
                    if len(groups) >= max_groups:
                        raise BatchError('group count exceeds configured bound')
                    groups[key] = [0, Fraction(0)]
            except (KeyError, BatchError) as exc:
                message = 'required row field is missing' if isinstance(exc, KeyError) else str(exc)
                raise BatchError(message, row=initial_records + processed + 1,
                                 byte_offset=offset) from exc
            groups[key][0] += 1
            groups[key][1] += Fraction(number)
            processed += 1
            if checkpoint_every is not None and processed % checkpoint_every == 0:
                commit()
        if not commits or state['records'] != initial_records + processed:
            commit()
    report = {'status': 'complete' if state['complete'] else 'checkpointed',
            'complete': state['complete'], 'records_total': state['records'],
            'records_this_run': processed, 'offset': state['offset'],
            'source_sha256': identity['sha256'], 'config': config,
            'groups': _groups(groups, value, means=True, decimal_places=decimal_places)}
    if decimal_places is not None:
        report['decimal_display'] = {'places': decimal_places, 'rounding': 'half-even'}
    if checkpoint_every is not None:
        report['periodic'] = {'checkpoint_every': checkpoint_every, 'max_seconds': max_seconds,
                              'max_records': max_records, 'commits_this_run': commits}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'latent-state-lab {__version__}')
    parser.add_argument('input', type=Path)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--input-format', choices=('jsonl', 'csv'), default='jsonl')
    parser.add_argument('--append-only', action='store_true',
                        help='allow changes only after the committed byte prefix between runs')
    parser.add_argument('--group-by', help='top-level string field; omit for a single group')
    parser.add_argument('--value', help='top-level numeric field for exact sum and mean')
    parser.add_argument('--max-records', type=int, help='maximum rows aggregated in this process')
    parser.add_argument('--max-groups', type=int, default=MAX_GROUPS)
    parser.add_argument('--max-line-bytes', type=int, default=MAX_LINE)
    parser.add_argument('--checkpoint-every', type=int, help='opt-in commit cadence in new rows')
    parser.add_argument('--max-seconds', type=float, help='required with periodic commits; at most 86400')
    parser.add_argument('--decimal-places', type=int, help='report-only half-even decimals, 0 to 18 places')
    args = parser.parse_args()
    try:
        report = aggregate(args.input, args.checkpoint, resume=args.resume,
                           group_by=args.group_by, value=args.value, max_records=args.max_records,
                           max_groups=args.max_groups, max_line_bytes=args.max_line_bytes,
                           checkpoint_every=args.checkpoint_every, max_seconds=args.max_seconds,
                           decimal_places=args.decimal_places, input_format=args.input_format,
                           append_only=args.append_only)
    except (BatchError, OSError) as exc:
        message = str(exc) if isinstance(exc, BatchError) else 'cannot access input or checkpoint'
        error = {'status': 'error', 'code': 'batch_failed',
                 'message': message, 'request_id': uuid.uuid4().hex}
        if isinstance(exc, BatchError) and exc.row is not None:
            error.update(row=exc.row, byte_offset=exc.byte_offset)
        sys.stderr.write(json.dumps(error) + '\n')
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=True, allow_nan=False) + '\n')
    return 0


if __name__ == '__main__':
    from .batch import main as entrypoint
    raise SystemExit(entrypoint())
