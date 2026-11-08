#!/usr/bin/env python3
"""Offline, same-codepath UDP SPSC benchmark. Metrics are host-specific, not wire latency."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import socket
import statistics
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MAGIC = 0x52494C31
MODES = (('scalar', 'none', 'sleep'), ('batch', 'none', 'sleep'),
         ('batch', 'jsonl', 'sleep'), ('batch', 'none', 'spin'))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def frame(seq: int) -> bytes:
    return struct.pack('!IIQ', MAGIC, 7, seq)


def run_trial(binary: Path, outdir: Path, name: str, *, mode: tuple[str, str, str],
              count: int, burst: int, pause_us: int, capacity: int,
              startup_seconds: float, cpu_set: tuple[int, int, int] | None = None) -> dict:
    rx, sink, idle = mode
    output = outdir / f'{name}.jsonl'
    chosen_port = port()
    command = [str(binary.resolve()), '-', str(chosen_port), str(count), '3500',
               str(capacity), '0', str(output if sink == 'jsonl' else '-'),
               f'--rx={rx}', f'--sink={sink}', f'--idle={idle}']
    affinity = None
    if cpu_set:
        # Pin both receiver threads on two allowed CPUs; keep sender separate.
        affinity = lambda: os.sched_setaffinity(0, set(cpu_set[:2]))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, preexec_fn=affinity)
    started = time.monotonic_ns()
    try:
        time.sleep(startup_seconds)
        if process.poll() is not None:
            raise RuntimeError(f'{name}: receiver failed before send: {process.stderr.read()}')
        original_affinity = os.sched_getaffinity(0) if cpu_set else None
        try:
            if cpu_set:
                os.sched_setaffinity(0, {cpu_set[2]})
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
                sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
                t0 = time.monotonic_ns()
                for seq in range(1, count + 1):
                    sender.sendto(frame(seq), ('127.0.0.1', chosen_port))
                    if seq % burst == 0 and pause_us:
                        time.sleep(pause_us / 1_000_000)
                sender_ns = time.monotonic_ns() - t0
        finally:
            if original_affinity is not None:
                os.sched_setaffinity(0, original_affinity)
        stdout, stderr = process.communicate(timeout=8)
        if not stdout.strip():
            raise RuntimeError(f'{name}: no receiver JSON: code={process.returncode}: {stderr}')
        summary = json.loads(stdout)
        if process.returncode != 0 or not summary['complete']:
            (outdir / f'{name}.failure.json').write_text(json.dumps(
                {'status': 'FAIL', 'summary': summary, 'stderr': stderr}, indent=2)+'\n')
            raise RuntimeError(f'{name}: receiver incomplete: {summary}, {stderr}')
        if summary['received_datagrams'] != count:
            raise RuntimeError(f'{name}: received {summary["received_datagrams"]}/{count}')
        if (summary['malformed_frames'] or summary['application_queue_drops'] or
                summary.get('kernel_rx_overflow_cumulative', 0)):
            raise RuntimeError(f'{name}: frame loss inside application, not a valid matched trial: {summary}')
        if summary['processed'] != count:
            raise RuntimeError(f'{name}: not all frames processed')
        if summary['duplicate_events'] or summary['reordered_events'] or summary['forward_gap_events']:
            raise RuntimeError(f'{name}: sequence unexpectedly disrupted')
        if sink == 'jsonl':
            with output.open() as fp:
                if sum(1 for _ in fp) != count:
                    raise RuntimeError(f'{name}: invalid JSONL row count')
            raw_hash = sha256(output)
        else:
            if output.exists():
                raise RuntimeError(f'{name}: metrics-only mode unexpectedly wrote output')
            raw_hash = None
        result = {'name': name, 'mode': {'rx': rx, 'sink': sink, 'idle':idle},
                  'offered_count': count, 'burst': burst, 'pause_us': pause_us,
                  'publisher_wall_ns': sender_ns, 'total_wall_ns': time.monotonic_ns()-started,
                  'summary': summary, 'jsonl_sha256': raw_hash,
                  'summary_sha256': hashlib.sha256((json.dumps(summary,sort_keys=True)+'\n').encode()).hexdigest()}
        (outdir / f'{name}.summary.json').write_text(json.dumps(result, indent=2)+'\n')
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def summary_stats(records):
    groups = {}
    for r in records:
        key = '/'.join(r['mode'][k] for k in ('rx','sink','idle'))
        groups.setdefault(key, []).append(r)
    result = {}
    for name, group in groups.items():
        def med(key):
            return statistics.median(x['summary'][key] for x in group)
        result[name] = {'trials': len(group),
                        'median_active_cpu_us_per_frame': med('process_cpu_active_ns') / group[0]['offered_count'] / 1000,
                        'median_total_cpu_us_per_frame': med('process_cpu_ns') / group[0]['offered_count'] / 1000,
                        'median_startup_cpu_us': med('process_cpu_startup_ns') / 1000,
                        'median_consumer_cpu_us_per_frame': med('consumer_thread_cpu_ns') / group[0]['offered_count'] / 1000,
                        'median_rx_syscalls_per_frame': med('rx_successful_syscalls') / group[0]['offered_count'],
                        'median_queue_p99_us': med('queue_p99_us'),
                        'median_publisher_wall_ms': statistics.median(x['publisher_wall_ns'] for x in group)/1e6}
    return result


def run(binary: Path, output: Path, *, repetitions: int = 5, warmups: int = 1,
        count: int = 4000, burst: int = 8, pause_us: int = 250,
        capacity: int = 16384, seed: int = 20261108, pin: bool = False,
        startup_seconds: float = 0.13) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cpu_set = None
    if pin:
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus) < 3:
            raise RuntimeError('--pin needs at least three allowed logical CPUs')
        cpu_set = tuple(cpus[:3])
    env = {'utc': datetime.now(timezone.utc).isoformat(), 'kernel': platform.release(),
           'cpu': platform.processor(), 'python': platform.python_version(),
           'allowed_cpus': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
           'cpu_affinity': cpu_set, 'binary_sha256': sha256(binary),
           'cgroup_cpu_max': (Path('/sys/fs/cgroup/cpu.max').read_text().strip()
                              if Path('/sys/fs/cgroup/cpu.max').exists() else None)}
    records, order_log, rng = [], [], random.Random(seed)
    for phase, rounds in (('warmup', warmups), ('measured', repetitions)):
        for index in range(rounds):
            order = list(MODES)
            rng.shuffle(order)
            order_log.append({'phase': phase, 'pair': index, 'order': [f'{a}/{b}/{c}' for a,b,c in order]})
            for rx, sink, idle in order:
                name = f'{phase}-{index:02d}-{rx}-{sink}-{idle}'
                rec = run_trial(binary, output, name, mode=(rx,sink,idle), count=count,
                                burst=burst, pause_us=pause_us, capacity=capacity,
                                startup_seconds=startup_seconds, cpu_set=cpu_set)
                if phase == 'measured':
                    rec['pair'] = index
                    records.append(rec)
    totals = summary_stats(records)
    report = {'experiment':'single_codepath_udp_rx_and_sink_comparison',
              'status':'PASS', 'environment':env,
              'design':{'repetitions':repetitions, 'warmups':warmups, 'count':count,
                        'burst':burst, 'pause_us':pause_us, 'capacity':capacity,
                        'seed':seed, 'modes':list(MODES), 'trial_order':order_log},
              'measurements':totals, 'trials':records,
              'limitations':[
                  'IPv4 Linux loopback only; no NIC, hardware timestamp, PTP or exchange multicast.',
                  'Sender sleep precision and CPU scheduling may vary under cgroup contention.',
                  'Throughput is publisher-paced offered load; not a saturation throughput or capacity result.',
                  'Queue p99 is application residence for processed frames, not wire-to-wire latency.',
                  'CPU costs measured with process CPU clock; startup CPU is recorded separately from first-receive-to-finish active CPU.',
                  'Busy-spin trades CPU for faster wakeups; comparisons include identical receiver startup duration.',
                  'Do not claim one mode always wins without robust repeated measurements on a controlled host.'
              ]}
    (output/'benchmark_report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=Path('build/udp_pipeline'))
    p.add_argument('--output',type=Path,default=Path('lab-output/udp-benchmark'))
    p.add_argument('--repetitions',type=int,default=5)
    p.add_argument('--warmups',type=int,default=1)
    p.add_argument('--count',type=int,default=4000)
    p.add_argument('--burst',type=int,default=8)
    p.add_argument('--pause-us',type=int,default=250)
    p.add_argument('--capacity',type=int,default=16384)
    p.add_argument('--pin',action='store_true')
    args=p.parse_args()
    if not (1<=args.repetitions<=30 and 0<=args.warmups<=3 and 100<=args.count<=200000
            and 1<=args.burst<=256 and 0<=args.pause_us<=100000 and
            2<=args.capacity<=65536 and args.capacity&(args.capacity-1)==0):
        p.error('invalid repetitions, warmups, count, burst, pause or capacity')
    try:
        report=run(args.binary,args.output,repetitions=args.repetitions,warmups=args.warmups,
                   count=args.count,burst=args.burst,pause_us=args.pause_us,
                   capacity=args.capacity,pin=args.pin)
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        print(f'benchmark FAIL: {exc}',file=sys.stderr)
        return 1
    print(json.dumps({'status':report['status'],'environment':report['environment'],
                      'measurements':report['measurements']},indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
