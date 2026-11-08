"""Local artifact consistency tests. The test data are constructed, not live FRR evidence."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts/audit_run.py'
spec = importlib.util.spec_from_file_location('audit_run', PATH)
audit_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_run)


def put(folder, stem, obj):
    (folder / (stem + '.json')).write_text(json.dumps(obj))


def fake_run(folder):
    """Self-consistent mock evidence; does not attest to real routing activity."""
    preflight = {'r1': {'p1': 'Established', 'p2': 'Established'},
                 'r2': {'p1': 'Established', 'p2': 'Established'},
                 'r3': {'p1': 'Established', 'p2': 'Established'}}
    put(folder, 'bgp-preflight', preflight)
    for phase, failed in (('before', False), ('fault', True), ('recovered', False)):
        devices = [{'name': name, 'commands': {k: {'ok': True} for k in audit_run.REQUIRED}}
                   for name in audit_run.EXPECTED]
        probe = {'name': 'h1-to-h2-http', 'attempts': 2,
                 'successes': 0 if failed else 2, 'failures': 2 if failed else 0}
        put(folder, phase, {'phase': phase, 'capture_mode': 'live_observation',
            'lab_attestation': {'lab': 'routeincident', 'preflight_passed': True},
            'devices': devices, 'probes': [probe]})
    samples = [{'ok': ok, 'observed_monotonic_ns': i * 10000}
               for i, ok in enumerate((True, False, False, True))]
    put(folder, 'continuous-http-samples', {'source': 'live_local_http_probe',
                                            'errors': [], 'samples': samples})
    put(folder, 'report', {
        'scenario': 'neighbor_outage', 'evidence_level': 'local_lab_observation',
        'assertions': {'route_withdrawn': True, 'application_unreachable': True},
        'assertions_passed': True,
        'actions': [{'test_container': 'clab-routeincident-r1', 'argv': ['ip', 'link', 'set'],
                     'performed_at_utc': 'a', 'command_finished_utc': 'b'}],
        'continuous_delivery': {'attempts': 4, 'successes': 2, 'failures': 2,
                                'probe_errors': [], 'observed_failure_span_ms': 0.01}})


class AuditRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'neighbor_outage'
        self.folder.mkdir()
        fake_run(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_mock_is_consistent_not_independent_proof(self):
        result = audit_run.audit(self.folder)
        self.assertEqual(result['verdict'], 'PASS')
        self.assertIn('self-reported', ' '.join(result['limitations']))
        self.assertIn('Evidence gate: PASS', audit_run.markdown(result))

    def test_missing_snapshot_fails_closed(self):
        (self.folder / 'fault.json').unlink()
        result = audit_run.audit(self.folder)
        self.assertEqual(result['verdict'], 'FAIL')
        self.assertFalse(result['checks']['file:fault'])

    def test_synthetic_report_cannot_pass(self):
        report = json.loads((self.folder / 'report.json').read_text())
        report['evidence_level'] = 'synthetic_fixture'
        put(self.folder, 'report', report)
        self.assertFalse(audit_run.audit(self.folder)['checks']['locally_attested_report'])

    def test_raw_probe_mismatch_cannot_pass(self):
        stream = json.loads((self.folder / 'continuous-http-samples.json').read_text())
        stream['samples'][1]['ok'] = True
        put(self.folder, 'continuous-http-samples', stream)
        self.assertFalse(audit_run.audit(self.folder)['checks']['continuous_report_matches_raw'])

    def test_bgp_peer_not_established_cannot_pass(self):
        peers = json.loads((self.folder / 'bgp-preflight.json').read_text())
        peers['r3']['p2'] = 'Idle'
        put(self.folder, 'bgp-preflight', peers)
        self.assertFalse(audit_run.audit(self.folder)['checks']['all_six_ebgp_views_established'])


if __name__ == '__main__':
    unittest.main()
