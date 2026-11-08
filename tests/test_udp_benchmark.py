"""Verify benchmark controls use real Linux sockets and reject bad inputs."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('udp_bench',HERE/'scripts'/'benchmark_udp_pipeline.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class PairedBenchmarkTests(unittest.TestCase):
    def test_one_round_all_four_modes(self):
        with tempfile.TemporaryDirectory() as d:
            report=mod.run(HERE/'build'/'udp_pipeline',Path(d),repetitions=1,
                           warmups=0,count=100,burst=8,pause_us=2500,
                           capacity=512,startup_seconds=.12)
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(len(report['trials']),4)
            self.assertEqual(len(report['measurements']),4)
            self.assertEqual(len(report['design']['trial_order']),1)
            for item in report['trials']:
                self.assertEqual(item['summary']['processed'],100)
                self.assertEqual(item['summary']['application_queue_drops'],0)
                self.assertGreater(item['summary']['process_cpu_ns'],0)
                self.assertGreater(item['summary']['rx_successful_syscalls'],0)
    def test_quantile_summary_does_not_use_publisher_wall_as_latency(self):
        sample={'mode':{'rx':'batch','sink':'none','idle':'spin'},
                'offered_count':100, 'publisher_wall_ns':1000000,
                'summary':{'process_cpu_ns':1200000,'process_cpu_active_ns':1000000,
                           'process_cpu_startup_ns':200000,'consumer_thread_cpu_ns':900000,
                           'rx_successful_syscalls':9,'queue_p99_us':4.25}}
        row=mod.summary_stats([sample])['batch/none/spin']
        self.assertAlmostEqual(row['median_total_cpu_us_per_frame'],12.0)
        self.assertAlmostEqual(row['median_active_cpu_us_per_frame'],10.0)
        self.assertAlmostEqual(row['median_queue_p99_us'],4.25)
        self.assertAlmostEqual(row['median_rx_syscalls_per_frame'],.09)

if __name__=='__main__':unittest.main()
