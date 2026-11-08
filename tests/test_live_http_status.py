"""Loopback socket tests: real HTTP bytes, no FRR or Docker required."""
import importlib.util
import socket
import threading
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'labs/three-router/tcp_http_probe.py'
spec = importlib.util.spec_from_file_location('lab_http_probe', PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class OneShotServer:
    def __init__(self, chunks):
        self.chunks = chunks
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.socket.accept()
            with conn:
                conn.settimeout(2)
                conn.recv(1024)
                for chunk in self.chunks:
                    conn.sendall(chunk)
        finally:
            self.socket.close()

    def request(self):
        result = probe.one('127.0.0.1', self.port, 1.0)
        self.thread.join(timeout=2)
        return result


class HttpStatusTests(unittest.TestCase):
    def test_fragmented_http_200(self):
        result = OneShotServer([b'HT', b'TP/1.1 2', b'00 OK\r\nContent-Length: 0\r\n\r\n']).request()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['http_status'], 200)
        self.assertIsNotNone(result['delivery_ms'])

    def test_http_404_is_not_delivery_success(self):
        result = OneShotServer([b'HTTP/1.0 404 Not Found\r\n\r\n']).request()
        self.assertFalse(result['ok'])
        self.assertEqual(result['http_status'], 404)

    def test_http_2000_is_invalid(self):
        result = OneShotServer([b'HTTP/1.0 2000 Fake\r\n\r\n']).request()
        self.assertFalse(result['ok'])
        self.assertIsNone(result['http_status'])

    def test_incomplete_status_not_accepted(self):
        result = OneShotServer([b'HTTP/1.1 200']).request()
        self.assertFalse(result['ok'])

    def test_oversized_status_rejected(self):
        result = OneShotServer([b'HTTP/1.1 200 ' + b'x' * 5000 + b'\r\n']).request()
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
