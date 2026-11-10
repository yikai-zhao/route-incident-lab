"""Validate real native UDP scheduling experiment evidence; does not claim speedup."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def summarize(records):
    groups = defaultdict(list)
    seen = set()
    for x in records:
        key = (x['rate_target'], x['repeat'], x['mode'])
        if key in seen:
            raise ValueError('duplicate run key')
        seen.add(key)
        if x['mode'] not in ('sleep', 'spin'):
            raise ValueError('unknown scheduling mode')
        if x['publisher_returncode'] or x['receiver_returncode']:
            raise ValueError('nonzero process exit')
        pub = x['publisher']; rx = x['receiver']
        target_rate = x['rate_target']
        if not 0.90*target_rate <= pub['achieved_sendto_pps'] <= 1.10*target_rate:
            raise ValueError('sender failed achieved-rate gate')
        if pub['successful_sendto'] != 600 or pub['send_errors'] != 0:
            raise ValueError('publisher incomplete')
        if rx['received_datagrams'] != 600 or rx['rx_datagrams_consumed_by_syscalls'] != 600:
            raise ValueError('receiver incomplete')
        if rx['processed'] != 600 or rx['application_queue_drops'] or rx['malformed_frames']:
            raise ValueError('application delivery not clean')
        if rx['duplicate_events'] or rx['reordered_events'] or rx['forward_gap_events']:
            raise ValueError('sequence integrity failure')
        if not rx['complete'] or rx['consumer_thread_cpu_ns'] < 0:
            raise ValueError('invalid receiver result')
        if not (0 <= rx['consumer_active_cpu_ns'] <= rx['consumer_thread_cpu_ns']):
            raise ValueError('active CPU accounting invalid')
        if not (0 <= rx['consumer_active_voluntary_context_switches'] <= rx['consumer_voluntary_context_switches']):
            raise ValueError('active context switch accounting invalid')
        if not (0 <= rx['consumer_active_involuntary_context_switches'] <= rx['consumer_involuntary_context_switches']):
            raise ValueError('active context switch accounting invalid')
        if rx['consumer_voluntary_context_switches'] < 0 or rx['consumer_involuntary_context_switches'] < 0:
            raise ValueError('invalid thread context switch counts')
        groups[(target_rate, x['mode'])].append(x)
    for rate in (1000, 5000):
        for repeat in range(3):
            a = next((x for x in records if x['rate_target'] == rate and x['repeat']==repeat and x['mode']=='sleep'), None)
            b = next((x for x in records if x['rate_target'] == rate and x['repeat']==repeat and x['mode']=='spin'), None)
            if not a or not b:
                raise ValueError('missing matched pair')
            if abs(a['publisher']['achieved_sendto_pps']-b['publisher']['achieved_sendto_pps'])>0.05*rate:
                raise ValueError('paired achieved-rate mismatch')
    return [{
        'target_pps': rate, 'mode': mode,
        'valid': len(groups[(rate,mode)]),
        'consumer_cpu_ms_median': round(median(x['receiver']['consumer_thread_cpu_ns']/1e6 for x in groups[(rate,mode)]),3),
        'consumer_active_cpu_ms_median': round(median(x['receiver']['consumer_active_cpu_ns']/1e6 for x in groups[(rate,mode)]),3),
        'consumer_active_voluntary_switches_median': median(x['receiver']['consumer_active_voluntary_context_switches'] for x in groups[(rate,mode)]),
        'consumer_voluntary_switches_median': median(x['receiver']['consumer_voluntary_context_switches'] for x in groups[(rate,mode)]),
        'consumer_involuntary_switches_median': median(x['receiver']['consumer_involuntary_context_switches'] for x in groups[(rate,mode)]),
        'queue_p99_us_median': round(median(x['receiver']['queue_p99_us'] for x in groups[(rate,mode)]),3)
    } for rate in (1000,5000) for mode in ('sleep','spin')]

if __name__=='__main__':
    raw=Path(__file__).resolve().parents[1] / 'evidence' / 'consumer-scheduling' / 'local-12-trials.json'
    result=summarize(json.loads(raw.read_text()))
    print(json.dumps(result,indent=2))
