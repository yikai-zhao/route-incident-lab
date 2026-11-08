# Linux epoll connectivity sampler

## Purpose

This is the systems-programming component of Route Incident Lab. It complements the
Python router-state collector and FRR lab with a native C++17, Linux-only, event-driven
TCP **connect-completion** sampler. It is not a packet generator, exchange-feed
benchmark, HTTP validator, or a claim of sub-microsecond application latency.

## Run on a local Linux host (no Docker or cloud required)

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 scripts/demo_epoll_local.py --binary build/epoll_connect_sampler \
    --output lab-output/epoll-loopback
python3 -m unittest discover -s tests -p 'test_epoll_sampler.py' -v
```

The demo starts a temporary listener bound to `127.0.0.1`, deliberately stops
that **local server** and restarts it, while the C++ client continues sampling.
The result contains `samples.jsonl` with one record per attempted connection
and `report.json` with phase-scoped counts and simple consistency checks.

Direct probe (target must be a **numeric IPv4 address**):

```bash
./build/epoll_connect_sampler 127.0.0.1 8080 240 16 10 300 samples.jsonl
# IPv4 PORT COUNT CONCURRENCY INTERVAL_MS TIMEOUT_MS [SAMPLES.jsonl]
```

This does not connect to production hosts unless an operator explicitly supplies
their address. Only use it on endpoints you own or have permission to test.
Limits: count 1..10000, concurrency 1..256, interval 0..1000 ms,
per-attempt timeout 10..10000 ms. DNS lookup is deliberately excluded.

## Relevant systems mechanisms

- Nonblocking sockets (`SOCK_NONBLOCK | SOCK_CLOEXEC`) and Linux `epoll`
  readiness with `getsockopt(SO_ERROR)` verification.
- Bounded concurrent in-flight connections with backpressure: a saturated
  concurrency limit delays launches instead of creating an unbounded queue.
- RAII ownership of socket descriptors; explicit epoll deregistration and
  bounded per-attempt deadlines, including no-response cases.
- Single-host monotonic timestamps for each sample; the optional JSONL file
  preserves success/failure, errno, launch/finish observations and duration.
- p50/p95/p99 computed over successful connect completions only. When all
  attempts fail, percentiles are `null`, never made-up zeros.
- The event loop defaults to **level-triggered** epoll, avoiding unjustified
  edge-triggered complexity. Socket readiness is not by itself a successful
  TCP connect; `SO_ERROR` is decisive.

## Evidence produced in local verification, 2026-11-08

The C++ program was compiled with g++ and executed on a Linux container host.
One real loopback server stop/restart run emitted 240 real socket attempts:
61/61 successful before stop, 0/60 successful while stopped,
119/119 successful after restart. All 7 new local Linux regression tests passed;
these include an intentionally unaccepted listener backlog to exercise
multiple actual in-flight sockets.

These figures describe that one host/run only. The 16-concurrent setting in
the lifecycle demo is a **limit**, not 16 continuously active connections:
the observed peak in that run was one because localhost connects completed
quickly. Under an intentionally saturated accept backlog, concurrent
in-flight operations were observed and bounded by the configured limit.

**Crucial limitations:** this tests service listener availability over loopback,
not BGP link failure, route convergence, exchange market-data latency,
packet loss, throughput under trading load, HTTP response success,
or vendor hardware. Host scheduling and TCP backlog behavior affect timings.
The reader should not compare these small-sample numbers to exchange or
cross-machine benchmarks. The entire repo and Docker/Containerlab live lab
still need a fresh complete verification run.

## Next meaningful systems increments

1. Prove the real FRR three-router scenarios, three repetitions each, and
   archive the resulting source snapshots, HTTP probes and route events.
2. Add sampled kernel route notifications (RTNETLINK) on the same test host,
   then compare **observed** route updates with observed application failures.
3. Benchmark `poll` vs `epoll` under identical controlled workloads and
   measure CPU scheduling/context-switch overhead with local profiling tools.
4. Investigate p99 variability under load only after the baseline and
   measurement uncertainty are established. Keep CPU governor, host,
   compiler flags and sample counts in benchmark metadata.

No GitHub Actions workflows, paid services, proprietary employer captures,
or scheduled network jobs are required.
