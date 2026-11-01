import json
import socket
import socketserver
import threading
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routeincident.analyze import _bgp_peers, _route_state, analyze_snapshot, compare
from routeincident.capture import validate_config, _command
from routeincident.probe import _quantile, tcp_probe


def fixture(name):
    return json.loads((ROOT / "examples" / name).read_text())


class AnalyzeTests(unittest.TestCase):
    def test_failure_detected(self):
        report = compare(fixture("healthy.json"), fixture("route-lost.json"))
        kinds = {e["kind"] for e in report["findings"]}
        self.assertIn("route_removed", kinds)
        self.assertIn("rib_fib_mismatch", kinds)
        self.assertIn("connect_failure_increased", kinds)
        self.assertGreaterEqual(report["summary"]["warnings"], 3)

    def test_recovery(self):
        report = compare(fixture("route-lost.json"), fixture("recovered.json"))
        kinds = {e["kind"] for e in report["findings"]}
        self.assertIn("route_added", kinds)
        self.assertNotIn("rib_fib_mismatch", kinds)

    def test_stable(self):
        snapshot = fixture("healthy.json")
        self.assertEqual(compare(snapshot, snapshot)["summary"]["total"], 0)

    def test_exact_prefix_watch(self):
        snapshot = fixture("route-lost.json")
        snapshot["devices"][0]["watch_prefixes"] = ["10.88.0.0/24"]
        self.assertEqual(analyze_snapshot(snapshot), [])

    def test_peer_state_changes(self):
        before = fixture("healthy.json")
        after = fixture("healthy.json")
        after["devices"][0]["commands"]["bgp_peers"]["stdout"] = json.dumps({
            "ipv4Unicast": {"peers": {"10.0.12.2": {"state": "Idle"}}}})
        self.assertIn("bgp_peer_state_changed", {x["kind"] for x in compare(before, after)["findings"]})

    def test_raw_diff(self):
        before = fixture("healthy.json")
        after = fixture("healthy.json")
        before["devices"][0]["commands"]["routes_raw"] = {"ok": True, "stdout": "a\n"}
        after["devices"][0]["commands"]["routes_raw"] = {"ok": True, "stdout": "b\n"}
        self.assertIn("raw_cli_changed", {x["kind"] for x in compare(before, after)["findings"]})

    def test_failed_capture_does_not_fake_route_loss(self):
        before = fixture("healthy.json")
        after = fixture("healthy.json")
        after["devices"][0]["commands"]["kernel_routes"]["ok"] = False
        kinds = {x["kind"] for x in compare(before, after)["findings"]}
        self.assertIn("route_snapshot_unavailable", kinds)
        self.assertNotIn("route_removed", kinds)

    def test_command_failure(self):
        snapshot = fixture("healthy.json")
        snapshot["devices"][0]["commands"]["kernel_routes"]["ok"] = False
        self.assertIn("capture_failed", {x["kind"] for x in analyze_snapshot(snapshot)})

    def test_interface_and_neighbor_transition(self):
        before = fixture("healthy.json")
        after = fixture("healthy.json")
        for snap, link_state, arp_state in ((before, "UP", "REACHABLE"), (after, "DOWN", "FAILED")):
            cmds = snap["devices"][0]["commands"]
            cmds["interfaces"] = {"ok": True, "stdout": json.dumps([
                {"ifname": "eth1", "operstate": link_state, "flags": ["UP"] if link_state == "UP" else []}])}
            cmds["arp_neighbors"] = {"ok": True, "stdout": json.dumps([
                {"dev": "eth1", "dst": "10.0.12.2", "state": arp_state}])}
        kinds = {x["kind"] for x in compare(before, after)["findings"]}
        self.assertIn("interface_state_changed", kinds)
        self.assertIn("arp_neighbor_state_changed", kinds)

    def test_real_local_tcp_probe(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                pass
        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 0.2
            t = threading.Thread(target=lambda: [server.handle_request() for _ in range(3)])
            t.start()
            results = tcp_probe("loopback", "127.0.0.1", server.server_address[1],
                                count=3, timeout_s=0.5, interval_s=0)
            t.join(timeout=2)
            self.assertEqual(results["failures"], 0)
            self.assertEqual(results["successes"], 3)
            self.assertIsNotNone(results["p95_ms"])

    def test_quantile(self):
        self.assertEqual(_quantile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertEqual(_quantile([], 0.5), None)
        self.assertEqual(_quantile([1, 2], 0.95), 1.95)

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            validate_config({"devices": [{"name": "-bad", "profile": "linux"}]})
        with self.assertRaises(ValueError):
            validate_config({"devices": [{"name": "a", "profile": "frr", "transport": "ssh", "host": "-evil", "user": "bob"}]})

    def test_valid_config(self):
        config = json.loads((ROOT / "examples" / "local-config.json").read_text())
        validate_config(config)

    def test_ssh_strict_host_key(self):
        parts = _command({"transport": "ssh", "user": "lab", "host": "192.0.2.1"}, ("vtysh", "-c", "show ip route json"))
        self.assertIn("StrictHostKeyChecking=yes", parts)
        self.assertIn("BatchMode=yes", parts)
        self.assertIn("show ip route json", parts[-1])


if __name__ == "__main__":
    unittest.main()
