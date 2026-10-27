"""Bounded CSV and append-between-invocations feeds. Legacy JSONL stays in batch."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
import sys
import time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .batch import (
    MAX_KEY_BYTES,
    BatchError,
    _check_deadline,
    _config_valid,
    _constant,
    _encode,
    _fraction,
    _groups,
    _identity,
    _load_row,
    _lock,
    _number,
    _object,
)
from .capsule import MAX_CHUNK, CapsuleError, _read_capsule, write_capsule

PIN = {
    'model_revision': 'none', 'tokenizer_revision': 'none',
    'runtime_revision': 'feed-batch-v2-package-0.4.0-' + sys.implementation.name + '-' +
                        '.'.join(map(str, sys.version_info[:3])),
    'dtype': 'exact-rational', 'device': 'cpu', 'capture_point': 'completed-logical-record',
    'pending_tool': False,
}
CSV_FIELD_BYTES = 128 * 1024
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')


class _Lines:
    def __init__(self, stream, bound, deadline):
        self.stream, self.bound, self.deadline = stream, bound, deadline
        self.raw = bytearray()
        self.lines = 0

    def __iter__(self):
        return self

    def __next__(self):
        _check_deadline(self.deadline)
        first = self.stream.tell() == 0
        raw = self.stream.readline(self.bound - len(self.raw) + 1)
        if not raw:
            raise StopIteration
        self.raw.extend(raw)
        if len(self.raw) > self.bound:
            raise BatchError('logical record exceeds configured byte bound')
        self.lines += 1
        return raw.decode('utf-8-sig' if first else 'utf-8')


def _csv_syntax(text):
    # csv strict=True permits quotes inside unquoted fields. Refuse those too.
    state = 'start'
    for index, char in enumerate(text):
        if state == 'quoted':
            if char == '"':
                state = 'closed'
        elif char in '\r\n':
            if text[index:] not in ('\n', '\r\n'):
                raise BatchError('invalid CSV record')
            return
        elif char == ',':
            state = 'start'
        elif char == '"':
            if state not in ('start', 'closed'):
                raise BatchError('invalid CSV record')
            state = 'quoted'
        elif state == 'closed':
            raise BatchError('invalid CSV record')
        else:
            state = 'plain'
    if state == 'quoted':
        raise BatchError('invalid CSV record')


def _record(stream, input_format, bound, deadline):
    if input_format == 'jsonl':
        raw = stream.readline(bound + 1)
        if not raw:
            return None
        if len(raw) > bound:
            raise BatchError('JSONL line exceeds configured byte bound')
        return _load_row(raw), raw, 1
    lines = _Lines(stream, bound, deadline)
    first = stream.tell() == 0
    try:
        fields = next(csv.reader(lines, strict=True))
        raw = bytes(lines.raw)
        _csv_syntax(raw.decode('utf-8-sig' if first else 'utf-8'))
        if not fields or any(len(field.encode('utf-8')) > CSV_FIELD_BYTES for field in fields):
            raise BatchError('empty CSV record or field exceeds 128 KiB bound')
        return fields, raw, lines.lines
    except StopIteration:
        return None
    except (csv.Error, UnicodeError) as exc:
        raise BatchError('invalid UTF-8 CSV record') from exc


def _header(fields):
    if (not fields or any(not field or field.startswith('\ufeff') for field in fields) or
            len(set(fields)) != len(fields)):
        raise BatchError('CSV header must contain unique nonempty names without a BOM')
    return fields


def _valid_config(config):
    if type(config) is not dict or set(config) != {
            'group_by', 'value', 'max_groups', 'max_line_bytes', 'input_format', 'append_only'}:
        return False
    return (_config_valid({key: config[key] for key in
                           ('group_by', 'value', 'max_groups', 'max_line_bytes')}) and
            config['input_format'] in ('csv', 'jsonl') and
            type(config['append_only']) is bool and
            (config['input_format'] == 'csv' or config['append_only']))


def read_state(checkpoint):
    try:
        with os.fdopen(os.open(checkpoint, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW),
                       'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BatchError('checkpoint must be a regular file')
            _, chunks = _read_capsule(stream, PIN)
        if set(chunks) != {'batch'}:
            raise BatchError('unexpected checkpoint chunks')
        state = json.loads(chunks['batch'], object_pairs_hook=_object, parse_constant=_constant)
        if (type(state) is not dict or set(state) != {
                'version', 'config', 'source', 'offset', 'records', 'complete', 'groups',
                'physical_lines', 'prefix_sha256', 'header'} or
                type(state['version']) is not int or state['version'] != 2 or
                not _valid_config(state['config'])):
            raise BatchError('invalid feed checkpoint schema')
        config, identity = state['config'], state['source']
        if (type(identity) is not dict or set(identity) != {
                'path_sha256', 'device', 'inode', 'size', 'mtime_ns', 'sha256'} or
                any(type(identity[name]) is not str or
                    re.fullmatch('[0-9a-f]{64}', identity[name]) is None
                    for name in ('sha256', 'path_sha256')) or
                any(type(identity[name]) is not int or identity[name] < 0
                    for name in ('device', 'inode', 'size')) or
                type(identity['mtime_ns']) is not int or
                type(state['prefix_sha256']) is not str or
                re.fullmatch('[0-9a-f]{64}', state['prefix_sha256']) is None):
            raise BatchError('invalid feed checkpoint identity')
        offset, records, lines = state['offset'], state['records'], state['physical_lines']
        if (type(offset) is not int or not 0 <= offset <= identity['size'] or
                type(records) is not int or not 0 <= records <= offset or
                type(lines) is not int or not records <= lines <= offset or
                type(state['complete']) is not bool or
                state['complete'] != (offset == identity['size']) or
                (state['complete'] and state['prefix_sha256'] != identity['sha256'])):
            raise BatchError('invalid feed checkpoint progress')
        header = state['header']
        if config['input_format'] == 'csv':
            if (type(header) is not list or not header or
                    any(type(field) is not str for field in header) or
                    any(len(field.encode('utf-8')) > CSV_FIELD_BYTES for field in header) or
                    sum(len(field.encode('utf-8')) for field in header) > config['max_line_bytes'] or
                    lines < records + 1):
                raise BatchError('invalid feed checkpoint header')
            _header(header)
            if any(field is not None and field not in header
                   for field in (config['group_by'], config['value'])):
                raise BatchError('invalid feed checkpoint selected field')
        elif header is not None or lines != records or (records == 0) != (offset == 0):
            raise BatchError('invalid feed checkpoint row progress')
        groups = state['groups']
        if type(groups) is not list or len(groups) > config['max_groups']:
            raise BatchError('invalid feed checkpoint groups')
        result = {}
        for group in groups:
            expected = {'key', 'count', 'sum'} if config['value'] else {'key', 'count'}
            if type(group) is not dict or set(group) != expected:
                raise BatchError('invalid feed checkpoint group schema')
            key, count = group['key'], group['count']
            if ((config['group_by'] is None and key is not None) or
                    (config['group_by'] is not None and type(key) is not str) or
                    key in result or type(count) is not int or count < 1 or
                    (type(key) is str and len(key.encode('utf-8')) > MAX_KEY_BYTES)):
                raise BatchError('invalid feed checkpoint group')
            result[key] = [count, _fraction(group['sum']) if config['value'] else Fraction(0)]
        if sum(group[0] for group in result.values()) != records:
            raise BatchError('feed checkpoint group counts differ from progress')
        return state, result
    except (CapsuleError, ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise BatchError('invalid or incompatible feed checkpoint') from exc


def _prefix(stream, state, deadline):
    stream.seek(0)
    digest = hashlib.sha256()
    remaining, lines, last = state['offset'], 0, b''
    while remaining:
        _check_deadline(deadline)
        block = stream.read(min(remaining, 1024 * 1024))
        if not block:
            raise BatchError('source is shorter than committed prefix')
        digest.update(block)
        lines += block.count(b'\n')
        last = block[-1:]
        remaining -= len(block)
    if (digest.hexdigest() != state['prefix_sha256'] or
            lines + int(bool(last) and last != b'\n') != state['physical_lines'] or
            (state['offset'] and state['config']['append_only'] and last != b'\n')):
        raise BatchError('committed source prefix changed or progress is invalid')
    return digest


def aggregate_feed(source, checkpoint, *, config, resume, max_records, checkpoint_every,
                   max_seconds, decimal_places):
    source, checkpoint = Path(source), Path(checkpoint)
    deadline = time.monotonic() + max_seconds if checkpoint_every is not None else None
    if checkpoint.is_symlink() or source.resolve() == checkpoint.resolve() or (
            checkpoint.exists() and os.path.samefile(source, checkpoint)):
        raise BatchError('input and checkpoint must be separate regular paths')
    integrity_bytes = 0
    with _lock(checkpoint, source), os.fdopen(
            os.open(source, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        if checkpoint.exists() and not checkpoint.is_file():
            raise BatchError('checkpoint must be a regular file')
        if not resume and checkpoint.exists():
            raise BatchError('checkpoint exists; choose a new path or use --resume')
        identity = _identity(stream, source, deadline)
        integrity_bytes += identity['size']
        if resume:
            state, groups = read_state(checkpoint)
            if state['config'] != config:
                raise BatchError('input or aggregation configuration changed')
            if config['append_only']:
                if (any(state['source'][key] != identity[key] for key in
                        ('path_sha256', 'device', 'inode')) or identity['size'] < state['offset']):
                    raise BatchError('input replaced or truncated below committed prefix')
            elif state['source'] != identity:
                raise BatchError('input or aggregation configuration changed')
            digest = _prefix(stream, state, deadline)
            integrity_bytes += state['offset']
        else:
            stream.seek(0)
            digest = hashlib.sha256()
            state = {'version': 2, 'config': config, 'source': identity, 'offset': 0,
                     'records': 0, 'physical_lines': 0, 'prefix_sha256': digest.hexdigest(),
                     'complete': identity['size'] == 0, 'header': None, 'groups': []}
            groups = {}
            if config['input_format'] == 'csv':
                try:
                    record = _record(stream, 'csv', config['max_line_bytes'], deadline)
                    if record is None:
                        raise BatchError('CSV input requires a header')
                    fields, raw, lines = record
                    state['header'] = _header(fields)
                    if any(field is not None and field not in fields for field in
                           (config['group_by'], config['value'])):
                        raise BatchError('required CSV header field is missing')
                    if config['append_only'] and not raw.endswith(b'\n'):
                        raise BatchError('append-only records require a terminating newline')
                    digest.update(raw)
                    state['offset'], state['physical_lines'] = len(raw), lines
                except BatchError as exc:
                    raise BatchError(str(exc), row=1, byte_offset=0) from exc
        stream.seek(state['offset'])
        initial_records, physical_lines = state['records'], state['physical_lines']
        processed, commits = 0, 0
        group_by, value = config['group_by'], config['value']

        def commit():
            nonlocal commits, integrity_bytes
            offset = stream.tell()
            checked = _identity(stream, source, deadline)
            integrity_bytes += checked['size']
            if checked != identity:
                raise BatchError('input changed during aggregation')
            stream.seek(offset)
            _check_deadline(deadline)
            state.update(source=identity, offset=offset, records=initial_records + processed,
                         physical_lines=physical_lines, prefix_sha256=digest.hexdigest(),
                         complete=offset == identity['size'], groups=_groups(groups, value))
            data = _encode(state)
            if len(data) > MAX_CHUNK:
                raise BatchError('checkpoint state exceeds 4 MiB bound')
            write_capsule(checkpoint, PIN, {'batch': data}, overwrite=resume or commits > 0)
            commits += 1

        while max_records is None or processed < max_records:
            _check_deadline(deadline)
            offset = stream.tell()
            try:
                # Avoid reparsing the header or any committed record on EOF resume.
                if offset == identity['size']:
                    break
                record = _record(stream, config['input_format'], config['max_line_bytes'], deadline)
                if record is None:
                    raise BatchError('input changed during aggregation')
                row, raw, lines = record
                if config['append_only'] and not raw.endswith(b'\n'):
                    raise BatchError('append-only records require a terminating newline')
                if config['input_format'] == 'csv':
                    if len(row) != len(state['header']):
                        raise BatchError('CSV record width differs from header')
                    row = dict(zip(state['header'], row))
                    if value:
                        if NUMBER.fullmatch(row[value]) is None:
                            raise BatchError('value field must be a finite decimal number')
                        row[value] = _number(row[value])
                key = row[group_by] if group_by else None
                if group_by and type(key) is not str:
                    raise BatchError('group field must be a string')
                if group_by and len(key.encode('utf-8')) > MAX_KEY_BYTES:
                    raise BatchError('group key exceeds 1024 UTF-8 bytes')
                number = row[value] if value else Decimal(0)
                if type(number) is not Decimal:
                    raise BatchError('value field must be a finite decimal number')
                if key not in groups:
                    if len(groups) >= config['max_groups']:
                        raise BatchError('group count exceeds configured bound')
                    groups[key] = [0, Fraction(0)]
            except (KeyError, BatchError) as exc:
                message = 'required row field is missing' if isinstance(exc, KeyError) else str(exc)
                raise BatchError(message, row=physical_lines + 1, byte_offset=offset) from exc
            groups[key][0] += 1
            groups[key][1] += Fraction(number)
            digest.update(raw)
            physical_lines += lines
            processed += 1
            if checkpoint_every is not None and processed % checkpoint_every == 0:
                commit()
        if not commits or state['records'] != initial_records + processed:
            commit()
    report = {'status': 'complete' if state['complete'] else 'checkpointed',
              'complete': state['complete'], 'state_version': 2,
              'records_total': state['records'], 'records_this_run': processed,
              'physical_lines': physical_lines, 'offset': state['offset'],
              'source_sha256': identity['sha256'], 'config': config,
              'integrity_bytes_read': integrity_bytes,
              'groups': _groups(groups, value, means=True, decimal_places=decimal_places)}
    if decimal_places is not None:
        report['decimal_display'] = {'places': decimal_places, 'rounding': 'half-even'}
    if checkpoint_every is not None:
        report['periodic'] = {'checkpoint_every': checkpoint_every, 'max_seconds': max_seconds,
                              'max_records': max_records, 'commits_this_run': commits}
    return report
