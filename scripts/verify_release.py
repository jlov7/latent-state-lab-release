"""Build an sdist and its wheel, then exercise isolated installed first use.

Run with uv run --no-project python scripts/verify_release.py.
Requires uv on PATH. Only public build tools are fetched; no model or provider is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
PUBLIC_FILES = {
    'README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'LICENSE', 'CITATION.cff', 'pyproject.toml', 'MANIFEST.in',
    'requirements-build.txt', 'requirements-tensor-probe.txt', 'requirements-dev.txt',
    'docs/images/workflow.svg', 'docs/VALIDATION.md',
    *('docs/' + name + '.md' for name in
      ('USAGE', 'DESIGN', 'EVALUATION', 'LIMITATIONS', 'REFERENCES', 'STABILITY', 'RELEASING')),
}


def allowed(name: str) -> bool:
    parts = Path(name).parts
    return (name in PUBLIC_FILES or name in {'PKG-INFO', 'setup.cfg'} or
            (len(parts) == 2 and parts[0] in {'tests', 'examples', 'scripts'} and
             parts[1].endswith('.py')) or
            (len(parts) == 3 and parts[:2] == ('src', 'latent_state_lab') and
             parts[2].endswith('.py')) or
            (len(parts) == 3 and parts[:2] == ('src', 'latent_state_lab.egg-info') and
             parts[2] in {'PKG-INFO', 'SOURCES.txt', 'dependency_links.txt',
                          'entry_points.txt', 'top_level.txt'}))


def run(args, cwd, env):
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, timeout=300, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep-work', type=Path, help='retain tested artifacts in a new directory')
    args = parser.parse_args()
    scratch = ROOT / '.release-scratch'
    scratch.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.setdefault('UV_CACHE_DIR', str(scratch / 'uv-cache'))
    env['UV_PYTHON_DOWNLOADS'] = 'never'
    if args.keep_work:
        args.keep_work.mkdir(parents=True, exist_ok=False)
    context = (nullcontext(str(args.keep_work.resolve())) if args.keep_work else
               tempfile.TemporaryDirectory(prefix='package-', dir=scratch))
    with context as tmp:
        work = Path(tmp)
        dist = work / 'dist'
        run(['uv', 'build', '--sdist', '--python', sys.executable,
             '--build-constraints', ROOT / 'requirements-build.txt', '--out-dir', dist],
            ROOT, env)
        sdist, = dist.glob('*.tar.gz')
        with tarfile.open(sdist) as archive:
            members = archive.getmembers()
            files = sorted('/'.join(Path(m.name).parts[1:]) for m in members if m.isfile())
            if any(not allowed(name) for name in files):
                raise RuntimeError('unexpected sdist file')
            required = PUBLIC_FILES | {'examples/toy_restart.py', 'scripts/verify_release.py',
                                       'tests/test_capsule.py', 'tests/test_demo.py',
                                       'tests/test_tensor_probe.py', 'tests/test_batch.py',
                                       'scripts/verify_batch.py', 'scripts/verify_batch_additions.py',
                                       'tests/test_batch_additions.py', 'tests/test_batch_feed.py',
                                       'scripts/verify_batch_feed.py', 'tests/test_sync.py',
                                       'scripts/verify_sync.py', 'tests/test_patch.py',
                                       'scripts/verify_patch.py', 'examples/correction_batch.py',
                                       'examples/guarded_snapshot.py',
                                       'tests/test_snapshot_deletion_guard.py',
                                       'tests/test_cli_contract.py', 'scripts/verify_docs.py',
                                       'scripts/verify_quickstart.py'}
            if not required <= set(files):
                raise RuntimeError('sdist missing reproduction resources')
            archive.extractall(work / 'source', filter='data')
        source, = (work / 'source').iterdir()
        run(['uv', 'build', '--wheel', '--python', sys.executable,
             '--build-constraints', source / 'requirements-build.txt', '--out-dir', dist],
            source, env)
        wheel, = dist.glob('*.whl')
        with zipfile.ZipFile(wheel) as archive:
            wheel_files = sorted(archive.namelist())
            for name in wheel_files:
                parts = Path(name).parts
                if len(parts) == 3 and parts[0].endswith('.dist-info') and parts[1:] == ('licenses', 'LICENSE'):
                    continue
                if (len(parts) != 2 or
                        not (parts[0] == 'latent_state_lab' and parts[1].endswith('.py') or
                             parts[0].endswith('.dist-info') and
                             parts[1] in {'METADATA', 'WHEEL', 'RECORD', 'entry_points.txt',
                                          'top_level.txt'})):
                    raise RuntimeError('unexpected wheel file')
            metadata = archive.read(f'latent_state_lab-{VERSION}.dist-info/METADATA').decode()
            if 'Description-Content-Type: text/markdown' not in metadata:
                raise RuntimeError('wheel lacks README metadata')
            if 'License-File: LICENSE-STATUS' in metadata:
                raise RuntimeError('private rights note leaked into metadata')
            if 'License-Expression: MIT' not in metadata or 'License-File: LICENSE' not in metadata:
                raise RuntimeError('wheel lacks approved licence metadata')
        installed = work / 'installed'
        run(['uv', 'venv', '--python', sys.executable, installed], work, env)
        python = installed / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run(['uv', 'pip', 'install', '--python', python, '--no-deps', wheel], work, env)
        outside = work / 'outside'
        outside.mkdir()
        run([python, '-c', 'import latent_state_lab; from pathlib import Path; '
             'assert Path(latent_state_lab.__file__).is_relative_to(Path(' +
             repr(str(installed)) + ')), latent_state_lab.__file__'], outside, env)
        run([python, '-m', 'unittest', 'discover', '-s', source / 'tests', '-v'], outside, env)
        run([python, source / 'examples/toy_restart.py'], outside, env)
        run([python, source / 'examples/guarded_snapshot.py', outside / 'guarded-verification'],
            outside, env)
        cli = installed / ('Scripts/latent-state-lab.exe' if os.name == 'nt'
                           else 'bin/latent-state-lab')
        run([cli, 'create', outside / 'toy.zip'], outside, env)
        run([cli, 'resume', outside / 'toy.zip'], outside, env)
        run([cli, 'uninterrupted'], outside, env)
        batch_cli = cli.with_name('latent-state-batch')
        run([python, source / 'scripts/verify_batch.py', batch_cli], outside, env)
        run([python, source / 'scripts/verify_batch_additions.py', installed / 'bin'], outside, env)
        run([python, source / 'scripts/verify_batch_feed.py', installed / 'bin',
             '--keep-work', outside / 'feed-verification'], outside, env)
        run([python, source / 'scripts/verify_sync.py', installed / 'bin',
             '--keep-work', outside / 'sync-verification'], outside, env)
        run([python, source / 'scripts/verify_patch.py', installed / 'bin',
             '--keep-work', outside / 'patch-verification'], outside, env)
        run([python, source / 'scripts/verify_docs.py'], outside, env)
        run([python, source / 'scripts/verify_quickstart.py', installed / 'bin',
             '--keep-work', outside / 'readme-verification'], outside, env)
        report = {'status': 'pass', 'python': sys.version, 'sdist_files': files,
                  'wheel_files': wheel_files, 'sha256': {
                      p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (sdist, wheel)},
                  'optional_tensor': 'NOT RUN in isolated dependency-free wheel environment'}
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
