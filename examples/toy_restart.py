"""Two fresh CLI processes must agree with uninterrupted authored toy execution."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args):
    result = subprocess.run([sys.executable, '-m', 'latent_state_lab.demo', *map(str, args)],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        capsule = Path(tmp) / 'toy.zip'
        run('create', capsule)
        resumed = run('resume', capsule)
        baseline = run('uninterrupted')
        if resumed['action'] != baseline['action'] or resumed['step'] != 3:
            raise RuntimeError('toy continuation differs')
        print(json.dumps({'scope': 'authored toy fixture', 'match': True,
                          'action': resumed['action'], 'step': resumed['step']}))


if __name__ == '__main__':
    main()
