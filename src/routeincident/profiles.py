"""Small set of fixed, read-only command profiles.

Other vendors are raw CLI captures only. Structured comparison currently covers
Linux IPv4 main-table routes and FRRouting IPv4 route JSON.
"""

PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "linux": {
        "kernel_routes": ("ip", "-j", "route", "show", "table", "main"),
        "interfaces": ("ip", "-j", "link", "show"),
        "arp_neighbors": ("ip", "-j", "neigh", "show"),
        "policy_rules": ("ip", "-j", "rule", "show"),
    },
    "frr": {
        "control_routes": ("vtysh", "-c", "show ip route json"),
        "kernel_routes": ("ip", "-j", "route", "show", "table", "main"),
        "bgp_peers": ("vtysh", "-c", "show bgp ipv4 unicast summary json"),
        "isis_neighbors": ("vtysh", "-c", "show isis neighbor json"),
        "policy_rules": ("ip", "-j", "rule", "show"),
        "interfaces": ("ip", "-j", "link", "show"),
    },
    "junos": {
        "routes_raw": ("show route terse",),
        "bgp_raw": ("show bgp summary",),
        "interfaces_raw": ("show interfaces terse",),
    },
    "ios": {
        "routes_raw": ("show ip route",),
        "bgp_raw": ("show ip bgp summary",),
        "interfaces_raw": ("show ip interface brief",),
    },
    "eos": {
        "routes_raw": ("show ip route",),
        "bgp_raw": ("show ip bgp summary",),
        "interfaces_raw": ("show interfaces status",),
    },
}

PROFILES["frr_bgp"] = {
    label: argv for label, argv in PROFILES["frr"].items() if label != "isis_neighbors"
}
