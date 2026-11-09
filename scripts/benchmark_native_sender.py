#!/usr/bin/env python3
"""Paired loopback experiment using measured C++ sendto pacing, not Python sleep.

This is an offered-load *screen*, not a calibrated receiver capacity test.
Preserve every failed trial. The receiver and sender report separate clocks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import statistics
from pathlib import Path
import random
import socket
import subprocess
import time
from datetime import datetime, timezone


def free_port():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def trial(receiver, sender, rate, idle, count, port, delay=0.12, cpus=None):
    cmd=[str(receiver.resolve()),'-',str(port),str(count),'5000','16384','0','-',
         '--rx=batch','--sink=none',f'--idle={idle}']
    receiver_affinity = (lambda: os.sched_setaffinity(0, set(cpus[:2]))) if cpus else None
    sender_affinity = (lambda: os.sched_setaffinity(0, {cpus[2]})) if cpus else None
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,preexec_fn=receiver_affinity)
    try:
        time.sleep(delay)
        if proc.poll() is not None:
            raise RuntimeError('receiver exited before send')
        send=subprocess.run([str(sender.resolve()),str(port),str(count),str(rate)],
                            capture_output=True,text=True,timeout=9,preexec_fn=sender_affinity)
        stdout,stderr=proc.communicate(timeout=8)
        sender_record=json.loads(send.stdout) if send.stdout.strip() else None
        receiver_record=json.loads(stdout) if stdout.strip() else None
        issues=[]
        if send.returncode or not sender_record:issues.append('sender failed')
        if proc.returncode or not receiver_record:issues.append('receiver failed/incomplete')
        if sender_record:
            if sender_record['successful_sendto']!=count:issues.append('sendto shortfall')
            if sender_record['send_errors']:issues.append('sendto errors')
        if receiver_record:
            for field,expected in [('received_datagrams',count),('processed',count),
                                   ('application_queue_drops',0),('malformed_frames',0),
                                   ('duplicate_events',0),('forward_gap_events',0),('reordered_events',0)]:
                if receiver_record[field]!=expected:issues.append(f'{field} != {expected}')
            if receiver_record.get('kernel_rx_overflow_cumulative',0):issues.append('kernel RX overflow')
            if not receiver_record['complete']:issues.append('receiver incomplete')
        achieved_ratio = sender_record['achieved_sendto_pps']/rate if sender_record else None
        rate_attained = achieved_ratio is not None and 0.9 <= achieved_ratio <= 1.1
        return {'status':'PASS' if not issues else 'FAIL','rate_target_pps':rate,'idle':idle,
                'rate_attained_within_10pct':rate_attained,
                'sender_rate_ratio':achieved_ratio,
                'issues':issues,'sender':sender_record,'receiver':receiver_record,
                'sender_stderr':send.stderr,'receiver_stderr':stderr,'sender_exit':send.returncode,
                'receiver_exit':proc.returncode}
    finally:
        if proc.poll() is None:proc.kill();proc.communicate()


def comparable_pair(rows, target_pps: int) -> dict:
    """Fail closed: complete delivery alone does not validate a latency pairing.

    Both trials must deliver all packets, meet their target offered rate, and
    have measured sender rates within 5% of each other. Do not silently omit
    invalid pairs from the reported denominator.
    """
    issues = []
    if len(rows) != 2 or {r.get('idle') for r in rows} != {'sleep', 'spin'}:
        issues.append('expected exactly one sleep and one spin trial')
    for r in rows:
        label = r.get('idle', 'unknown')
        if r.get('rate_target_pps') != target_pps:
            issues.append(f'{label}: mismatched target rate')
        if r.get('status') != 'PASS':
            issues.append(f'{label}: incomplete data-plane integrity')
        if not r.get('rate_attained_within_10pct'):
            issues.append(f'{label}: actual offered load missed target +/-10%')
    rates = [r.get('sender', {}).get('achieved_sendto_pps', 0) if isinstance(r.get('sender'), dict)
             else 0 for r in rows]
    if all(rate > 0 for rate in rates) and abs(rates[0]-rates[1]) / min(rates) > .05:
        issues.append('paired sender rates differ by >5%')
    if not all(rate > 0 for rate in rates):
        issues.append('missing positive measured sender rate')
    return {'comparable': not issues, 'issues': issues,
            'measured_rates_pps': rates}


def run(receiver, sender, output, rates=(1000,5000,20000),repeats=3,count=600,pin=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    rng=random.Random(20261108)
    allowed=sorted(os.sched_getaffinity(0))
    if pin and len(allowed)<3:raise ValueError("--pin requires three allowed CPUs")
    cpus=allowed[:3] if pin else None
    records=[]
    for rate in rates:
        for pair in range(repeats):
            modes=['sleep','spin'];rng.shuffle(modes)
            for idle in modes:
                try:
                    record=trial(receiver,sender,rate,idle,count,free_port(),cpus=cpus)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired,
                        json.JSONDecodeError) as exc:
                    # An incomplete/timeout trial must remain in the audit trail.
                    record={'status':'FAIL','rate_target_pps':rate,'idle':idle,
                            'rate_attained_within_10pct':False,
                            'issues':[f'{type(exc).__name__}: {exc}'],
                            'sender':None,'receiver':None}
                record['pair']=pair
                records.append(record)
                filename=f'{rate:06d}-{pair:02d}-{idle}.json'
                (output/filename).write_text(json.dumps(record,indent=2)+'\n')
    pairs=[]
    for rate in rates:
        for pair in range(repeats):
            matched=[r for r in records if r['rate_target_pps']==rate and r['pair']==pair]
            result=comparable_pair(matched,rate)
            result.update({'target_pps':rate,'pair':pair,
                           'queue_p99_us': {r['idle']:r['receiver']['queue_p99_us'] for r in matched
                                            if result['comparable']}})
            pairs.append(result)
    summary={'utc':datetime.now(timezone.utc).isoformat(),'kernel':os.uname().release,
             'allowed_cpus':allowed,'pinned_cpus':cpus,
             'cgroup_cpu_max':Path('/sys/fs/cgroup/cpu.max').read_text().strip() if Path('/sys/fs/cgroup/cpu.max').exists() else None,
             'sender_sha256':hashlib.sha256(sender.read_bytes()).hexdigest(),
             'receiver_sha256':hashlib.sha256(receiver.read_bytes()).hexdigest(),
             'count_per_trial':count,'rates_target_pps':list(rates),'repeats':repeats,
             'total_trials':len(records),'valid_trials':sum(r['status']=='PASS' for r in records),
             'rate_attained_trials':sum(r['rate_attained_within_10pct'] for r in records),
             'comparable_pairs':sum(p['comparable'] for p in pairs),
             'total_pairs':len(pairs), 'pairs':pairs,
             'failed_trials':[{'rate':r['rate_target_pps'],'pair':r['pair'],'idle':r['idle'],
                               'issues':r['issues']} for r in records if r['status']=='FAIL'],
             'by_rate':[],'limitations':[
                  'Local loopback only, not NIC, market-feed or wire-to-wire benchmark.',
                  'Measured sender sendto rate is offered load, not receiver throughput capacity.',
             'A trial can pass frame integrity but fail intended rate control; rate_attained_within_10pct captures that.',
                  'Absolute CLOCK_MONOTONIC sleeps are still subject to Linux scheduling jitter.',
                  'No operating-system profiler/perf or isolated hardware CPU available.',
                  'Failed trials are recorded; do not drop them from reliability reporting.']}
    for rate in rates:
        subset=[r for r in records if r['rate_target_pps']==rate]
        summary['by_rate'].append({'target_pps':rate,
             'trials':len(subset),'passes':sum(r['status']=='PASS' for r in subset),
             'comparable_pairs':sum(p['comparable'] and p['target_pps']==rate for p in pairs),
             'target_attained_trials':sum(r['rate_attained_within_10pct'] for r in subset),
             'achieved_sender_pps': [r['sender']['achieved_sendto_pps'] for r in subset if r.get('sender')],
             'p99_queue_us': {idle:[r['receiver']['queue_p99_us'] for r in subset
                        if r['status']=='PASS' and r['idle']==idle] for idle in ('sleep','spin')},
             'median_sender_achieved_pps': (statistics.median(r['sender']['achieved_sendto_pps']
                for r in subset if r.get('sender')) if any(r.get('sender') for r in subset) else None)})
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receiver',type=Path,default=Path('build/udp_pipeline'))
    p.add_argument('--sender',type=Path,default=Path('build/udp_publisher'))
    p.add_argument('--output',type=Path,default=Path('lab-output/native-pacing'))
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--count',type=int,default=600)
    p.add_argument('--rates',type=int,nargs='+',default=[1000,5000,20000])
    p.add_argument('--pin',action='store_true',help='pin receiver threads to two CPUs, sender to a third')
    a=p.parse_args()
    if not 1<=a.repeats<=10 or not 100<=a.count<=20000 or not all(1<=r<=500000 for r in a.rates):
        p.error('out-of-range experiment dimensions')
    report=run(a.receiver,a.sender,a.output,a.rates,a.repeats,a.count,pin=a.pin)
    print(json.dumps({'valid_trials':report['valid_trials'],'total_trials':report['total_trials'],
                      'rate_attained_trials':report['rate_attained_trials'],
                      'comparable_pairs':report['comparable_pairs'], 'total_pairs':report['total_pairs'],
                      'by_rate':report['by_rate'],'failed_trials':report['failed_trials']},indent=2))
    return 0 if report['comparable_pairs']==report['total_pairs'] else 1

if __name__=='__main__':raise SystemExit(main())
