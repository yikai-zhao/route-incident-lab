"""Linux RTNETLINK snapshot and cross-plane correlation tests.

Synthetic event fixtures exercise analysis only; real kernel snapshots are tested separately.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from correlate_kernel_delivery import analyze, load_jsonl  # noqa: E402


def tcp_rows():
    return [{'seq': i, 'ok': ok, 'start_monotonic_ns': (i + 1) * 100_000_000,
             'end_monotonic_ns': (i + 1) * 100_000_000 + 1_000_000}
            for i, ok in enumerate((True, True, False, False, True, True))]


def kernel_event(at=250_000_000):
    return {'record_type': 'kernel_route_event', 'operation': 'delroute',
            'prefix': '198.51.100.0/24', 'observed_monotonic_ns': at}


class CorrelationTests(unittest.TestCase):
    def test_route_snapshot_not_mistaken_for_notification(self):
        rows = [{'record_type': 'snapshot_route', 'operation': 'newroute',
                 'observed_monotonic_ns': 10}]
        report = analyze(rows, tcp_rows())
        self.assertEqual(report['route_snapshot_records'], 1)
        self.assertEqual(report['status'], 'NO_ROUTE_OR_LINK_NOTIFICATIONS_OBSERVED')
        self.assertEqual(report['event_windows'], [])

    def test_route_event_with_before_and_after_failure_windows(self):
        report = analyze([kernel_event()], tcp_rows(), window_ms=250)
        self.assertEqual(report['status'], 'OBSERVED_EVENTS')
        window = report['event_windows'][0]
        self.assertEqual(window['tcp_before'], {'samples': 2, 'failed': 0})
        self.assertEqual(window['tcp_after'], {'samples': 2, 'failed': 2})
        self.assertEqual(window['causality'], 'not_established')
        self.assertAlmostEqual(window['delta_failed_fraction'], 1.0, places=4)

    def test_link_event_and_missing_windows_remain_unknown(self):
        report = analyze([{'record_type': 'kernel_link_event', 'operation': 'newlink',
                           'observed_monotonic_ns': 10_000_000_000}], tcp_rows())
        self.assertEqual(report['link_notifications'], 1)
        self.assertIsNone(report['event_windows'][0]['delta_failed_fraction'])

    def test_rejects_missing_and_duplicate_tcp_samples(self):
        with self.assertRaises(ValueError): analyze([], [])
        rows = tcp_rows()
        rows[1]['seq'] = 0
        with self.assertRaises(ValueError): analyze([], rows)

    def test_rejects_malformed_or_out_of_order_kernel_observations(self):
        with self.assertRaises(ValueError): analyze([{'record_type': 'kernel_route_event'}], tcp_rows())
        with self.assertRaises(ValueError): analyze([kernel_event(3), kernel_event(2)], tcp_rows())

    def test_jsonl_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'raw.jsonl'
            path.write_text('{"a":1}\nINVALID\n')
            with self.assertRaises(ValueError): load_jsonl(path)


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('g++'),
                     'Linux and g++ are required for real RTNETLINK tests')
class RealKernelSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'rtnetlink_monitor'
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic',
                        str(ROOT / 'cpp/rtnetlink_monitor.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temp'): cls.temp.cleanup()

    def test_real_kernel_route_dump_readonly(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'kernel.jsonl'
            result = subprocess.run([str(self.binary), '--watch-ms', '100', '--output', str(path)],
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            rows = [json.loads(x) for x in path.read_text().splitlines()]
            self.assertEqual(summary['engine'], 'rtnetlink_read_only')
            self.assertGreater(summary['snapshot_routes'], 0)
            self.assertEqual(sum(x['record_type'] == 'snapshot_route' for x in rows),
                             summary['snapshot_routes'])
            self.assertTrue(all(type(x['observed_monotonic_ns']) is int for x in rows))

    def test_cpp_synthetic_parser_validation(self):
        result = subprocess.run([str(self.binary), '--self-test'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('synthetic_parser_self_test PASS', result.stdout)

    def test_out_of_bounds_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([str(self.binary), '--watch-ms', '120001',
                                     '--output', str(Path(temp) / 'raw.jsonl')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    def test_real_integrated_loopback_and_kernel_snapshot(self):
        epoll_binary = Path(self.temp.name) / 'epoll_connect_sampler'
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic',
                        str(ROOT / 'cpp/epoll_connect_sampler.cpp'), '-o', str(epoll_binary)], check=True)
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/demo_observability.py'),
                                     '--epoll', str(epoll_binary), '--netlink', str(self.binary),
                                     '--output', temp], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads((Path(temp) / 'correlation.json').read_text())
            self.assertEqual(report['tcp_demo_status'], 'PASS')
            self.assertEqual(report['tcp_samples'], 240)
            self.assertGreater(report['route_snapshot_records'], 0)
            if report['route_notifications'] + report['link_notifications'] == 0:
                self.assertEqual(report['status'], 'NO_ROUTE_OR_LINK_NOTIFICATIONS_OBSERVED')


if __name__ == '__main__': unittest.main()
