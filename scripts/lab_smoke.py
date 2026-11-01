#!/usr/bin/env python3
"""Read-only smoke test for a deployed local Route Incident Lab.

Requires Linux, Docker, Containerlab and the explicitly labeled test containers.
Never injects faults, edits router configuration, or contacts external hosts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from lab_run import preflight, initial_wait, check_lab_health, http_probe  # noqa: E402
from routeincident.capture import capture, write_json  # noqa: E402


def smoke() -> dict:
    preflight()
    initial_wait()
    neighbors = check_lab_health()
    config = json.loads((ROOT / "labs/three-router/collector-config.json").read_text())
    snapshot = capture(config)
    for router in snapshot["devices"]:
        for label in ("control_routes", "kernel_routes", "bgp_peers", "policy_rules"):
            if not router["commands"].get(label, {}).get("ok"):
                raise RuntimeError(f"{router['name']} {label} could not be collected")
    delivery = http_probe(count=5)
    if delivery["failures"] != 0:
        raise RuntimeError(f"baseline client HTTP failed: {delivery!r}")
    return {"evidence_level": "local_read_only_smoke", "bgp_peers": neighbors,
            "http": delivery, "capture": snapshot,
            "limitations": ["A smoke test is not proof of failover or convergence time."]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="lab-output/smoke.json")
    args = parser.parse_args()
    try:
        result = smoke()
        write_json(args.output, result)
        print(f"Read-only smoke PASS: 3 routers / 6 BGP peer views, "
              f"{result['http']['attempts']} successful HTTP responses; {args.output}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Read-only smoke FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
