"""Turn snapshots into concrete, machine-checkable incident findings."""
from __future__ import annotations

import difflib
import json
from typing import Any


def _json_command(device: dict, label: str) -> Any | None:
    data = device.get("commands", {}).get(label, {})
    if not data.get("ok"):
        return None
    try:
        return json.loads(data.get("stdout", ""))
    except (json.JSONDecodeError, TypeError):
        return None


def _route_state(device: dict) -> dict[str, dict] | None:
    """Canonical IPv4 Linux FIB entries or selected FRR RIB entries."""
    state: dict[str, dict] = {}
    data = _json_command(device, "kernel_routes")
    if "kernel_routes" in device.get("commands", {}) and not isinstance(data, list):
        return None
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict) or not row.get("dst"):
                continue
            prefix = row["dst"]
            state[prefix] = {
                "gateway": row.get("gateway"), "dev": row.get("dev"),
                "protocol": row.get("protocol"), "metric": row.get("metric"),
            }
    elif device.get("profile") in ("frr", "frr_bgp"):
        data = _json_command(device, "control_routes")
        if isinstance(data, dict):
            for prefix, entries in data.items():
                if not isinstance(entries, list):
                    continue
                selected = next((e for e in entries
                                 if isinstance(e, dict) and e.get("selected")), None)
                if selected is None:
                    continue
                next_hops = selected.get("nexthops", [])
                state[prefix] = {
                    "protocol": selected.get("protocol"),
                    "installed": selected.get("installed"),
                    "nexthops": next_hops if isinstance(next_hops, list) else [],
                }
    return state


def _selected_control_routes(device: dict) -> dict[str, dict]:
    data = _json_command(device, "control_routes")
    result: dict[str, dict] = {}
    if not isinstance(data, dict):
        return result
    for prefix, routes in data.items():
        if isinstance(routes, list):
            for route in routes:
                if isinstance(route, dict) and route.get("selected") is True:
                    result[prefix] = route
                    break
    return result


def _bgp_peers(device: dict) -> dict[str, str] | None:
    """Extract peer FSM state from FRR BGP summary JSON, where available."""
    root = _json_command(device, "bgp_peers")
    if not isinstance(root, dict):
        return None
    containers = [root]
    containers.extend(v for v in root.values() if isinstance(v, dict))
    result = {}
    for block in containers:
        peers = block.get("peers")
        if isinstance(peers, dict):
            for address, info in peers.items():
                if isinstance(info, dict):
                    state = info.get("state") or info.get("bgpState")
                    if isinstance(state, str):
                        result[str(address)] = state
    return result


def _linux_interfaces(device: dict) -> dict[str, dict] | None:
    data = _json_command(device, "interfaces")
    if not isinstance(data, list):
        return None
    return {
        row["ifname"]: {
            "operstate": row.get("operstate"),
            "admin_up": "UP" in row.get("flags", []),
        }
        for row in data if isinstance(row, dict) and isinstance(row.get("ifname"), str)
    }


def _linux_neighbors(device: dict) -> dict[str, str] | None:
    data = _json_command(device, "arp_neighbors")
    if not isinstance(data, list):
        return None
    return {
        f"{row.get('dev', '?')}:{row['dst']}": str(row.get("state", "UNKNOWN"))
        for row in data if isinstance(row, dict) and isinstance(row.get("dst"), str)
    }


def _linux_policy_rules(device: dict) -> list[str] | None:
    """Normalized Linux RPDB entries. Preserve relevant decision fields, not volatile counters."""
    data = _json_command(device, "policy_rules")
    if not isinstance(data, list):
        return None
    selected = ("priority", "src", "dst", "action", "table", "fwmark", "iif", "oif", "protocol")
    return sorted(json.dumps({k: row[k] for k in selected if k in row}, sort_keys=True)
                  for row in data if isinstance(row, dict))


def _device_index(snapshot: dict) -> dict[str, dict]:
    return {d["name"]: d for d in snapshot.get("devices", []) if "name" in d}


def analyze_snapshot(snapshot: dict) -> list[dict]:
    """Conservative checks against an operator-supplied exact prefix watch list.

    Only compares same-device main IPv4 FIB with FRR's selected RIB routes.
    Missing kernel entries can have legitimate reasons (VRF, FIB suppression,
    summarization). Findings are leads, not proven causes.
    """
    events = []
    for dev in snapshot.get("devices", []):
        for label, result in dev.get("commands", {}).items():
            if not result.get("ok"):
                events.append({"severity": "warning", "kind": "capture_failed",
                               "device": dev["name"], "detail": f"{label}: {result.get('stderr', '')[:160]}"})
        if dev.get("profile") not in ("frr", "frr_bgp"):
            continue
        control = _selected_control_routes(dev)
        kernel = _json_command(dev, "kernel_routes")
        if not isinstance(kernel, list) or not isinstance(_json_command(dev, "control_routes"), dict):
            continue
        fib = {r.get("dst") for r in kernel if isinstance(r, dict)}
        for prefix in dev.get("watch_prefixes", []):
            route = control.get(prefix)
            if route and prefix not in fib:
                events.append({
                    "severity": "warning", "kind": "rib_fib_mismatch",
                    "device": dev["name"], "prefix": prefix,
                    "detail": "selected FRR IPv4 route, no exact prefix in Linux main-table snapshot; verify VRFs and next-hop resolution",
                })
    return events


