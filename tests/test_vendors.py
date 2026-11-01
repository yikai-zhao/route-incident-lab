import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from routeincident.vendors import parse_vendor_commands, parse_bgp, parse_routes, parse_interfaces
from routeincident.analyze import compare


class VendorParserTests(unittest.TestCase):
    def test_ios_bgp_and_routes(self):
        summary = ("Neighbor V AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd\n"
                   "10.0.12.2 4 65102 10 10 0 0 0 00:10:00 2\n"
                   "10.0.13.2 4 65103 0 0 0 0 0 never Idle\n")
        peers = parse_bgp("ios", summary)
        self.assertEqual(peers["10.0.12.2"]["state"], "Established")
        self.assertEqual(peers["10.0.13.2"]["state"], "Idle")
        self.assertEqual(parse_routes("ios", "B    10.10.2.0/24 [20/0] via 10.0.12.2, 00:00:01")["10.10.2.0/24"]["nexthop"], "10.0.12.2")

    def test_eos_bgp_and_interfaces(self):
        self.assertEqual(parse_bgp("eos", "10.0.13.2 4 65103 1 1 0 0 0 00:00:05 1")["10.0.13.2"]["state"], "Established")
        self.assertEqual(parse_interfaces("eos", "Et1 connected 1 full 10G 10GBASE-SR")["Et1"]["status"], "connected")

    def test_junos_cli(self):
        bgp = "Peer AS InPkt OutPkt OutQ Flaps Last Up/Dwn State\n10.0.12.2 65102 100 99 0 0 0 00:15:00 Establ\n"
        self.assertEqual(parse_bgp("junos", bgp)["10.0.12.2"]["state"], "Established")
        routes = ("10.10.2.0/24 *[BGP/170] 00:00:12, localpref 100\n"
                  "                    > to 10.0.12.2 via ge-0/0/0.0\n")
        self.assertEqual(parse_routes("junos", routes)["10.10.2.0/24"]["protocol"], "BGP")
        self.assertIn("ge-0/0/0", parse_interfaces("junos", "ge-0/0/0 up up inet 10.0.12.1/30"))

    def test_unrecognized_not_assumed_healthy(self):
        self.assertEqual(parse_bgp("ios", "10.0.12.2 garbage"), {})
        result = parse_vendor_commands("ios", {
            "bgp_raw": {"ok": True, "stdout": "unknown version layout"},
            "routes_raw": {"ok": False, "stdout": ""},
        })
        self.assertEqual(result["parse_status"]["bgp_peers"], "unrecognized_or_empty")
        self.assertEqual(result["parse_status"]["routes"], "unavailable")

    def test_vendor_structured_changes(self):
        def snapshot(bgp):
            return {"devices": [{"name": "sw1", "profile": "ios", "commands": {},
                                  "structured": {"vendor": "ios", "bgp_peers":
                                    {"10.0.12.2": {"state": bgp}}, "routes": {},
                                    "interfaces": {}, "parse_status": {"bgp_peers": "parsed", "routes": "parsed", "interfaces": "parsed"}}}]}
        kinds = {f["kind"] for f in compare(snapshot("Established"), snapshot("Idle"))["findings"]}
        self.assertIn("vendor_bgp_peer_changed", kinds)


if __name__ == "__main__":
    unittest.main()
