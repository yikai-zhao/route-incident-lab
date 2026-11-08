# Local connection stress and measurement discipline

## What the harness actually does

The optional scripts/local_stress_observe.py runs six actual localhost TCP
connection experiments with the existing C++ Linux epoll sampler:

- Three burst trials: 2000 attempts each, maximum 64 in flight, 0ms spacing.
- Three paced trials: 2000 attempts each, maximum 16 in flight, 1ms spacing.

For each trial it starts its own real localhost TCP listener, preserves
2000 individual Socket connect observations, and records success/failure,
errno distribution, p50/p95/p99 for successful connects, sampler CPU
time (user/system), wall time, and deltas of Linux TcpExt ListenOverflows
and ListenDrops counters when available.

The script creates no external network traffic, does not change Linux
routing, and uses no hosted CI or paid services.

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/local_stress_observe.py

Outputs are in lab-output/stress-experiment/repeated-runs.json and six
raw JSONL files, ignored by Git.

## What we actually observed on one Linux host (2026-11-08)

An initial 2000-attempt burst trial once had 64 TCP connect timeouts and
one repeated burst run later had 36 timeouts; other burst runs had zero.
That is a warning about environmental and load variability, not a
measured stable failure rate.

The latest three burst trials each completed 2000 connects with no
failures and successful-connect p99 values from approximately 0.78 to
2.07 ms. Three paced trials had zero observed failures and p99 ranging
roughly 0.13 to 0.36 ms. Every trial used real loopback TCP sockets,
but the system and scheduling conditions were not tightly controlled.

Critically: these are DIFFERENT OFFERED LOADS. Burst versus paced is
NOT a fair apples-to-apples epoll speed comparison and cannot establish
that one event handling strategy is faster. The paced run consumes
approximately 2 seconds just in scheduled spacing; its observed p99
cannot be compared as a general claim of faster networking.

When any attempts fail, successful-only p99 excludes the failures and
can misleadingly improve even as reliability worsens. Report the
failure percentage and errno distribution beside every percentile.

## What is not yet proven

- No hardware performance counter study (perf/PMU), CPU affinity controls,
  CPU governor tracking, NIC timestamping or controlled network load.
- No poll versus epoll implementation matched under equivalent offered load.
- No trading exchange, colocated server or external multicast-feed testing.
- No claim of packet loss rates, wire RTT, bandwidth, production capacity,
  deterministic p99 or any microsecond advantage.

## Next serious experiment

Implement a matched Linux poll/epoll workload in one benchmark binary
using identical outstanding connection limits, issue schedule, target,
socket options, warmup and measurement duration. Run multiple randomized
trials on a pinned CPU (when supported) with context-switch counters,
branch/cache perf events (when permitted), host/CPU/compiler metadata,
failure rates, confidence intervals and raw samples. Report negative
results and measurement uncertainty, not only favorable runs.
