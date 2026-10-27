"""Toy continuity fixture: exercise lifecycle tooling without pretending to run an LLM."""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass

from .capsule import MAX_CHUNK, CapsuleError

PIN = {'model_revision': 'SYNTHETIC-FIXTURE-NOT-MODEL',
       'tokenizer_revision': 'toy-tokenizer-v1', 'runtime_revision': 'toy-runtime-v2',
       'dtype': 'f32le', 'device': 'cpu', 'capture_point': 'completed_tool', 'pending_tool': False}


@dataclass(frozen=True)
class ToySession:
    step: int
    latent: bytes
    transcript: tuple[str, ...]

    @classmethod
    def start(cls):
        return cls(0, struct.pack('<ff', 0.125, -0.5), ())

    def advance(self, observation: str):
        a, b = struct.unpack('<ff', self.latent)
        # Intentionally latent-state-dependent synthetic transition.
        delta = (sum(observation.encode('utf-8')) % 101) / 101.0
        return ToySession(self.step + 1,
                          struct.pack('<ff', (a + delta) / 1.5, (b - delta) / 1.5),
                          self.transcript + (observation,))

    def decide(self) -> str:
        return hashlib.sha256(self.latent + b'next-action-v1').hexdigest()[:16]

    def chunks(self) -> dict[str, bytes]:
        transcript = json.dumps(self.transcript, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(transcript) > MAX_CHUNK:
            raise CapsuleError('fixture transcript exceeds chunk limit')
        return {'latent': self.latent, 'step': self.step.to_bytes(4, 'little'),
                'transcript': transcript}

    @classmethod
    def from_chunks(cls, chunks: dict[str, bytes]) -> ToySession:
        if set(chunks) != {'latent', 'step', 'transcript'} or len(chunks['latent']) != 8 or len(chunks['step']) != 4:
            raise CapsuleError('invalid fixture state')
        step=int.from_bytes(chunks['step'],'little')
        if len(chunks['transcript']) > MAX_CHUNK:
            raise CapsuleError('fixture transcript exceeds chunk limit')
        try:
            transcript = json.loads(chunks['transcript'].decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise CapsuleError('invalid fixture transcript') from exc
        if type(transcript) is not list or any(type(item) is not str for item in transcript):
            raise CapsuleError('fixture transcript must be a string list')
        try:
            for item in transcript:
                item.encode('utf-8')
        except UnicodeError as exc:
            raise CapsuleError('invalid fixture transcript Unicode') from exc
        if step != len(transcript):
            raise CapsuleError('history/step mismatch')
        return cls(step, chunks['latent'], tuple(transcript))
