# Route Incident Lab

I worked on routing software, CLI diagnostics, and regression tests during my co-op. A show command can tell you that a route exists, but not whether application traffic actually reaches its destination. I built this small lab to make those questions easier to test together.

The goal is to record **what changed**, **what traffic did**, and **what can actually be concluded**. No AI diagnosis, network dashboards, cloud services, or automatic production router configuration.

## Quick start (works without Docker)

Python 3.10+ is enough for the offline demo. The Python core has no runtime pip dependencies.

```bash
PYTHONPATH=src python3 -m routeincident.cli demo
PYTHONPATH=src python3 -m routeincident.cli scenario-demo --out-dir reports/synthetic
python3 -m unittest discover -s tests -v
```

Three fixture scenarios: loss of both BGP paths, failover from a direct BGP link to an alternate, and an application blackhole even while the FRR selected route and Linux main-table route remain in place. These are **deliberately synthetic fixtures** that exercise the analyzer, not measurements from a live network.

## Verified Linux routing + matched reactor evidence (2026-11-08)

Two new reproducible **real Linux systems experiments** add evidence beyond
synthetic route fixtures:

- **Isolated real kernel routing fault:** two Linux network namespaces with
  independent primary/backup veth links; an actual RTNETLINK observer and
  C++ client watch link failure, manual static route failover, blackhole
  injection and recovery. Three full runs passed, observing 1080 TCP attempts,
  18 route notifications and 9 link notifications. This is **NOT FRR/BGP
  dynamic convergence** and changes no host route.
- **Matched poll vs epoll:** one C++17 state machine with identical socket
  issue/deadline/sampling code switches only readiness backend. Five paired
  trials per workload; raw connection results, errors, CPU/context switches,
  execution order and samples' SHA-256 are recorded. No general winner
  or exchange latency claim.

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/benchmark_connect_reactors.py --binary build/connect_reactor_compare
    python3 scripts/run_isolated_kernel_lab.py \
      --compare build/connect_reactor_compare --netlink build/rtnetlink_monitor
    python3 -m unittest discover -s tests -p test_connect_reactor_compare.py -v
    RIL_RUN_NETNS_LIVE=1 python3 -m unittest discover \
      -s tests -p test_isolated_kernel_lab.py -v

The namespace experiment requires user-namespace and veth support on Linux,
and refuses to modify network state without first entering new isolated
user+network namespaces. Users on hosts without this support should treat
the integration test as unavailable, not passed.

[Detailed executed evidence, limitations and history](docs/CORE_EVIDENCE_20261108.md)

## Linux C++ event-driven systems exercise (tested on real loopback sockets)

