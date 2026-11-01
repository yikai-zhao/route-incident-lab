import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from routeincident.scenarios import analyze_three_phases
from routeincident.analyze import compare
from routeincident.capture import validate_config, _command


def load(name):
    return json.loads((ROOT / "examples/scenarios" / (name + ".json")).read_text())


class ReproducibleScenarios(unittest.TestCase):
    def test_three_distinct_synthetic_cases(self):
        for scenario in ("neighbor_outage", "backup_failover", "route_ok_blackhole"):
            with self.subTest(scenario=scenario):
                case = load(scenario)
                report = analyze_three_phases(**case, scenario=scenario)
                self.assertTrue(report["assertions_passed"], report["assertions"])
                self.assertEqual(report["evidence_level"], "synthetic_fixture")
                self.assertEqual(len(report["capture_windows"]), 3)
                self.assertEqual(len(report["timeline"]), 3)
                self.assertTrue(all(e["kind"] == "snapshot_window" for e in report["timeline"]))

    def test_synthetic_snapshots_match_lab_wiring(self):
        backup = load("backup_failover")["fault"]["devices"][0]["commands"]
        kernel = json.loads(backup["kernel_routes"]["stdout"])[0]
        frr = json.loads(backup["control_routes"]["stdout"])
        self.assertEqual(kernel["gateway"], "10.0.13.2")
        self.assertEqual(kernel["dev"], "eth2")
        self.assertEqual(frr["10.10.2.0/24"][0]["nexthops"][0]["ip"], "10.0.13.2")
        blackhole = load("route_ok_blackhole")["fault"]["devices"][0]["commands"]
        rules = json.loads(blackhole["policy_rules"]["stdout"])
        self.assertTrue(any(row.get("action") == "unreachable"
                            and row.get("dst") == "10.10.2.0/24" for row in rules))

    def test_blackhole_keeps_route_and_changes_policy(self):
        case = load("route_ok_blackhole")
        findings = compare(case["before"], case["fault"])["findings"]
        kinds = {x["kind"] for x in findings}
        self.assertIn("policy_rules_changed", kinds)
        self.assertNotIn("route_removed", kinds)
        report = analyze_three_phases(**case, scenario="route_ok_blackhole")
        self.assertIn("route_present_but_application_unreachable", {d["kind"] for d in report["diagnostic_leads"]})
        self.assertEqual(report["diagnostic_leads"][0]["classification"], "observation_not_root_cause")

    def test_failed_assertion_not_counted_as_success(self):
        case = load("route_ok_blackhole")
        case["fault"]["probes"][0]["failures"] = 0
        report = analyze_three_phases(**case, scenario="route_ok_blackhole")
        self.assertFalse(report["assertions_passed"])
        self.assertFalse(report["assertions"]["application_unreachable"])

    def test_missing_probe_is_unknown_not_failure(self):
        case = load("neighbor_outage")
        case["fault"]["probes"] = []
        report = analyze_three_phases(**case, scenario="neighbor_outage")
        self.assertFalse(report["assertions_passed"])
        self.assertFalse(report["assertions"]["application_unreachable"])

    def test_timeline_distinguishes_action_and_observation(self):
        case = load("route_ok_blackhole")
        actions = [{"performed_at_utc":"2026-11-01T12:00:30+00:00",
                    "command_finished_utc":"2026-11-01T12:00:30.200000+00:00",
                    "test_container":"clab-routeincident-r1", "argv":["ip","rule","add"]}]
        report = analyze_three_phases(**case, scenario="route_ok_blackhole", action_records=actions)
        self.assertTrue(any(t["kind"] == "local_test_action" for t in report["timeline"]))
        self.assertTrue(any(t["precision"] == "observed_within_window_not_exact_state_transition"
                            for t in report["timeline"]))

    def test_unknown_scenario_rejected(self):
        case = load("neighbor_outage")
        with self.assertRaises(ValueError):
            analyze_three_phases(**case, scenario="magic")

    def test_policy_detection_skipped_if_capture_invalid(self):
        case = load("route_ok_blackhole")
        case["fault"]["devices"][0]["commands"]["policy_rules"]["ok"] = False
        findings = compare(case["before"], case["fault"])["findings"]
        self.assertNotIn("policy_rules_changed", {x["kind"] for x in findings})

    def test_docker_transport_limited_to_lab_names(self):
        config = json.loads((ROOT / "labs/three-router/collector-config.json").read_text())
        validate_config(config)
        arg = _command(config["devices"][0], ("ip", "-j", "route", "show"))
        self.assertEqual(arg, ["docker", "exec", "clab-routeincident-r1", "ip", "-j", "route", "show"])
        bad = copy.deepcopy(config)
        bad["devices"][0]["container"] = "production-router"
        with self.assertRaises(ValueError):
            validate_config(bad)

    def test_no_automatic_faults_or_cloud_workflows(self):
        self.assertFalse((ROOT / ".github/workflows").exists())
        self.assertIn("--allow-faults", (ROOT / "scripts/lab_run.py").read_text())


if __name__ == "__main__":
    unittest.main()
