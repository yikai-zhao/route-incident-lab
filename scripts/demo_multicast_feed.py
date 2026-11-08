#!/usr/bin/env python3
"""Real Linux loopback multicast: synthetic feed sequence gaps/duplicate/reorder.

Publisher intentionally skips numbers. Gaps do NOT demonstrate network loss.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import subprocess
import time
from pathlib import Path

GROUP = '239.255.42.99'
MAGIC = 0x52494C31  # RIL1
PATTERN = [1, 2, 4, 3, 4, 5, 9, 10, 8, 11]


def run(binary: Path, output: Path, *, pattern: list[int] | None = None) -> dict:
    pattern = pattern if pattern is not None else PATTERN
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temp:
        temp.bind(('127.0.0.1', 0))
        port = temp.getsockname()[1]
    raw = output / 'feed.jsonl'
    receiver = subprocess.Popen([str(binary.resolve()), GROUP, str(port), str(len(pattern)),
                                 '5000', str(raw)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(.25)  # Local join; missing packets cause a failed test, not invented results.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as publisher:
            publisher.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton('127.0.0.1'))
            publisher.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            for seq in pattern:
                publisher.sendto(struct.pack('!IIQ', MAGIC, 7, seq), (GROUP, port))
                time.sleep(.015)
        stdout, stderr = receiver.communicate(timeout=7)
        if receiver.returncode != 0:
            raise RuntimeError('receiver did not complete: ' + stderr)
        summary = json.loads(stdout)
        samples = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
        checks = {
            'all_packets_received': len(samples) == len(pattern) == summary['received'],
            'published_order_preserved': [s['seq'] for s in samples] == pattern,
            'forward_jump_observed': summary['forward_jump'] == 2,
            'out_of_order_observed': summary['out_of_order'] == 2,
            'duplicate_observed': summary['duplicate'] == 1,
            'synthetic_feed_format_accepted': summary['malformed'] == 0,
        }
        report = {'status': 'PASS' if all(checks.values()) else 'FAIL',
                  'test': 'real_loopback_IPv4_multicast_synthetic_sequence_feed',
                  'published_sequences': pattern, 'receiver': summary, 'checks': checks,
                  'limitations': [
                      'Publisher deliberately skipped numbers. A gap is not evidence of network packet loss.',
                      'This is real loopback multicast socket I/O, not an external exchange feed.',
                      'SO_TIMESTAMPNS uses CLOCK_REALTIME, not the monotonic receive-observation clock.',
                      'No trading performance, NIC hardware timestamping or loss probability is claimed.',
                  ]}
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    finally:
        if receiver.poll() is None:
            receiver.kill()
            receiver.communicate()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('build/udp_feed_monitor'))
    p.add_argument('--output', type=Path, default=Path('lab-output/multicast-feed'))
    opts = p.parse_args()
    try:
        result = run(opts.binary, opts.output)
        print(json.dumps({'status': result['status'], 'receiver': result['receiver'],
                          'report': str(opts.output / 'report.json')}, indent=2))
        return 0 if result['status'] == 'PASS' else 1
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('multicast demo failed: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