A Linux-only `epoll_connect_sampler` now samples multiple nonblocking TCP
connections with bounded concurrency, per-connection deadlines, RAII file
descriptor management, `SO_ERROR` verification, and raw timestamped JSONL.
Unlike the offline route fixtures, its test harness interacts with **real
localhost socket connections**.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 scripts/demo_epoll_local.py --binary build/epoll_connect_sampler
python3 -m unittest discover -s tests -p test_epoll_sampler.py -v
```

The demonstration stops and restarts a temporary local TCP listener while the
C++ client is continuously sampling. It writes `lab-output/epoll-loopback/report.json`
and `samples.jsonl`. In one actual Linux loopback run there were 240 connection
attempts: **61 healthy successes, 60 refusals while the server was down, and
119 recovery successes**. Seven new Linux socket tests passed, including a
real accept-backlog saturation test that forces overlapping pending sockets.
This is **not** evidence of live FRR/BGP fault recovery, HTTP delivery, remote
network throughput, wire latency, or trading-system performance.

Read [systems design, verification and limits](docs/SYSTEMS_ENGINEERING.md).

## Real Linux kernel event observability (2026-11-08)

The native C++ **read-only RTNETLINK monitor** now collects actual kernel IPv4
route snapshots and subscribes to route/link notifications. A separate
cross-plane analyzer correlates those observations with Linux epoll TCP
connection samples from the **same host and boot**, using monotonic clocks.
It does **not** claim that time-adjacent events prove causality.

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/demo_observability.py --output lab-output/kernel-and-tcp

**Verified locally:** the kernel returned 7 actual IPv4 route entries; the
concurrent loopback server stop/restart run recorded 240 TCP attempts (180
successes, 60 failures), and **no route/link notifications**. The correct
diagnosis is that no kernel route/link changes were observed, not that a BGP
failure happened. Ten new targeted kernel/correlation tests and seven existing
epoll real-socket tests passed in this Linux runtime. Full repository tests
and real FRR Containerlab fault injection remain unverified.

Read [Linux kernel observability: evidence and limitations](docs/KERNEL_OBSERVABILITY.md).

## Two-thread C++ SPSC UDP pipeline and real backpressure evidence

The new Linux C++17 pipeline separates a real epoll/recvmmsg UDP receiver
from its sequence-processing thread with a bounded **single-producer,
single-consumer queue** using acquire/release atomics. Unlike inferring
loss from missing sequence IDs, it directly counts valid packets that
were actually received but then rejected when the application ring
was full. It separately reports whether a kernel SO_RXQ_OVFL notice
was observed.

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    ctest --test-dir build --output-on-failure
    python3 -m unittest discover -s tests -p test_udp_pipeline.py -v
    python3 scripts/demo_spsc_pipeline.py --binary build/udp_pipeline

Local verification: **7/7 real UDP socket tests** passed under both
normal and ASan/UBSan builds. The queue completed **200,000 strictly
ordered cross-thread transfers**, additionally passing ThreadSanitizer.
A real loopback multicast baseline processed 64/64 frames with 0
application drops. In three deliberately pressured 160-frame runs,
**131, 131 and 130** were dropped by the four-slot application queue
respectively; kernel RX overflow was NOT observed. These are induced
software-queue drops, not claims of wire packet loss or low latency.

[Architecture, evidence and limitations](docs/SPSC_PIPELINE.md)

Verify saved evidence without rerunning sockets:

    python3 scripts/audit_spsc_evidence.py lab-output/spsc-udp

This checks raw SHA-256, frame accounting, queue capacity and monotonic timestamps; it cannot authenticate publisher identity or prove physical wire loss.


## Synthetic UDP multicast feed diagnostics (actual Linux sockets, 2026-11-08)

A new **Linux C++17 multicast receiver** uses epoll and batched recvmmsg,
strict 16-byte binary parsing, bounded per-stream sequence state, and
optional kernel socket receive timestamp / RX queue overflow data.
It distinguishes **forward sequence jumps, duplicates, and out-of-order
arrivals**, not imaginary network packet-loss measurements.

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/demo_multicast_feed.py --output lab-output/multicast-feed

**Real local validation:** 10/10 actual IPv4 loopback multicast datagrams
were received: 2 forward jumps, 2 late arrivals and 1 duplicate.
The publisher intentionally sent these anomalies; no network loss claim
is supported. Six new real-socket regressions passed, bringing the local
subset for epoll, RTNETLINK and UDP to **23/23 passing**. The full
repository checkout and FRR Containerlab scenarios still need validation.

[Multicast packet format, receiver architecture and evidence limits](docs/MULTICAST_FEED.md)

To run the entire local verification **on your own Linux checkout** without
Docker/cloud/hosted CI:

    bash scripts/verify_local.sh

This creates lab-output/full-local-verification with local build/test logs
and real loopback evidence. It is a script, not a scheduled background job.

## Repeatable local network stress observations

The optional Linux stress harness repeats **real TCP Socket connect tests**
under two different workload shapes and saves individual samples, errno
distributions, successful-connect p99, CPU time and available kernel
TcpExt listen queue counters:

    python3 scripts/local_stress_observe.py

An early heavy burst observed 64 connect timeouts in 2000 attempts; other
repetitions did not. The difference is why failures and raw samples matter.
**Burst and paced trials are not equivalent offered loads**; these numbers
are not a claim of low-latency optimization or trading-system performance.

[Read the stress-test methodology and caveats](docs/PERFORMANCE_MEASUREMENT.md)

## Real local lab (requires Docker + Containerlab)

`labs/three-router/` has a three-FRR-router topology, two Linux hosts, a simple HTTP target, and an explicitly opt-in local fault controller. The topology is based on Containerlab's native `frr` kind and FRRouting configuration, **not** any private employer setup.

See [local lab instructions](labs/three-router/README.md). The current execution environment cannot run containers or network namespaces, so the real BGP/FRR scenarios are **implemented but not yet validated end-to-end**. Their results must not be represented as live measurements until you run them on a compatible Linux host.

The controller can change only locally labeled test containers and requires `--allow-faults`. Normal device collection is separate and read-only.

## New functionality (2026-11-02)

- **Multi-vendor structured parsing:** Junos, IOS and EOS BGP peer, IPv4 route and interface text adapters, with synthetic format regression tests. Unrecognized OS-version layouts remain inconclusive. Real vendor hardware has not been tested.
- **Live-lab safety/preflight:** read-only smoke test checks expected BGP sessions, captured router state and actual HTTP responses before fault injection. Fault actions require explicit opt-in and testbed container labels.
- **Continuous application evidence:** finite HTTP response sampling inside h1 during a local fault, with client monotonic timestamps and bounded observed interruption estimates. This is not exact BGP convergence, passive loss measurement or one-way latency.
- **Corrected offline fixtures:** backup routing changes now match r1/r3 interface wiring, and the blackhole fixture models an unreachable RPDB rule. Fixtures are synthetic and not proof of live outcomes.

### Run validations on a local Linux Docker host

~~~bash
python3 -m unittest discover -s tests -v
containerlab validate -t labs/three-router/topology.clab.yml
cd labs/three-router
docker build -t routeincident-endpoint:local -f Dockerfile.endpoint .
sudo containerlab deploy -t topology.clab.yml
cd ../..
PYTHONPATH=src python3 scripts/lab_smoke.py
PYTHONPATH=src python3 scripts/lab_run.py --scenario neighbor_outage --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario backup_failover --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario route_ok_blackhole --allow-faults
~~~

The real topology and its fault controller have **not yet been executed end-to-end** in this environment; inspect generated reports and test results instead of assuming successful runs.

## What is implemented

- Read-only Linux IPv4 routes, interfaces, ARP neighbors, policy routing rules (RPDB), and FRR route/BGP state collection via fixed commands.
- SSH key-based capture (strict host-key checking) and Docker-local collection; Junos/IOS/EOS have conservative structured BGP, route and interface text parsers; unfamiliar formats remain inconclusive, and no hardware validation is claimed.
- Snapshot diffs for Linux routes, interfaces, ARP, BGP peers, and policy rules. A watched exact prefix in FRR selected RIB but missing from Linux main-table routes is flagged as **possible** RIB/FIB disagreement, not proof of a forwarding fault.
- An explicit three-phase report: state before fault, first observed settled fault state, and observed recovered state, with action records, capture windows, HTTP probe results and test assertions.
- A cross-plane warning if both selected RIB and a main-table route remain present while end-to-end HTTP fails. The report calls it an **observation, not a root-cause claim**.
- A bounded Python TCP connect probe and optional C++ non-blocking connect probe (RAII FD wrapper, `poll`, `SO_ERROR`, p50/p95/p99). An independent lab probe checks an actual HTTP response from the client container.

## Capturing a router without changing its state

For Linux:

```bash
PYTHONPATH=src python3 -m routeincident.cli capture --config examples/local-config.json --out before.json
# Perform an authorized change *in your own lab*, outside the collector.
PYTHONPATH=src python3 -m routeincident.cli capture --config examples/local-config.json --out after.json
PYTHONPATH=src python3 -m routeincident.cli diff --before before.json --after after.json --out incident.json
```

See `examples/frr-ssh-config.json` for a read-only SSH example. Addresses under `192.0.2.0/24` are placeholders, not live destinations.

## Optional C++ latency probe

```bash
cmake -S . -B build
cmake --build build
python3 -m http.server 8000 &
./build/tcp_connect_probe 127.0.0.1 8000 100
```

This times client-side **TCP connection completion**, not one-way latency, exchange feed latency, or actual packet forwarding time. DNS lookup is outside the measured loop. A few samples do not provide statistically meaningful tail latency.

## Evidence audit for a completed local fault run (2026-11-07)

The HTTP response probe now reads a complete TCP-fragmented HTTP status line, accepts **exactly 200** (not 2000/404) and records `http_status`. Its real-socket loopback regression suite checks fragmented, incomplete, oversized, and non-200 replies. A verified **local loopback HTTP** run is not a verified FRR test.

The opt-in local fault runner automatically writes `audit.json` and a readable `incident.md`, in addition to its original phase snapshots and raw continuous sampling. The evidence gate checks all six expected preflight eBGP peer views, three phase captures and HTTP results, local action records, unmodified raw-sample counts and report assertions. Missing or inconsistent evidence produces `FAIL` and a nonzero exit status. You can independently recheck a saved run with:

```bash
python3 -m unittest discover -s tests -v
bash scripts/smoke_once.sh                # read-only local 3-router BGP/HTTP, requires WSL2/Linux + Docker + Containerlab
PYTHONPATH=src python3 scripts/lab_run.py --scenario neighbor_outage --allow-faults
python3 scripts/audit_run.py --run-dir lab-output/neighbor_outage
# Repeat with backup_failover and route_ok_blackhole.
```

The audit only checks consistency of self-reported local files; it is **not independent proof** of execution, forwarding causality, exact convergence time or hardware support. Keep the real generated `lab-output/*/incident.md`, `audit.json`, `report.json` and phase snapshots as evidence rather than inventing results. No GitHub Actions or paid cloud tools are used.

## Limits and evidence status

- Three synthetic fixtures and regression tests exist; this revised public commit requires a fresh test run. Live FRR failover and route-policy scenarios still need first execution on a compatible Linux host.
- State capture runs commands sequentially; a change between commands may produce a mixed snapshot. The report preserves capture windows and does not fabricate exact convergence times.
- BGP + Linux main-table route presence does not prove end-to-end delivery. Kernel RPDB, VRF, ACL, next-hop reachability, process health, and traffic path may matter.
- HTTP connect and response probes are *sampled delivery*, not passive packet-loss monitors. The continuous sampler produces sample-window bounds, not exact recovery or convergence times.
- The fault controller is restricted to its local Containerlab topology. Never use it on a production router or company testbed.

There are no GitHub Actions workflows, hosted services, paid APIs, cron jobs, or background tasks in this project. Everything runs locally only when explicitly started. Public Docker images download to your computer and occupy local disk/network bandwidth, but require no cloud infrastructure charges.

## References

- [Containerlab native FRRouting kind](https://containerlab.dev/manual/kinds/frr/)
- [Containerlab Linux nodes](https://containerlab.dev/manual/kinds/linux/)
- [FRRouting Topotests developer guide](https://docs.frrouting.org/projects/dev-guide/en/latest/topotests.html)

FRR Topotests already cover topology construction and protocol tests. The goal here is narrower: correlate *operator-visible state* and *application delivery* into an evidence-limited incident report, not replace FRR's own test suite.

## License

MIT.
