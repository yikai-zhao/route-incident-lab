"""Fail-closed comparison eligibility for measured Linux UDP sender/receiver trials.

A complete receive at *different actual offered loads* is not a matched benchmark.
Keep all trials, including rejected ones, in the experiment evidence.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median


def assess_pairs(records, *, target_tolerance=0.10, pair_tolerance=0.05):
    """Compare p99 only when both runs completed at the requested, matched load.

    Rate control is based on achieved sendto rate, not requested pacing. The
    tolerated spread is relative to the requested rate, to avoid a zero divide
    in noisy measurements. Pair integrity is independent of relative performance.
    """
    if not 0 < target_tolerance < 1 or not 0 < pair_tolerance < 1:
        raise ValueError('tolerances must be between 0 and 1')
    groups = defaultdict(list)
    for trial in records:
        groups[(trial['rate_target_pps'], trial['pair'])].append(trial)
    decisions = []
    for (rate, pair), trials in sorted(groups.items()):
        issues = []
        by_idle = {}
        for trial in trials:
            idle = trial.get('idle')
            if idle not in ('sleep', 'spin') or idle in by_idle:
                issues.append('missing_or_duplicate_mode')
            else:
                by_idle[idle] = trial
        if set(by_idle) != {'sleep', 'spin'} or len(trials) != 2:
            issues.append('incomplete_pair')
        details = {}
        for idle in ('sleep', 'spin'):
            t = by_idle.get(idle)
            if not t:
                continue
            if t.get('status') != 'PASS':
                issues.append(f'{idle}_integrity_failed')
            sender, receiver = t.get('sender'), t.get('receiver')
            if not isinstance(sender, dict) or not isinstance(receiver, dict):
                issues.append(f'{idle}_missing_raw_metrics')
                continue
            pps = sender.get('achieved_sendto_pps')
            p99 = receiver.get('queue_p99_us')
            offered = sender.get('successful_sendto')
            if not isinstance(pps, (float, int)) or not 0 < pps <= 1e9:
                issues.append(f'{idle}_invalid_achieved_rate')
                continue
            if not isinstance(p99, (float, int)) or not 0 <= p99 <= 1e9:
                issues.append(f'{idle}_invalid_queue_latency')
                continue
            if abs(pps / rate - 1) > target_tolerance:
                issues.append(f'{idle}_target_rate_not_attained')
            if offered != receiver.get('processed'):
                issues.append(f'{idle}_sender_receiver_count_mismatch')
            details[idle] = {'achieved_pps': pps, 'queue_p99_us': p99}
        if len(details) == 2 and abs(details['sleep']['achieved_pps'] -
                                     details['spin']['achieved_pps']) / rate > pair_tolerance:
            issues.append('within_pair_load_mismatch')
        decisions.append({'target_pps': rate, 'pair': pair,
                          'comparable': not issues,
                          'rejection_reasons': sorted(set(issues)),
                          'measurements': details})
    return decisions


def eligible_p99_summary(decisions):
    """Return medians only over *eligible matched pairs*, otherwise abstain."""
    groups = defaultdict(list)
    for decision in decisions:
        groups[decision['target_pps']].append(decision)
    report = []
    for rate, decisions_at_rate in sorted(groups.items()):
        accepted = [d for d in decisions_at_rate if d['comparable']]
        row = {'target_pps': rate, 'eligible_pairs': len(accepted),
               'total_pairs': len(decisions_at_rate),
               'rejected_pairs': [d['pair'] for d in decisions_at_rate if not d['comparable']],
               'median_queue_p99_us': None}
        if accepted:
            row['median_queue_p99_us'] = {
                idle: median(d['measurements'][idle]['queue_p99_us'] for d in accepted)
                for idle in ('sleep', 'spin')}
        report.append(row)
    return report


def main(argv=None):
    """Audit existing trial JSON without re-running the network benchmark."""
    import argparse
    import json
    from pathlib import Path
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial_directory', type=Path,
                   help='directory with rate-pair-idle.json raw trial files')
    p.add_argument('--output', type=Path, default=None)
    args = p.parse_args(argv)
    if args.trial_directory.is_file():
        packed=json.loads(args.trial_directory.read_text())
        if not isinstance(packed, dict) or not isinstance(packed.get('records'), list):
            p.error('expected JSON object with records array')
        raw=packed['records']
    else:
        raw=[]
        for file in sorted(args.trial_directory.glob('[0-9]*-*-*.json')):
            record=json.loads(file.read_text())
            record['source_file']=file.name
            raw.append(record)
    if not raw:
        p.error('no individual trial records found: do not infer from summary medians')
    decisions = assess_pairs(raw)
    report = {'raw_trials': len(raw), 'total_pairs': len(decisions),
              'eligible_pairs': sum(d['comparable'] for d in decisions),
              'pair_decisions': decisions,
              'eligible_only_summary': eligible_p99_summary(decisions),
              'limits': [
                  'Actual sendto rate is not application throughput capacity.',
                  'Failed or load-mismatched trials stay in the audit; never compare their p99.',
                  'Passing these gates does not control CPU scheduling, cache state, or NIC traffic.'
              ]}
    payload = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end='')
    # A report with zero eligible pairs must not appear successful.
    return 0 if report['eligible_pairs'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
