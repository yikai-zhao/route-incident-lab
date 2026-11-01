import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from routeincident.delivery import summarize_delivery, LocalLabHttpSampler


class ContinuousDeliveryTests(unittest.TestCase):
    def test_bounded_outage(self):
        rows = [{"ok": ok, "observed_monotonic_ns": n * 100_000_000}
                for n, ok in enumerate([True, True, False, False, False, True, True])]
        r = summarize_delivery(rows, fault_started_ns=150_000_000)
        self.assertTrue(r["outage_observed"])
        self.assertEqual(r["observed_failure_span_ms"], 200)
        self.assertEqual(r["possible_outage_window_ms"], 400)
        self.assertTrue(r["recovered_delivery_observed"])

    def test_no_outage_is_not_zero_downtime(self):
        rows = [{"ok": True, "observed_monotonic_ns": n * 100_000_000}
                for n in range(3)]
        r = summarize_delivery(rows, fault_started_ns=10)
        self.assertFalse(r["outage_observed"])
        self.assertIsNone(r["observed_failure_span_ms"])

    def test_missing_recovery_not_claimed(self):
        rows = [{"ok": ok, "observed_monotonic_ns": n * 100_000_000}
                for n, ok in enumerate([True, False, False])]
        r = summarize_delivery(rows, fault_started_ns=1)
        self.assertFalse(r["recovered_delivery_observed"])
        self.assertIsNone(r["possible_outage_window_ms"])

    def test_sampler_rejects_nonlab_targets(self):
        with self.assertRaises(ValueError):
            LocalLabHttpSampler(host="example.com")


if __name__ == "__main__":
    unittest.main()
