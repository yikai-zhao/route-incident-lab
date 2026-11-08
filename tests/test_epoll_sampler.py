"""Real Linux socket regressions for the C++ epoll engine. No Docker or network privileges."""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'cpp' / 'epoll_connect_sampler.cpp'
sys.path.insert(0, str(ROOT / 'scripts'))
from demo_epoll_local import LocalListener, run_demo, classify  # noqa: E402


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('g++'),
                     'Linux and g++ required for epoll regression')
class EpollSamplerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls._temp.name) / 'epoll_connect_sampler'
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic',
                        str(SRC), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, '_temp'):
            cls._temp.cleanup()

    def invoke(self, port, count, concurrency, interval=0, timeout=100, output=None):
        args = [str(self.binary), '127.0.0.1', str(port), str(count), str(concurrency),
                str(interval), str(timeout)]
        if output:
            args.append(str(output))
        return subprocess.run(args, text=True, capture_output=True, timeout=10)

    def test_real_loopback_success_and_raw_samples(self):
        server = LocalListener()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / 'raw.jsonl'
                result = self.invoke(server.port, 120, 12, 2, output=out)
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                rows = [json.loads(x) for x in out.read_text().splitlines()]
                self.assertEqual(summary['engine'], 'linux_epoll')
                self.assertEqual((summary['attempts'], summary['successes'], summary['failures']),
                                 (120, 120, 0))
                self.assertEqual(len(rows), 120)
                self.assertEqual([x['seq'] for x in rows], list(range(120)))
                self.assertTrue(all(x['ok'] and x['error_errno'] == 0 and
                                    x['end_monotonic_ns'] >= x['start_monotonic_ns'] for x in rows))
                self.assertLessEqual(summary['peak_inflight'], 12)
                self.assertIsNotNone(summary['p99_ms'])
        finally:
            server.stop()

    def test_refused_port_fails_without_inventing_percentiles(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        result = self.invoke(port, 20, 4)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['failures'], 20)
        self.assertIsNone(summary['p50_ms'])
        self.assertIsNone(summary['p99_ms'])

    def test_backlog_pressure_caps_real_inflight_sockets(self):
        # A listening socket that never accepts creates actual overlapping EINPROGRESS sockets.
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            result = self.invoke(server.getsockname()[1], 30, 8, 0, 150)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['attempts'], 30)
        self.assertEqual(summary['successes'] + summary['failures'], 30)
        self.assertGreater(summary['peak_inflight'], 1)
        self.assertLessEqual(summary['peak_inflight'], 8)

    def test_invalid_bounds_rejected_before_network(self):
        for args in ((0, 1, 1), (8080, 0, 1), (8080, 1, 0), (8080, 10001, 1),
                     (8080, 1, 257)):
            with self.subTest(args=args):
                proc = self.invoke(*args)
                self.assertEqual(proc.returncode, 2)

    def test_non_numeric_target_rejected(self):
        p = subprocess.run([str(self.binary), 'localhost', '80', '1', '1', '0', '100'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn('numeric IPv4', p.stderr)

    def test_real_listener_outage_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_demo(self.binary, Path(tmp), count=150, interval_ms=8)
            self.assertEqual(result['status'], 'PASS', result)
            phases = result['phase_observations']
            self.assertGreater(phases['healthy']['successes'], 0)
            self.assertGreater(phases['listener_down']['failures'], 0)
            self.assertGreater(phases['recovered']['successes'], 0)
            self.assertEqual(sum(x['attempts'] for x in phases.values()), 150)

    def test_classify_uses_monotonic_sample_start(self):
        rows = [{'start_monotonic_ns': t, 'ok': ok} for t, ok in
                ((10, True), (20, False), (30, True))]
        report = classify(rows, 15, 25)
        self.assertEqual(report['healthy']['successes'], 1)
        self.assertEqual(report['listener_down']['failures'], 1)
        self.assertEqual(report['recovered']['successes'], 1)


if __name__ == '__main__':
    unittest.main()
