#!/usr/bin/env python3
"""Verify a local lab run's saved evidence, without network access or fault injection.

This is a consistency check of self-reported files, NOT independent attestation
that Docker/FRR actually ran or that an outage duration was measured exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCENARIOS = {'neighbor_outage', 'backup_failover', 'route_ok_blackhole'}
PHASES = ('before', 'fault', 'recovered')
REQUIRED = ('control_routes', 'kernel_routes', 'bgp_peers', 'policy_rules')
EXPECTED = {'r1': 2, 'r2': 2, 'r3': 2}


def audit(folder: Path) -> dict:
    checks: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    inputs: dict[str, dict] = {}
    for stem in (*PHASES, 'report', 'bgp-preflight', 'continuous-http-samples'):
        path = folder / (stem + '.json')
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('JSON root is not an object')
            inputs[stem] = value
            evidence[path.name] = hashlib.sha256(raw).hexdigest()
            checks['file:' + stem] = True
        except (OSError, ValueError, UnicodeDecodeError):
            checks['file:' + stem] = False
    report = inputs.get('report', {})
    scenario = report.get('scenario')
    checks['known_scenario_and_directory'] = scenario in SCENARIOS and folder.name == scenario
    checks['locally_attested_report'] = report.get('evidence_level') == 'local_lab_observation'
    assertions = report.get('assertions')
    checks['scenario_assertions'] = (isinstance(assertions, dict) and bool(assertions)
                                    and all(v is True for v in assertions.values())
                                    and report.get('assertions_passed') is True)
    peers = inputs.get('bgp-preflight', {})
    checks['all_six_ebgp_views_established'] = (
        set(peers) == set(EXPECTED) and all(
            isinstance(peers.get(node), dict) and len(peers[node]) == count
            and all(value == 'Established' for value in peers[node].values())
            for node, count in EXPECTED.items()))
    for phase in PHASES:
        snap = inputs.get(phase, {})
        devices = snap.get('devices')
        checks['capture:' + phase] = (
            snap.get('phase') == phase and snap.get('capture_mode') == 'live_observation'
            and snap.get('lab_attestation', {}).get('lab') == 'routeincident'
            and snap.get('lab_attestation', {}).get('preflight_passed') is True
            and isinstance(devices, list) and {d.get('name') for d in devices} == set(EXPECTED)
            and all(all(d.get('commands', {}).get(k, {}).get('ok') is True for k in REQUIRED)
                    for d in devices)) if isinstance(devices, list) else False
        probes = snap.get('probes')
        probe = next((p for p in probes if isinstance(p, dict)
                      and p.get('name') == 'h1-to-h2-http'), None) if isinstance(probes, list) else None
        checks['http:' + phase] = (probe is not None
            and type(probe.get('attempts')) is int and probe['attempts'] > 0
            and type(probe.get('failures')) is int
            and type(probe.get('successes')) is int
            and probe['failures'] + probe['successes'] == probe['attempts']
            and (probe['failures'] == 0 if phase != 'fault' or scenario == 'backup_failover'
                 else probe['failures'] > 0)) if probe is not None else False
    actions = report.get('actions')
    checks['recorded_local_actions'] = isinstance(actions, list) and bool(actions) and all(
        isinstance(a, dict) and a.get('test_container', '').startswith('clab-routeincident-')
        and isinstance(a.get('argv'), list) and bool(a['argv'])
        and a.get('performed_at_utc') and a.get('command_finished_utc')
        for a in actions) if isinstance(actions, list) else False
    stream = inputs.get('continuous-http-samples', {})
    samples = stream.get('samples')
    valid_samples = isinstance(samples, list) and bool(samples) and all(
        isinstance(s, dict) and type(s.get('observed_monotonic_ns')) is int
        and type(s.get('ok')) is bool for s in samples) if isinstance(samples, list) else False
    ordered = (valid_samples and all(samples[i]['observed_monotonic_ns'] <
                                     samples[i+1]['observed_monotonic_ns']
                                     for i in range(len(samples)-1)))
    checks['continuous_http_samples'] = (stream.get('source') == 'live_local_http_probe'
        and stream.get('errors') == [] and ordered)
    delivery = report.get('continuous_delivery', {})
    checks['continuous_report_matches_raw'] = (ordered and isinstance(delivery, dict)
        and delivery.get('probe_errors') == []
        and delivery.get('attempts') == len(samples)
        and delivery.get('successes') == sum(s['ok'] for s in samples)
        and delivery.get('failures') == sum(not s['ok'] for s in samples))
    return {
        'verdict': 'PASS' if checks and all(checks.values()) else 'FAIL',
        'scenario': scenario, 'checks': checks, 'sha256_of_inputs': evidence,
        'continuous_http': {'attempts': delivery.get('attempts'),
                            'failures': delivery.get('failures'),
                            'observed_failure_span_ms': delivery.get('observed_failure_span_ms'),
                            'possible_outage_window_ms': delivery.get('possible_outage_window_ms')},
        'limitations': [
            'Checks self-reported artifacts for internal consistency, not independent authenticity.',
            'No packet capture, exact convergence measurement or production-hardware validation.',
            'The sample-derived outage windows are not exact network downtime.',
        ],
    }


def markdown(result: dict) -> str:
    checks = result['checks']
    lines = [f"# Local incident evidence: {result.get('scenario') or 'unknown'}",
             '', f"**Evidence gate: {result['verdict']}**", '',
             'This result audits local files only; it does not independently prove that FRR ran.', '',
             '## Checks', '']
    lines += [f"- {'PASS' if ok else 'FAIL'}: {name}" for name, ok in checks.items()]
    metrics = result['continuous_http']
    lines += ['', '## Client-observed HTTP sampling', '',
              f"- Attempts: {metrics['attempts']}; failed responses: {metrics['failures']}",
              f"- Observed failed-sample span (ms): {metrics['observed_failure_span_ms']}",
              f"- Bounded possible outage window (ms): {metrics['possible_outage_window_ms']}",
              '', '## Evidence SHA-256 (local file integrity reference)', '']
    lines += [f"- {name}: {digest}" for name, digest in result['sha256_of_inputs'].items()]
    lines += ['', '## Limitations', '']
    lines += [f'- {x}' for x in result['limitations']]
    return '\n'.join(lines) + '\n'


def main() -> int:
    p = argparse.ArgumentParser(description='Offline consistency audit of a completed local lab run')
    p.add_argument('--run-dir', type=Path, required=True)
    args = p.parse_args()
    folder = args.run_dir.resolve()
    result = audit(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'audit.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    (folder / 'incident.md').write_text(markdown(result))
    failed = [k for k, ok in result['checks'].items() if not ok]
    print(f"{result['verdict']}: {len(result['checks']) - len(failed)}/{len(result['checks'])} evidence checks; {folder / 'incident.md'}")
    if failed:
        print('Failed checks: ' + ', '.join(failed))
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
