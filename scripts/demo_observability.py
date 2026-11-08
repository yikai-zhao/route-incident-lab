#!/usr/bin/env python3
"""Run a read-only real Linux route snapshot alongside a localhost TCP outage.

No routing changes. With no actual kernel events, report must say none observed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from demo_epoll_local import run_demo
from correlate_kernel_delivery import analyze, load_jsonl


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--epoll', type=Path, default=Path('build/epoll_connect_sampler'))
    p.add_argument('--netlink', type=Path, default=Path('build/rtnetlink_monitor'))
    p.add_argument('--output', type=Path, default=Path('lab-output/kernel-and-tcp'))
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    watcher = subprocess.Popen([str(args.netlink.resolve()), '--watch-ms', '5000',
                                '--output', str(out / 'kernel.jsonl')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # Give monitor time to request the real kernel route snapshot.
        time.sleep(0.15)
        tcp_report = run_demo(args.epoll, out, count=240, interval_ms=10)
        monitor_out, monitor_err = watcher.communicate(timeout=10)
        if watcher.returncode != 0:
            raise RuntimeError('kernel monitor failed: ' + monitor_err)
        kernel_report = json.loads(monitor_out)
        routes, route_sha = load_jsonl(out / 'kernel.jsonl')
        tcp, tcp_sha = load_jsonl(out / 'samples.jsonl')
        correlation = analyze(routes, tcp)
        correlation['input_sha256'] = {'kernel': route_sha, 'tcp': tcp_sha}
        correlation['monitor_summary'] = kernel_report
        correlation['tcp_demo_status'] = tcp_report['status']
        (out / 'correlation.json').write_text(json.dumps(correlation, indent=2) + '\n')
        print(json.dumps({'tcp': tcp_report['status'], 'kernel_routes_read': kernel_report['snapshot_routes'],
                          'kernel_notifications': len(correlation['event_windows']),
                          'correlation': correlation['status'], 'directory': str(out)}, indent=2))
        return 0 if tcp_report['status'] == 'PASS' and kernel_report['snapshot_routes'] > 0 else 1
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f'integration demo failed: {e}')
        return 2
    finally:
        if watcher.poll() is None:
            watcher.terminate()
            try: watcher.wait(timeout=2)
            except subprocess.TimeoutExpired: watcher.kill(); watcher.wait()


if __name__ == '__main__':
    raise SystemExit(main())
