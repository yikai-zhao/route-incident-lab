#!/usr/bin/env python3
"""Local, explicitly opt-in fault controller for *one labeled Containerlab topology*.

The normal collector is read-only. This file is separate and only executes
writes inside the three test containers, after checking their labels. It never
applies host `ip`/`tc`/`iptables` changes or contacts production addresses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from routeincident.capture import capture, write_json  # noqa: E402
from routeincident.scenarios import analyze_three_phases  # noqa: E402

CONTAINERS = {name: f"clab-routeincident-{name}" for name in ("r1", "r2", "r3", "h1", "h2")}
SCENARIOS = ("neighbor_outage", "backup_failover", "route_ok_blackhole")


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(args: list[str], timeout: int = 10) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {args!r}: {p.stderr[-500:]}")
    return p.stdout


def docker(node: str, *argv: str, timeout: int = 10) -> str:
    return run(["docker", "exec", CONTAINERS[node], *argv], timeout)


def preflight() -> None:
    for node, name in CONTAINERS.items():
        label = run(["docker", "inspect", "-f", '{{index .Config.Labels "routeincident.testbed"}}', name]).strip()
        if label != "true":
            raise RuntimeError(f"{name}: missing routeincident.testbed=true label; refusing lab faults")
    if "10.10.2.2" not in docker("h2", "ip", "-4", "-o", "addr", "show", "dev", "eth1"):
        raise RuntimeError("h2 expected test address missing; refusing faults")


def capture_state(config: dict, phase: str, output: Path) -> dict:
    snap = capture(config)
    snap["phase"] = phase
    write_json(str(output / f"{phase}.json"), snap)
    return snap


def http_probe(count: int = 8) -> dict:
    content = docker("h1", "python3", "/opt/routeincident/tcp_http_probe.py",
                     "--host", "10.10.2.2", "--port", "8080",
                     "--count", str(count), "--interval", "0.20", "--timeout", "0.50", timeout=35)
    attempts = [json.loads(line) for line in content.splitlines() if line.strip()]
    if len(attempts) != count:
        raise RuntimeError("incomplete probe output")
    successes = [row for row in attempts if row["ok"]]
    return {
        "name": "h1-to-h2-http", "target": "10.10.2.2:8080", "attempts": len(attempts),
        "successes": len(successes), "failures": len(attempts) - len(successes),
        "connect_failure_pct": round(100 * (len(attempts)-len(successes)) / len(attempts), 2),
        "samples": attempts,
    }


def mutate(node: str, args: list[str], actions: list[dict]) -> None:
    started = timestamp()
    run(["docker", "exec", CONTAINERS[node], *args])
    actions.append({"performed_at_utc": started, "command_finished_utc": timestamp(),
                    "test_container": CONTAINERS[node], "argv": args})


def initial_wait(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = json.loads(docker("r1", "ip", "-j", "route", "show", "10.10.2.0/24"))
            if any(r.get("dst") == "10.10.2.0/24" and r.get("gateway") == "10.0.12.2"
                   for r in result):
                if json.loads(docker("r1", "vtysh", "-c", "show ip route json")):
                    return
        except (RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise RuntimeError("BGP primary path not ready in 60s; inspect vtysh/FRR daemon logs")


def wait_route(condition, timeout: float = 35.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rows = json.loads(docker("r1", "ip", "-j", "route", "show", "10.10.2.0/24"))
            route = next((r for r in rows if r.get("dst") == "10.10.2.0/24"), None)
            if condition(route):
                return
        except (RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise RuntimeError("route did not reach expected state within timeout")


def apply_fault(scenario: str, actions: list[dict]) -> None:
    if scenario == "neighbor_outage":
        mutate("r1", ["ip", "link", "set", "eth2", "down"], actions)
        mutate("r1", ["ip", "link", "set", "eth1", "down"], actions)
        wait_route(lambda r: r is None)
    elif scenario == "backup_failover":
        mutate("r1", ["ip", "link", "set", "eth1", "down"], actions)
        wait_route(lambda r: r is not None and r.get("gateway") == "10.0.13.2")
    else:
        mutate("r1", ["ip", "rule", "add", "to", "10.10.2.0/24", "unreachable", "priority", "100"], actions)
        wait_route(lambda r: r is not None)


def recover(scenario: str, actions: list[dict]) -> None:
    if scenario == "neighbor_outage":
        mutate("r1", ["ip", "link", "set", "eth1", "up"], actions)
        mutate("r1", ["ip", "link", "set", "eth2", "up"], actions)
    elif scenario == "backup_failover":
        mutate("r1", ["ip", "link", "set", "eth1", "up"], actions)
    else:
        mutate("r1", ["ip", "rule", "del", "to", "10.10.2.0/24", "unreachable", "priority", "100"], actions)
    wait_route(lambda r: r is not None and r.get("gateway") == "10.0.12.2")


def emergency_cleanup(scenario: str) -> None:
    if scenario in ("neighbor_outage", "backup_failover"):
        for iface in (("eth1", "eth2") if scenario == "neighbor_outage" else ("eth1",)):
            try:
                docker("r1", "ip", "link", "set", iface, "up")
            except RuntimeError as exc:
                print(f"WARNING: manual cleanup may be needed ({iface}): {exc}", file=sys.stderr)
    else:
        try:
            docker("r1", "ip", "rule", "del", "to", "10.10.2.0/24", "unreachable", "priority", "100")
        except RuntimeError:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Opt-in local Containerlab-only fault validation")
    p.add_argument("--scenario", choices=SCENARIOS, required=True)
    p.add_argument("--allow-faults", action="store_true", help="REQUIRED: modify only labeled local lab containers")
    p.add_argument("--output", default="lab-output")
    a = p.parse_args(argv)
    if not a.allow_faults:
        p.error("refusing to change any network state without --allow-faults")
    try:
        preflight()
        config = json.loads((ROOT / "labs/three-router/collector-config.json").read_text())
        out = Path(a.output).resolve() / a.scenario
        out.mkdir(parents=True, exist_ok=True)
        actions: list[dict] = []
        initial_wait()
        before = capture_state(config, "before", out)
        for dev in before["devices"]:
            for required in ("control_routes", "kernel_routes", "bgp_peers", "policy_rules"):
                if not dev["commands"].get(required, {}).get("ok"):
                    raise RuntimeError(f"{dev['name']} {required} collection failed; no faults injected")
        before["probes"] = [http_probe()]
        if before["probes"][0]["failures"]:
            raise RuntimeError("baseline HTTP delivery is not healthy; no faults injected")
        write_json(str(out / "before.json"), before)
        try:
            apply_fault(a.scenario, actions)
            fault = capture_state(config, "fault", out)
            fault["probes"] = [http_probe()]
            write_json(str(out / "fault.json"), fault)
        finally:
            try:
                recover(a.scenario, actions)
            finally:
                emergency_cleanup(a.scenario)
        initial_wait()
        recovered = capture_state(config, "recovered", out)
        recovered["probes"] = [http_probe()]
        write_json(str(out / "recovered.json"), recovered)
        report = analyze_three_phases(before, fault, recovered, a.scenario, actions)
        write_json(str(out / "report.json"), report)
        print(f"Wrote: {out / 'report.json'}")
        print(f"Assertions: {sum(report['assertions'].values())}/{len(report['assertions'])}")
        return 0 if report["assertions_passed"] else 1
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"lab validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
