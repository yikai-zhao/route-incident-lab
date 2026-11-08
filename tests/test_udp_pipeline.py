"""Tests that exercise real Linux UDP sockets and the native two-thread pipeline."""
import json
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
BINARY = BASE / 'build' / 'udp_pipeline'
MAGIC = 0x52494C31
GROUP = '239.255.42.99'


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def send(port, frames, interval=.012, multicast=True):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        if multicast:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton('127.0.0.1'))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        for stream, seq in frames:
            s.sendto(struct.pack('!IIQ', MAGIC, stream, seq),
                     (GROUP if multicast else '127.0.0.1', port))
            time.sleep(interval)


def probe(frames, cap, delay_us, interval=.012, multicast=True, malformed=False):
    port = free_port()
    with tempfile.TemporaryDirectory() as directory:
        raw = Path(directory) / 'pipeline.jsonl'
        cmd = [str(BINARY), GROUP if multicast else '-', str(port),
               str(len(frames) + bool(malformed)), '3500', str(cap), str(delay_us), str(raw)]
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(.20)
            if malformed:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(b'BAD', ('127.0.0.1', port))
                time.sleep(.04)
            send(port, frames, interval=interval, multicast=multicast)
            stdout, stderr = proc.communicate(timeout=6)
            assert proc.returncode == 0, stderr + stdout
            summary = json.loads(stdout)
            rows = [json.loads(x) for x in raw.read_text().splitlines()]
            return summary, rows
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


class PipelineTests(unittest.TestCase):
    def test_real_multicast_ordered_delivery(self):
        frames = [(7, n) for n in range(1, 21)]
        summary, rows = probe(frames, 128, 0)
        self.assertEqual(summary['received_datagrams'], 20)
        self.assertEqual(summary['processed'], 20)
        self.assertEqual(summary['application_queue_drops'], 0)
        self.assertEqual([r['seq'] for r in rows], list(range(1, 21)))
        self.assertEqual(summary['forward_gap_events'], 0)
        self.assertGreaterEqual(summary['queue_p99_us'], 0)

    def test_real_multicast_software_queue_pressure(self):
        frames = [(7, n) for n in range(1, 121)]
        summary, rows = probe(frames, 4, 6000, interval=.001)
        self.assertEqual(summary['received_datagrams'], 120)
        self.assertEqual(summary['valid_frames'], 120)
        self.assertGreater(summary['application_queue_drops'], 0)
        self.assertEqual(summary['processed'] + summary['application_queue_drops'], 120)
        self.assertEqual(len(rows), summary['processed'])

    def test_synthetic_sequence_skips_not_claimed_as_packet_loss(self):
        frames = [(7, n) for n in [1, 2, 4, 5, 7]]
        summary, rows = probe(frames, 16, 0)
        self.assertEqual(summary['forward_gap_events'], 2)
        self.assertEqual(summary['forward_missing_candidates'], 2)
        self.assertEqual(summary['application_queue_drops'], 0)

    def test_duplicate_and_reordering_have_distinct_classifications(self):
        frames = [(7, n) for n in [1, 2, 4, 3, 4, 5]]
        summary, rows = probe(frames, 16, 0)
        self.assertEqual(summary['duplicate_events'], 1)
        self.assertEqual(summary['reordered_events'], 1)
        self.assertEqual(summary['forward_gap_events'], 1)
        self.assertEqual([r['classification'] for r in rows],
                         ['first', 'in_order', 'forward_jump', 'out_of_order',
                          'duplicate', 'in_order'])

    def test_two_stream_ids_and_malformed_unicast(self):
        frames = [(1, 1), (2, 20), (1, 2), (2, 21)]
        summary, rows = probe(frames, 32, 0, multicast=False, malformed=True)
        self.assertEqual(summary['malformed_frames'], 1)
        self.assertEqual(summary['valid_frames'], 4)
        self.assertEqual(summary['processed'], 4)
        self.assertEqual([(r['stream_id'], r['seq']) for r in rows], frames)

    def test_no_data_returns_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([str(BINARY), '-', str(free_port()), '5', '150', '8',
                                   '0', str(Path(d) / 'raw.jsonl')], text=True,
                                  capture_output=True, timeout=3)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertFalse(json.loads(proc.stdout)['complete'])

    def test_wrong_ring_capacity_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            for cap in ('0', '3', '65537'):
                proc = subprocess.run([str(BINARY), '-', '9998', '5', '200', cap,
                                       '0', str(Path(d) / 'raw.jsonl')],
                                      text=True, capture_output=True, timeout=3)
                self.assertEqual(proc.returncode, 2)


if __name__ == '__main__':
    unittest.main()
