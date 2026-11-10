from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_consumer_scheduling import summarize

RECORDS=json.loads((Path(__file__).resolve().parents[1]/'evidence'/'consumer-scheduling'/'local-12-trials.json').read_text())
class TestExperiment(unittest.TestCase):
    def test_real_records(self):
        x=summarize(RECORDS)
        self.assertEqual([z['valid'] for z in x],[3,3,3,3])
    def test_missing_pair(self):
        with self.assertRaisesRegex(ValueError,'missing matched pair'):
            summarize(RECORDS[:-1])
    def test_receiver_drop_fails(self):
        x=deepcopy(RECORDS); x[0]['receiver']['application_queue_drops']=1
        with self.assertRaisesRegex(ValueError,'delivery not clean'):summarize(x)
    def test_sender_rate_fails(self):
        x=deepcopy(RECORDS);x[0]['publisher']['achieved_sendto_pps']=400
        with self.assertRaisesRegex(ValueError,'achieved-rate gate'):summarize(x)
    def test_duplicates_fail(self):
        with self.assertRaisesRegex(ValueError,'duplicate run key'):summarize(RECORDS+RECORDS[:1])
    def test_active_cpu_cannot_exceed_thread_cpu(self):
        x=deepcopy(RECORDS); x[0]['receiver']['consumer_active_cpu_ns']=x[0]['receiver']['consumer_thread_cpu_ns']+1
        with self.assertRaisesRegex(ValueError,'active CPU accounting invalid'):summarize(x)
    def test_active_switches_cannot_exceed_total(self):
        x=deepcopy(RECORDS); x[0]['receiver']['consumer_active_voluntary_context_switches']=x[0]['receiver']['consumer_voluntary_context_switches']+1
        with self.assertRaisesRegex(ValueError,'active context switch accounting invalid'):summarize(x)
    def test_sequence_fail(self):
        x=deepcopy(RECORDS); x[0]['receiver']['forward_gap_events']=1
        with self.assertRaisesRegex(ValueError,'sequence integrity'):summarize(x)
if __name__=='__main__':unittest.main()
