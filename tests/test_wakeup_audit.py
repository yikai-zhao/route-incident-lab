"""Offline checks for strict comparisons and eventfd counter invariants."""
import importlib.util
from pathlib import Path
import unittest

script=Path(__file__).resolve().parents[1]/'scripts'/'benchmark_consumer_wakeup.py'
spec=importlib.util.spec_from_file_location('wakeup_benchmark',script)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def valid_trial(mode,rate=1000,actual=1000):
    return {
      'mode':mode, 'target_pps':rate,'target_frames':600,'repeat':0,
      'receiver_returncode':0,'sender_returncode':0,
      'sender':{'successful_sendto':600,'send_errors':0,'achieved_sendto_pps':actual},
      'receiver':{'received_datagrams':600,'valid_frames':600,'complete':True,
        'malformed_frames':0,'application_queue_drops':0,'processed':600,
        'rx_datagrams_consumed_by_syscalls':600,'duplicate_events':0,
        'reordered_events':0,'forward_gap_events':0,'queue_sample_capacity_grows':0,
        'eventfd_notification_writes':601 if mode=='eventfd' else 0,
        'eventfd_wakeup_reads':20 if mode=='eventfd' else 0,
        'queue_p99_us':30,'consumer_active_cpu_ns':1_000_000,
        'consumer_active_voluntary_context_switches':10,
        'rx_datagrams_per_successful_call':1}}


class WakeupAuditTests(unittest.TestCase):
    def test_valid_triplet(self):
        ts=[valid_trial(m) for m in module.MODES]
        for t in ts:t['acceptance']=module.check_trial(t)
        s=module.summarize({'trials':ts,'rates':[1000],'repeats':1})
        self.assertEqual(s['triplet_matched']['accepted'],1)

    def test_duplicate_mode_cannot_pass_as_triplet(self):
        ts=[valid_trial('sleep'),valid_trial('spin'),valid_trial('sleep')]
        for t in ts:t['acceptance']=module.check_trial(t)
        s=module.summarize({'trials':ts,'rates':[1000],'repeats':1})
        self.assertEqual(s['triplet_matched']['accepted'],0)

    def test_skewed_achieved_rates_cannot_pass_pair(self):
        ts=[valid_trial('sleep',actual=950),valid_trial('spin',actual=1000),valid_trial('eventfd',actual=1050)]
        for t in ts:t['acceptance']=module.check_trial(t)
        self.assertTrue(all(t['acceptance']['valid'] for t in ts))
        s=module.summarize({'trials':ts,'rates':[1000],'repeats':1})
        self.assertEqual(s['triplet_matched']['accepted'],0)

    def test_sender_pacing_failure_rejected(self):
        t=valid_trial('eventfd',actual=850)
        self.assertFalse(module.check_trial(t)['valid'])

    def test_missing_eventfd_shutdown_notification_rejected(self):
        t=valid_trial('eventfd')
        t['receiver']['eventfd_notification_writes']=600
        self.assertFalse(module.check_trial(t)['valid'])

    def test_queue_drop_rejected_from_latency_comparison(self):
        t=valid_trial('eventfd')
        t['receiver']['application_queue_drops']=1
        t['receiver']['processed']=599
        self.assertFalse(module.check_trial(t)['valid'])

    def test_corrupted_receive_count_rejected(self):
        t=valid_trial('eventfd')
        t['receiver']['rx_datagrams_consumed_by_syscalls']=601
        self.assertFalse(module.check_trial(t)['valid'])

if __name__=='__main__':unittest.main()
