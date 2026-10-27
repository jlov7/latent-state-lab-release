"""Untrained CPU attention/continuous-input probe, NOT the HybridAgent research model.

Uses real PyTorch KV tensors and a dedicated sampler RNG. No downloaded weights,
no tool executor, no network, no arbitrary code loading and no pickle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import torch
from safetensors.torch import load, save

from .capsule import CapsuleError, read_capsule, write_capsule

WIDTH, VOCAB, MAX_STEPS = 8, 17, 256


def descriptor() -> dict:
    return {'model_revision': 'UNTRAINED-attention-probe-v1-seed123',
            'tokenizer_revision': 'integer-vocabulary-17-not-language',
            'runtime_revision': f'torch-{torch.__version__}-probe-v1',
            'dtype': 'float32', 'device': 'cpu',
            'capture_point': 'completed_step', 'pending_tool': False}


def json_object(blob: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CapsuleError('duplicate metadata field')
            result[key] = value
        return result
    def invalid_constant(_):
        raise CapsuleError('non-finite metadata')
    if len(blob) > 128 * 1024:
        raise CapsuleError('metadata exceeds limit')
    try:
        result = json.loads(blob, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise CapsuleError('invalid metadata') from exc
    if type(result) is not dict:
        raise CapsuleError('metadata must be an object')
    return result


class Probe:
    def __init__(self, seed: int = 7):
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise CapsuleError('invalid sampler seed')
        weights = torch.Generator(device='cpu').manual_seed(123)
        self.embed = torch.randn(VOCAB, WIDTH, generator=weights, dtype=torch.float32, device='cpu') / 3
        self.qw, self.kw, self.vw, self.link = [
            torch.randn(WIDTH, WIDTH, generator=weights, dtype=torch.float32, device='cpu') / 3 for _ in range(4)]
        self.head = torch.randn(WIDTH, VOCAB, generator=weights, dtype=torch.float32, device='cpu') / 3
        self.generator = torch.Generator(device='cpu').manual_seed(seed)
        self.seed, self.position, self.last_token = seed, 0, 0
        self.k = torch.empty(0, WIDTH, dtype=torch.float32, device='cpu')
        self.v = torch.empty(0, WIDTH, dtype=torch.float32, device='cpu')
        self.hidden = torch.zeros(WIDTH, dtype=torch.float32, device='cpu')
        self.tape = torch.empty(0, WIDTH, dtype=torch.float32, device='cpu')
        self.modes, self.outputs = [], []
        self.pending_tool = False

    @torch.autocast(device_type='cpu', enabled=False)
    def _consume(self, raw: torch.Tensor, mode: str) -> int:
        if self.position >= MAX_STEPS:
            raise CapsuleError('probe step cap reached')
        x = raw + torch.sin(torch.arange(WIDTH, dtype=torch.float32, device='cpu') * 0.01 + self.position * 0.1)
        q = x @ self.qw
        self.k = torch.cat([self.k, (x @ self.kw).unsqueeze(0)])
        self.v = torch.cat([self.v, (x @ self.vw).unsqueeze(0)])
        attention = torch.softmax(q @ self.k.T / WIDTH**0.5, dim=-1)
        self.hidden = torch.tanh(attention @ self.v + x)
        probabilities = torch.softmax(self.hidden @ self.head, dim=-1)
        token = int(torch.multinomial(probabilities, 1, generator=self.generator).item())
        self.tape = torch.cat([self.tape, raw.clone().unsqueeze(0)])
        self.modes.append(mode)
        self.outputs.append(token)
        self.position += 1
        self.last_token = token
        return token

    @torch.autocast(device_type='cpu', enabled=False)
    def step(self, mode: str = 'text') -> int:
        if mode not in ('text', 'latent'):
            raise CapsuleError('only text or latent numerical steps are supported')
        raw = self.embed[self.last_token] if mode == 'text' else torch.tanh(self.hidden @ self.link)
        return self._consume(raw, mode)

    def tensors(self) -> dict:
        return {'k': self.k.contiguous(), 'v': self.v.contiguous(),
                'hidden': self.hidden.contiguous(), 'rng': self.generator.get_state(),
                'tape': self.tape.contiguous()}

    def metadata(self) -> dict:
        return {'seed': self.seed, 'position': self.position, 'last_token': self.last_token,
                'modes': self.modes, 'outputs': self.outputs}

    def signature(self) -> str:
        # Per-name hashes avoid reliance on the serializer's metadata/key ordering.
        digest = hashlib.sha256(json.dumps(self.metadata(), sort_keys=True).encode())
        for name, tensor in sorted(self.tensors().items()):
            digest.update(name.encode())
            digest.update(str(tensor.dtype).encode())
            digest.update(str(tuple(tensor.shape)).encode())
            digest.update(tensor.numpy().tobytes())
        return digest.hexdigest()

    def snapshot(self, path: Path) -> None:
        pin = dict(descriptor(), pending_tool=self.pending_tool)
        metadata = json.dumps(self.metadata(), sort_keys=True, allow_nan=False).encode()
        write_capsule(path, pin, {'state': save(self.tensors()), 'metadata': metadata})

    @classmethod
    def load(cls, path: Path, arm: str = 'restore') -> Probe:
        if arm not in ('restore', 'replay', 'text'):
            raise CapsuleError('unknown restoration arm')
        _, chunks = read_capsule(path, descriptor())
        if set(chunks) != {'state', 'metadata'}:
            raise CapsuleError('missing or unexpected state files')
        meta = json_object(chunks['metadata'])
        if set(meta) != {'seed', 'position', 'last_token', 'modes', 'outputs'}:
            raise CapsuleError('missing metadata fields')
        pos = meta['position']
        if type(pos) is not int or not 0 <= pos <= MAX_STEPS:
            raise CapsuleError('invalid position')
        if (type(meta['last_token']) is not int or not 0 <= meta['last_token'] < VOCAB or
                type(meta['modes']) is not list or len(meta['modes']) != pos or
                any(m not in ('text', 'latent') for m in meta['modes']) or
                type(meta['outputs']) is not list or len(meta['outputs']) != pos or
                any(type(t) is not int or not 0 <= t < VOCAB for t in meta['outputs']) or
                meta['last_token'] != (meta['outputs'][-1] if pos else 0)):
            raise CapsuleError('inconsistent history')
        result = cls(meta['seed'])
        try:
            tensors = load(chunks['state'])
        except Exception as exc:
            raise CapsuleError('invalid safetensors data') from exc
        expected = {'k': ((pos, WIDTH), torch.float32), 'v': ((pos, WIDTH), torch.float32),
                    'hidden': ((WIDTH,), torch.float32), 'tape': ((pos, WIDTH), torch.float32),
                    'rng': (tuple(result.generator.get_state().shape), torch.uint8)}
        if set(tensors) != set(expected):
            raise CapsuleError('missing or unexpected tensor')
        for name, (shape, dtype) in expected.items():
            t = tensors[name]
            if tuple(t.shape) != shape or t.dtype != dtype or not bool(torch.isfinite(t).all()):
                raise CapsuleError(f'tensor contract mismatch: {name}')
        if arm == 'restore':
            try:
                result.generator.set_state(tensors['rng'])
            except RuntimeError as exc:
                raise CapsuleError('invalid RNG state') from exc
            result.k, result.v, result.hidden, result.tape = [
                tensors[name].clone() for name in ('k', 'v', 'hidden', 'tape')]
            result.position, result.last_token = pos, meta['last_token']
            result.modes, result.outputs = list(meta['modes']), list(meta['outputs'])
        elif arm == 'replay':
            for i, raw in enumerate(tensors['tape']):
                if result._consume(raw, meta['modes'][i]) != meta['outputs'][i]:
                    raise CapsuleError('tape reconstruction diverged from recorded output')
        else:
            # Explicit lossy control: feed visible text tokens only, dropping latent inputs.
            for mode, token in zip(meta['modes'], meta['outputs']):
                if mode == 'text':
                    result.step('text')
                    # Teacher-force the recorded visible token for the next text input.
                    # Pure text must reconstruct exactly; only latent steps are removed.
                    result.outputs[-1] = token
                    result.last_token = token
        return result


def compare() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'session.lsc'
        source = Probe(7)
        for i in range(8):
            source.step('latent' if i % 3 == 1 else 'text')
        source.snapshot(path)
        rows = {}
        for arm in ('uninterrupted', 'restore', 'replay', 'text'):
            result = subprocess.run([sys.executable, '-m', 'latent_state_lab.tensor_probe',
                                     '--arm', arm, '--capsule', str(path), '--steps', '12'],
                                    capture_output=True, text=True, timeout=30, check=True)
            rows[arm] = json.loads(result.stdout)
        baseline = rows['uninterrupted']
        for row in rows.values():
            row['exact_state_match'] = row['signature'] == baseline['signature']
            row['token_matches'] = sum(a == b for a, b in zip(row['tokens'], baseline['tokens']))
        return {'scope': 'UNTRAINED numerical probe; no language or HybridAgent result.',
                'capsule_bytes': path.stat().st_size, 'descriptor': descriptor(), 'arms': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arm', choices=('uninterrupted', 'restore', 'replay', 'text'))
    parser.add_argument('--capsule', type=Path)
    parser.add_argument('--steps', type=int, default=12)
    args = parser.parse_args()
    if args.arm is None:
        print(json.dumps(compare(), indent=2))
        return
    if args.capsule is None or not 0 <= args.steps <= 128:
        parser.error('an arm requires a capsule path and 0..128 continuation steps')
    start = time.perf_counter_ns()
    if args.arm == 'uninterrupted':
        session = Probe(7)
        for i in range(8):
            session.step('latent' if i % 3 == 1 else 'text')
    else:
        session = Probe.load(args.capsule, args.arm)
    restore_ns = time.perf_counter_ns() - start
    tokens = [session.step('latent' if i % 3 == 1 else 'text') for i in range(args.steps)]
    print(json.dumps({'arm': args.arm, 'signature': session.signature(),
                      'tokens': tokens, 'position': session.position, 'prepare_ns': restore_ns}))


if __name__ == '__main__':
    main()
