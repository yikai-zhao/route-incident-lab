#!/usr/bin/env python3
"""Explicitly run repeated local FRR faults; independently gate saved evidence.

Docker and Containerlab must already be installed and the labeled local lab
must be deployed. Audit-only mode requires neither and performs no mutations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("neighbor_outage", "backup_failover", "route_ok_blackhole")
REQUIRED = ("bgp-preflight.json", "before.json", "fault.json", "recovered.json",
            "continuous-http-samples.json", "report.json", "audit.json")


def inspect_trial(folder: Path, scenario: str, repetition: int) -> dict:
    errors = []
    digests = {}
    for filename in REQUIRED:
        path = folder / filename
        if not path.is_file():
            errors.append("missing " + filename)
        else:
            digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = {"scenario": scenario, "repetition": repetition,
              "evidence_sha256": digests, "errors": errors, "status": "FAIL"}
    if errors:
        return result
    try:
        values = {name: json.loads((folder / name).read_text()) for name in REQUIRED}
        audit, report, bgp = (values["audit.json"], values["report.json"],
                              values["bgp-preflight.json"])
        samples = values["continuous-http-samples.json"]
        if audit.get("verdict") != "PASS":
            errors.append("independent audit not PASS")
        checks = audit.get("checks")
        if not isinstance(checks, dict) or not checks or not all(
            value is True for value in checks.values()
        ):
            errors.append("missing or failed audit checks")
        if report.get("assertions_passed") is not True:
            errors.append("controller assertions failed")
        if not isinstance(bgp, dict) or set(bgp) != {"r1", "r2", "r3"} or any(
            not isinstance(peers, dict) or len(peers) != 2 or
            any(state != "Established" for state in peers.values())
            for peers in bgp.values()
        ):
            errors.append("six baseline BGP views not Established")
        if values["before.json"].get("phase") != "before" or (
            values["recovered.json"].get("phase") != "recovered"
        ):
            errors.append("mismatched phase")
        if not isinstance(samples.get("samples"), list) or not samples["samples"] or samples.get("errors"):
            errors.append("continuous samples absent or contain errors")
        delivery = report.get("continuous_delivery")
        if not isinstance(delivery, dict) or not isinstance(delivery.get("attempts"), int) or delivery["attempts"] < 1:
            errors.append("no continuous HTTP attempts")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append("unreadable or invalid evidence: " + str(exc))
    result["status"] = "PASS" if not errors else "FAIL"
    return result


def inspect_campaign(out: Path, repeats: int) -> dict:
    trials = [inspect_trial(out / ("round-" + str(i)) / scenario, scenario, i)
              for i in range(1, repeats + 1) for scenario in SCENARIOS]
    passes = sum(t["status"] == "PASS" for t in trials)
    return {"schema": 1, "evidence_level": "local_real_frr_only_if_executed",
            "expected_trials": len(trials), "passed_trials": passes,
            "status": "PASS" if passes == len(trials) else "INCOMPLETE_OR_FAILED",
            "trials": trials,
            "limitations": ["Sampled HTTP outage windows are not exact BGP convergence.",
                            "No wire packet loss or NIC timestamp measurements."]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path("lab-output/frr-campaign"))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--audit-only", action="store_true")
    p.add_argument("--allow-faults", action="store_true")
    a = p.parse_args(argv)
    if not 1 <= a.repeats <= 10:
        p.error("repeats must be within 1..10")
    if not a.audit_only and not a.allow_faults:
        p.error("opt-in --allow-faults is mandatory for real local faults")
    out = a.output.resolve()
    if not a.audit_only:
        if any((out / ("round-" + str(i)) / s).exists()
               for i in range(1, a.repeats + 1) for s in SCENARIOS):
            p.error("old trial folders present: use a new output path to avoid stale evidence")
        out.mkdir(parents=True, exist_ok=True)
        smoke = [sys.executable, str(ROOT / "scripts/lab_smoke.py"),
                 "--output", str(out / "smoke.json")]
        try:
            for i in range(1, a.repeats + 1):
                for scenario in SCENARIOS:
                    subprocess.run(smoke, check=True, timeout=180)
                    subprocess.run(
                        [sys.executable, str(ROOT / "scripts/lab_run.py"),
                         "--scenario", scenario, "--allow-faults",
                         "--output", str(out / ("round-" + str(i)))],
                        check=True, timeout=360)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print("Live lab stopped: " + str(exc), file=sys.stderr)
    report = inspect_campaign(out, a.repeats)
    out.mkdir(parents=True, exist_ok=True)
    (out / "campaign-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report["status"], report["passed_trials"], "/", report["expected_trials"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
