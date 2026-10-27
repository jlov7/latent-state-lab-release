"""CLI executable across real process restarts but operating ONLY on a toy fixture."""
import argparse
import json
import sys
import uuid
from pathlib import Path

from . import __version__
from .capsule import CapsuleError, read_capsule, write_capsule
from .fixture import PIN, ToySession


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version=f'latent-state-lab {__version__}')
    p.add_argument('mode', choices=['create','resume','uninterrupted'])
    p.add_argument('path', type=Path, nargs='?', help='capsule path (create/resume only)')
    p.add_argument('--overwrite', action='store_true',
                   help='replace an existing destination (create only)')
    args=p.parse_args()
    if args.mode != 'create' and args.overwrite:
        p.error('--overwrite requires create')
    if args.mode != 'uninterrupted' and args.path is None:
        p.error('path required')
    if args.mode == 'uninterrupted' and args.path is not None:
        p.error('uninterrupted does not accept a capsule path')
    try:
        if args.mode == 'create':
            s=ToySession.start().advance('read-only:alpha').advance('read-only:beta')
            write_capsule(args.path,PIN,s.chunks(),overwrite=args.overwrite)
            print(json.dumps({'status':'toy_capsule_written','state':s.decide()}))
        elif args.mode == 'resume':
            _, chunks=read_capsule(args.path,PIN)
            s=ToySession.from_chunks(chunks).advance('read-only:gamma')
            print(json.dumps({'status':'toy_resumed','step':s.step,'action':s.decide()}))
        else:
            s=ToySession.start()
            for obs in ('read-only:alpha','read-only:beta','read-only:gamma'):
                s=s.advance(obs)
            print(json.dumps({'status':'toy_uninterrupted','step':s.step,'action':s.decide()}))
    except (CapsuleError, OSError) as exc:
        if isinstance(exc, FileExistsError):
            code, message = 'destination_exists', 'destination exists; use --overwrite to replace it'
        elif isinstance(exc, FileNotFoundError):
            code, message = 'input_missing', 'capsule or parent directory is missing; check the path'
        elif isinstance(exc, CapsuleError):
            code, message = 'capsule_invalid', str(exc)
        else:
            code, message = 'io_error', 'cannot access capsule; check permissions and path type'
        print(json.dumps({'status': 'error', 'code': code, 'message': message,
                          'request_id': uuid.uuid4().hex}), file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
