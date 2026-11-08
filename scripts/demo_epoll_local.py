#!/usr/bin/env python3
"""Run a real local TCP-listener outage/recovery against the Linux epoll sampler.

Only binds 127.0.0.1; no Docker, privileged operations, external network, or fault injection.
This tests client Socket connect observations, NOT BGP or route convergence.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import threading
import time
from pathlib import Path


class LocalListener:
    def __init__(self, port: int = 0) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('127.0.0.1', port))
        self.port = self.socket.getsockname()[1]
        self.socket.listen(256)
        self.socket.settimeout(0.05)
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stop_requested.is_set():
            try:
                conn, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            else:
                conn.close()

    def stop(self) -> None:
        self.stop_requested.set()
        self.socket.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError('local loopback listener did not exit')


def classify(samples: list[dict], stopped_ns: int, restarted_ns: int) -> dict:
    phases = {'healthy': [], 'listener_down': [], 'recovered': []}
    for sample in samples:
        t = sample['start_monotonic_ns']
        phase = 'healthy' if t < stopped_ns else 'listener_down' if t < restarted_ns else 'recovered'
        phases[phase].append(sample)
    return {phase: {'attempts': len(rows), 'successes': sum(x['ok'] for x in rows),
                    'failures': sum(not x['ok'] for x in rows)}
            for phase, rows in phases.items()}


def run_demo(binary: Path, dest: Path, count: int = 240, interval_ms: int = 10) -> dict:
    if count < 60 or not 1 <= interval_ms <= 100:
        raise ValueError('count must be >=60; interval_ms must be 1..100')
    dest.mkdir(parents=True, exist_ok=True)
    listener = LocalListener()
    port = listener.port
    samples_file = dest / 'samples.jsonl'
    proc = None
    stopped_ns = restarted_ns = None
    try:
        args = [str(binary.resolve()), '127.0.0.1', str(port), str(count),
                '16', str(interval_ms), '300', str(samples_file)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Allow approximately a quarter of the scheduled samples to be healthy.
        time.sleep(count * interval_ms / 4000)
        listener.stop()
        stopped_ns = time.monotonic_ns()
        # Keep the listener down for about one quarter of the planned samples.
        time.sleep(count * interval_ms / 4000)
        listener = LocalListener(port)
        restarted_ns = time.monotonic_ns()
        out, err = proc.communicate(timeout=max(10, count * interval_ms / 1000 * 4))
        if proc.returncode != 0:
            raise RuntimeError(f'epoll sampler failed: {err.strip()}')
        summary = json.loads(out)
        samples = [json.loads(row) for row in samples_file.read_text().splitlines() if row.strip()]
        if len(samples) != count:
            raise RuntimeError('missing raw C++ samples')
        phases = classify(samples, stopped_ns, restarted_ns)
        checks = {'healthy_phase_observed': phases['healthy']['successes'] > 0,
                  'outage_failure_observed': phases['listener_down']['failures'] > 0,
                  'recovered_success_observed': phases['recovered']['successes'] > 0,
                  'sampler_count_consistent': (len(samples) == summary['attempts']
                                               and sum(s['ok'] for s in samples) == summary['successes'])}
        report = {'status': 'PASS' if all(checks.values()) else 'FAIL',
                  'experiment': 'local_loopback_listener_stop_restart',
                  'timestamp_basis': 'same-host Linux steady_clock / Python monotonic_ns',
                  'listener_stopped_monotonic_ns': stopped_ns,
                  'listener_restarted_monotonic_ns': restarted_ns,
                  'phase_observations': phases, 'checks': checks, 'sampler': summary,
                  'limitations': ['This exercises a localhost server lifecycle, not FRR/BGP or hardware.',
                                  'TCP connect success does not prove HTTP response delivery.',
                                  'Observed phase categories use client sample start times; not kernel event timestamps.',
                                  'Connect latency depends on host scheduling and is not wire or exchange latency.']}
        (dest / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    finally:
        listener.stop()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('build/epoll_connect_sampler'))
    p.add_argument('--output', type=Path, default=Path('lab-output/epoll-loopback'))
    p.add_argument('--count', type=int, default=240)
    p.add_argument('--interval-ms', type=int, default=10)
    args = p.parse_args()
    try:
        result = run_demo(args.binary, args.output, args.count, args.interval_ms)
        print(json.dumps({'status': result['status'], 'phase_observations': result['phase_observations'],
                          'checks': result['checks'], 'report': str(args.output / 'report.json')}, indent=2))
        return 0 if result['status'] == 'PASS' else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'local demo error: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
