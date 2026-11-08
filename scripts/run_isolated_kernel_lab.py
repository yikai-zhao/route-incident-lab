#!/usr/bin/env python3
"""Opt-in isolated two-host dual-link Linux netns routing lab: actual kernel route changes + TCP samples.

Safe by construction: outer process reexecs inside a NEW user, network and mount
namespace. Requires a kernel with unprivileged user namespaces and veth enabled.
NOT FRRouting/BGP: static Linux FIB routes are the only control plane here.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODES = ('ril-h1', 'ril-h2')
NAMESPACE_EDGES = (
    ('primary', 'ril-h1', 'eth1', '10.0.12.1/30', 'ril-h2', 'eth1', '10.0.12.2/30'),
    ('backup', 'ril-h1', 'eth2', '10.0.13.1/30', 'ril-h2', 'eth2', '10.0.13.2/30'),
)


def cmd(args: list[str], *, timeout: float = 10, check: bool = True) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f'{args!r}: exit {p.returncode}: {p.stderr[-400:]}')
    return p.stdout


def ns(node: str, *args: str) -> str:
    if node not in NODES:
        raise ValueError('unknown isolated node')
    return cmd(['ip', 'netns', 'exec', node, *args])


def marker() -> None:
    """Avoid any chance of accidentally setting host routes when --inside is supplied."""
    if os.geteuid() != 0:
        raise RuntimeError('must run as uid 0 *inside* an isolated user namespace')
    outer = os.environ.get('RIL_PARENT_USER_NS', '')
    inner = os.readlink('/proc/self/ns/user')
    outer_net = os.environ.get('RIL_PARENT_NET_NS', '')
    inner_net = os.readlink('/proc/self/ns/net')
    uidmap = Path('/proc/self/uid_map').read_text().split()
    if (not outer or outer == inner or not outer_net or outer_net == inner_net
            or len(uidmap) != 3 or uidmap[0] != '0' or uidmap[2] != '1'):
        raise RuntimeError('refusing to modify host namespace; isolated user+network mapping missing')


def setup() -> None:
    for node in NODES:
        cmd(['ip', 'netns', 'add', node])
        ns(node, 'ip', 'link', 'set', 'lo', 'up')
    for prefix, node_a, name_a, ip_a, node_b, name_b, ip_b in NAMESPACE_EDGES:
        side_a = 'v' + prefix + 'a'
        side_b = 'v' + prefix + 'b'
        cmd(['ip', 'link', 'add', side_a, 'type', 'veth', 'peer', 'name', side_b])
        cmd(['ip', 'link', 'set', side_a, 'netns', node_a])
        cmd(['ip', 'link', 'set', side_b, 'netns', node_b])
        for node, old, new, address in ((node_a, side_a, name_a, ip_a),
                                        (node_b, side_b, name_b, ip_b)):
            ns(node, 'ip', 'link', 'set', old, 'name', new)
            ns(node, 'ip', 'addr', 'add', address, 'dev', new)
            ns(node, 'ip', 'link', 'set', new, 'up')
    ns('ril-h2', 'ip', 'addr', 'add', '10.10.2.2/32', 'dev', 'lo')
    ns('ril-h1', 'ip', 'route', 'add', '10.10.2.2/32', 'via', '10.0.12.2', 'dev', 'eth1')


def isolate_run(output: Path, compare: Path, netlink: Path) -> dict:
    marker()
    output.mkdir(parents=True, exist_ok=True)
    action_log = []
    server = None
    watcher = None
    sampler = None

    def action(label: str, argv: tuple[str, ...]) -> None:
        before = time.monotonic_ns()
        ns('ril-h1', *argv)
        after = time.monotonic_ns()
        action_log.append({'action': label, 'command': argv,
                           'start_monotonic_ns': before, 'finish_monotonic_ns': after,
                           'h1_route_table': ns('ril-h1', 'ip', '-4', 'route', 'show')})

    try:
        setup()
        server = subprocess.Popen(['ip', 'netns', 'exec', 'ril-h2', sys.executable,
                                   '-m', 'http.server', '8080', '--bind', '10.10.2.2'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ok = False
        for _ in range(40):
            if server.poll() is not None:
                raise RuntimeError('h2 server exited during startup')
            try:
                ns('ril-h1', sys.executable, '-c',
                   'import socket;s=socket.create_connection(("10.10.2.2",8080),.5);s.close()')
                ok = True
                break
            except RuntimeError:
                time.sleep(.1)
        if not ok:
            raise RuntimeError('cross-namespace TCP healthy baseline did not come up')
        watcher = subprocess.Popen(['ip', 'netns', 'exec', 'ril-h1', str(netlink.resolve()),
                                    '--watch-ms', '8000', '--output', str(output / 'kernel.jsonl')],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(.2)
        sampler = subprocess.Popen(['ip', 'netns', 'exec', 'ril-h1', str(compare.resolve()), 'epoll',
                                    '10.10.2.2', '8080', '360', '16', '15', '150',
                                    str(output / 'tcp.jsonl')],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(.70)
        action('primary_link_down', ('ip', 'link', 'set', 'eth1', 'down'))
        time.sleep(.55)
        action('switch_to_backup', ('ip', 'route', 'replace', '10.10.2.2/32',
                                     'via', '10.0.13.2', 'dev', 'eth2'))
        time.sleep(.70)
        action('inject_static_blackhole', ('ip', 'route', 'replace', 'blackhole', '10.10.2.2/32'))
        time.sleep(.65)
        action('recover_via_backup', ('ip', 'route', 'replace', '10.10.2.2/32',
                                      'via', '10.0.13.2', 'dev', 'eth2'))
        time.sleep(.50)
        action('restore_primary_link', ('ip', 'link', 'set', 'eth1', 'up'))
        time.sleep(.15)
        action('restore_primary_route', ('ip', 'route', 'replace', '10.10.2.2/32',
                                         'via', '10.0.12.2', 'dev', 'eth1'))
        tcp_out, tcp_err = sampler.communicate(timeout=15)
        if sampler.returncode:
            raise RuntimeError(f'TCP sampler failed: {tcp_err}')
        watcher_out, watcher_err = watcher.communicate(timeout=12)
        if watcher.returncode:
            raise RuntimeError(f'kernel monitor failed: {watcher_err}')
        summary = json.loads(tcp_out)
        kernel = json.loads(watcher_out)
        sys.path.insert(0, str(ROOT / 'scripts'))
        from correlate_kernel_delivery import analyze, load_jsonl
        kernel_rows, kernel_sha = load_jsonl(output / 'kernel.jsonl')
        tcp_rows, tcp_sha = load_jsonl(output / 'tcp.jsonl')
        comparison = analyze(kernel_rows, tcp_rows, window_ms=400)
        phase_names = ['healthy', 'primary_down', 'backup', 'blackhole',
                       'backup_recovered', 'primary_link_up', 'primary_restored']
        boundaries = [x['finish_monotonic_ns'] for x in action_log]
        per_phase = {}
        for i, name in enumerate(phase_names):
            low = boundaries[i-1] if i else 0
            high = boundaries[i] if i < len(boundaries) else 2**63
            rows = [x for x in tcp_rows if low <= x['start_monotonic_ns'] < high]
            per_phase[name] = {'attempts': len(rows), 'successes': sum(r['ok'] for r in rows),
                               'failures': sum(not r['ok'] for r in rows)}
        checks = {
            'baseline_connectivity': per_phase['healthy']['successes'] > 0,
            'primary_link_fault_observed': per_phase['primary_down']['failures'] > 0,
            'backup_connectivity_restored': per_phase['backup']['successes'] > 0,
            'blackhole_failure_observed': per_phase['blackhole']['failures'] > 0,
            'blackhole_recovery_observed': per_phase['backup_recovered']['successes'] > 0,
            'kernel_link_notification': kernel['link_notifications'] > 0,
            'kernel_route_notification': kernel['route_notifications'] > 0,
            'sampler_count_consistency': summary['attempts'] == len(tcp_rows),
        }
        report = {'schema_version': 1, 'experiment': 'isolated_real_linux_fib_faults_not_bgp',
                  'isolation': 'new user+network+mount namespace, 2 ip netns, 2 veth links',
                  'topology': 'h1--h2 using two independent primary/backup veth links and static host route',
                  'monitor': kernel, 'sampler': summary, 'action_log': action_log,
                  'per_phase': per_phase, 'correlation': comparison,
                  'source_sha256': {'kernel': kernel_sha, 'tcp': tcp_sha},
                  'checks': checks, 'status': 'PASS' if all(checks.values()) else 'FAIL',
                  'limitations': [
                      'Real Linux kernel static FIB and link changes across two isolated hosts; NOT FRR/BGP or routed three-router convergence.',
                      'Controlled failure actions are known, but sample windows do not prove exact route convergence.',
                      'User-space route and TCP clocks share host monotonic time; receive timestamps may lag.',
                      'TCP connect only; HTTP response body not verified by continuous sampler.',
                      'No external network, production router or privileged host route modified.',
                  ]}
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    finally:
        for p in (sampler, watcher, server):
            if p is not None and p.poll() is None:
                p.terminate()
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill();p.wait(timeout=2)
        # Isolated mount+user+network namespaces already protect the host;
        # explicitly delete test nodes for clear teardown inside the sandbox.
        for node in reversed(NODES):
            cmd(['ip', 'netns', 'del', node], check=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--output', type=Path, default=ROOT / 'lab-output/netns-kernel-fault')
    p.add_argument('--compare', type=Path, default=ROOT / 'build/connect_reactor_compare')
    p.add_argument('--netlink', type=Path, default=ROOT / 'build/rtnetlink_monitor')
    opts = p.parse_args()
    for binary in (opts.compare, opts.netlink):
        if not binary.exists():
            p.error(f'missing compiled binary: {binary}')
    if not opts.inside:
        if not shutil.which('unshare'):
            p.error('unshare is required; never run host routing changes directly')
        command = ['unshare', '--user', '--map-root-user', '--net', '--mount', '--fork',
                   sys.executable, str(Path(__file__).resolve()), '--inside',
                   '--output', str(opts.output.resolve()),
                   '--compare', str(opts.compare.resolve()), '--netlink', str(opts.netlink.resolve())]
        env = dict(os.environ)
        env['RIL_PARENT_USER_NS'] = os.readlink('/proc/self/ns/user')
        env['RIL_PARENT_NET_NS'] = os.readlink('/proc/self/ns/net')
        return subprocess.call(command, env=env)
    try:
        result = isolate_run(opts.output.resolve(), opts.compare, opts.netlink)
        print(json.dumps({'status': result['status'], 'per_phase': result['per_phase'],
                          'kernel_notifications': (result['monitor']['route_notifications'],
                                                   result['monitor']['link_notifications']),
                          'checks': result['checks']}, indent=2))
        return 0 if result['status'] == 'PASS' else 1
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        print(f'isolated lab failed: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
