import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from benchmark_native_sender import run, trial

BASE=Path(__file__).resolve().parents[1]
RECEIVER=BASE/'build'/'udp_pipeline'
SENDER=BASE/'build'/'udp_publisher'

class NativeSenderTests(unittest.TestCase):
    def test_sender_refuses_invalid_rate(self):
        x=subprocess.run([str(SENDER),'10','10','999999'],capture_output=True,text=True)
        self.assertEqual(x.returncode,2)
    def test_sender_does_not_confuse_sendto_success_with_delivery(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1',0))
            p=sock.getsockname()[1]
        x=subprocess.run([str(SENDER),str(p),'50','4000'],capture_output=True,text=True)
        self.assertEqual(x.returncode,0)
        s=json.loads(x.stdout)
        self.assertEqual(s['successful_sendto'],50)
        self.assertIn('not receiver delivery',s['note'])
    def test_real_receiver_both_modes(self):
        for idle in ('sleep','spin'):
            with self.subTest(idle=idle):
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                    sock.bind(('127.0.0.1',0))
                    p=sock.getsockname()[1]
                r=trial(RECEIVER,SENDER,2000,idle,100,p)
                self.assertEqual(r['status'],'PASS',r['issues'])
                self.assertEqual(r['receiver']['processed'],100)
    def test_matrix_preserves_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            x=run(RECEIVER,SENDER,Path(d),rates=(1000,5000),repeats=1,count=100)
            self.assertEqual(x['total_trials'],4)
            self.assertEqual(len(x['by_rate']),2)
            self.assertTrue((Path(d)/'summary.json').exists())

class ComparabilityGateTests(unittest.TestCase):
    def setUp(self):
        from benchmark_native_sender import comparable_pair
        self.check = comparable_pair

    def item(self, idle, actual=1000, target=1000, status='PASS', rate_ok=True):
        return {'idle': idle, 'status': status, 'rate_target_pps': target,
                'rate_attained_within_10pct': rate_ok,
                'sender': {'achieved_sendto_pps': actual},
                'receiver': {'queue_p99_us': 100}}

    def test_valid_matched_pair(self):
        self.assertTrue(self.check([self.item('sleep'), self.item('spin', actual=1002)], 1000)['comparable'])

    def test_rate_missed_even_when_all_frames_arrived(self):
        p=self.check([self.item('sleep'), self.item('spin', actual=840, rate_ok=False)], 1000)
        self.assertFalse(p['comparable'])
        self.assertTrue(any('offered load' in s for s in p['issues']))

    def test_paired_load_difference_rejected(self):
        p=self.check([self.item('sleep', actual=900), self.item('spin', actual=1080)], 1000)
        self.assertFalse(p['comparable'])
        self.assertIn('paired sender rates differ by >5%', p['issues'])

    def test_packet_failure_rejected(self):
        p=self.check([self.item('sleep', status='FAIL'), self.item('spin')], 1000)
        self.assertFalse(p['comparable'])

    def test_duplicate_mode_rejected(self):
        self.assertFalse(self.check([self.item('sleep'), self.item('sleep')], 1000)['comparable'])

    def test_missing_sender_metrics_rejected(self):
        r=self.item('spin');r['sender']=None
        self.assertFalse(self.check([self.item('sleep'), r], 1000)['comparable'])

if __name__=='__main__':
    unittest.main()
