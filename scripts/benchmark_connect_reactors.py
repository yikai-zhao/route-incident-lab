#!/usr/bin/env python3
"""Paired real-Socket poll vs epoll comparisons on a single Linux host.

Results are exploratory, not a wire-latency or exchange-performance claim.
Each pair uses identical workload inputs and alternates execution order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

# The existing sampler demo's LocalListener accepts promptly without remote hosts.
from demo_epoll_local import LocalListener

ROOT = Path(__file__).resolve().parents[1]


def sample_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_trial(binary: Path, directory: Path, workload: dict, backend: str, pair: int) -> dict:
    name = f"{workload['name']}-pair{pair:02d}-{backend}"
    target = directory / f"{name}.jsonl"
    listener = None
    pending_socket = None
    try:
        if workload['listener'] == 'accepting':
            listener = LocalListener()
            port = listener.port
        else:
            # A deliberately non-accepting listener creates genuine overlapping
            # pending connects / backlog pressure; no kernel routes are changed.
            pending_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            pending_socket.bind(('127.0.0.1', 0))
            pending_socket.listen(1)
            port = pending_socket.getsockname()[1]
        cmd = [str(binary.resolve()), backend, '127.0.0.1', str(port),
               str(workload['count']), str(workload['concurrency']),
               str(workload['interval_ms']), str(workload['timeout_ms']), str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError(f'{name} failed: {result.stderr}')
        summary = json.loads(result.stdout)
        raw = [json.loads(row) for row in target.read_text().splitlines() if row.strip()]
        if len(raw) != workload['count'] or [row['seq'] for row in raw] != list(range(workload['count'])):
            raise ValueError(f'{name}: samples missing or reordered')
        if sum(row['ok'] for row in raw) != summary['successes']:
            raise ValueError(f'{name}: summary inconsistent with raw records')
        if summary['successes'] + summary['failures'] != summary['attempts']:
            raise ValueError(f'{name}: malformed counts')
        if summary['peak_inflight'] > workload['concurrency']:
            raise ValueError(f'{name}: exceeded concurrency cap')
        errnos = Counter(row['error_errno'] for row in raw if not row['ok'])
        return {'name': name, 'pair': pair, 'backend': backend,
                'input': workload, 'summary': summary,
                'error_errno_counts': dict(sorted(errnos.items())),
                'samples_sha256': sample_hash(target),
                'samples_file': target.name}
    finally:
        if listener is not None:
            listener.stop()
        if pending_socket is not None:
            pending_socket.close()


def aggregate(results: list[dict], workload: str) -> dict:
    rows = [r for r in results if r['input']['name'] == workload]
    output: dict[str, dict] = {}
    for backend in ('poll', 'epoll'):
        group = [r for r in rows if r['backend'] == backend]
        p99 = [r['summary']['p99_ms'] for r in group if r['summary']['p99_ms'] is not None]
        out = {'trials': len(group),
               'attempts': sum(r['summary']['attempts'] for r in group),
               'failures': sum(r['summary']['failures'] for r in group),
               'observed_peak_inflight': max(r['summary']['peak_inflight'] for r in group),
               'p99_success_only_ms_by_trial': p99,
               'median_p99_success_only_ms': statistics.median(p99) if p99 else None,
               'median_user_cpu_ms': statistics.median(r['summary']['user_cpu_ms'] for r in group),
               'median_system_cpu_ms': statistics.median(r['summary']['system_cpu_ms'] for r in group)}
        output[backend] = out
    return output


def run(binary: Path, output: Path, *, pairs: int = 5, count: int = 500, seed: int = 20261108) -> dict:
    if not 1 <= pairs <= 12 or not 20 <= count <= 3000:
        raise ValueError('pairs 1..12, count 20..3000')
    output.mkdir(parents=True, exist_ok=True)
    workloads = [
        {'name': 'accepting', 'listener': 'accepting', 'count': count,
         'concurrency': 32, 'interval_ms': 0, 'timeout_ms': 250},
        {'name': 'backlog_pressure', 'listener': 'nonaccepting',
         'count': min(count, 180), 'concurrency': 32, 'interval_ms': 0, 'timeout_ms': 100},
    ]
    # A paired AB / BA randomized order reduces (but does not eliminate)
    # temporal drift effects. The seed and executed order are recorded.
    rng = random.Random(seed)
    results: list[dict] = []
    order: list[dict] = []
    for workload in workloads:
        for pair in range(1, pairs + 1):
            backends = ['poll', 'epoll']
            rng.shuffle(backends)
            order.append({'workload': workload['name'], 'pair': pair, 'order': list(backends)})
            for backend in backends:
                results.append(run_trial(binary, output, workload, backend, pair))
    uname = platform.uname()
    report = {
        'schema_version': 1,
        'experiment': 'same_program_matched_poll_epoll_real_loopback',
        'date_basis': 'execution_local_time',
        'host': {'system': uname.system, 'release': uname.release,
                 'machine': uname.machine, 'processor': uname.processor,
                 'cpu_count': os.cpu_count()},
        'binary_sha256': sample_hash(binary),
        'paired_order_seed': seed,
        'pairs_per_workload': pairs,
        'workload_order': order,
        'trials': results,
        'aggregate': {x['name']: aggregate(results, x['name']) for x in workloads},
        'limitations': [
            'Identical state machine, sampling logic and socket options; readiness backend differs.',
            'The local server and kernel backlog are not identical across successive trials.',
            'Loopback is not an exchange network, colocated NIC, or wire-latency measurement.',
            'Successful-connect p99 excludes failed attempts; inspect failure counts and errno.',
            'Scheduling noise, CPU contention and background processes can dominate short runs.',
            'Kernel PMU/perf and CPU affinity were not necessarily available or controlled.',
            'Backlog-pressure is not realistic market-data application traffic.',
            'This experiment does not establish a general performance winner.',
        ],
    }
    (output / 'benchmark.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('build/connect_reactor_compare'))
    p.add_argument('--output', type=Path, default=Path('lab-output/reactor-benchmark'))
    p.add_argument('--pairs', type=int, default=5)
    p.add_argument('--count', type=int, default=500)
    opts = p.parse_args()
    try:
        report = run(opts.binary, opts.output, pairs=opts.pairs, count=opts.count)
        print(json.dumps({'aggregate': report['aggregate'], 'report': str(opts.output / 'benchmark.json')}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f'benchmark failed: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
