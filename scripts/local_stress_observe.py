#!/usr/bin/env python3
"""Repeat local TCP connect workloads and preserve p99/errno/CPU variability.

This is NOT a poll-vs-epoll benchmark, trading-latency or kernel-bypass proof.
"""
from __future__ import annotations

import json
import pathlib
import platform
import resource
import subprocess
import sys
import time
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from demo_epoll_local import LocalListener

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'lab-output' / 'stress-experiment'
BINARY = ROOT / 'build' / 'epoll_connect_sampler'


def netstat() -> dict[str, int] | None:
    try:
        rows = pathlib.Path('/proc/net/netstat').read_text().splitlines()
        for i in range(0, len(rows) - 1, 2):
            if rows[i].startswith('TcpExt:') and rows[i + 1].startswith('TcpExt:'):
                values = dict(zip(rows[i].split()[1:], map(int, rows[i + 1].split()[1:])))
                return {k: values[k] for k in ('ListenOverflows', 'ListenDrops') if k in values}
    except (OSError, ValueError):
        pass
    return None


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for label, concurrency, spacing_ms in [('burst', 64, 0), ('paced', 16, 1)]:
        for trial in range(1, 4):
            server = LocalListener()
            before = resource.getrusage(resource.RUSAGE_CHILDREN)
            before_netstat = netstat()
            start = time.monotonic()
            try:
                samples = OUT / f'{label}-{trial}.jsonl'
                p = subprocess.run([str(BINARY), '127.0.0.1', str(server.port),
                                    '2000', str(concurrency), str(spacing_ms), '500',
                                    str(samples)], capture_output=True, text=True, timeout=30)
            finally:
                server.stop()
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            after_netstat = netstat()
            if p.returncode != 0:
                raise RuntimeError('sampler failed: ' + p.stderr.strip())
            summary = json.loads(p.stdout)
            rows = [json.loads(line) for line in samples.read_text().splitlines()]
            if len(rows) != 2000 or sum(r['ok'] for r in rows) != summary['successes']:
                raise RuntimeError('raw sample counts disagree with summary')
            errors = Counter(r['error_errno'] for r in rows if not r['ok'])
            counters = None if before_netstat is None or after_netstat is None else {
                k: after_netstat[k] - before_netstat[k]
                for k in before_netstat.keys() & after_netstat.keys()}
            results.append({
                'workload': label, 'trial': trial, 'concurrency_limit': concurrency,
                'scheduled_interval_ms': spacing_ms, 'summary': summary,
                'failed_errno_counts': dict(errors), 'kernel_tcp_ext_delta': counters,
                'sampler_user_cpu_s': round(after.ru_utime - before.ru_utime, 4),
                'sampler_system_cpu_s': round(after.ru_stime - before.ru_stime, 4),
                'orchestrator_wall_s': round(time.monotonic() - start, 4),
            })
    report = {
        'experiment': 'same-host_tcp_connect_variability_not_capacity_benchmark',
        'platform': platform.system(), 'architecture': platform.machine(),
        'attempts_per_trial': 2000, 'trials_per_workload': 3,
        'conditions': results,
        'limitations': [
            'Burst vs paced workloads have different offered load and are NOT a matched-performance comparison.',
            'CPU time accounts for the sampler subprocess, not Python listener CPU, and varies with scheduling.',
            'Tail latency is TCP client connect completion, not HTTP delivery or NIC hardware timestamp.',
            'Kernel TcpExt counters may include background traffic and do not by themselves prove cause.',
            'No exchange performance, forwarding convergence, or kernel-bypass claim.',
        ],
    }
    (OUT / 'repeated-runs.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    try:
        for row in run()['conditions']:
            s = row['summary']
            print(row['workload'], 'trial', row['trial'], 'success', s['successes'],
                  'failed', s['failures'], 'p99_ms', s['p99_ms'],
                  'elapsed_ms', s['elapsed_ms'], 'kernel', row['kernel_tcp_ext_delta'])
        print('Raw observations and report: lab-output/stress-experiment/')
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('local stress observation failed:', error, file=sys.stderr)
        raise SystemExit(2)
