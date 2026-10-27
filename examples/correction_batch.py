"""Write a portable authored patch-v1 fixture into a new local directory."""
import json
import sys
from pathlib import Path


def write_fixture(root):
    root.mkdir(parents=True, exist_ok=False)
    (root / 'billing.csv').write_text(
        'run_id,lab,cost\na,CPU,0.10\nb,CPU,0.20\nc,GPU,-0.50\n')
    changes = [
        {'id': 'a', 'before': {'group': 'CPU', 'value': '0.10'},
         'after': {'group': 'GPU', 'value': '0.15'}},
        {'id': 'b', 'before': {'group': 'CPU', 'value': '0.20'}, 'after': None},
        {'id': 'd', 'before': None, 'after': {'group': 'CPU', 'value': '0.25'}},
    ]
    (root / 'changes.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in changes))
    (root / 'corrected.csv').write_text(
        'run_id,lab,cost\na,GPU,0.15\nc,GPU,-0.50\nd,CPU,0.25\n')


if __name__ == '__main__':
    write_fixture(Path(sys.argv[1]))
