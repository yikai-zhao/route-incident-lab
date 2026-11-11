#!/usr/bin/env python3
"""Compile an auditable summary from TWO already-captured local raw experiments."""
import hashlib, json, statistics, sys
from pathlib import Path
from benchmark_consumer_wakeup import check_trial, summarize

def git_blob(path: Path) -> str:
    b=path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()

root=Path(__file__).resolve().parents[1]
paths=[root/'evidence/eventfd-benchmark.json',root/'evidence/eventfd-benchmark-independent.json']
raws=[json.loads(x.read_text()) for x in paths]
for campaign in raws:
    for trial in campaign['trials']:
        if trial['acceptance'] != check_trial(trial):
            raise ValueError('recorded trial acceptance does not match raw sender/receiver fields')
    if campaign['summary'] != summarize(campaign):
        raise ValueError('recorded summary does not match raw trials')
    if campaign['summary']['triplet_matched']['accepted'] != campaign['summary']['triplet_matched']['expected']:
        raise ValueError('ineligible matched comparison')
report={'schema_version':1,'experiment':'three-mode Linux UDP SPSC consumer wakeup comparison',
        'scope':'real localhost UDP sockets; shared container scheduling; not NIC/wire latency',
        'data_source':'synthetic locally generated UDP packets, NOT production or exchange feed',
        'baseline_main':'ed42dc8bac79d49ff333260d03369479076bbf35',
        'receiver_source_git_blob':git_blob(root/'cpp/udp_pipeline.cpp'),
        'publisher_source_git_blob':git_blob(root/'cpp/udp_publisher.cpp'),
        'spsc_source_git_blob':git_blob(root/'cpp/spsc_ring.h'),
        'total_trials':sum(len(x['trials']) for x in raws),
        'total_matched_triplets':sum(x['summary']['triplet_matched']['accepted'] for x in raws),
        'enforced_gates':'sender 100% success, receiver lossless, sequence clean, achieved target +-10%, three modes within 5% target',
        'campaigns':[],
        'important_caveats':[
             'eventfd uses one write syscall per successful queue enqueue, plus termination; not a coalesced fast path',
             'consumer_active_cpu_ns starts at first dequeued frame and includes waiting and work until worker exit',
             'process_cpu_active_ns is whole receiver process CPU after first received frame, not only consumer CPU',
             'sample p99 is application queue residence from user-space receive to consumer dequeue, not wire-to-wire latency',
             'container scheduling causes large per-round tail variability; no stable p99 speedup is established',
             'independent random orders do not imply independent machines or production validation',
        ]}
for i,data in enumerate(raws):
  camp={'name':['first','independent'][i], 'seed':data['seed'],'raw_sha256':hashlib.sha256(paths[i].read_bytes()).hexdigest(),
        'raw_filename':paths[i].name,'accepted_triplets':data['summary']['triplet_matched']['accepted'],
        'total_trials':len(data['trials']),'rates':{}}
  for rate in data['rates']:
    metric={}
    for mode in ('eventfd','sleep','spin'):
      trials=[x for x in data['trials'] if x['target_pps']==rate and x['mode']==mode and x['acceptance']['valid']]
      assert len(trials)==data['repeats']
      def med(key):return statistics.median(x['receiver'][key] for x in trials)
      metric[mode]={
         'observed_pps_median':round(statistics.median(x['sender']['achieved_sendto_pps'] for x in trials),3),
         'consumer_active_cpu_ms_median':round(med('consumer_active_cpu_ns')/1e6,3),
         'receiver_process_active_cpu_ms_median':round(med('process_cpu_active_ns')/1e6,3),
         'queue_p99_us_median':round(med('queue_p99_us'),3),
         'queue_p99_us_range':[round(min(x['receiver']['queue_p99_us'] for x in trials),3),round(max(x['receiver']['queue_p99_us'] for x in trials),3)],
         'voluntary_context_switches_median':med('consumer_active_voluntary_context_switches'),
         'eventfd_notification_writes_median':med('eventfd_notification_writes'),
         'eventfd_wakeup_reads_median':med('eventfd_wakeup_reads')
      }
    camp['rates'][str(rate)]=metric
  report['campaigns'].append(camp)
output=root/'evidence/consumer-wakeup/20261110-summary.json'
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(output)
