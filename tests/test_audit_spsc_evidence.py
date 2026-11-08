import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_spsc_evidence import audit

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        trials = []
        for i in range(4):
            name = 'baseline.jsonl' if i == 0 else f'backpressure-{i}.jsonl'
            n = 64 if i == 0 else 29
            raw = ''.join(json.dumps({'received_monotonic_ns': k,
                'processed_monotonic_ns': k + 2}) + '\n' for k in range(n)).encode()
            (self.folder / name).write_bytes(raw)
            s = {'processed': n, 'valid_frames': 64 if i == 0 else 160,
                 'application_queue_drops': 0 if i == 0 else 160-n,
                 'received_datagrams': 64 if i == 0 else 160,
                 'malformed_frames': 0, 'queue_depth_highwater': 4,
                 'ring_capacity': 64 if i == 0 else 4,
                 'complete': True, 'target': 64 if i == 0 else 160}
            trials.append({'processed_file': name, 'processed_rows_sha256':
                           hashlib.sha256(raw).hexdigest(), 'summary': s})
        self.report = {'status': 'PASS', 'checks': {'accounting': True},
                       'baseline': trials[0], 'pressured_repetitions': trials[1:]}
        self.write()
    def write(self):
        (self.folder / 'report.json').write_text(json.dumps(self.report))
    def test_valid(self):
        self.assertEqual(audit(self.folder)['status'], 'PASS')
    def test_tamper(self):
        with (self.folder / 'baseline.jsonl').open('ab') as f: f.write(b'{}\n')
        self.assertEqual(audit(self.folder)['status'], 'FAIL')
    def test_missing(self):
        (self.folder / 'backpressure-2.jsonl').unlink()
        self.assertEqual(audit(self.folder)['status'], 'FAIL')
    def test_forged_accounting(self):
        self.report['pressured_repetitions'][0]['summary']['application_queue_drops'] = 0
        self.write()
        self.assertEqual(audit(self.folder)['status'], 'FAIL')
    def test_negative_timestamp(self):
        path = self.folder / 'baseline.jsonl'
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        rows[0]['processed_monotonic_ns'] = -1
        raw = ''.join(json.dumps(x) + '\n' for x in rows).encode()
        path.write_bytes(raw)
        self.report['baseline']['processed_rows_sha256'] = hashlib.sha256(raw).hexdigest()
        self.write()
        self.assertEqual(audit(self.folder)['status'], 'FAIL')
