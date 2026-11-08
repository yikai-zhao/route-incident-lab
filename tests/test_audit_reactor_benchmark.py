import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_reactor_benchmark import audit

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.d=Path(self.temp.name)
        self.trials=[]
        for backend in ('poll','epoll'):
            name=f'accepting-pair01-{backend}'
            content=(json.dumps({'seq':0,'ok':True,'error_errno':0,
                      'start_monotonic_ns':10,'end_monotonic_ns':20})+'\n').encode()
            (self.d/f'{name}.jsonl').write_bytes(content)
            self.trials.append({'name':name,'samples_file':name+'.jsonl',
                'samples_sha256':hashlib.sha256(content).hexdigest(),
                'backend':backend,'pair':1,
                'input':{'name':'accepting','count':1,'concurrency':8},
                'summary':{'attempts':1,'successes':1,'failures':0,
                           'backend':backend,'peak_inflight':1}})
        self.report={'trials':self.trials,'workload_order':[{
            'workload':'accepting','pair':1,'order':['poll','epoll']}]}
        self.save()
    def save(self): (self.d/'benchmark.json').write_text(json.dumps(self.report))
    def test_valid(self): self.assertEqual(audit(self.d)['status'],'PASS')
    def test_raw_tamper(self):
        (self.d/self.trials[0]['samples_file']).write_text('{}\n')
        self.assertEqual(audit(self.d)['status'],'FAIL')
    def test_missing_pair(self):
        self.report['trials'].pop();self.save()
        self.assertEqual(audit(self.d)['status'],'FAIL')
    def test_mismatched_inputs(self):
        self.trials[1]['input']['concurrency']=4;self.save()
        self.assertEqual(audit(self.d)['status'],'FAIL')
    def test_forged_count(self):
        self.trials[0]['summary']['successes']=0;self.save()
        self.assertEqual(audit(self.d)['status'],'FAIL')
    def test_invalid_clock(self):
        t=self.trials[0];path=self.d/t['samples_file']
        item=json.loads(path.read_text());item['end_monotonic_ns']=1
        content=(json.dumps(item)+'\n').encode();path.write_bytes(content)
        t['samples_sha256']=hashlib.sha256(content).hexdigest();self.save()
        self.assertEqual(audit(self.d)['status'],'FAIL')

if __name__=='__main__':unittest.main()
