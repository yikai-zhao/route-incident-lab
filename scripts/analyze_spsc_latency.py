#!/usr/bin/env python3
"""Read-only raw-sample SPSC queue residence statistics; NOT wire latency."""
import argparse
import hashlib
import json
import math
from pathlib import Path

def percentile(values, p):
    if not values: return None
    x=(len(values)-1)*p
    lo,hi=math.floor(x),math.ceil(x)
    return values[lo]+(values[hi]-values[lo])*(x-lo)

def analyze(folder):
    report=json.loads((folder/'report.json').read_text())
    trials=[report['baseline'],*report['pressured_repetitions']]
    results=[]
    for i,trial in enumerate(trials):
        name='baseline.jsonl' if i==0 else f'backpressure-{i}.jsonl'
        if trial['processed_file']!=name: raise ValueError('noncanonical filename')
        raw=(folder/name).read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=trial['processed_rows_sha256']: raise ValueError(f'{name}: SHA mismatch')
        rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
        s=trial['summary']
        if len(rows)!=s['processed'] or s['valid_frames']!=len(rows)+s['application_queue_drops']:
            raise ValueError(f'{name}: count conservation failed')
        delays=[]
        for row in rows:
            a,b=row['received_monotonic_ns'],row['processed_monotonic_ns']
            if type(a) is not int or type(b) is not int or b<a:
                raise ValueError(f'{name}: invalid monotonic timestamps')
            delays.append((b-a)/1000)
        delays.sort()
        results.append({'trial':name,'received':s['valid_frames'],'processed':len(rows),
            'application_queue_drops':s['application_queue_drops'],
            'application_queue_drop_fraction_of_received':s['application_queue_drops']/s['valid_frames'],
            'queue_residence_p50_us':percentile(delays,.5),
            'queue_residence_p95_us':percentile(delays,.95),
            'queue_residence_p99_us':percentile(delays,.99),
            'queue_residence_max_us':max(delays) if delays else None,
            'source_sha256':digest})
    return {'scope':'observed user-space queue residence, not wire latency',
        'method':'linear interpolation of per-frame monotonic timestamps',
        'trials':results,
        'warning':'Only processed frames have queue residence samples; dropped frames are excluded.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    content=json.dumps(analyze(args.directory),indent=2)+'\n'
    if args.output: args.output.write_text(content)
    else: print(content,end='')
