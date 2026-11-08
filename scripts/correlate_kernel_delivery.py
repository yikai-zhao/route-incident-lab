#!/usr/bin/env python3
"""Offline correlation of Linux RTNETLINK observations and C++ TCP connect samples.

Both files must have been collected on the same Linux host and boot with
compatible CLOCK_MONOTONIC timebases. Correlation is NOT causal attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load_jsonl(path: Path, limit: int = 100_000) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    if len(raw) > 20_000_000:
        raise ValueError(f'{path}: data exceeds 20MB limit')
    rows = []
    for no, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{path}: invalid JSONL line {no}') from exc
        if not isinstance(value, dict):
            raise ValueError(f'{path}: record at line {no} is not an object')
        rows.append(value)
        if len(rows) > limit:
            raise ValueError(f'{path}: too many records')
    return rows, hashlib.sha256(raw).hexdigest()


def analyze(routes: list[dict], tcp: list[dict], *, window_ms: int = 500) -> dict:
    if not 1 <= window_ms <= 60_000:
        raise ValueError('window_ms must be 1..60000')
    if not tcp:
        raise ValueError('TCP samples missing: not enough evidence')
    if not all(type(p.get('seq')) is int and type(p.get('ok')) is bool
               and type(p.get('start_monotonic_ns')) is int
               and type(p.get('end_monotonic_ns')) is int
               and p['end_monotonic_ns'] >= p['start_monotonic_ns'] for p in tcp):
        raise ValueError('TCP records have missing or invalid fields')
    if sorted(p['seq'] for p in tcp) != list(range(len(tcp))):
        raise ValueError('TCP seq must be complete and unique')
    if any(tcp[i]['start_monotonic_ns'] > tcp[i+1]['start_monotonic_ns']
           for i in range(len(tcp)-1)):
        raise ValueError('TCP observations not in launch order')
    valid_kinds = {'snapshot_route', 'kernel_route_event', 'kernel_link_event'}
    if not all(r.get('record_type') in valid_kinds and
               type(r.get('observed_monotonic_ns')) is int for r in routes):
        raise ValueError('kernel records have missing or invalid fields')
    if any(routes[i]['observed_monotonic_ns'] > routes[i+1]['observed_monotonic_ns']
           for i in range(len(routes)-1)):
        raise ValueError('kernel observations not chronological')
    events = [r for r in routes if r['record_type'] != 'snapshot_route']
    snapshots = [r for r in routes if r['record_type'] == 'snapshot_route']
    width_ns = window_ms * 1_000_000
    windows = []
    for event in events:
        center = event['observed_monotonic_ns']
        before = [p for p in tcp if center - width_ns <= p['start_monotonic_ns'] < center]
        after = [p for p in tcp if center <= p['start_monotonic_ns'] < center + width_ns]
        windows.append({
            'event_record_type': event['record_type'],
            'event_operation': event.get('operation'),
            'prefix': event.get('prefix'),
            'interface': event.get('interface') or event.get('out_interface'),
            'event_observed_monotonic_ns': center,
            'tcp_before': {'samples': len(before), 'failed': sum(not p['ok'] for p in before)},
            'tcp_after': {'samples': len(after), 'failed': sum(not p['ok'] for p in after)},
            'delta_failed_fraction': (round(sum(not p['ok'] for p in after) / len(after) -
                                             sum(not p['ok'] for p in before) / len(before), 4)
                                      if before and after else None),
            'causality': 'not_established',
        })
    return {
        'schema_version': 1,
        'measurement': 'same_host_observation_correlation',
        'route_snapshot_records': len(snapshots),
        'route_notifications': sum(r['record_type'] == 'kernel_route_event' for r in events),
        'link_notifications': sum(r['record_type'] == 'kernel_link_event' for r in events),
        'tcp_samples': len(tcp),
        'tcp_successes': sum(p['ok'] for p in tcp),
        'tcp_failures': sum(not p['ok'] for p in tcp),
        'event_windows': windows,
        'event_window_ms': window_ms,
        'status': 'OBSERVED_EVENTS' if events else 'NO_ROUTE_OR_LINK_NOTIFICATIONS_OBSERVED',
        'interpretation': ('No kernel route/link notifications were observed in the monitor window.'
                           if not events else 'Temporal co-occurrence only; route events do not prove causation.'),
        'limitations': [
            'Both recordings must originate from one Linux host/boot using CLOCK_MONOTONIC.',
            'RTNETLINK timestamps record userspace receive observations, not kernel state transitions.',
            'TCP sample timestamp is attempt START, not connection completion or packet delivery.',
            'Missing netlink messages, scheduling delays and snapshot inconsistency remain possible.',
            'No route notification does not prove route stability; no packet loss or convergence time is measured.',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--tcp', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--window-ms', type=int, default=500)
    opts = parser.parse_args()
    try:
        routes, route_sha = load_jsonl(opts.kernel)
        tcp, tcp_sha = load_jsonl(opts.tcp)
        report = analyze(routes, tcp, window_ms=opts.window_ms)
        report['input_sha256'] = {'kernel': route_sha, 'tcp': tcp_sha}
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'status': report['status'], 'route_snapshot_records': report['route_snapshot_records'],
                          'kernel_events': len(report['event_windows']), 'tcp_samples': report['tcp_samples'],
                          'tcp_failures': report['tcp_failures'], 'report': str(opts.output)}, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print('correlation failed: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
