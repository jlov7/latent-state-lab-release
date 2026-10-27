"""Portable bounded binary capsule for a synthetic fixture; NOT a model checkpoint."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MAX_ARCHIVE = 16 * 1024 * 1024
MAX_CHUNKS = 32
MAX_CHUNK = 4 * 1024 * 1024
MAX_MANIFEST = 128 * 1024
DESCRIPTOR_KEYS = {'model_revision', 'tokenizer_revision', 'runtime_revision', 'dtype', 'device', 'capture_point', 'pending_tool'}


class CapsuleError(ValueError):
    """Unsafe, incompatible or structurally invalid session capsule."""


def _canonical(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleError('duplicate manifest field')
        result[key] = value
    return result


def _check_descriptor(d: dict[str, Any]) -> None:
    if type(d) is not dict or set(d) != DESCRIPTOR_KEYS:
        raise CapsuleError('required descriptor fields missing or unexpected')
    for k in DESCRIPTOR_KEYS - {'pending_tool'}:
        if type(d[k]) is not str or not d[k]:
            raise CapsuleError(f'invalid descriptor {k}')
    if type(d['pending_tool']) is not bool:
        raise CapsuleError('pending_tool must be boolean')
    if d['pending_tool']:
        raise CapsuleError('cannot checkpoint an unfinished tool effect')


def _check_name(name: str) -> None:
    if (type(name) is not str or not name or len(name) > 96 or
            not all(c.isascii() and (c.isalnum() or c in '._-') for c in name) or
            name.startswith('.')):
        raise CapsuleError('unsafe tensor name')


def write_capsule(path: Path, descriptor: dict[str, Any], chunks: dict[str, bytes],
                  *, overwrite: bool = True) -> None:
    _check_descriptor(descriptor)
    if type(chunks) is not dict or not chunks or len(chunks) > MAX_CHUNKS:
        raise CapsuleError('invalid number of tensor chunks')
    manifest: dict[str, Any] = {'format_version': 1, 'descriptor': descriptor, 'chunks': {}}
    for name, data in sorted(chunks.items()):
        _check_name(name)
        if type(data) is not bytes or not data or len(data) > MAX_CHUNK:
            raise CapsuleError('chunk requires bounded nonempty raw bytes')
        manifest['chunks'][name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    manifest_bytes = _canonical(manifest)
    if len(manifest_bytes) > MAX_MANIFEST:
        raise CapsuleError('manifest too large')
    if len(manifest_bytes) + sum(map(len, chunks.values())) > MAX_ARCHIVE:
        raise CapsuleError('capsule larger than configured limit')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    filename = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tmp-capsule-', delete=False) as f:
            filename = f.name
        os.chmod(filename, 0o600)
        with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_STORED) as z:
            z.writestr('manifest.json', manifest_bytes)
            for name, blob in sorted(chunks.items()):
                z.writestr(f'tensors/{name}.bin', blob)
        if os.stat(filename).st_size > MAX_ARCHIVE:
            raise CapsuleError('serialized archive exceeds bound')
        if overwrite:
            os.replace(filename, path)
        else:
            # An atomic exclusive link also refuses a destination created during capture.
            os.link(filename, path)
    finally:
        if filename is not None and os.path.exists(filename):
            os.unlink(filename)


def read_capsule(path: Path, expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    _check_descriptor(expected)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        return _read_capsule(stream, expected)


def _read_capsule(stream, expected):
    """Read from an already opened regular file, retaining the same file identity."""
    _check_descriptor(expected)
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise CapsuleError('capsule must be a regular file')
    if info.st_size > MAX_ARCHIVE:
        raise CapsuleError('archive exceeds bound')
    try:
        with zipfile.ZipFile(stream) as z:
            infos = z.infolist()
            if len(infos) > MAX_CHUNKS + 1 or len({v.filename for v in infos}) != len(infos):
                raise CapsuleError('duplicate or excessive archive entries')
            for info in infos:
                if (info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 or
                        info.file_size > MAX_CHUNK or info.compress_size > MAX_CHUNK):
                    raise CapsuleError('compressed, encrypted or oversized input is refused')
            if 'manifest.json' not in z.namelist():
                raise CapsuleError('no manifest')
            if z.getinfo('manifest.json').file_size > MAX_MANIFEST:
                raise CapsuleError('manifest too large')
            try:
                manifest = json.loads(z.read('manifest.json'), object_pairs_hook=_unique_object)
            except (ValueError, RecursionError) as exc:
                raise CapsuleError('invalid manifest JSON') from exc
            if (type(manifest) is not dict or type(manifest.get('format_version')) is not int or
                    manifest['format_version'] != 1 or
                    set(manifest) != {'format_version','descriptor','chunks'}):
                raise CapsuleError('unsupported format')
            _check_descriptor(manifest['descriptor'])
            if manifest['descriptor'] != expected:
                raise CapsuleError('pinned model/runtime descriptor mismatch')
            entries = manifest['chunks']
            if type(entries) is not dict or not entries or len(entries) > MAX_CHUNKS:
                raise CapsuleError('invalid chunk manifest')
            required = {'manifest.json'}
            chunks = {}
            for name, record in entries.items():
                _check_name(name)
                if type(record) is not dict or set(record) != {'size','sha256'}:
                    raise CapsuleError('invalid chunk metadata')
                if type(record['size']) is not int or record['size'] < 1 or record['size'] > MAX_CHUNK:
                    raise CapsuleError('invalid chunk size')
                filename = f'tensors/{name}.bin'
                required.add(filename)
                blob = z.read(filename)
                if len(blob) != record['size'] or hashlib.sha256(blob).hexdigest() != record['sha256']:
                    raise CapsuleError('corrupt tensor bytes')
                chunks[name] = blob
            if set(z.namelist()) != required:
                raise CapsuleError('unexpected or missing archive entry')
            return manifest['descriptor'], chunks
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapsuleError('bad archive or missing tensor') from exc
