# Route Incident Lab

A reproducible Linux networking lab for investigating **routing failures, packet-processing backpressure, and the latency/CPU cost of design choices**. It combines read-only routing-state capture, fault reproduction, and a small C++17 UDP data path. The aim is to explain *why* a failure happens, not to claim HFT-grade latency from loopback measurements.

## Two engineering problems

**1. Packet processing under load (C++17/Linux).** The two-thread UDP receiver uses nonblocking sockets, `epoll`, optional batched `recvmmsg`, a preallocated bounded SPSC queue, and per-stream sequence accounting. It independently tracks malformed messages, queue drops, available kernel RX overflow signals and application queue residence. A loopback-only C++ publisher paces with absolute `CLOCK_MONOTONIC` deadlines and measures the *actual* `sendto` rate and jitter.

**2. Routing failure and recovery.** A local Linux namespace lab injects primary-link failure, backup failover and a forwarding blackhole. It correlates Linux RTNETLINK observations with TCP connection outcomes. The separate FRRouting/Containerlab three-router BGP topology is provisioned as a reproducible target; **real live BGP failover has not been verified here**.

## What the experiments actually showed

- **Real backpressure control:** three paired paced Linux UDP trials (600 datagrams each) had 0 control queue drops versus 418 deliberately induced application queue drops per pressured run; full receiver accounting and matched achieved publisher rates passed in 3/3 pairs. This is a controlled failure reproduction, **not** wire loss or a throughput claim. [Method and raw-summary evidence](docs/UDP_BACKPRESSURE_CASE_STUDY.md).
- The SPSC ring transferred **200,000 ordered items** between threads in its correctness test, including explicit full/empty behavior. Targeted ThreadSanitizer and ASan/UBSan checks were performed. This is not a performance throughput claim.
- An earlier 5-pair, pinned, sender-paced-by-Python loopback experiment showed a median **queue-residence p99 of 366.8 us (sleep) vs 73.5 us (spin)** while process active CPU per frame increased **12.2 to 114.1 us**. It is one workload on one container, not a generally repeatable improvement.
- More recent C++ publisher tests found **no stable tail-latency winner** across run conditions. The publisher can miss a requested rate even when all frames arrive. The benchmark now rejects comparisons unless both runs have full frame integrity, each publisher is within **±10% of the target**, and the two achieved rates are within **5% of each other**. The latest 6/6 pairs passed these controls; an earlier high-load experiment had only 7/9 comparable pairs. See [experimental evidence and limitations](docs/NATIVE_PACING_VALIDATION.md). A [separate read-only raw-trial eligibility audit](docs/NATIVE_PAIR_ELIGIBILITY.md) rechecks archived pairs against the published ±10%/5% controls; it also records conditions where Spin is worse.
- The namespace lab has three documented successful local runs with TCP/RTNETLINK evidence, but those are **static-route failures**, not timed dynamic BGP reconvergence.

The main lesson is that lowering application queue residence with busy-spin consumes CPU and can lose its advantage under scheduling contention. We deliberately keep failed trials rather than present a selectively favorable p99 number.

## Reproduce (local Linux, no paid infrastructure)

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
ctest --test-dir build --output-on-failure
python3 -m unittest discover -s tests -p test_udp_pipeline.py -v
python3 -m unittest discover -s tests -p test_native_sender.py -v
python3 scripts/benchmark_native_sender.py \
  --receiver build/udp_pipeline --sender build/udp_publisher \
  --output lab-output/native-pacing --pin --rates 1000 3000 5000 \
  --repeats 3 --count 600
```

`--pin` needs at least three permitted logical CPUs. The script writes each sender/receiver result and its failure reasons, plus a complete paired summary, to `lab-output/` (intentionally not checked in). CPU load, Linux scheduling and container quotas affect results. The full repository check is `bash scripts/verify_local.sh`; some tests need Linux namespace privileges or optional Docker/Containerlab and cannot be presumed green just because the UDP tests pass.

For routing reproduction, see [isolated kernel lab](docs/CORE_EVIDENCE_20261108.md), [kernel observability](docs/KERNEL_OBSERVABILITY.md), and [three-router BGP instructions](labs/three-router/README.md).

## Important boundaries

- **Not measured:** NIC hardware timestamps, exchange market data, wire-to-wire latency, maximum sustainable line rate, cross-host time synchronization, `perf` flamegraphs, or real BGP convergence times.
- C++ publisher `sendto()` success is not proof of receiver delivery; a gap in sequence IDs is not automatically network packet loss.
- Data is synthetic and local. No production/customer or employer data is included. No GitHub Actions, hosted CI, paid cloud, or external API required.

[Measurement details](docs/LOW_LATENCY_RECEIVE_BENCHMARK.md) · [SPSC architecture](docs/SPSC_PIPELINE.md) · [Test status](docs/TEST_STATUS.md) · [License](LICENSE)
