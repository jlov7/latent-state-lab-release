"""Exercise installed correction-batch CLI against an independent exact restart."""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin', type=Path)
    parser.add_argument('--keep-work', type=Path, required=True)
    args = parser.parse_args()
    work = args.keep_work.resolve()
    bin_dir = args.bin.resolve()
    fixture = Path(__file__).resolve().parents[1] / 'examples/correction_batch.py'
    subprocess.run([sys.executable, str(fixture), str(work)], check=True, timeout=30)
    events = []
    state = work / 'billing.state'
    common = ['--id', 'run_id', '--input-format', 'csv', '--group-by', 'lab', '--value', 'cost']

    def run(command, expected=0):
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=30, check=False)
        events.append({'command': list(map(str, command)), 'returncode': result.returncode,
                           'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode == expected, events[-1]
        return json.loads(result.stdout) if result.stdout else None

    def patch(*extra, expected=0):
        return run([bin_dir / 'latent-state-patch', work / 'changes.jsonl', state,
                    *common, *extra], expected)

    def digest():
        return hashlib.sha256(state.read_bytes()).hexdigest()

    run([bin_dir / 'latent-state-sync', work / 'billing.csv', state, *common, '--apply'])
    original = digest()
    preview = patch('--details-limit', '2')
    assert digest() == original and preview['details_omitted'] == 1
    assert preview['changes'] == {'applicable': 3, 'already_present': 0, 'conflicts': 0}
    # Independent ordinary restart of a complete corrected export.
    groups = {}
    with (work / 'corrected.csv').open(newline='') as stream:
        for row in csv.DictReader(stream):
            count, total = groups.get(row['lab'], (0, Fraction(0)))
            groups[row['lab']] = count + 1, total + Fraction(Decimal(row['cost']))
    expected = {'records': sum(count for count, _ in groups.values()), 'groups': [
        {'key': key, 'count': count, 'sum': str(total), 'mean': str(total / count)}
        for key, (count, total) in sorted(groups.items())]}
    assert preview['after'] == expected
    applied = patch('--apply', '--expect-review', preview['review_sha256'])
    assert applied['store_written'] and applied['after'] == expected
    committed = digest()
    patch('--apply', '--expect-review', preview['review_sha256'], expected=2)
    assert digest() == committed
    replay = patch('--apply')
    assert replay['changes'] == {'applicable': 0, 'already_present': 3, 'conflicts': 0}
    assert not replay['store_written'] and digest() == committed
    # One applicable change and two conflicts must not partially commit.
    (work / 'changes.jsonl').write_text(
        '{"id":"c","before":{"group":"GPU","value":"-0.50"},"after":null}\n'
        '{"id":"a","before":null,"after":{"group":"CPU","value":"99"}}\n'
        '{"id":"d","before":null,"after":{"group":"GPU","value":"88"}}\n')
    conflicts = patch('--details-limit', '2')
    assert conflicts['changes'] == {'applicable': 1, 'already_present': 0, 'conflicts': 2}
    assert conflicts['after'] == conflicts['before']
    refused = patch('--apply', expected=2)
    assert refused['changes']['conflicts'] == 2 and digest() == committed
    (work / 'changes.jsonl').write_text('PRIVATE_BAD_RECORD\n')
    patch('--apply', expected=2)
    assert digest() == committed and 'PRIVATE_BAD_RECORD' not in events[-1]['stderr']
    report = {'status': 'pass', 'provenance': 'authored local software fixture',
                  'independent_restart': expected, 'preview': preview, 'applied': applied, 'replay': replay,
                  'conflict': conflicts, 'commands': events,
                  'limits': ['Whole state read/rewrite; no speed claim.',
                          'After-value replay only, no event identity deduplication.',
                          'Human usefulness and real-model continuation NOT RUN.']}
    (work / 'TASK-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'pass', 'report': str(work / 'TASK-RESULT.json')}))


if __name__ == '__main__':
    main()
