"""Three-phase, evidence-limited diagnosis for local lab scenarios.

This module never changes network configuration. The separate opt-in lab
controller owns fault injection and is restricted to labeled test containers.
"""
from __future__ import annotations

from .analyze import _route_state, _selected_control_routes, compare

WATCH = "10.10.2.0/24"
SOURCE = "r1"


def _device(snapshot: dict, name: str = SOURCE) -> dict:
    return next((d for d in snapshot.get("devices", []) if d.get("name") == name), {})


def _fib_route(snapshot: dict) -> dict | None:
    return (_route_state(_device(snapshot)) or {}).get(WATCH)


def _control_route(snapshot: dict) -> bool:
    return WATCH in _selected_control_routes(_device(snapshot))


def _probe(snapshot: dict) -> dict | None:
    return next((p for p in snapshot.get("probes", []) if p.get("name") == "h1-to-h2-http"), None)


def _full_delivery(snapshot: dict) -> bool | None:
    probe = _probe(snapshot)
    if probe is None or not probe.get("attempts"):
        return None
    return probe.get("failures") == 0


def build_observation_timeline(before: dict, fault: dict, recovered: dict,
                               actions: list[dict] | None = None) -> list[dict]:
    """Show command/action/probe observations with their *precision*, not invented convergence times."""
    entries: list[dict] = []
    for phase, snap in (("before", before), ("fault", fault), ("recovered", recovered)):
        start = snap.get("capture_started_utc", snap.get("timestamp"))
        finish = snap.get("capture_finished_utc", snap.get("timestamp"))
        if start and finish:
            entries.append({"time_utc": finish, "kind": "snapshot_window", "phase": phase,
                            "capture_started_utc": start, "capture_finished_utc": finish,
                            "precision": "observed_within_window_not_exact_state_transition"})
        for probe in snap.get("probes", []):
            for sample in probe.get("samples", []):
                time_utc = sample.get("observed_at_utc")
                if time_utc:
                    entries.append({"time_utc": time_utc, "kind": "http_probe_observation",
                                    "phase": phase, "probe": probe.get("name"),
                                    "success": sample.get("ok"),
                                    "connect_ms": sample.get("connect_ms"),
                                    "delivery_ms": sample.get("delivery_ms"),
                                    "precision": "client_side_observation"})
    for action in actions or []:
        when = action.get("performed_at_utc")
        if when:
            entries.append({"time_utc": when, "kind": "local_test_action",
                            "container": action.get("test_container"), "argv": action.get("argv"),
                            "command_finished_utc": action.get("command_finished_utc"),
                            "precision": "command_execution_window_not_exact_network_effect"})
    return sorted(entries, key=lambda entry: (entry["time_utc"], entry["kind"]))


def analyze_three_phases(before: dict, fault: dict, recovered: dict,
                         scenario: str, action_records: list[dict] | None = None) -> dict:
    """Build findings and explicit test assertions, never assert a root cause.

    Capture windows and action stamps are observational boundaries, not
    microsecond-accurate network event timestamps.
    """
    if scenario not in ("neighbor_outage", "backup_failover", "route_ok_blackhole"):
        raise ValueError(f"unknown scenario: {scenario}")
    b_fib, f_fib, r_fib = (_fib_route(x) for x in (before, fault, recovered))
    b_delivery, f_delivery, r_delivery = (_full_delivery(x) for x in (before, fault, recovered))
    observed = {
        "route_initially_present": b_fib is not None,
        "traffic_initially_works": b_delivery is True,
        "route_during_fault": f_fib,
        "selected_control_route_during_fault": _control_route(fault),
        "traffic_during_fault": f_delivery,
        "route_after_recovery": r_fib,
        "traffic_after_recovery": r_delivery,
    }
    if scenario == "neighbor_outage":
        expectations = {
            "route_withdrawn": f_fib is None,
            "application_unreachable": f_delivery is False,
            "route_reinstalled": r_fib is not None,
        }
    elif scenario == "backup_failover":
        expectations = {
            "primary_nexthop_replaced": b_fib is not None and f_fib is not None
               and b_fib.get("gateway") != f_fib.get("gateway"),
            "backup_nexthop_is_r3": f_fib is not None and f_fib.get("gateway") == "10.0.13.2",
            "application_works_after_convergence": f_delivery is True,
            "original_nexthop_restored": r_fib is not None and b_fib is not None
               and r_fib.get("gateway") == b_fib.get("gateway"),
        }
    else:
        expectations = {
            "main_route_unchanged": b_fib is not None and f_fib == b_fib,
            "control_plane_route_present": _control_route(fault),
            "application_unreachable": f_delivery is False,
            "route_still_present_on_recovery": r_fib is not None,
        }
    expectations["initially_healthy"] = b_fib is not None and b_delivery is True
    expectations["application_recovers"] = r_delivery is True
    suspected = []
    if f_fib is not None and _control_route(fault) and f_delivery is False:
        suspected.append({
            "kind": "route_present_but_application_unreachable",
            "classification": "observation_not_root_cause",
            "detail": "Selected RIB and exact main-table route coexist with failed HTTP delivery."
                      " Check RPDB rules, filters, next-hop reachability, server health and capture timing.",
        })
    report = {
        "schema_version": 2, "scenario": scenario,
        "evidence_level": ("local_lab_observation" if action_records and all(
            s.get("capture_mode") == "live_observation"
            and s.get("lab_attestation", {}).get("preflight_passed") is True
            and s.get("lab_attestation", {}).get("lab") == "routeincident"
            for s in (before, fault, recovered)
        ) else "synthetic_or_unverified"),
        "capture_windows": [
            {"phase": phase, "started_utc": s.get("capture_started_utc", s.get("timestamp")),
             "finished_utc": s.get("capture_finished_utc", s.get("timestamp"))}
            for phase, s in (("before", before), ("fault", fault), ("recovered", recovered))
        ],
        "actions": action_records or [],
        "timeline": build_observation_timeline(before, fault, recovered, action_records),
        "observations": observed,
        "assertions": expectations,
        "assertions_passed": all(expectations.values()),
        "changes": {"on_fault": compare(before, fault)["findings"],
                    "on_recovery": compare(fault, recovered)["findings"]},
        "diagnostic_leads": suspected,
        "limitations": [
            "Snapshot times bound when a state was observed; they do not timestamp convergence events.",
            "HTTP results are sampled application delivery, not packet loss or one-way wire latency.",
            "Simultaneous events do not establish causation. Each result needs independent validation.",
            "FRR selected RIB vs Linux main FIB is an exact-prefix heuristic, not full VRF/RPDB forwarding validation.",
        ],
    }
    return report
