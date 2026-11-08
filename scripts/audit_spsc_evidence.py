#!/usr/bin/env python3
"""Read-only SPSC evidence consistency audit. Not publisher authentication."""
import argparse
import hashlib
import json
from pathlib import Path

def audit(folder: Path) -> dict:
    failures = []
    audited = []
    try:
        report = json.loads((folder / 'report.json').read_text())
        trials = [report['baseline'], *report['pressured_repetitions']]
        if len(trials) != 4:
            failures.append('expected four trials')
        if report.get('status') != 'PASS' or not all(report.get('checks', {}).values()):
            failures.append('experiment self-report not PASS')
        for i, trial in enumerate(trials):
            name = 'baseline.jsonl' if i == 0 else f'backpressure-{i}.jsonl'
            if trial['processed_file'] != name:
                failures.append('noncanonical filename')
                continue
            content = (folder / name).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != trial['processed_rows_sha256']:
                failures.append(f'{name}: SHA-256 mismatch')
            rows = [json.loads(x) for x in content.splitlines() if x.strip()]
            s = trial['summary']
            if len(rows) != s['processed']:
                failures.append(f'{name}: processed count mismatch')
            if s['valid_frames'] != s['processed'] + s['application_queue_drops']:
                failures.append(f'{name}: application drop accounting mismatch')
            if s['received_datagrams'] != s['valid_frames'] + s['malformed_frames']:
                failures.append(f'{name}: receive accounting mismatch')
            if s['queue_depth_highwater'] > s['ring_capacity']:
                failures.append(f'{name}: queue depth exceeds capacity')
            if any(x['processed_monotonic_ns'] < x['received_monotonic_ns'] for x in rows):
                failures.append(f'{name}: negative residence time')
            if s['complete'] is not True or s['received_datagrams'] != s['target']:
                failures.append(f'{name}: incomplete receive')
            audited.append({'file': name, 'sha256': digest,
                            'processed': len(rows), 'queue_drops': s['application_queue_drops']})
        if len(audited) == 4:
            if audited[0]['processed'] != 64 or audited[0]['queue_drops'] != 0:
                failures.append('baseline failed')
            if any(x['queue_drops'] <= 0 for x in audited[1:]):
                failures.append('no pressure drops')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        failures.append(f'missing or malformed evidence: {exc}')
    return {'status': 'PASS' if not failures else 'FAIL', 'failures': failures,
            'trials': audited, 'scope': 'internal consistency, not source authenticity'}

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    args = p.parse_args()
    result = audit(args.directory)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)
