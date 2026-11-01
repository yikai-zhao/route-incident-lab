import importlib.util
import http.server
import socket
import socketserver
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tcp_http_probe", ROOT / "labs/three-router/tcp_http_probe.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestHttpDelivery(unittest.TestCase):
    def test_real_http_response(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            def log_message(self, *_):
                pass
        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thr = threading.Thread(target=server.handle_request)
            thr.start()
            result = module.one("127.0.0.1", server.server_address[1], 0.5)
            thr.join(timeout=2)
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["delivery_ms"], result["connect_ms"])

    def test_tcp_connection_without_http_is_not_delivery(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(128)
                self.request.sendall(b"not HTTP\r\n")
        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thr = threading.Thread(target=server.handle_request)
            thr.start()
            result = module.one("127.0.0.1", server.server_address[1], 0.5)
            thr.join(timeout=2)
            self.assertFalse(result["ok"])

    def test_refused_connection_reported_without_throwing(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        result = module.one("127.0.0.1", port, 0.1)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["error"])


if __name__ == "__main__":
    unittest.main()
