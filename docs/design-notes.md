# Design notes

The code tries to keep three different observations separate:

1. Control-plane state: selected FRRouting RIB and BGP neighbor states.
2. Linux routing/forwarding clues: main-table routes, policy rules, interfaces and ARP entries.
3. Client-observed delivery: TCP connect sampling and actual HTTP response from the lab client.

The same destination prefix can exist in a route table while user traffic still fails. The analyzer therefore reports **what was observed** and avoids declaring a root cause solely because two events happened near each other.

## Boundaries

- Collection profiles execute a fixed set of read-only commands; live collection never deploys configurations.
- Linux routing snapshots and FRR state collection are sequential and cannot prove a single atomic network state.
- Snapshot windows bound observations, rather than pretending to measure exact convergence timestamps.
- FRR selected RIB and Linux main table comparisons use exact-prefix heuristics; policy routing, VRFs and next-hop recursion can affect actual forwarding.
- Loss from failed TCP connect attempts is labeled connect failure; successful connect is not proof of a successful HTTP request.
- A separate, explicitly opt-in controller may inject faults only in local lab containers with an expected testbed label.
- Junos, IOS and EOS support raw read-only CLI collection only. Their output is not parsed into structured router state.

## Next engineering work

Validate the actual three-router topology and record repeated real runs, then add independent continuous probing and a bounded incident sampler. Do not claim microsecond forwarding latency or a confirmed root cause from the current synthetic tests.
