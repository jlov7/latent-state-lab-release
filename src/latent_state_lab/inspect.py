"""Read-only inspection of a batch checkpoint without source validation."""
import argparse
import json
import sys
import uuid
from pathlib import Path

from . import __version__
from .batch import BatchError, inspect_checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'latent-state-lab {__version__}')
    parser.add_argument('checkpoint', type=Path)
    args = parser.parse_args()
    try:
        report = inspect_checkpoint(args.checkpoint)
    except (BatchError, OSError):
        sys.stderr.write(json.dumps({'status': 'error', 'code': 'inspect_failed',
            'message': 'cannot read a valid compatible batch checkpoint',
            'request_id': uuid.uuid4().hex}) + '\n')
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=True) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
