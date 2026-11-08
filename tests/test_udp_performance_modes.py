"""Real Linux UDP integrations for scalar/batch receive and metrics-only sink."""
import json
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
BINARY=BASE/'build'/'udp_pipeline'


def free_port():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]

class PerformanceModesTests(unittest.TestCase):
    def run_mode(self,rx,sink,count=80,idle='sleep'):
        p=free_port()
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'frames.jsonl'
            cmd=[str(BINARY),'-',str(p),str(count),'3500','1024','0',str(dest),f'--rx={rx}',f'--sink={sink}',f'--idle={idle}']
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                time.sleep(.12)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                    for seq in range(1,count+1):
                        sender.sendto(struct.pack('!IIQ',0x52494c31,7,seq),('127.0.0.1',p))
                        if seq%8==0:time.sleep(.002)
                out,err=proc.communicate(timeout=5)
                self.assertEqual(proc.returncode,0,err+out)
                data=json.loads(out)
                self.assertEqual(data['received_datagrams'],count)
                self.assertEqual(data['processed'],count)
                self.assertEqual(data['application_queue_drops'],0)
                self.assertEqual(data['receive_mode'],rx)
                self.assertEqual(data['sink'],sink)
                self.assertEqual(data['consumer_idle_strategy'], 'spin' if idle=='spin' else 'sleep100us')
                self.assertGreater(data['process_cpu_ns'],0)
                self.assertGreaterEqual(data['process_cpu_active_ns'],0)
                self.assertEqual(data['process_cpu_ns'],data['process_cpu_active_ns']+data['process_cpu_startup_ns'])
                self.assertGreater(data['rx_successful_syscalls'],0)
                if sink=='none':self.assertFalse(dest.exists())
                else:self.assertEqual(len(dest.read_text().splitlines()),count)
                return data
            finally:
                if proc.poll() is None:
                    proc.kill();proc.communicate()
    def test_scalar_metrics_only(self):self.run_mode('scalar','none')
    def test_batched_metrics_only(self):self.run_mode('batch','none')
    def test_batched_spin(self):self.run_mode('batch','none',idle='spin')
    def test_batched_jsonl(self):self.run_mode('batch','jsonl')
    def test_scalar_jsonl(self):self.run_mode('scalar','jsonl')
    def test_invalid_option_fails_closed(self):
        proc=subprocess.run([str(BINARY),'-','55001','10','200','64','0','-',
                             '--rx=turbo'],capture_output=True,text=True,timeout=3)
        self.assertEqual(proc.returncode,2)
        self.assertIn('unknown',proc.stderr)

if __name__=='__main__':unittest.main()
