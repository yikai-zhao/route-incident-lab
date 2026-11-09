# C++ loopback publisher: validity controls before latency comparisons

**2026-11-08 · local Linux · C++17 · 127.0.0.1 only**

## Why

The first latency benchmark used `time.sleep` in the Python publisher. That made the offered load sensitive to scheduler timing. A sender that successfully calls `sendto()` has not proven that the receiver saw those packets; neither does sending 600 packets automatically mean the intended packet rate was achieved.

A small native publisher now uses absolute monotonic deadlines (`clock_nanosleep` with `TIMER_ABSTIME`) and records successful sends, send errors, actual first-to-last send rate, per-send lateness p50/p99, spacing p50/p99, and sender CPU time. The unchanged receiver separately reports real processed counts, dropped records, sequence anomalies, kernel RX overflow if available, queue-residence p99 and process CPU time.

## Validity gates

A latency **pair** is comparable only when:

1. Each run's receiver is complete; every sent frame was observed and processed, with no unexpected duplicate, sequence gap, malformed frame, application queue loss or reported kernel receive overflow.
2. Each sender's measured offered rate is within ±10% of the requested rate, **independently** of packet integrity.
3. Sleep and Spin runs' measured sender rates differ by no more than 5%. A trial has to pass every gate; invalid trials remain in the denominator with reasons.

A rate miss is **not** reported as a packet drop. The benchmark saves every single run, marks failed or timed-out subprocesses explicitly, and returns a nonzero status when any paired comparison is invalid.

## Real observations

- The previous pinned 1k/5k/20k-pps matrix had **18/18 complete receiver trials**, but **only 7/9 comparable pairs** under the stricter gate: one 20k-pps pair missed the sender rate target and another pair had a >5% measured rate difference.
- The new gate was retested with an independent pinned 1k/3k/5k-pps matrix: **12/12 complete trials**, all 12 actual sender rates within ±10%, **6/6 comparable pairs** (400 frames per run). The source and per-trial records are included in the local evidence archive.
- The tail latency was *not* uniformly improved by Spin. At 5k requested pps, the measured medians for this small two-pair set were ~518us (Sleep) and ~3,737us (Spin). This illustrates host scheduling noise and makes a universal optimization claim inappropriate. It is not a receiver saturation or capacity measurement.

## Reproduce

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 -m unittest discover -s tests -p test_native_sender.py -v
python3 scripts/benchmark_native_sender.py --receiver build/udp_pipeline \
  --sender build/udp_publisher --output lab-output/native-pacing \
  --pin --rates 1000 3000 5000 --repeats 3 --count 600
```

The script randomizes the Sleep/Spin order within each pair and writes SHA-256s of both binaries, CPU affinity and cgroup quota, and full per-run observations. `--pin` requires at least three allowed logical CPUs. No NIC hardware clock, isolated bare-metal host or `perf` is available in the current environment; the experiment **cannot** establish an HFT latency or best-in-class performance claim.

**Next engineering work:** verify a real source of scheduler latency with kernel-level scheduling/profiler evidence on suitable Linux hardware; only then make and retest a minimal queue or waiting-policy change. Focus on repeatable performance, not on adding more queue variants.
