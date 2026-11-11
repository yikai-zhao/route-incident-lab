# Event-driven consumer wakeup: real Linux UDP / SPSC experiment

**Scope:** C++17, Linux UDP loopback, one producer receiving datagrams and one consumer processing a bounded lock-free SPSC ring. **No exchange data, NIC timing, kernel bypass, or trading throughput claim.** All payloads are locally generated and synthetic. Date of captured experiments: 2026-11-10 (UTC; container may be on November 11 UTC).

## Problem and intervention

Prior to this experiment, the consumer supported two waits on an empty ring: periodic `sleep_for(100us)` and busy spinning. The former saves more CPU but polls periodically; the latter can monopolize a core without a stable tail-latency advantage in the shared container.

**New opt-in `--idle=eventfd` mode:** `eventfd(0, EFD_CLOEXEC)` creates a blocking kernel counter. Each successfully queued packet is followed by one 8-byte write (each unsuccessful enqueue is counted separately as an application queue drop). When the ring is empty, the consumer blocks in `read(eventfd)`. `read()` combines any accumulated notifications; a notification made between observing the empty queue and blocking is retained by the kernel counter. Completion stores the release flag and signals once more, ensuring a worker blocked on an empty queue can exit when the receive deadline fires. All existing `sleep`/`spin` modes remain unchanged.

**Cost and design limit:** Producer now makes an additional `write` syscall per accepted datagram, and the consumer makes a `read` when it waits. This trades producer overhead/context switches against worker idle polling. It is a safe reference implementation; notification coalescing/arming would require a separate concurrency proof, not a casual optimization. The code does **not** claim to eliminate all allocations, or to be kernel-bypass/market-data-ready.

## Reproduce on Linux

```bash
g++ -O2 -std=c++17 -pthread -Wall -Wextra -Wpedantic cpp/udp_pipeline.cpp -o build/udp_pipeline
g++ -O2 -std=c++17 -Wall -Wextra -Wpedantic cpp/udp_publisher.cpp -o build/udp_publisher
python3 -m unittest discover -s tests -p test_eventfd_wakeup.py -v
python3 -m unittest discover -s tests -p test_wakeup_audit.py -v
python3 scripts/benchmark_consumer_wakeup.py \
  --receiver build/udp_pipeline --sender build/udp_publisher \
  --rates 1000 5000 --repeats 4 --count 600 --seed 10271110 \
  --output lab-output/eventfd-campaign-a.json
python3 scripts/benchmark_consumer_wakeup.py \
  --receiver build/udp_pipeline --sender build/udp_publisher \
  --rates 1000 5000 --repeats 5 --count 600 --seed 3311042 \
  --output lab-output/eventfd-campaign-b.json
```

The runner randomizes the order of three modes within each repeat/rate. It **fails** (nonzero exit) unless all modes have full sender and receiver integrity, no malformed/duplicate/reordered/gap events or application drops, no queue sample vector reallocation, actual sender rate within ±10% of target, and *three-mode achieved rates* within 5% of target. The command also records CPU quota and CPU affinity count. A rejected run is retained in the JSON file; it must not be silently discarded.

`eventfd_notification_writes = accepted-to-ring datagrams + 1 shutdown signal` is checked in loss-free trials. Actual `eventfd_wakeup_reads` can be lower because one read drains accumulated notifications. The output exposes both. This is a kernel notification counter, not an acknowledgment of each packet's processing.

## Observed results on one KVM/AMD EPYC shared Linux container

Each trial received and processed **600/600 packets**, no receiver/application queue drops. Two campaigns of 24 and 30 real socket trials yielded **54/54 integrity-and-rate-valid trials** and **18/18 rate-matched triplets**. CPU figures include the period from the first consumer-dequeued packet to its exit, including waiting. Receiver process CPU is measured from first received datagram onward. All figures below are medians across modes at the specified load; `p99` measures **application queue residence**, not wire latency.

| Campaign | Target | Mode | Receiver process active CPU | Consumer active CPU | Queue residence p99 |
|---|---:|---|---:|---:|---:|
| A (4 repeats) | 1,000 pps | eventfd | 15.26 ms | 4.01 ms | 125.5 μs |
| A | 1,000 pps | sleep100us | 45.44 ms | 41.04 ms | 177.4 μs |
| A | 1,000 pps | spin | 603.71 ms | 598.19 ms | 567.8 μs |
| B (5 repeats) | 1,000 pps | eventfd | 14.88 ms | 3.87 ms | 129.4 μs |
| B | 1,000 pps | sleep100us | 45.82 ms | 40.79 ms | 178.7 μs |
| B | 1,000 pps | spin | 605.52 ms | 598.79 ms | 303.3 μs |
| A | 5,000 pps | eventfd | 8.67 ms | 1.99 ms | 639.7 μs |
| A | 5,000 pps | sleep100us | 11.43 ms | 9.20 ms | 171.1 μs |
| A | 5,000 pps | spin | 125.03 ms | 119.53 ms | 732.3 μs |
| B | 5,000 pps | eventfd | 8.63 ms | 2.16 ms | 87.0 μs |
| B | 5,000 pps | sleep100us | 11.42 ms | 9.25 ms | 174.9 μs |
| B | 5,000 pps | spin | 123.35 ms | 119.58 ms | 27.9 μs |

**Stable finding under these conditions:** eventfd uses less consumer and receiver-process active CPU than fixed sleep/Spin at both tested offered rates. At 1,000 pps, process active CPU was about one third of Sleep; at 5,000 pps the difference was smaller (roughly 75% of Sleep). Neither result is a hardware-normalized universal improvement. At 5,000 pps, the `eventfd` p99 range was **68.6–4,688.4 μs** in A and **65.7–937.1 μs** in B. There is **no demonstrated stable tail latency win**. Busy spin can also suffer multi-millisecond p99 despite high CPU.

The container exposed an AMD EPYC virtual CPU and a 4-CPU cgroup quota. Hardware PMU profiling was unavailable; no reliable cycles/instructions/cache-miss attribution was collected. No CPU isolation, IRQ affinity, RPS/XPS, kernel-bypass, PTP synchronization or production network validation was performed. The sender's success only proves Linux accepted a `sendto` operation; receiver completeness is separately checked.

## Correctness and adversarial tests

- Real paced delivery, `eventfd` notification accounting, scalar receive and JSONL sink
- Multiple short-lived instances to exercise no-lost-wakeup and graceful termination
- No-sender deadline, partial-sender deadline, malformed frame and application ring backpressure
- Original Sleep/Spin modes, prior target-boundary regression and sample-buffer regression
- Negative evidence tests: missing shutdown notification, duplicate mode triplet, pace mismatch, queue drop and syscall accounting corruption
- ASan/UBSan instrumented local UDP run passed without reports on this container

These checks do not constitute a formal proof of concurrent correctness; the design deliberately relies on `eventfd`'s counting semantics, keeping the wakeup protocol simple. If introducing notification coalescing or a timeout-free shutdown handshake in a production system, rerun targeted race/stress tests and review the memory-order protocol.

## Evidence

- `evidence/consumer-wakeup/20261110-summary.json` — compact publicly checked-in, source-SHA-linked summary
- Full raw experiment files (24 and 30 trials), full regression log, sanitizer notes and SHA-256 manifest: companion downloadable artifact; re-generate using the commands above
- Original upstream baseline `ed42dc8bac79d49ff333260d03369479076bbf35`

**No GitHub Actions**, employer/customer data, paid infrastructure or autonomous operations.
