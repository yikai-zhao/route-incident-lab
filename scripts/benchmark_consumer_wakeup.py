#!/usr/bin/env python3
"""Read-only Linux UDP consumer wakeup comparison with fail-closed acceptance gates.

No network outside 127.0.0.1. Run only after building C++ udp_pipeline +
udp_publisher from this repository. Does NOT benchmark NIC / exchange latency.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import platform
import random
import socket
import statistics
import subprocess
import time

MODES = ("sleep", "spin", "eventfd")


def loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get_cpu_quota():
    path = Path('/sys/fs/cgroup/cpu.max')
    return path.read_text().strip() if path.exists() else None


def run_trial(receiver: Path, sender: Path, *, rate: int, count: int, mode: str, timeout_ms=5000, capacity=4096):
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    port = loopback_port()
    rx_command = [str(receiver), '-', str(port), str(count), str(timeout_ms), str(capacity),
                  '0', '/dev/null', '--rx=batch', '--sink=none', f'--idle={mode}']
    tx_command = [str(sender), str(port), str(count), str(rate)]
    rx = subprocess.Popen(rx_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    try:
        # There is no explicit receiver ready handshake; a bounded 80ms setup
        # period is sufficient for local binding under normal load and startup
        # failures are exposed by the acceptance checks, not silently skipped.
        time.sleep(0.08)
        tx = subprocess.run(tx_command, text=True, capture_output=True,
                            timeout=timeout_ms/1000+5)
        out, err = rx.communicate(timeout=timeout_ms/1000+5)
    finally:
        if rx.poll() is None:
            rx.kill()
            rx.communicate()
    result = {'mode':mode, 'target_pps':rate, 'target_frames':count,
              'elapsed_wall_s':round(time.monotonic()-started, 4),
              'receiver_returncode':rx.returncode,'sender_returncode':tx.returncode,
              'receiver_stderr':err.strip(),'sender_stderr':tx.stderr.strip(),
              'receiver_command':rx_command,'sender_command':tx_command}
    try:
        result['receiver']=json.loads(out)
        result['sender']=json.loads(tx.stdout)
    except json.JSONDecodeError as exc:
        result['parse_error']=str(exc)
    result['acceptance']=check_trial(result)
    return result


def check_trial(trial: dict) -> dict:
    issues = []
    if trial.get('receiver_returncode') != 0 or trial.get('sender_returncode') != 0:
        issues.append('nonzero process return code')
    if not (isinstance(trial.get('sender'),dict) and isinstance(trial.get('receiver'),dict)):
        return {'valid':False,'reasons':issues+['missing structured result']}
    tx,rx = trial['sender'],trial['receiver']
    count,rate = trial['target_frames'],trial['target_pps']
    if tx.get('successful_sendto') != count or tx.get('send_errors') != 0:
        issues.append('sender frame integrity')
    observed = tx.get('achieved_sendto_pps',0)
    if not isinstance(observed,(int,float)) or not (rate*0.90 <= observed <= rate*1.10):
        issues.append('achieved sender rate outside +-10pct target')
    if rx.get('received_datagrams') != count or rx.get('valid_frames') != count or not rx.get('complete'):
        issues.append('receiver frame integrity')
    if rx.get('malformed_frames') != 0 or rx.get('application_queue_drops') != 0:
        issues.append('malformed or application drop')
    if rx.get('processed') != count:
        issues.append('consumer did not process all frames')
    if rx.get('rx_datagrams_consumed_by_syscalls') != rx.get('received_datagrams'):
        issues.append('syscall accounting mismatch')
    if rx.get('duplicate_events') != 0 or rx.get('reordered_events') != 0 or rx.get('forward_gap_events') != 0:
        issues.append('sequence anomaly')
    if rx.get('queue_sample_capacity_grows') != 0:
        issues.append('sample vector grew on hot path')
    if trial['mode']=='eventfd':
        # One notification for each successfully enqueued frame, plus one
        # for termination. Consumer may combine many notifications per read.
        if rx.get('eventfd_notification_writes') != count+1:
            issues.append('eventfd notifications missing')
        if not isinstance(rx.get('eventfd_wakeup_reads'),int) or rx['eventfd_wakeup_reads'] < 1:
            issues.append('eventfd never woke worker')
    return {'valid': not issues,'reasons':issues}


def summarize(raw: dict) -> dict:
    valid = defaultdict(list)
    for trial in raw['trials']:
        if trial['acceptance']['valid']:
            valid[(trial['target_pps'],trial['mode'])].append(trial)
    summary={}
    for rate in raw['rates']:
        for mode in MODES:
            group=valid[rate,mode]
            summary[f'{rate}/{mode}']={
                'valid_runs':len(group),'total_runs':raw['repeats'],
                'median_queue_p99_us':statistics.median([t['receiver']['queue_p99_us'] for t in group]) if group else None,
                'median_consumer_active_cpu_ms':statistics.median([t['receiver']['consumer_active_cpu_ns']/1e6 for t in group]) if group else None,
                'median_sender_pps':statistics.median([t['sender']['achieved_sendto_pps'] for t in group]) if group else None,
                'median_consumer_active_voluntary_switches':statistics.median([t['receiver']['consumer_active_voluntary_context_switches'] for t in group]) if group else None,
                'median_rx_datagrams_per_successful_call':statistics.median([t['receiver']['rx_datagrams_per_successful_call'] for t in group]) if group else None
            }
    accepted_triplets=[]
    for index in range(raw['repeats']):
        for rate in raw['rates']:
            runs=[t for t in raw['trials'] if t['repeat']==index and t['target_pps']==rate]
            if (len(runs)!=len(MODES) or {t['mode'] for t in runs}!=set(MODES) or
                    any(not t['acceptance']['valid'] for t in runs)):
                continue
            rates=[t['sender']['achieved_sendto_pps'] for t in runs]
            if max(rates)-min(rates)>0.05*rate:continue
            accepted_triplets.append({'repeat':index,'target_pps':rate,
                                     'observed_pps':[round(x,3) for x in rates],
                                     'p99_us':{t['mode']:t['receiver']['queue_p99_us'] for t in runs},
                                     'active_cpu_ms':{t['mode']:round(t['receiver']['consumer_active_cpu_ns']/1e6,4) for t in runs}})
    summary['triplet_matched']={'accepted':len(accepted_triplets),'expected':raw['repeats']*len(raw['rates']),'cases':accepted_triplets}
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver',type=Path,required=True)
    parser.add_argument('--sender',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=4)
    parser.add_argument('--count',type=int,default=600)
    parser.add_argument('--rates',type=int,nargs='+',default=[1000,5000])
    parser.add_argument('--seed',type=int,default=10271110)
    a=parser.parse_args()
    if not (1<=a.repeats<=20 and 50<=a.count<=20000 and a.rates and all(100<=x<=30000 for x in a.rates)):
        parser.error('invalid test size')
    receiver=a.receiver.resolve();sender=a.sender.resolve()
    randomizer=random.Random(a.seed)
    raw={'schema_version':1,'receiver':str(receiver),'sender':str(sender),
         'rates':a.rates,'repeats':a.repeats,'count':a.count,'seed':a.seed,
         'environment':{'platform':platform.platform(),'python':platform.python_version(),
                        'cpu_affinity_count':len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
                        'cgroup_cpu_max':get_cpu_quota()},'trials':[]}
    for repeat in range(a.repeats):
        for rate in a.rates:
            order=list(MODES);randomizer.shuffle(order)
            for mode in order:
                trial=run_trial(receiver,sender,rate=rate,count=a.count,mode=mode)
                trial['repeat']=repeat
                raw['trials'].append(trial)
    raw['summary']=summarize(raw)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(raw,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'output':str(a.output),'summary':raw['summary']},sort_keys=True))
    # Do not silently pass with a single skewed or incomplete comparison.
    return 0 if raw['summary']['triplet_matched']['accepted']==a.repeats*len(a.rates) else 1


if __name__=='__main__':
    raise SystemExit(main())
