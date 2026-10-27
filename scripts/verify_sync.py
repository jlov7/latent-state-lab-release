"""Fresh-process full-snapshot correction cycle and independent conventional controls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import signal
import sqlite3
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['run_id', 'lab', 'cost', 'ignored'])
        for key, group, units in rows:
            sign = '-' if units < 0 else ''
            whole, part = divmod(abs(units), 10000)
            writer.writerow([key, group, f'{sign}{whole}.{part:04d}', 'synthetic'])


def read_csv(path):
    with path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    return [(r['run_id'], r['lab'], int(Fraction(r['cost']) * 10000)) for r in rows]


def groups(totals):
    return [{'key': group, 'count': count, 'sum': str(Fraction(units, 10000)),
             'mean': str(Fraction(units, 10000 * count))}
            for group, count, units in sorted(totals)]


def baseline(source, db=None):
    rows = read_csv(source)
    if db is not None:
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, g TEXT, u INTEGER)')
            conn.execute('CREATE TEMP TABLE incoming (id TEXT PRIMARY KEY, g TEXT, u INTEGER)')
            conn.executemany('INSERT INTO incoming VALUES (?, ?, ?)', rows)
            counts = dict(zip(('added', 'updated', 'deleted', 'unchanged'), [conn.execute(q).fetchone()[0]
                for q in (
                    'SELECT count(*) FROM incoming i LEFT JOIN records r USING(id) WHERE r.id IS NULL',
                    'SELECT count(*) FROM incoming i JOIN records r USING(id) WHERE i.g!=r.g OR i.u!=r.u',
                    'SELECT count(*) FROM records r LEFT JOIN incoming i USING(id) WHERE i.id IS NULL',
                    'SELECT count(*) FROM incoming i JOIN records r USING(id) WHERE i.g=r.g AND i.u=r.u')]))
            conn.execute('DELETE FROM records WHERE id NOT IN (SELECT id FROM incoming)')
            conn.execute('INSERT INTO records SELECT * FROM incoming WHERE true '
                         'ON CONFLICT(id) DO UPDATE SET g=excluded.g,u=excluded.u '
                         'WHERE records.g!=excluded.g OR records.u!=excluded.u')
            result = groups(conn.execute('SELECT g,count(*),sum(u) FROM records GROUP BY g').fetchall())
        return {'records': len(rows), 'groups': result, 'changes': counts}
    totals = {}
    for _, group, units in rows:
        count, total = totals.get(group, (0, 0))
        totals[group] = (count + 1, total + units)
    return {'records': len(rows), 'groups': groups([(g, *v) for g, v in totals.items()])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin_dir', type=Path)
    parser.add_argument('--keep-work', type=Path, required=True)
    args = parser.parse_args()
    work = args.keep_work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    cli = args.bin_dir.resolve() / 'latent-state-sync'
    python = args.bin_dir.resolve() / 'python'
    source, store = work / 'billing.csv', work / 'billing.state'
    records = [(f'run-{i:05d}' + ('\n再試行' if i % 83 == 0 else ''),
                ['GPU,東京', 'CPU', 'Archive', 'Research'][i % 4],
                (-1 if i % 19 == 0 else 1) * ((i * 7717) % 700019)) for i in range(3119)]
    revised = [(key, ('Corrections' if i < 70 else group), units + (2251 if i < 104 else 0))
               for i, (key, group, units) in enumerate(records) if i >= 47]
    revised += [(f'late-{i:04d}', 'CPU' if i % 2 else 'GPU,東京', i * 1301) for i in range(83)]
    fixtures = work / 'fixtures'
    fixtures.mkdir()
    write_csv(fixtures / 'initial.csv', records)
    write_csv(fixtures / 'revised.csv', revised)
    write_csv(fixtures / 'reordered.csv', list(reversed(revised)))
    frozen = {'kind': 'authored synthetic local billing snapshots, fixed before CLI execution',
              'rows': [len(records), len(revised), len(revised)],
              'expected_revised_changes': {'added': 83, 'updated': 57, 'deleted': 47,
                                           'unchanged': 3015},
              'fixtures_sha256': {p.name: digest(p) for p in fixtures.iterdir()}}
    (work / 'FROZEN-TASK.json').write_text(json.dumps(frozen, indent=2) + '\n')
    events = []

    def run(extra=(), *, success=True):
        command = [cli, source, store, '--id', 'run_id', '--input-format', 'csv',
                   '--group-by', 'lab', '--value', 'cost', *extra]
        started = time.perf_counter_ns()
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=30, check=False)
        event = {'command': list(map(str, command)), 'returncode': result.returncode,
                 'elapsed_ns': time.perf_counter_ns() - started, 'stdout': result.stdout,
                 'stderr': result.stderr}
        events.append(event)
        assert result.returncode == (0 if success else 2), event
        if not success:
            error = json.loads(result.stderr)
            assert error['request_id'] and error['code'] == 'sync_failed'
            assert 'PRIVATE' not in result.stderr and str(work) not in result.stderr
            return error
        return json.loads(result.stdout)

    stages = []
    for name in ('initial', 'revised', 'reordered'):
        source.write_bytes((fixtures / f'{name}.csv').read_bytes())
        previous = digest(store) if store.exists() else None
        preview = run(['--details-limit', '3'])
        assert (digest(store) if store.exists() else None) == previous
        controls = []
        for kind in ('restart', 'sqlite'):
            command = [python, Path(__file__).resolve(), '--baseline', source]
            if kind == 'sqlite':
                command.append(work / 'simple.sqlite')
            started = time.perf_counter_ns()
            control = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=30, check=False)
            assert control.returncode == 0, control.stderr
            answer = json.loads(control.stdout)
            assert preview['after'] == {key: answer[key] for key in ('records', 'groups')}
            if kind == 'sqlite':
                assert preview['changes'] == answer['changes']
            controls.append({'kind': kind, 'elapsed_ns': time.perf_counter_ns() - started,
                             'output': answer})
        applied = run(['--apply'])
        assert applied['after'] == preview['after'] and applied['changes'] == preview['changes']
        if name == 'revised':
            assert applied['changes'] == frozen['expected_revised_changes']
        if name == 'reordered':
            assert not applied['store_written']
        stages.append({'name': name, 'preview': preview, 'apply': applied, 'controls': controls})
    unchanged = digest(store)
    failures = []
    good = source.read_bytes()
    for name, suffix in (
            ('duplicate', b'late-0000,CPU,1.0000,PRIVATE_DUPLICATE\r\n'),
            ('bad-decimal', b'PRIVATE_ID,CPU,PRIVATE_VALUE,late\r\n'),
            ('missing-id', b',CPU,1.0000,PRIVATE_MISSING\r\n')):
        source.write_bytes(good + suffix)
        failures.append({'case': name, 'error': run(['--apply'], success=False)})
        assert digest(store) == unchanged
    source.write_bytes(good)
    failures.append({'case': 'config', 'error': run(['--id', 'lab', '--apply'], success=False)})
    assert digest(store) == unchanged
    failures.append({'case': 'row-limit', 'error': run(['--max-rows', '3000', '--apply'], success=False)})
    assert digest(store) == unchanged
    foreign = work / 'foreign.state'
    foreign.write_text('PRIVATE_FOREIGN')
    foreign.chmod(0o600)
    saved_store = store
    store = foreign
    run(['--apply'], success=False)
    assert foreign.read_text() == 'PRIVATE_FOREIGN'
    store = saved_store
    # Kill an actual installed interpreter immediately before/after atomic replacement.
    interruption = []
    write_csv(source, revised + [('after-kill', 'CPU', 12500)])
    worker = '''import os,sys,time
from pathlib import Path
from latent_state_lab import sync
original=sync.os.replace
marker=Path(sys.argv[3])
def boundary(src,dst):
    if sys.argv[4]=='after': original(src,dst)
    marker.write_text('ready')
    time.sleep(60)
    if sys.argv[4]=='before': original(src,dst)
sync.os.replace=boundary
sync.reconcile(Path(sys.argv[1]),Path(sys.argv[2]),id_field='run_id',input_format='csv',group_by='lab',value='cost',apply=True)
'''
    for boundary in ('before', 'after'):
        marker = work / f'kill-{boundary}.ready'
        proc = subprocess.Popen([str(python), '-c', worker, str(source), str(store),
                                 str(marker), boundary], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 15
            while not marker.exists() and proc.poll() is None and time.monotonic() < end:
                time.sleep(0.01)
            assert marker.exists(), 'worker did not reach commit boundary'
            proc.send_signal(signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=10)
            assert proc.returncode == -signal.SIGKILL, (stdout, stderr)
            if boundary == 'before':
                assert digest(store) == unchanged
            observed = run()
            if boundary == 'before':
                assert observed['changes']['added'] == 1
            else:
                assert observed['changes']['unchanged'] == len(revised) + 1
            interruption.append({'boundary': boundary, 'returncode': proc.returncode,
                                 'preview': observed, 'orphan_temporaries':
                                 [p.name for p in work.glob('.sync-*')]})
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
    final = run(['--apply'])
    assert final['after'] == baseline(source)
    initial_interruption = []
    existing_store = store
    for boundary in ('before', 'after'):
        store = work / f'initial-{boundary}.state'
        marker = work / f'initial-kill-{boundary}.ready'
        proc = subprocess.Popen([str(python), '-c', worker, str(source), str(store),
                                 str(marker), boundary], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 15
            while not marker.exists() and proc.poll() is None and time.monotonic() < end:
                time.sleep(0.01)
            assert marker.exists(), 'initial worker did not reach commit boundary'
            proc.send_signal(signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=10)
            assert proc.returncode == -signal.SIGKILL, (stdout, stderr)
            # Ordinary CLI preview/apply must recover without first removing any orphan.
            observed = run()
            if boundary == 'before':
                assert not store.exists() and observed['changes']['added'] == len(revised) + 1
            else:
                assert store.stat().st_nlink == 1
                assert observed['changes']['unchanged'] == len(revised) + 1
            recovered = run(['--apply'])
            assert recovered['after'] == final['after']
            initial_interruption.append({'boundary': boundary, 'returncode': proc.returncode,
                                         'preview': observed, 'recovered': recovered,
                                         'recovered_without_orphan_cleanup': True,
                                         'orphan_temporaries':
                                         [p.name for p in work.glob('.sync-*')]})
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
    store = existing_store
    orphans = list(work.glob('.sync-*'))
    for path in orphans:
        path.unlink()
    report = {'status': 'pass', 'provenance': 'authored synthetic, agent-operated local checks',
              'stages': stages, 'failures_preserve_state': failures,
              'foreign_store_refused_unchanged': True, 'interruption': interruption,
              'initial_interruption': initial_interruption,
              'orphan_temporaries_removed_after_workers_stopped': len(orphans),
              'final': final, 'commands': events,
              'limits': ['Every arm scans a full snapshot. Changed sync state is fully rewritten.',
                         'One local run includes CLI/interpreter overhead; no speed superiority.',
                         'Direct SQLite supports corrections with ordinary SQL.',
                         'No human-usefulness or real-model experiment.']}
    (work / 'TASK-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'pass', 'stages': 3, 'rows': frozen['rows'],
                      'failures_preserve_state': len(failures) + 1,
                      'sigkill_boundaries': 4, 'report': str(work / 'TASK-RESULT.json')}))


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--baseline':
        print(json.dumps(baseline(Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)))
    else:
        main()