def compare(before: dict, after: dict) -> dict:
    findings: list[dict] = []
    a, b = _device_index(before), _device_index(after)
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            findings.append({"severity": "info", "kind": "device_set_changed", "device": name})
            continue
        old, new = a[name], b[name]
        old_rules, new_rules = _linux_policy_rules(old), _linux_policy_rules(new)
        if old_rules is not None and new_rules is not None and old_rules != new_rules:
            findings.append({"severity": "warning", "kind": "policy_rules_changed", "device": name,
                             "added": sorted(set(new_rules) - set(old_rules)),
                             "removed": sorted(set(old_rules) - set(new_rules))})
        for label, parser in (("interface", _linux_interfaces), ("arp_neighbor", _linux_neighbors)):
            prev_state, next_state = parser(old), parser(new)
            if prev_state is None or next_state is None:
                continue
            for key in sorted(set(prev_state) | set(next_state)):
                if prev_state.get(key) != next_state.get(key):
                    findings.append({
                        "severity": "warning", "kind": f"{label}_state_changed",
                        "device": name, "item": key,
                        "before": prev_state.get(key), "after": next_state.get(key),
                    })

        old_peers, new_peers = _bgp_peers(old), _bgp_peers(new)
        if old_peers is not None and new_peers is not None:
            for peer in sorted(set(old_peers) | set(new_peers)):
                if old_peers.get(peer) != new_peers.get(peer):
                    findings.append({
                        "severity": "warning", "kind": "bgp_peer_state_changed",
                        "device": name, "peer": peer,
                        "before": old_peers.get(peer), "after": new_peers.get(peer),
                    })

        old_routes, new_routes = _route_state(old), _route_state(new)
        if old_routes is None or new_routes is None:
            findings.append({"severity": "warning", "kind": "route_snapshot_unavailable", "device": name,
                             "detail": "route diff skipped because a routing snapshot was invalid or failed"})
        else:
            for prefix in sorted(set(old_routes) | set(new_routes)):
                if prefix not in old_routes:
                    kind, severity = "route_added", "info"
                elif prefix not in new_routes:
                    kind, severity = "route_removed", "warning"
                elif old_routes[prefix] != new_routes[prefix]:
                    kind, severity = "route_changed", "warning"
                else:
                    continue
                findings.append({"severity": severity, "kind": kind,
                                 "device": name, "prefix": prefix,
                                 "before": old_routes.get(prefix), "after": new_routes.get(prefix)})
        for label in sorted(set(old.get("commands", {})) | set(new.get("commands", {}))):
            before_cmd = old.get("commands", {}).get(label)
            after_cmd = new.get("commands", {}).get(label)
            if before_cmd is None or after_cmd is None:
                continue
            if before_cmd.get("ok") != after_cmd.get("ok"):
                findings.append({"severity": "warning", "kind": "command_status_changed",
                                 "device": name, "command": label,
                                 "before": before_cmd.get("ok"), "after": after_cmd.get("ok")})
            if label.endswith("_raw") and before_cmd.get("ok") and after_cmd.get("ok"):
                raw_before = before_cmd.get("stdout", "").splitlines()
                raw_after = after_cmd.get("stdout", "").splitlines()
                if raw_before != raw_after:
                    diff = list(difflib.unified_diff(raw_before, raw_after,
                                    fromfile="before", tofile="after", lineterm=""))
                    findings.append({"severity": "info", "kind": "raw_cli_changed",
                                     "device": name, "command": label,
                                     "diff_preview": diff[:40]})
    probes_before = {p["name"]: p for p in before.get("probes", [])}
    for p in after.get("probes", []):
        old = probes_before.get(p["name"])
        if old is None:
            continue
        old_fail = old.get("connect_failure_pct", old.get("loss_pct"))
        new_fail = p.get("connect_failure_pct", p.get("loss_pct"))
        if isinstance(new_fail, (int, float)) and isinstance(old_fail, (int, float)) and new_fail > old_fail:
            findings.append({"severity": "warning", "kind": "connect_failure_increased",
                             "probe": p["name"], "before_failure_pct": old_fail,
                             "after_failure_pct": new_fail})
        old_p95, new_p95 = old.get("p95_ms"), p.get("p95_ms")
        if old_p95 is not None and new_p95 is not None and old_p95 > 0:
            if new_p95 > old_p95 * 2 and new_p95 - old_p95 >= 5:
                findings.append({"severity": "warning", "kind": "tcp_connect_latency_regression",
                                 "probe": p["name"], "before_p95_ms": old_p95,
                                 "after_p95_ms": new_p95})
    findings.extend(analyze_snapshot(after))
    return {
        "schema_version": 1, "before_timestamp": before.get("timestamp"),
        "after_timestamp": after.get("timestamp"),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "warnings": sum(x["severity"] == "warning" for x in findings),
            "info": sum(x["severity"] == "info" for x in findings),
        },
    }
