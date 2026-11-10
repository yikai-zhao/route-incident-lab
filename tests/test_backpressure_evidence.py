"""Evidence accounting checks including a real socket integration when built."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('backpressure',ROOT/'scripts'/'verify_udp_backpressure.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def sample():
    return ({'successful_sendto':100,'send_errors':0,'achieved_sendto_pps':1000},
            {'complete':True,'received_datagrams':100,'malformed_frames':0,
             'valid_frames':100,'processed':80,'application_queue_drops':20,
             'kernel_rx_overflow_cumulative':0})

class BackpressureTests(unittest.TestCase):
    def test_accounting_not_mistaken_for_kernel_loss(self):
        a,b=sample()
        self.assertEqual(mod.classify(a,b,rate=1000,count=100),[])
    def test_incomplete_receiver_refused(self):
        a,b=sample();b['received_datagrams']=99
        self.assertIn('socket_receive_incomplete',mod.classify(a,b,rate=1000,count=100))
    def test_uncontrolled_pacing_refused(self):
        a,b=sample();a['achieved_sendto_pps']=850
        self.assertIn('publisher_rate_missed',mod.classify(a,b,rate=1000,count=100))
    def test_kernel_overflow_not_labeled_app_loss(self):
        a,b=sample();b['kernel_rx_overflow_cumulative']=3
        self.assertIn('kernel_overflow_observed',mod.classify(a,b,rate=1000,count=100))
    def test_control_sequence_mismatch_rejected(self):
        a,b=sample()
        control={'publisher':a,'receiver':dict(b,processed=100,application_queue_drops=0,forward_gap_events=1),'reject_reasons':[]}
        stress={'publisher':a,'receiver':b,'reject_reasons':[]}
        self.assertIn('control_sequence_anomaly',mod.evaluate_pair(control,stress,1000))
    def test_pair_requires_backpressure_and_matched_rate(self):
        a,b=sample()
        control={'publisher':a,'receiver':dict(b,processed=100,application_queue_drops=0),'reject_reasons':[]}
        stressed={'publisher':dict(a,achieved_sendto_pps=1080),'receiver':b,'reject_reasons':[]}
        self.assertIn('paired_rate_mismatch',mod.evaluate_pair(control,stressed,1000))
        stressed['publisher']['achieved_sendto_pps']=1000
        self.assertEqual(mod.evaluate_pair(control,stressed,1000),[])

if __name__=='__main__':unittest.main()
