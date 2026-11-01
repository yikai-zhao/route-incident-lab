import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class ThreeRouterTopologyTests(unittest.TestCase):
    def test_required_links_and_nodes(self):
        yml = (ROOT / "labs/three-router/topology.clab.yml").read_text()
        for node in ("r1", "r2", "r3", "h1", "h2"):
            self.assertIn(f"    {node}:", yml)
        for link in (
            '["r1:eth1", "r2:eth1"]',
            '["r1:eth2", "r3:eth1"]',
            '["r2:eth2", "r3:eth2"]',
            '["r1:eth3", "h1:eth1"]',
            '["r2:eth3", "h2:eth1"]',
        ):
            self.assertIn(link, yml)
        self.assertIn('routeincident.testbed: "true"', yml)

    def test_bgp_neighbour_symmetry(self):
        conf = {name: (ROOT / f"labs/three-router/configs/{name}.conf").read_text()
                for name in ("r1", "r2", "r3")}
        self.assertIn("neighbor 10.0.12.2 remote-as 65102", conf["r1"])
        self.assertIn("neighbor 10.0.12.1 remote-as 65101", conf["r2"])
        self.assertIn("neighbor 10.0.13.2 remote-as 65103", conf["r1"])
        self.assertIn("neighbor 10.0.13.1 remote-as 65101", conf["r3"])
        self.assertIn("neighbor 10.0.23.2 remote-as 65103", conf["r2"])
        self.assertIn("neighbor 10.0.23.1 remote-as 65102", conf["r3"])
        self.assertIn("network 10.10.1.0/24", conf["r1"])
        self.assertIn("network 10.10.2.0/24", conf["r2"])

    def test_lab_actions_always_require_opt_in(self):
        controller = (ROOT / "scripts/lab_run.py").read_text()
        self.assertIn('if not a.allow_faults:', controller)
        self.assertIn('def check_lab_health()', controller)
        self.assertIn('routeincident.testbed=true', controller)


if __name__ == "__main__":
    unittest.main()
