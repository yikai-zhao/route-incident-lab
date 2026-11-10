"""Real Linux UDP proof: the queue sample vector does not grow on dequeue.

Builds from source into a temporary directory, with no network outside
127.0.0.1, paid infrastructure, or CI dependency.
"""
from pathlib import Path
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class HotPathStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('g++') is None:
            raise unittest.SkipTest('g++ required')
        cls.directory = tempfile.TemporaryDirectory()
        cls.receiver = Path(cls.directory.name) / 'receiver'
        cls.publisher = Path(cls.directory.name) / 'publisher'
        for source, binary in ((ROOT / 'cpp/udp_pipeline.cpp', cls.receiver),
                               (ROOT / 'cpp/udp_publisher.cpp', cls.publisher)):
            subprocess.run(['g++','-O2','-std=c++17','-pthread','-Wall','-Wextra',
                            str(source),'-o',str(binary)],check=True,timeout=35)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'directory'):cls.directory.cleanup()

    def run_mode(self, count, rx, idle):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        receiver=subprocess.Popen([str(self.receiver),'-',str(port),str(count),
                                   '5000','1024','0','-',f'--rx={rx}',
                                   '--sink=none',f'--idle={idle}'],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            time.sleep(0.15)
            publisher=subprocess.run([str(self.publisher),str(port),str(count),'600'],
                                     capture_output=True,text=True,timeout=8)
            output,errors=receiver.communicate(timeout=8)
            self.assertEqual(publisher.returncode,0,publisher.stderr)
            self.assertEqual(receiver.returncode,0,errors)
            send=json.loads(publisher.stdout)
            receive=json.loads(output)
            self.assertEqual(send['successful_sendto'],count)
            self.assertEqual(receive['received_datagrams'],count)
            self.assertEqual(receive['processed'],count)
            self.assertEqual(receive['application_queue_drops'],0)
            self.assertEqual(receive['queue_sample_capacity_grows'],0)
            self.assertEqual(receive['rx_datagrams_consumed_by_syscalls'],count)
            return receive
        finally:
            if receiver.poll() is None:
                receiver.kill();receiver.communicate()

    def test_batch_sleep(self):self.run_mode(200,'batch','sleep')
    def test_scalar_sleep(self):self.run_mode(200,'scalar','sleep')
    def test_batch_spin(self):self.run_mode(200,'batch','spin')

if __name__=='__main__':unittest.main()
