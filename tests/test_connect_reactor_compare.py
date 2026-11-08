"""True C++ poll/epoll socket I/O with bounded test-only localhost listeners."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_connect_reactors import run as run_pairs
from demo_epoll_local import LocalListener


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux-only socket readiness')
class PairedReactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'compare'
        p = subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic',
                            str(ROOT / 'cpp/connect_reactor_compare.cpp'), '-o', str(cls.binary)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def probe(self, backend, port, count, concurrency, out, interval=0, timeout=150):
        return subprocess.run([str(self.binary), backend, '127.0.0.1', str(port),
                               str(count), str(concurrency), str(interval), str(timeout), str(out)],
                              capture_output=True, text=True, timeout=15)

    def test_each_backend_uses_real_listening_socket(self):
        server = LocalListener()
        try:
            with tempfile.TemporaryDirectory() as d:
                for backend in ('poll', 'epoll'):
                    with self.subTest(backend=backend):
                        path = Path(d) / f'{backend}.jsonl'
                        p = self.probe(backend, server.port, 100, 12, path, 1)
                        self.assertEqual(p.returncode, 0, p.stderr)
                        report = json.loads(p.stdout)
                        rows = [json.loads(x) for x in path.read_text().splitlines()]
                        self.assertEqual(report['successes'], 100)
                        self.assertEqual(report['failures'], 0)
                        self.assertEqual(len(rows), 100)
                        self.assertEqual([x['seq'] for x in rows], list(range(100)))
                        self.assertLessEqual(report['peak_inflight'], 12)
                        self.assertIsNotNone(report['p99_ms'])
        finally: server.stop()

    def test_closed_port_has_null_success_percentiles(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            for backend in ('poll', 'epoll'):
                p = self.probe(backend, port, 30, 8, Path(d)/f'{backend}.jsonl')
                self.assertEqual(p.returncode, 0, p.stderr)
                report = json.loads(p.stdout)
                self.assertEqual(report['failures'], 30)
                self.assertIsNone(report['p99_ms'])

    def test_genuine_multi_socket_backlog_concurrency(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen(1)
            with tempfile.TemporaryDirectory() as d:
                for backend in ('poll', 'epoll'):
                    p = self.probe(backend, server.getsockname()[1], 70, 8,
                                   Path(d)/f'{backend}.jsonl', timeout=80)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    s = json.loads(p.stdout)
                    self.assertGreater(s['peak_inflight'], 1)
                    self.assertLessEqual(s['peak_inflight'], 8)
                    self.assertEqual(s['successes']+s['failures'], 70)

    def test_invalid_backend_and_bounds_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for backend, port, count, inflight in [('bad', 8000, 5, 1), ('poll', 0, 5, 1),
                                                   ('epoll', 8000, 0, 1), ('poll', 8000, 1, 0)]:
                p = subprocess.run([str(self.binary), backend, '127.0.0.1', str(port),
                                    str(count), str(inflight), '0', '100', str(Path(d)/'out')],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 2)

    def test_real_paired_trial_driver_counts_and_sha(self):
        with tempfile.TemporaryDirectory() as d:
            report = run_pairs(self.binary, Path(d), pairs=1, count=60)
            self.assertEqual(len(report['trials']), 4)
            for item in report['trials']:
                self.assertEqual(item['summary']['attempts'], item['input']['count'])
                self.assertEqual(len(item['samples_sha256']), 64)
                self.assertTrue((Path(d)/item['samples_file']).exists())
            self.assertTrue((Path(d)/'benchmark.json').exists())


if __name__ == '__main__': unittest.main()
