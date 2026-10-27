import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from latent_state_lab.capsule import (
    MAX_CHUNK,
    CapsuleError,
    _read_capsule,
    read_capsule,
    write_capsule,
)
from latent_state_lab.fixture import PIN, ToySession


class Capsules(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.path=Path(self.t.name)/'state.zip'
        self.toy=ToySession.start().advance('read-only:alpha')

    def capture(self):
        write_capsule(self.path,PIN,self.toy.chunks())

    def test_roundtrip(self):
        self.capture()
        descriptor,chunks=read_capsule(self.path,PIN)
        self.assertEqual(descriptor,PIN)
        self.assertEqual(ToySession.from_chunks(chunks),self.toy)

    def test_fifo_path_refused_promptly_by_public_api(self):
        os.mkfifo(self.path)
        code = (
            'import sys\n'
            'from pathlib import Path\n'
            'from latent_state_lab.capsule import CapsuleError, read_capsule\n'
            'from latent_state_lab.fixture import PIN\n'
            'try:\n'
            '    read_capsule(Path(sys.argv[1]), PIN)\n'
            'except CapsuleError:\n'
            '    pass\n'
            'else:\n'
            '    raise AssertionError("non-regular capsule accepted")\n'
        )
        try:
            result = subprocess.run([sys.executable, '-c', code, str(self.path)],
                                    capture_output=True, text=True, timeout=2, check=False)
        except subprocess.TimeoutExpired:
            self.fail('capsule read blocked on an empty FIFO; child killed and reaped')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_open_fifo_descriptor_refused_before_archive_parsing(self):
        os.mkfifo(self.path)
        with (
            os.fdopen(os.open(self.path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream,
            self.assertRaisesRegex(CapsuleError, 'regular file'),
        ):
            _read_capsule(stream, PIN)

    def test_regular_file_symlink_roundtrip(self):
        self.capture()
        alias = self.path.with_suffix('.link')
        alias.symlink_to(self.path)
        self.assertEqual(read_capsule(alias, PIN)[1], self.toy.chunks())

    def manifest_descriptor(self, size):
        descriptor = dict(PIN)
        manifest = {'format_version': 1, 'descriptor': descriptor, 'chunks': {
            'state': {'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}}}
        encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        descriptor['model_revision'] += 'x' * (size - len(encoded))
        return descriptor

    def test_manifest_at_reader_limit_roundtrips(self):
        descriptor = self.manifest_descriptor(128 * 1024)
        write_capsule(self.path, descriptor, {'state': b'x'})
        with zipfile.ZipFile(self.path) as archive:
            self.assertEqual(archive.getinfo('manifest.json').file_size, 128 * 1024)
        self.assertEqual(read_capsule(self.path, descriptor)[1], {'state': b'x'})

    def test_manifest_one_byte_over_limit_preserves_destination(self):
        self.capture()
        original = self.path.read_bytes()
        with self.assertRaisesRegex(CapsuleError, 'manifest too large'):
            write_capsule(self.path, self.manifest_descriptor(128 * 1024 + 1), {'state': b'x'})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.tmp-capsule-*')), [])

    def rewrite_manifest(self, blob):
        with zipfile.ZipFile(self.path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['manifest.json'] = blob
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)

    def test_format_version_requires_integer_one(self):
        for version in (True, 1.0):
            with self.subTest(version=version, kind=type(version).__name__):
                self.capture()
                with zipfile.ZipFile(self.path) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                manifest['format_version'] = version
                self.rewrite_manifest(json.dumps(manifest).encode())
                with self.assertRaisesRegex(CapsuleError, 'unsupported format'):
                    read_capsule(self.path, PIN)

    def test_duplicate_manifest_keys_refused_at_every_level(self):
        write_capsule(self.path, PIN, {'state': b'x'})
        with zipfile.ZipFile(self.path) as archive:
            original = archive.read('manifest.json')
        record = json.dumps({'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()},
                            sort_keys=True, separators=(',', ':')).encode()
        mutations = {
            'root': (b'"format_version":1', b'"format_version":1,"format_version":1'),
            'descriptor': (b'"dtype":"f32le"', b'"dtype":"f32le","dtype":"f32le"'),
            'chunks': (b'"state":' + record, b'"state":' + record + b',"state":' + record),
            'chunk record': (b'"size":1', b'"size":1,"size":1'),
        }
        for level, (before, after) in mutations.items():
            with self.subTest(level=level):
                self.assertIn(before, original)
                self.rewrite_manifest(original.replace(before, after, 1))
                with self.assertRaises(CapsuleError):
                    read_capsule(self.path, PIN)

    def test_chunk_size_requires_integer_not_boolean_or_float(self):
        for size in (True, 1.0):
            with self.subTest(size=size, kind=type(size).__name__):
                write_capsule(self.path, PIN, {'state': b'x'})
                with zipfile.ZipFile(self.path) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                manifest['chunks']['state']['size'] = size
                self.rewrite_manifest(json.dumps(manifest).encode())
                with self.assertRaisesRegex(CapsuleError, 'invalid chunk size'):
                    read_capsule(self.path, PIN)

    def test_strict_mismatch(self):
        self.capture()
        other=dict(PIN,model_revision='another')
        with self.assertRaisesRegex(CapsuleError,'mismatch'):
            read_capsule(self.path,other)

    def test_pending_tool_refused(self):
        with self.assertRaisesRegex(CapsuleError,'unfinished'):
            write_capsule(self.path,dict(PIN,pending_tool=True),self.toy.chunks())

    def test_missing_chunk_refused(self):
        self.capture()
        with zipfile.ZipFile(self.path) as src:
            entries={n:src.read(n) for n in src.namelist() if n!='tensors/latent.bin'}
        with zipfile.ZipFile(self.path,'w') as dst:
            for n, b in entries.items():
                dst.writestr(n,b)
        with self.assertRaises(CapsuleError):
            read_capsule(self.path,PIN)

    def test_corrupted_binary_refused(self):
        self.capture()
        with zipfile.ZipFile(self.path) as src:
            entries={n:src.read(n) for n in src.namelist()}
        entries['tensors/latent.bin']=b'wrong!!!'
        with zipfile.ZipFile(self.path,'w') as dst:
            for n,b in entries.items():
                dst.writestr(n,b)
        with self.assertRaisesRegex(CapsuleError,'corrupt'):
            read_capsule(self.path,PIN)

    def test_extra_path_traversal_refused(self):
        self.capture()
        with zipfile.ZipFile(self.path,'a') as z:
            z.writestr('../evil','boom')
        with self.assertRaisesRegex(CapsuleError,'unexpected'):
            read_capsule(self.path,PIN)
        self.assertFalse((self.path.parent/'evil').exists())

    def test_extra_compressed_member_refused(self):
        self.capture()
        with zipfile.ZipFile(self.path,'a',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('extra','xx')
        with self.assertRaises(CapsuleError):
            read_capsule(self.path,PIN)

    def test_oversized_chunk_refused(self):
        with self.assertRaises(CapsuleError):
            write_capsule(self.path,PIN,{'big':b'x'*(MAX_CHUNK+1)})

    def test_unexpected_tensor_name_refused(self):
        with self.assertRaises(CapsuleError):
            write_capsule(self.path,PIN,{'../path':b'hello'})

    def test_empty_tape_allowed_for_fixture(self):
        s=ToySession.start()
        write_capsule(self.path,PIN,s.chunks())
        self.assertEqual(ToySession.from_chunks(read_capsule(self.path,PIN)[1]),s)

    def test_observation_boundaries_roundtrip(self):
        histories = [(), ('',), ('EMPTY',), ('a\nb',), ('\n',), ('a', 'b'),
                     ('', ''), ('EMPTY', '', '雪\n☃', '\x00"\\')]
        for history in histories:
            with self.subTest(history=history):
                session = ToySession.start()
                for observation in history:
                    session = session.advance(observation)
                write_capsule(self.path, PIN, session.chunks())
                restored = ToySession.from_chunks(read_capsule(self.path, PIN)[1])
                self.assertEqual(restored, session)
                self.assertEqual(restored.advance('next'), session.advance('next'))

    def test_transcript_encoding_preserves_distinct_histories(self):
        histories = [(), ('',), ('EMPTY',), ('a\nb',), ('a', 'b')]
        encoded = []
        for history in histories:
            session = ToySession.start()
            for observation in history:
                session = session.advance(observation)
            encoded.append(session.chunks()['transcript'])
        self.assertEqual(len(set(encoded)), len(histories))

    def test_invalid_transcript_refused(self):
        for blob in [b'EMPTY', b'', b'\xff', b'{}', b'"text"', b'[1]',
                     b'[null]', b'[["nested"]]', b'["unterminated]',
                     b'[' * 2000 + b']' * 2000]:
            with self.subTest(blob=blob[:40]):
                chunks = self.toy.chunks()
                chunks['transcript'] = blob
                with self.assertRaises(CapsuleError):
                    ToySession.from_chunks(chunks)

    def test_invalid_unicode_transcript_refused(self):
        for blob in [b'["\\ud800"]', b'["\\udfff"]']:
            with self.subTest(blob=blob):
                chunks = self.toy.chunks()
                chunks['transcript'] = blob
                with self.assertRaises(CapsuleError):
                    ToySession.from_chunks(chunks)

    def test_paired_surrogate_escapes_roundtrip(self):
        session = ToySession.start().advance('雪😀')
        chunks = session.chunks()
        chunks['transcript'] = b'["\\u96ea\\ud83d\\ude00"]'
        restored = ToySession.from_chunks(chunks)
        self.assertEqual(restored, session)
        self.assertEqual(ToySession.from_chunks(restored.chunks()), session)

    def test_transcript_size_bound_on_export_and_import(self):
        session = ToySession.start().advance('x' * MAX_CHUNK)
        with self.assertRaises(CapsuleError):
            session.chunks()
        chunks = self.toy.chunks()
        chunks['transcript'] = b'["' + b'x' * MAX_CHUNK + b'"]'
        with self.assertRaises(CapsuleError):
            ToySession.from_chunks(chunks)

    def test_previous_fixture_runtime_refused(self):
        write_capsule(self.path, dict(PIN, runtime_revision='toy-runtime-v1'),
                      self.toy.chunks())
        with self.assertRaisesRegex(CapsuleError, 'mismatch'):
            read_capsule(self.path, PIN)

    def test_history_count_mismatch_refused(self):
        chunks=self.toy.chunks()
        chunks['step']=(7).to_bytes(4,'little')
        with self.assertRaisesRegex(CapsuleError,'mismatch'):
            ToySession.from_chunks(chunks)

    def test_export_is_private_mode(self):
        self.capture()
        if sys.platform != 'win32':
            self.assertEqual(self.path.stat().st_mode & 0o077,0)

    def test_real_process_restart_matches_uninterrupted_toy(self):
        def cli(*args):
            return json.loads(subprocess.check_output([sys.executable,'-m','latent_state_lab.demo',*map(str,args)],text=True))
        cli('create',self.path)
        resumed=cli('resume',self.path)
        original=cli('uninterrupted')
        self.assertEqual(resumed['action'],original['action'])
        self.assertEqual(resumed['step'],3)


if __name__=='__main__':
    unittest.main()
