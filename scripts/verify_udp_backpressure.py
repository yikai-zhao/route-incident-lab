#!/usr/bin/env python3
"""Real loopback UDP queue-backpressure reproduction, not a capacity benchmark.

Uses the unchanged production receive path and native paced publisher. It
separates observed application queue rejection from possible socket overflow,
and refuses to interpret timing when actual offered load is not comparable.
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import socket
import subprocess
import time
from pathlib import Path
from statistics import median


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def classify(sender: dict, receiver: dict, *, rate: int, count: int) -> list[str]:
    reasons = []
    if sender['successful_sendto'] != count or sender['send_errors']:
        reasons.append('send_incomplete')
    if not receiver['complete'] or receiver['received_datagrams'] != count:
        reasons.append('socket_receive_incomplete')
    if receiver['malformed_frames'] or receiver['valid_frames'] != receiver['received_datagrams']:
        reasons.append('invalid_frames')
    if receiver['processed'] + receiver['application_queue_drops'] != receiver['valid_frames']:
        reasons.append('accounting_mismatch')
    if receiver['kernel_rx_overflow_cumulative'] != 0:
        reasons.append('kernel_overflow_observed')
    if abs(sender['achieved_sendto_pps'] / rate - 1) > .10:
        reasons.append('publisher_rate_missed')
    return reasons


def run_trial(receiver: Path, sender: Path, *, name: str, output: Path,
              count: int, rate: int, queue: int, delay_us: int) -> dict:
    chosen_port = free_port()
    command = [str(receiver.resolve()), '-', str(chosen_port), str(count), '3500',
               str(queue), str(delay_us), '-', '--rx=batch', '--sink=none', '--idle=sleep']
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(.12)
        if proc.poll() is not None:
            raise RuntimeError(f'{name}: receiver exited before send')
        publication = subprocess.run([str(sender.resolve()), str(chosen_port), str(count), str(rate)],
                                     capture_output=True, text=True, timeout=12)
        stdout, stderr = proc.communicate(timeout=9)
        if not publication.stdout or not stdout:
            raise RuntimeError(f'{name}: missing JSON: sender={publication.stderr}, receiver={stderr}')
        publisher = json.loads(publication.stdout)
        reading = json.loads(stdout)
        reasons = classify(publisher, reading, rate=rate, count=count)
        if publication.returncode or proc.returncode:
            reasons.append('process_nonzero_exit')
        data = {'name': name, 'target_pps':rate, 'sent_count':count,
                'producer_command': [Path(sender).name, str(chosen_port), str(count), str(rate)],
                'consumer_delay_us': delay_us, 'queue_capacity': queue,
                'publisher': publisher, 'receiver': reading, 'reject_reasons': sorted(set(reasons))}
        (output / f'{name}.json').write_text(json.dumps(data, indent=2)+'\n')
        return data
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def evaluate_pair(control: dict, pressured: dict, target_pps: int) -> list[str]:
    reasons = [f'control:{x}' for x in control['reject_reasons']]
    reasons += [f'pressured:{x}' for x in pressured['reject_reasons']]
    a = control['publisher']['achieved_sendto_pps']
    b = pressured['publisher']['achieved_sendto_pps']
    if abs(a-b) / target_pps > .05:
        reasons.append('paired_rate_mismatch')
    if control['receiver']['application_queue_drops'] != 0:
        reasons.append('control_queue_drop')
    if any(control['receiver'].get(x, 0) for x in
           ('duplicate_events', 'reordered_events', 'forward_gap_events')):
        reasons.append('control_sequence_anomaly')
    if pressured['receiver']['application_queue_drops'] == 0:
        reasons.append('no_application_backpressure_detected')
    return reasons


def reproduce(receiver: Path, sender: Path, output: Path, *, count: int=600,
              rate: int=1000, repetitions: int=3, seed: int=7) -> dict:
    if not (100 <= count <= 10000 and 100 <= rate <= 50000 and 1 <= repetitions <= 10):
        raise ValueError('invalid experiment parameters')
    output.mkdir(parents=True, exist_ok=True)
    rng=random.Random(seed)
    pairs=[]
    for i in range(repetitions):
        order = ['control','pressured']
        rng.shuffle(order)
        tests={}
        for label in order:
            tests[label]=run_trial(receiver,sender,name=f'{i:02d}-{label}',output=output,
                                   count=count,rate=rate,
                                   queue=4096 if label=='control' else 64,
                                   delay_us=0 if label=='control' else 5000)
        reasons=evaluate_pair(tests['control'],tests['pressured'],rate)
        pairs.append({'pair':i,'order':order,'valid':not reasons,'reject_reasons':reasons,
                      'control_app_drops':tests['control']['receiver']['application_queue_drops'],
                      'pressured_app_drops':tests['pressured']['receiver']['application_queue_drops'],
                      'control_achieved_pps':tests['control']['publisher']['achieved_sendto_pps'],
                      'pressured_achieved_pps':tests['pressured']['publisher']['achieved_sendto_pps']})
    valid=[p for p in pairs if p['valid']]
    report={'scope':'local_udp_application_backpressure_accounting',
            'status':'PASS' if len(valid)==len(pairs) else 'INCOMPLETE',
            'kernel':platform.release(),'count_per_trial':count,'target_pps':rate,
            'pair_count':len(pairs),'valid_pairs':len(valid),'pairs':pairs,
            'median_pressure_app_drops':median(p['pressured_app_drops'] for p in valid) if valid else None,
            'limitations':[
                'Local host UDP only; no network wire-loss inference.',
                'Intentional 5000us consumer sleep and 64-slot queue create artificial backpressure.',
                'SO_RXQ_OVFL reports only drops observed through later delivered ancillary records; zero does not prove no kernel loss.',
                'Publisher target pacing is not achieved load; paired comparisons require measured rates within 5%.',
                'Tests reject mixed kernel overflow and application backpressure; no HFT throughput or tail-latency claim.'
            ]}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receiver', type=Path,required=True)
    p.add_argument('--sender',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('lab-output/backpressure'))
    p.add_argument('--count',type=int,default=600)
    p.add_argument('--rate',type=int,default=1000)
    p.add_argument('--repetitions',type=int,default=3)
    args=p.parse_args()
    result=reproduce(args.receiver,args.sender,args.output,count=args.count,
                     rate=args.rate,repetitions=args.repetitions)
    print(json.dumps({'status':result['status'],'valid_pairs':result['valid_pairs'],
                      'pair_count':result['pair_count'],
                      'median_pressure_app_drops':result['median_pressure_app_drops']},indent=2))
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
