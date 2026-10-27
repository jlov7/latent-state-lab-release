"""Compare a guarded CSV replacement with exact script and SQLite alternatives.

Synthetic software journey, not a study of human effort or a performance claim.
Run with the installed package's Python; provide a new owned output directory.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

INITIAL = [('meter-a', 'CPU', '0.10'), ('meter-b', 'GPU', '0.20'),
           ('meter-c', 'GPU', '-0.05'), ('meter-d', 'CPU', '9007199254740993.01')]
UPDATED = [('meter-a', 'GPU', '0.15'), ('meter-c', 'GPU', '-0.05'),
           ('meter-d', 'CPU', '9007199254740993.01'), ('meter-e', 'CPU', '0.25')]


def exact_groups(rows):
    groups = {}
    for _, group, amount in rows:
        count, total = groups.get(group, (0, Fraction(0)))
        groups[group] = count + 1, total + Fraction(Decimal(amount))
    return [{'key': key, 'count': count, 'sum': str(total), 'mean': str(total / count)}
            for key, (count, total) in sorted(groups.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    source, store = root / 'snapshot.csv', root / 'selected.state'
    events = []

    def write(rows):
        with source.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['id', 'team', 'amount'])
            writer.writerows(rows)

    def invoke(extra=(), expected=0):
        command = [sys.executable, '-m', 'latent_state_lab.sync', str(source), str(store),
                   '--input-format', 'csv', '--id', 'id', '--group-by', 'team', '--value', 'amount',
                   *extra]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == expected, (result.returncode, result.stderr)
        report = json.loads(result.stdout)
        events.append({'step': len(events) + 1, 'arguments': list(extra),
                       'exit_code': result.returncode, 'report': report})
        return report

    write(INITIAL)
    initial = invoke(('--apply', '--max-deletions', '0'))
    assert initial['after']['groups'] == exact_groups(INITIAL)
    saved = store.read_bytes()
    write(INITIAL[:1])  # Deliberately filtered export, not a complete snapshot.
    blocked = invoke(('--apply', '--max-deletions', '0'), expected=2)
    assert blocked['deletion_guard'] == {'maximum': 0, 'proposed': 3, 'passed': False}
    assert not blocked['applied'] and not blocked['store_written']
    assert store.read_bytes() == saved

    # A short exact script can implement the same guard; retain that tie.
    script_rows = {key: (group, amount) for key, group, amount in INITIAL}
    incoming = {key: (group, amount) for key, group, amount in INITIAL[:1]}
    script_refused = len(script_rows.keys() - incoming.keys()) > 0
    assert script_refused and len(script_rows) == 4

    # SQLite stores decimals as TEXT and sums with Fraction, not binary REAL.
    database = sqlite3.connect(root / 'ordinary.sqlite')
    database.execute('CREATE TABLE selected(id TEXT PRIMARY KEY, team TEXT, amount TEXT)')
    database.executemany('INSERT INTO selected VALUES (?,?,?)', INITIAL)
    database.commit()
    database.execute('CREATE TEMP TABLE incoming(id TEXT PRIMARY KEY, team TEXT, amount TEXT)')
    database.executemany('INSERT INTO incoming VALUES (?,?,?)', INITIAL[:1])
    deletions = database.execute('SELECT count(*) FROM selected WHERE id NOT IN (SELECT id FROM incoming)').fetchone()[0]
    database.rollback()
    assert deletions == 3
    assert exact_groups(database.execute('SELECT * FROM selected').fetchall()) == exact_groups(INITIAL)

    write(UPDATED)
    preview = invoke(('--max-deletions', '1', '--details-limit', '4'))
    applied = invoke(('--apply', '--max-deletions', '1', '--expect-review', preview['review_sha256']))
    expected = exact_groups(UPDATED)
    assert applied['after']['groups'] == expected
    assert applied['changes'] == {'added': 1, 'updated': 1, 'deleted': 1, 'unchanged': 2}
    committed = store.read_bytes()
    repeated = invoke(('--apply', '--max-deletions', '0'))
    assert not repeated['store_written'] and store.read_bytes() == committed
    with source.open(newline='', encoding='utf-8') as stream:
        script_result = exact_groups([(row['id'], row['team'], row['amount']) for row in csv.DictReader(stream)])
    with database:
        database.execute('DELETE FROM selected')
        database.executemany('INSERT INTO selected VALUES (?,?,?)', UPDATED)
    sqlite_result = exact_groups(database.execute('SELECT * FROM selected').fetchall())
    database.close()
    assert expected == script_result == sqlite_result
    receipt = {'status': 'PASS', 'fixture': 'authored CSV replacement with filtered-export hazard',
               'before': 'omitted IDs are deletions; no optional ceiling was available',
               'after': 'explicit ceiling refused 3 deletions; reviewed 1-deletion replacement applied',
               'script_guard': 'same refusal possible with explicit code',
               'sqlite_guard': 'same refusal possible within an explicit transaction',
               'exact_output': expected, 'all_three_outputs_equal': True,
               'input_digest': hashlib.sha256(json.dumps([INITIAL, UPDATED]).encode()).hexdigest(),
               'commands': events,
               'limits': ['No speed advantage measured.', 'No human-effort or AI checkpoint result.',
                          'SQLite and a short exact script remain competent alternatives.']}
    (root / 'RESULT.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
