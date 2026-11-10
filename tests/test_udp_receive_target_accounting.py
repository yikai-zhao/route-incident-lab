"""Linux real-socket regression: recvmmsg must not silently consume beyond target."""
import json
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BINARY = Path(__file__).resolve().parents[1] / 'build' / 'udp_pipeline'


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ReceiveAccountingTests(unittest.TestCase):
    def run_case(self, target, mode):
        port = free_udp_port()
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [str(BINARY), '-', str(port), str(target), '2000', '1024',
                 '0', str(Path(tmp) / 'raw.jsonl'), f'--rx={mode}', '--sink=none'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(0.08)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    for i in range(64):
                        sender.sendto(struct.pack('!IIQ', 0x52494C31, 1, i+1),
                                      ('127.0.0.1', port))
                out, err = proc.communicate(timeout=4)
                self.assertEqual(proc.returncode, 0, err)
                data = json.loads(out)
                self.assertTrue(data['complete'])
                self.assertEqual(data['target'], target)
                self.assertEqual(data['received_datagrams'], target)
                # Core invariant: every datagram consumed by recvmsg/recvmmsg
                # must be represented in receiver counters, even at batch tail.
                self.assertEqual(data['rx_datagrams_consumed_by_syscalls'], target)
                self.assertEqual(data['valid_frames'] + data['malformed_frames'], target)
                self.assertEqual(data['processed'] + data['application_queue_drops'], target)
                self.assertLessEqual(data['rx_successful_syscalls'], target)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def test_batch_target_boundaries(self):
        for target in (1, 3, 15, 16, 17, 31):
            with self.subTest(target=target):
                self.run_case(target, 'batch')

    def test_scalar_target_boundaries(self):
        for target in (1, 3, 17):
            with self.subTest(target=target):
                self.run_case(target, 'scalar')


if __name__ == '__main__':
    unittest.main()
