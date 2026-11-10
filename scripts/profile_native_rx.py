#!/usr/bin/env python3
"""Paired native-publisher UDP receiver cost study, fail-closed on mismatched load.

Two independent factors are receive syscall mode and consumer wakeup policy.
Measures observed CPU (not PMU profiling) and local queue residence, never wire
latency or sustainable throughput. Published trials include rejected runs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
import random
import socket
import subprocess
import time
from pathlib import Path
from statistics import median

MODES=(('batch','sleep'),('scalar','sleep'),('batch','spin'),('scalar','spin'))

def free_port():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

def trial(receiver,sender,name,folder,count,rate,rx,idle):
    port=free_port()
    cmd=[str(receiver.resolve()),'-',str(port),str(count),'3500','8192','0','-',
         f'--rx={rx}','--sink=none',f'--idle={idle}']
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(.125)
        send=subprocess.run([str(sender.resolve()),str(port),str(count),str(rate)],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=9)
        output,err=proc.communicate(timeout=8)
        if not output or not send.stdout:raise RuntimeError(f'{name}: empty output {err} / {send.stderr}')
        recv=json.loads(output);pub=json.loads(send.stdout)
        failures=[]
        if send.returncode or proc.returncode:failures.append('process_failure')
        if pub['successful_sendto']!=count or pub['send_errors']:failures.append('send_count')
        if not recv['complete'] or recv['received_datagrams']!=count:failures.append('receive_count')
        if recv['valid_frames']!=count or recv['processed']!=count:failures.append('processed_count')
        if recv['application_queue_drops'] or recv['kernel_rx_overflow_cumulative']:failures.append('drop_detected')
        if any(recv[k] for k in ('duplicate_events','reordered_events','forward_gap_events')):failures.append('sequence_anomaly')
        if abs(pub['achieved_sendto_pps']/rate-1)>.10:failures.append('rate_out_of_tolerance')
        if recv['rx_successful_syscalls']<=0:failures.append('no_receive_syscalls')
        result={'name':name,'mode':{'rx':rx,'idle':idle},'target_pps':rate,
                'pub':pub,'recv':recv,'reject_reasons':failures}
        (folder/f'{name}.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
    finally:
        if proc.poll() is None:proc.kill();proc.communicate()

def run(receiver,sender,folder,*,rates=(1000,5000),count=600,repeats=3,seed=1811):
    if not 100<=count<=20000 or not 1<=repeats<=15 or not rates or any(r<100 or r>50000 for r in rates):
        raise ValueError('invalid work parameters')
    folder.mkdir(parents=True,exist_ok=True)
    rng=random.Random(seed)
    cohorts=[]
    for rate in rates:
        for pair_id in range(repeats):
            order=list(MODES);rng.shuffle(order)
            outcomes=[]
            for rx,idle in order:
                outcomes.append(trial(receiver,sender,f'{rate}-{pair_id}-{rx}-{idle}',folder,
                                      count,rate,rx,idle))
            speeds=[x['pub']['achieved_sendto_pps'] for x in outcomes]
            comparable=(all(not x['reject_reasons'] for x in outcomes) and
                        (max(speeds)-min(speeds))/rate<=.05)
            reason=[] if comparable else sorted(set(y for x in outcomes for y in x['reject_reasons'])|
                                                   ({'mode_rate_mismatch'} if (max(speeds)-min(speeds))/rate>.05 else set()))
            cohorts.append({'target_pps':rate,'pair':pair_id,'order':order,'valid':comparable,
                            'reject_reasons':reason,'modes':outcomes})
    summaries=[]
    for rate in rates:
        valid=[pair for pair in cohorts if pair['target_pps']==rate and pair['valid']]
        rows=[]
        for rx,idle in MODES:
            src=[next(c for c in p['modes'] if c['mode']=={'rx':rx,'idle':idle}) for p in valid]
            rows.append({'rx':rx,'idle':idle,'valid_comparable_trials':len(src),
                         'median_queue_p99_us':round(median(x['recv']['queue_p99_us'] for x in src),3) if src else None,
                         'median_active_cpu_us_frame':round(median(x['recv']['process_cpu_active_ns']/count/1000 for x in src),3) if src else None,
                         'median_syscalls_per_frame':round(median(x['recv']['rx_successful_syscalls']/count for x in src),6) if src else None,
                         'median_actual_pps':round(median(x['pub']['achieved_sendto_pps'] for x in src),1) if src else None})
        summaries.append({'target_pps':rate,'valid_pairs':len(valid),'total_pairs':repeats,'modes':rows})
    report={'scope':'Linux loopback sender paced, no NIC data or perf PMU',
            'version':1,'environment':{'kernel':platform.release(),
              'receiver_sha256':hashlib.sha256(receiver.read_bytes()).hexdigest(),
              'sender_sha256':hashlib.sha256(sender.read_bytes()).hexdigest()},
            'design':{'rates':list(rates),'count':count,'repeats':repeats,'seed':seed,
                      'rate_tolerance':.1,'within_group_rate_tolerance':.05,
                      'mode_order':'randomized, every group contains all modes'},
            'summaries':summaries,'pairs':cohorts,
            'limitations':['All trials use Linux IPv4 loopback, publisher-paced synthetic 16-byte datagrams.',
                           'Different modes can perturb system scheduling; paired randomization reduces order effects but cannot eliminate host contention.',
                           'Process CPU per frame includes user+kernel CPU from first packet until completion, not hardware performance counters.',
                           'Queue p99 is post-recv user-space residence until consumer handling, not end-to-end latency.',
                           'No claims about maximum pps, NIC wire loss, production market-data handling or causal speedup.']}
    (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--receiver',type=Path,required=True);p.add_argument('--sender',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('lab-output/native-receiver-comparison'))
    p.add_argument('--rates',type=int,nargs='+',default=[1000,5000]);p.add_argument('--count',type=int,default=600)
    p.add_argument('--repeats',type=int,default=3)
    a=p.parse_args()
    report=run(a.receiver,a.sender,a.output,rates=tuple(a.rates),count=a.count,repeats=a.repeats)
    print(json.dumps(report['summaries'],indent=2));return 0 if all(x['valid_pairs']==x['total_pairs'] for x in report['summaries']) else 1

if __name__=='__main__':raise SystemExit(main())
