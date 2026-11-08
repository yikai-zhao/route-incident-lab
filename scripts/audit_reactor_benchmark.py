#!/usr/bin/env python3
"""Offline paired poll/epoll benchmark evidence integrity audit, not performance proof."""
import argparse
import hashlib
import json
from pathlib import Path

def audit(directory):
    errors, verified, seen = [], [], set()
    try:
        report = json.loads((directory / 'benchmark.json').read_text())
        trials, order = report['trials'], report['workload_order']
        if not trials or not order: raise ValueError('empty experiment')
        for trial in trials:
            name, filename = trial['name'], trial['samples_file']
            if filename != name + '.jsonl' or Path(filename).name != filename:
                errors.append('noncanonical filename'); continue
            key = (trial['input']['name'], trial['pair'], trial['backend'])
            if key in seen: errors.append('duplicate trial')
            seen.add(key)
            content = (directory / filename).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != trial['samples_sha256']: errors.append(f'{filename}: SHA mismatch')
            rows = [json.loads(x) for x in content.splitlines() if x.strip()]
            s, n = trial['summary'], trial['input']['count']
            if len(rows) != n or s['attempts'] != n or [x['seq'] for x in rows] != list(range(n)):
                errors.append(f'{filename}: missing or reordered samples')
            ok = sum(x.get('ok') is True for x in rows)
            bad = sum(x.get('ok') is False for x in rows)
            if ok + bad != n or s['successes'] != ok or s['failures'] != bad:
                errors.append(f'{filename}: counts inconsistent')
            if s['backend'] != trial['backend'] or s['peak_inflight'] > trial['input']['concurrency']:
                errors.append(f'{filename}: backend or concurrency inconsistent')
            for row in rows:
                if (type(row.get('start_monotonic_ns')) is not int or
                    type(row.get('end_monotonic_ns')) is not int or
                    row['end_monotonic_ns'] < row['start_monotonic_ns']):
                    errors.append(f'{filename}: invalid timestamp'); break
                if (row['ok'] and row['error_errno'] != 0) or (not row['ok'] and row['error_errno'] == 0):
                    errors.append(f'{filename}: errno inconsistent'); break
            verified.append({'file':filename,'sha256':digest})
        expected = set()
        for pair in order:
            if pair['order'] not in (['poll','epoll'], ['epoll','poll']):
                errors.append('invalid randomized order')
            for backend in ('poll','epoll'):
                expected.add((pair['workload'], pair['pair'], backend))
        if seen != expected or len(trials) != len(expected):
            errors.append('incomplete paired design')
        groups = {}
        for trial in trials:
            groups.setdefault((trial['input']['name'],trial['pair']), []).append(trial)
        for key, pair in groups.items():
            if len(pair) != 2 or pair[0]['input'] != pair[1]['input']:
                errors.append(f'{key}: mismatched workload')
    except (OSError, ValueError, KeyError, TypeError) as e:
        errors.append(f'invalid evidence: {e}')
    return {'status':'FAIL' if errors else 'PASS','errors':errors,
            'verified_trials':len(verified),
            'scope':'internal consistency only, not benchmark fairness or performance winner'}

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    a=p.parse_args()
    result=audit(a.directory)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 1)
