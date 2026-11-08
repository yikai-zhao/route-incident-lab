import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_spsc_latency import analyze,percentile

class LatencyAnalysisTests(unittest.TestCase):
    def test_interpolation(self):
        self.assertEqual(percentile([1,2,3,4,5],.5),3)
        self.assertEqual(percentile([1,2,3,4],.5),2.5)
    def test_empty(self): self.assertIsNone(percentile([],.99))
    def test_real_evidence_if_present(self):
        folder=Path(os.environ.get('RIL_SPSC_EVIDENCE_DIR','lab-output/spsc-udp'))
        if not folder.exists(): self.skipTest('optional local evidence not installed')
        result=analyze(folder)
        self.assertEqual(len(result['trials']),4)
        self.assertEqual(result['trials'][0]['processed'],64)
        self.assertEqual([x['application_queue_drops'] for x in result['trials'][1:]],[131,131,130])
    def test_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);row={'received_monotonic_ns':10,'processed_monotonic_ns':20}
            raw=(json.dumps(row)+'\n').encode();trials=[]
            for i in range(4):
                name='baseline.jsonl' if i==0 else f'backpressure-{i}.jsonl'
                (folder/name).write_bytes(raw)
                trials.append({'processed_file':name,'processed_rows_sha256':hashlib.sha256(raw).hexdigest(),
                    'summary':{'processed':1,'valid_frames':1,'application_queue_drops':0}})
            (folder/'report.json').write_text(json.dumps({'baseline':trials[0],'pressured_repetitions':trials[1:]}))
            self.assertAlmostEqual(analyze(folder)['trials'][0]['queue_residence_p99_us'],.01)
            (folder/'baseline.jsonl').write_bytes(b'{}\n')
            with self.assertRaises(ValueError):analyze(folder)
