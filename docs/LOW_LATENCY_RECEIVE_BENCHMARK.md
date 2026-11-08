# UDP pipeline: controlled latency versus CPU trade-off

**Scope:** Real Linux IPv4 UDP loopback sockets and the actual C++17 `udp_pipeline` receive/consumer codepath. This is **not** a NIC, exchange-feed, kernel-bypass, throughput-capacity, or microsecond wire-latency benchmark.

## Question and implementation

Can a spin-waiting SPSC consumer lower queue-residence tail latency versus the original 100 us sleep-on-empty loop? What is the CPU cost? Separately, what do `recvmmsg` batching and per-packet JSONL output cost under the same publisher-paced load?

The production-like code uses nonblocking sockets, `epoll`, a 16-slot `recvmmsg` batch (or a scalar `recvmsg` on the same socket/event loop), a bounded preallocated SPSC queue, binary packet validation, per-stream sequence checks, and actual monotonic receive/consumer timestamps. Four *same-codepath* modes are compared:

| Configuration | Receiver | Consumer idle | Per-packet output |
|---|---|---|---|
| baseline | batched `recvmmsg` | sleep 100us | none |
| tail-latency trade-off | batched `recvmmsg` | busy spin | none |
| syscall comparison | scalar `recvmsg` | sleep 100us | none |
| observability overhead | batched `recvmmsg` | sleep 100us | JSONL |

The metrics-only sink removes the large `iostream` serialization cost from both receiver/consumer comparisons while preserving parsing, classification, queue residence recording and summary output. CLI defaults still generate JSONL and use batched receive and the original sleep strategy.

## Reproduction (free local Linux)

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 -m unittest discover -s tests -p test_udp_pipeline.py -v
python3 -m unittest discover -s tests -p test_udp_performance_modes.py -v
python3 -m unittest discover -s tests -p test_udp_benchmark.py -v
ctest --test-dir build --output-on-failure
python3 scripts/benchmark_udp_pipeline.py --binary build/udp_pipeline \
  --output lab-output/udp-benchmark --repetitions 5 --warmups 1 \
  --count 3000 --burst 8 --pause-us 750 --pin
```

`--pin` requires three permitted CPUs: receiver process (producer and consumer) may run on the first two; Python publisher runs on the third. The harness randomizes the order of four modes within each paired round with a fixed seed, has one excluded warmup round, rejects incomplete trials and packet/sequence anomalies, checks explicit application queue drop and kernel overflow reports, and saves full trial summaries, JSONL where enabled, hashes, publisher pacing, CPU affinity and cgroup CPU quota.

## Observed on one Linux container on 2026-11-08

Host kernel 6.18.57, `g++ 14.2`, Linux allowed CPUs 0–4, cgroup quota 4 CPUs, pinned receiver CPUs 0/1 and sender CPU 2. Five matched measured rounds, 3,000 frames per mode per round, eight-frame bursts every 750us (Python sleep is not precise); **all 20 measured trials passed**, with every datagram consumed and no reported application drop.

| Configuration | Median queue p99 (us) | Median active process CPU (us/frame) | Receiver successful syscalls/frame |
|---|---:|---:|---:|
| batch / none / **sleep** | **366.77** | **12.24** | **0.137** |
| batch / none / **spin** | **73.51** | **114.08** | **0.137** |
| scalar / none / sleep | 439.89 | 12.99 | 1.000 |
| batch / JSONL / sleep | 332.82 | 13.67 | 0.137 |

The *ratio of medians* for spin relative to batch+sleep is ~**80% lower observed queue p99** at ~**9.3x active process CPU per frame**. Spin had lower queue p99 than matched batch+sleep in **all five** pairs. These are one host's workload-dependent observations, not a guarantee or a cross-host throughput claim.

- `process_cpu_active_ns` covers **first received datagram to consumer exit**, excluding launch-to-first-datagram CPU time. `process_cpu_startup_ns` and total CPU are also retained. Under spin, the startup wait alone consumed ~125 ms CPU per trial, compared with ~10 ms under sleep. Ignoring startup would understate total deployment cost; including it in per-frame active CPU would overstate per-frame processing costs. Both are reported.
- `queue_p99_us` is *receive timestamp to consumer dequeue time* for **processed** frames. It is not the tail of wire packet delivery, scheduler dispatch independently, NIC hardware time, or end-to-end application processing.
- `recvmmsg` reduced successful receive syscalls from 1.0 to ~0.137 per datagram but did **not** establish a stable CPU or tail-latency win over `recvmsg` at this offered load.
- JSONL incurs observable CPU work, but short-run queue p99 is noisy: its median was lower than the no-logging counterpart in this run. Never present one run's incidental tail-latency ordering as a causal logging advantage.
- A preliminary higher-rate burst test failed completion with **88 SO_RXQ_OVFL kernel drop counts**. The harness rejected it rather than misclassifying skipped feed IDs as wire loss. This exploratory failure is not part of the matched PASS results, and the sender's 88 missing IDs are not a physically measured wire-loss rate.

## Remaining evidence for exceptional low-latency claims

1. Clean-clone full repository regression is still outstanding. Current testing validates the changed native component, integration tests and benchmark harness; optional FRR/Containerlab tests were **not** run here.
2. Hardware NIC + PTP/HW timestamps, kernel bypass, pinned isolated CPUs and kernel/perf event profiling are **not available/proven** in this container. Do not describe this as exchange latency or HFT feed production performance.
3. Repeat saturation-curve measurements across offered-load levels with kernel RX socket buffer and drops captured, compare best achievable throughput on a dedicated host, and diagnose syscall/scheduler/cache contention with `perf` when authorized.
4. Verify three-router FRRouting eBGP failover live through Containerlab before claiming real BGP convergence times. The existing actual double-link kernel lab uses manually programmed static routes.

**Result worth citing:** reproduced an explicit p99 queue-residence vs CPU trade-off in a real Linux/C++ two-thread UDP datapath using paired same-codepath experiments with raw observations and fail-closed loss accounting. Nothing here justifies claiming best-in-class HFT performance.
