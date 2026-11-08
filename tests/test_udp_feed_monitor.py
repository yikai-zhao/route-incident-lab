"""Real local UDP unicast/multicast regressions for a synthetic sequence feed."""
from __future__ import annotations

import json
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from demo_multicast_feed import run as multicast_demo, MAGIC  # noqa: E402


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('g++'),
                     'Linux and g++ required for real UDP feed tests')
class UdpFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.workspace.name) / 'udp_feed_monitor'
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic',
                        str(ROOT / 'cpp/udp_feed_monitor.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls): cls.workspace.cleanup()

    def receiver(self, port, count, duration_ms, dest):
        return subprocess.Popen([str(self.binary), '-', str(port), str(count),
                                 str(duration_ms), str(dest)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    @staticmethod
    def free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    @staticmethod
    def send(port, items):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for stream, seq in items:
                s.sendto(struct.pack('!IIQ', MAGIC, stream, seq), ('127.0.0.1', port))
                time.sleep(.003)

    def test_multicast_loopback_real_packets(self):
        with tempfile.TemporaryDirectory() as d:
            result = multicast_demo(self.binary, Path(d))
            self.assertEqual(result['status'], 'PASS', result)
            self.assertEqual(result['receiver']['received'], 10)
            self.assertEqual(result['receiver']['forward_missing_candidates'], 4)
            rows = [json.loads(r) for r in (Path(d)/'feed.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 10)
            if result['receiver']['kernel_receive_timestamp_enabled']:
                self.assertTrue(all(type(r['kernel_receive_realtime_ns']) is int for r in rows))

    def test_real_unicast_detects_duplicate_and_reorder(self):
        port = self.free_port()
        with tempfile.TemporaryDirectory() as d:
            p = self.receiver(port, 6, 2000, Path(d)/'feed.jsonl')
            try:
                time.sleep(.10)
                self.send(port, [(3, s) for s in [1, 2, 4, 3, 4, 5]])
                out, err = p.communicate(timeout=4)
                self.assertEqual(p.returncode, 0, err)
                x = json.loads(out)
                self.assertEqual((x['received'], x['forward_jump'], x['out_of_order'], x['duplicate']),
                                 (6, 1, 1, 1))
                self.assertEqual(x['forward_missing_candidates'], 1)
                self.assertEqual(x['streams'], 1)
            finally:
                if p.poll() is None: p.kill(); p.communicate()

    def test_two_independent_streams(self):
        port = self.free_port()
        with tempfile.TemporaryDirectory() as d:
            p = self.receiver(port, 4, 2000, Path(d)/'feed.jsonl')
            try:
                time.sleep(.10)
                self.send(port, [(1, 1), (2, 8), (1, 1), (2, 9)])
                out, err = p.communicate(timeout=4)
                self.assertEqual(p.returncode, 0, err)
                result = json.loads(out)
                self.assertEqual((result['streams'], result['first'], result['duplicate'], result['in_order']),
                                 (2, 2, 1, 1))
            finally:
                if p.poll() is None: p.kill(); p.communicate()

    def test_invalid_numeric_arguments_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for port, count, timeout in [(0, 2, 300), (1234, 0, 300), (1234, 2, 0)]:
                with self.subTest(port=port, count=count, timeout=timeout):
                    x = subprocess.run([str(self.binary), '-', str(port), str(count),
                                        str(timeout), str(Path(d)/'out')], capture_output=True)
                    self.assertEqual(x.returncode, 2)

    def test_no_packets_exits_incomplete_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([str(self.binary), '-', str(self.free_port()), '1', '150',
                                   str(Path(d)/'out')], capture_output=True, text=True, timeout=3)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertFalse(json.loads(proc.stdout)['complete'])

    def test_malformed_payload_ignored(self):
        port = self.free_port()
        with tempfile.TemporaryDirectory() as d:
            p = self.receiver(port, 1, 2000, Path(d)/'feed.jsonl')
            try:
                time.sleep(.10)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(b'not-a-valid-feed', ('127.0.0.1', port))
                    time.sleep(.03)
                self.send(port, [(10, 42)])
                out, err = p.communicate(timeout=4)
                self.assertEqual(p.returncode, 0, err)
                result = json.loads(out)
                self.assertEqual(result['malformed'], 1)
                self.assertEqual(result['received'], 1)
            finally:
                if p.poll() is None: p.kill(); p.communicate()


if __name__ == '__main__': unittest.main()
