"""Actual Linux UDP socket regression tests for the eventfd consumer strategy."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CPP = ROOT / 'cpp'


def open_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.bind(('127.0.0.1',0))
        return sk.getsockname()[1]


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('g++'), 'Linux and g++ required')
class EventfdWakeupIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.rx=Path(cls.temp.name)/'receiver'
        cls.tx=Path(cls.temp.name)/'sender'
        subprocess.run(['g++','-std=c++17','-O2','-pthread','-Wall','-Wextra','-Wpedantic',str(CPP/'udp_pipeline.cpp'),'-o',str(cls.rx)],check=True)
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Wpedantic',str(CPP/'udp_publisher.cpp'),'-o',str(cls.tx)],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def start(self, count, *, timeout=1600, capacity=1024, delay_us=0, mode='eventfd'):
        port=open_port()
        rx=subprocess.Popen([str(self.rx),'-',str(port),str(count),str(timeout),str(capacity),str(delay_us),
                             '/dev/null','--rx=batch','--sink=none',f'--idle={mode}'],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(0.07)
        self.assertIsNone(rx.poll(), 'receiver crashed before sender started')
        return rx, port

    def stop(self,rx):
        try:
            out,err=rx.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            rx.kill();rx.communicate();self.fail('consumer failed to exit, possible lost wakeup')
        self.assertTrue(out.strip(),err)
        return json.loads(out),rx.returncode,err

    def test_normal_paced_delivery_and_explicit_wake_counters(self):
        rx,port=self.start(120)
        tx=subprocess.run([str(self.tx),str(port),'120','2000'],check=True,capture_output=True,text=True,timeout=5)
        r,code,err=self.stop(rx)
        self.assertEqual(code,0,err)
        self.assertEqual(r['valid_frames'],120)
        self.assertEqual(r['processed'],120)
        self.assertEqual(r['application_queue_drops'],0)
        self.assertEqual(r['eventfd_notification_writes'],121)
        self.assertGreaterEqual(r['eventfd_wakeup_reads'],1)
        self.assertEqual(r['queue_sample_capacity_grows'],0)
        self.assertEqual(r['rx_datagrams_consumed_by_syscalls'],120)
        self.assertEqual(json.loads(tx.stdout)['successful_sendto'],120)

    def test_consumer_burst_backpressure_reconciles_all_frames(self):
        rx,port=self.start(600,capacity=2,delay_us=2000,timeout=4000)
        tx=subprocess.run([str(self.tx),str(port),'600','2500'],check=True,capture_output=True,text=True,timeout=5)
        r,code,err=self.stop(rx)
        self.assertEqual(code,0,err)
        self.assertEqual(r['received_datagrams'],600)
        self.assertGreater(r['application_queue_drops'],0)
        self.assertEqual(r['processed']+r['application_queue_drops'],r['valid_frames'])
        self.assertEqual(r['eventfd_notification_writes'],r['processed']+1)

    def test_missing_sender_timeout_unblocks_consumer(self):
        rx,port=self.start(10,timeout=250)
        r,code,err=self.stop(rx)
        self.assertEqual(code,1,err)
        self.assertFalse(r['complete'])
        self.assertEqual(r['received_datagrams'],0)
        self.assertEqual(r['processed'],0)
        self.assertEqual(r['eventfd_notification_writes'],1)

    def test_partial_sender_then_timeout_drains_queue(self):
        rx,port=self.start(10,timeout=280)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            for i in range(3):
                sock.sendto(struct.pack('!IIQ',0x52494c31,7,i+1),('127.0.0.1',port))
        r,code,err=self.stop(rx)
        self.assertEqual(code,1,err)
        self.assertEqual((r['received_datagrams'],r['processed']), (3,3))
        self.assertEqual(r['eventfd_notification_writes'],4)

    def test_malformed_and_valid_packets_accounted_independently(self):
        rx,port=self.start(4)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            for payload in [b'bad',struct.pack('!IIQ',0x52494c31,7,1),
                            struct.pack('!IIQ',0x52494c31,7,2),struct.pack('!IIQ',0x52494c31,7,3)]:
                sock.sendto(payload,('127.0.0.1',port))
                time.sleep(0.003)
        r,code,err=self.stop(rx)
        self.assertEqual(code,0,err)
        self.assertEqual((r['received_datagrams'],r['malformed_frames'],r['processed']), (4,1,3))
        self.assertEqual(r['eventfd_notification_writes'],4)

    def test_eventfd_scalar_receive_jsonl_sink(self):
        port=open_port()
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'packets.jsonl'
            rx=subprocess.Popen([str(self.rx),'-',str(port),'25','2000','128','0',
                                 str(output),'--rx=scalar','--sink=jsonl','--idle=eventfd'],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            time.sleep(0.06)
            subprocess.run([str(self.tx),str(port),'25','800'],capture_output=True,
                           text=True,check=True,timeout=5)
            r,code,err=self.stop(rx)
            self.assertEqual(code,0,err)
            self.assertEqual(r['processed'],25)
            rows=[json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows),25)
            self.assertEqual([row['seq'] for row in rows],list(range(1,26)))

    def test_many_short_lived_receivers_have_no_lost_wakeup(self):
        for iteration in range(15):
            with self.subTest(iteration=iteration):
                rx,port=self.start(3,timeout=750)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sk:
                    for i in range(3):
                        sk.sendto(struct.pack('!IIQ',0x52494c31,3,i+1),('127.0.0.1',port))
                r,code,err=self.stop(rx)
                self.assertEqual(code,0,err)
                self.assertEqual(r['processed'],3)
                self.assertEqual(r['eventfd_notification_writes'],4)

    def test_existing_sleep_and_spin_modes_remain_compatible(self):
        for mode in ['sleep','spin']:
            with self.subTest(mode=mode):
                rx,port=self.start(20,mode=mode)
                subprocess.run([str(self.tx),str(port),'20','1000'],check=True,capture_output=True,text=True,timeout=5)
                r,code,err=self.stop(rx)
                self.assertEqual(code,0,err)
                self.assertEqual(r['processed'],20)
                self.assertEqual(r['eventfd_notification_writes'],0)
                self.assertEqual(r['eventfd_wakeup_reads'],0)

if __name__=='__main__':unittest.main()
