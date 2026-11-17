"""Offline tests for failure-closed FRR evidence; never execute Docker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_frr_campaign.py"
spec = importlib.util.spec_from_file_location("run_frr_campaign", SCRIPT)
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


def valid_trial(base):
    folder = base / "round-1" / "backup_failover"
    folder.mkdir(parents=True)
    data = {
        "bgp-preflight.json": {node: {"a": "Established", "b": "Established"}
                              for node in ("r1", "r2", "r3")},
        "before.json": {"phase": "before"}, "fault.json": {"phase": "fault"},
        "recovered.json": {"phase": "recovered"},
        "continuous-http-samples.json": {"samples": [{"ok": True}], "errors": []},
        "report.json": {"assertions_passed": True, "continuous_delivery": {"attempts": 1}},
        "audit.json": {"verdict": "PASS", "checks": {"artifacts": True}},
    }
    for name, value in data.items():
        (folder / name).write_text(json.dumps(value))
    return folder


class EvidenceGateTests(unittest.TestCase):
    def test_missing_campaign_never_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = campaign.inspect_campaign(Path(tmp), 3)
            self.assertEqual(data["expected_trials"], 9)
            self.assertEqual(data["passed_trials"], 0)
            self.assertEqual(data["status"], "INCOMPLETE_OR_FAILED")

    def test_valid_then_tampered(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = valid_trial(Path(tmp))
            self.assertEqual(campaign.inspect_trial(folder, "backup_failover", 1)["status"], "PASS")
            (folder / "audit.json").write_text('{"verdict":"PASS","checks":{"x":false}}')
            self.assertEqual(campaign.inspect_trial(folder, "backup_failover", 1)["status"], "FAIL")
            (folder / "audit.json").unlink()
            self.assertIn("missing audit.json",
                          campaign.inspect_trial(folder, "backup_failover", 1)["errors"])

    def test_bgp_and_http_evidence_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = valid_trial(Path(tmp))
            (folder / "bgp-preflight.json").write_text(json.dumps(
                {n: {"a": "Idle", "b": "Established"} for n in ("r1", "r2", "r3")}))
            self.assertEqual(campaign.inspect_trial(folder, "backup_failover", 1)["status"], "FAIL")
            (folder / "continuous-http-samples.json").write_text('{"samples":[],"errors":[]}')
            self.assertEqual(campaign.inspect_trial(folder, "backup_failover", 1)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
