"""Conservative, version-scoped text parsers for common router CLI formats.

These parsers intentionally return only recognized BGP peer, IPv4 route, and
interface fields. They are NOT complete vendor routing/FIB parsers.
Unknown lines are reported instead of silently interpreted as valid state.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

STATES = {"Idle", "Connect", "Active", "OpenSent", "OpenConfirm", "Established", "Establ"}
IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
PREFIX = re.compile(rf"(?P<prefix>{IPV4}/\d{{1,2}})")
PEER = re.compile(rf"^\s*(?P<address>{IPV4})\s+(?P<body>.+?)\s*$")
CISCO_ROUTE = re.compile(
    rf"^\s*(?P<code>[A-Z])(?:[ *+>]*)\s+(?P<prefix>{IPV4}/\d{{1,2}})"
    rf".*?\bvia\s+(?P<nexthop>{IPV4})\b", re.I
)
JUNOS_ROUTE = re.compile(rf"^\s*(?P<prefix>{IPV4}/\d{{1,2}})\s+\*?\[(?P<protocol>[A-Z]+)/\d+\]", re.I)
JUNOS_VIA = re.compile(rf"\b(?:to\s+)?(?P<nexthop>{IPV4})\s+via\s+(?P<interface>[\w./:-]+)", re.I)
CISCO_IFACE = re.compile(r"^\s*(?P<interface>\S+)\s+(?P<address>\S+)\s+"
                         r"(?:YES|NO)\s+\S+\s+(?P<status>up|down|administratively down)\s+"
                         r"(?P<protocol>up|down)\s*$", re.I)
JUNOS_IFACE = re.compile(r"^\s*(?P<interface>(?:ge|xe|et|ae|lo|irb)-?[\w./:-]+)\s+"
                        r"(?P<admin>up|down)\s+(?P<link>up|down)\b", re.I)


def _valid_address(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def _valid_prefix(value: str) -> bool:
    try:
        ipaddress.IPv4Network(value, strict=False)
        return True
    except ValueError:
        return False


def parse_bgp(profile: str, raw: str) -> dict[str, dict[str, Any]]:
    """Parse a narrow subset of Junos 'show bgp summary' / IOS/EOS summary.

    Cisco/EOS State/PfxRcd is numeric when Established; Junos often prints
    Establ plus counters. Unrecognized rows are NOT labeled Established.
    """
    peers: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        match = PEER.match(line)
        if not match or not _valid_address(match["address"]):
            continue
        columns = match["body"].split()
        if len(columns) < 5:
            continue
        if profile in ("ios", "eos"):
            # Neighbor V AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd
            if columns[0] not in ("4", "6") or not columns[1].isdigit():
                continue
            state_col = columns[-1]
            if state_col.isdigit():
                state = "Established"
            elif state_col in STATES:
                state = "Established" if state_col == "Establ" else state_col
            else:
                continue
            peers[match["address"]] = {"state": state, "remote_as": int(columns[1])}
        elif profile == "junos":
            # Peer AS InPkt OutPkt OutQ Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped
            if not columns[0].isdigit():
                continue
            tail = columns[-1]
            state_token = next((s for s in reversed(columns) if s in STATES), None)
            if state_token is not None:
                state = "Established" if state_token in ("Establ", "Established") else state_token
            elif re.fullmatch(r"\d+/\d+/\d+/\d+", tail):
                state = "Established"
            else:
                continue
            peers[match["address"]] = {"state": state, "remote_as": int(columns[0])}
    return peers


def parse_routes(profile: str, raw: str) -> dict[str, dict[str, Any]]:
    routes: dict[str, dict[str, Any]] = {}
    pending: tuple[str, str] | None = None
    for line in raw.splitlines():
        if profile in ("ios", "eos"):
            m = CISCO_ROUTE.match(line)
            if m and _valid_prefix(m["prefix"]) and _valid_address(m["nexthop"]):
                routes[m["prefix"]] = {"protocol_code": m["code"].upper(),
                                       "nexthop": m["nexthop"]}
        elif profile == "junos":
            m = JUNOS_ROUTE.match(line)
            if m and _valid_prefix(m["prefix"]):
                pending = (m["prefix"], m["protocol"].upper())
            elif pending:
                hop = JUNOS_VIA.search(line)
                if hop and _valid_address(hop["nexthop"]):
                    routes[pending[0]] = {"protocol": pending[1],
                                          "nexthop": hop["nexthop"],
                                          "interface": hop["interface"]}
                    pending = None
                elif line.strip() and not line[0].isspace():
                    pending = None
    return routes


def parse_interfaces(profile: str, raw: str) -> dict[str, dict[str, str]]:
    interfaces: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        if profile == "ios":
            m = CISCO_IFACE.match(line)
            if m:
                interfaces[m["interface"]] = {"admin_oper": m["status"].lower(),
                                               "protocol": m["protocol"].lower()}
        elif profile == "junos":
            m = JUNOS_IFACE.match(line)
            if m:
                interfaces[m["interface"]] = {"admin_oper": m["admin"].lower(),
                                               "protocol": m["link"].lower()}
        elif profile == "eos":
            # 'show interfaces status': Port Name Status Vlan Duplex Speed Type
            cols = line.split()
            if not cols or not re.match(r"^(?:Et|Ethernet|Po|Port-Channel)\d", cols[0], re.I):
                continue
            status = next((s for s in cols[1:] if s.lower() in
                           {"connected", "notconnect", "disabled", "errdisabled"}), None)
            if status:
                interfaces[cols[0]] = {"status": status.lower()}
    return interfaces


def parse_vendor_commands(profile: str, commands: dict) -> dict | None:
    if profile not in ("ios", "junos", "eos"):
        return None
    output: dict[str, Any] = {"vendor": profile, "bgp_peers": {}, "routes": {},
                              "interfaces": {}, "parse_status": {}, "limitations": [
        "Text CLI formats vary by OS version; only recognized rows are included.",
        "Vendor RIB is not a kernel FIB or application delivery measurement.",
    ]}
    mapping = (("bgp_raw", "bgp_peers", parse_bgp),
               ("routes_raw", "routes", parse_routes),
               ("interfaces_raw", "interfaces", parse_interfaces))
    for label, target, parser in mapping:
        result = commands.get(label)
        if not result or not result.get("ok"):
            output["parse_status"][target] = "unavailable"
            continue
        raw = result.get("stdout", "")
        values = parser(profile, raw)
        output[target] = values
        output["parse_status"][target] = "parsed" if values else "unrecognized_or_empty"
    return output
