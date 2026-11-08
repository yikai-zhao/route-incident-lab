# Linux kernel observability and cross-plane incident correlation

## Why this matters

The C++17 Linux NETLINK_ROUTE monitor captures actual IPv4 route snapshots
(RTM_GETROUTE) and subscribes to IPv4 route/link notifications, read-only.
The Python correlator combines those timestamped records with the existing
native epoll TCP-connect sampler. It reports temporal windows without claiming
that a route event caused an application connection failure.

## Reproduce on a Linux host

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    ./build/rtnetlink_monitor --watch-ms 5000 --output kernel.jsonl

    python3 scripts/demo_observability.py \
      --epoll build/epoll_connect_sampler \
      --netlink build/rtnetlink_monitor \
      --output lab-output/kernel-and-tcp

    python3 -m unittest discover -s tests -p test_kernel_observability.py -v
    python3 -m unittest discover -s tests -p test_epoll_sampler.py -v

The integration demo writes kernel.jsonl, samples.jsonl, report.json and
correlation.json into the output folder. It deliberately stops and restarts
only an ephemeral 127.0.0.1 listener; it never changes a route or link.

To correlate two previously collected files from the SAME Linux host/boot:

    python3 scripts/correlate_kernel_delivery.py \
      --kernel kernel.jsonl --tcp samples.jsonl \
      --window-ms 500 --output correlation.json

The correlator validates complete sequence IDs, input JSONL structure,
observation ordering and monotonic sample timing. It includes SHA-256 digests
of source bytes in the output, and refuses invalid data.

## 2026-11-08 actual local verification

Compiled the native monitor with g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic.
A real kernel RTM_GETROUTE dump returned SEVEN IPv4 route records.
A 500ms read-only watch observed ZERO route and ZERO link notifications.
That does NOT prove the kernel routes were stable.

A second integrated run concurrently monitored Linux routes and sampled 240
real localhost TCP connects during a temporary local service stop/restart.
It recorded 180 successes and 60 refused/failed attempts; seven route
snapshot records, and zero route/link notifications. This is a loopback
service lifecycle observation, NOT a measured BGP convergence incident.

All TEN new kernel/correlation targeted tests passed (real kernel dump, real
same-host integration, malformed input rejection and synthetic event-window
fixtures), and the previous SEVEN epoll Socket tests were rerun and passed.
This is NOT a claim that the entire repository test suite or live FRR
Containerlab experiments have passed.

## Measurement boundaries

- Kernel route dump is not a single atomic system-wide snapshot.
- NETLINK receive time is a user-space observation, not a kernel transition time.
- CPU scheduling, dropped notifications and socket buffering affect evidence.
- IPv4 single-path information is parsed; IPv6, nested multipath attributes,
  VRF/policy routing, application HTTP delivery and packet traces are not.
- Route presence does not establish packet forwarding or application delivery.
- TCP connect is not a packet-loss statistic, multicast feed quality, or NIC latency.
- Kernel events and TCP attempts must share Linux host and boot; monotonic time
  values from different machines are not directly comparable.
- Temporal co-occurrence supplies a diagnostic lead; causality remains unproven.

## Next validation milestones

1. Live FRR three-router Containerlab runs with authorized fault injection,
   repetitions and archived raw router/kernel/application evidence.
2. UDP multicast sequence-aware receiver with real local socket tests that
   distinguish injected sender gaps from genuine network loss.
3. Matched workload poll/epoll profiling with cycles, CPU, scheduling, p99 and
   uncertainty, not a cherry-picked favorable latency result.
4. Resource and backpressure limits, fd leak checks and bounded memory tests.

No scheduled GitHub Actions, cloud infrastructure or proprietary employer
network data is required.
