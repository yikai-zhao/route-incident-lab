#!/usr/bin/env python3
"""Locally reproduce C++ SPSC UDP feed backpressure; no hosted services or external hosts."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import subprocess
import time
from pathlib import Path

GROUP = '239.255.42.99'
MAGIC = 0x52494C31


def one_run(binary: Path, output: Path, *, count: int, capacity: int,
            interval: float, delay_us: int) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tmp:
        tmp.bind(('127.0.0.1', 0))
        port = tmp.getsockname()[1]
    proc = subprocess.Popen([str(binary.resolve()), GROUP, str(port), str(count),
                             '8000', str(capacity), str(delay_us), str(output)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(.2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                              socket.inet_aton('127.0.0.1'))
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            for seq in range(1, count + 1):
                sender.sendto(struct.pack('!IIQ', MAGIC, 7, seq), (GROUP, port))
                time.sleep(interval)
        out, err = proc.communicate(timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(f'UDP pipeline incomplete: {err} {out}')
        summary = json.loads(out)
        samples = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
        if summary['processed'] != len(samples):
            raise ValueError('consumer output missing rows')
        if summary['valid_frames'] != summary['processed'] + summary['application_queue_drops']:
            raise ValueError('received datagram accounting mismatch')
        return {'summary': summary, 'processed_rows_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'processed_file': output.name}
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def demo(binary: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = one_run(binary, output_dir / 'baseline.jsonl', count=64, capacity=64,
                       interval=.005, delay_us=0)
    pressured = [one_run(binary, output_dir / f'backpressure-{i}.jsonl', count=160,
                          capacity=4, interval=.001, delay_us=7000) for i in (1, 2, 3)]
    checks = {
        'baseline_all_received_processed': (baseline['summary']['received_datagrams'] == 64 and
                                            baseline['summary']['processed'] == 64 and
                                            baseline['summary']['application_queue_drops'] == 0),
        'pressured_all_received': all(x['summary']['received_datagrams'] == 160 for x in pressured),
        'pressured_app_queue_drop_observed': all(x['summary']['application_queue_drops'] > 0
                                                for x in pressured),
        'accounting_closed_every_trial': all(x['summary']['valid_frames'] == x['summary']['processed'] +
                                              x['summary']['application_queue_drops'] for x in [baseline, *pressured]),
    }
    report = {'experiment': 'real_loopback_multicast_spsc_app_backpressure',
              'baseline': baseline, 'pressured_repetitions': pressured, 'checks': checks,
              'status': 'PASS' if all(checks.values()) else 'FAIL',
              'limitations': ['Sender and receiver use actual Linux loopback multicast only.',
                              'Application queue drops are observed explicitly, not inferred from skipped feed IDs.',
                              'The publisher sent every ID; network delivery is not independently proven.',
                              'No external exchange feed, wire packet-loss measurement or throughput claim.']}
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('build/udp_pipeline'))
    p.add_argument('--output', type=Path, default=Path('lab-output/spsc-udp'))
    args = p.parse_args()
    try:
        report = demo(args.binary, args.output)
        print(json.dumps({'status': report['status'], 'checks': report['checks'],
                          'baseline': report['baseline']['summary'],
                          'backpressure': [x['summary'] for x in report['pressured_repetitions']]}, indent=2))
        return 0 if report['status'] == 'PASS' else 1
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as err:
        print(f'local experiment failed: {err}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
