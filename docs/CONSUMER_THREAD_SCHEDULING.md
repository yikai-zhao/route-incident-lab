# Thread-level idle policy study: real local UDP

**Scope.** 2026-11-10 local Linux loopback, existing `udp_pipeline` C++17 SPSC receiver and native `udp_publisher`. This is *not* a trading venue latency benchmark, hardware `perf` study, or performance improvement claim.

## Question

When the consumer waits for packets, how much thread CPU time and scheduling activity is attributable to `--idle=sleep` versus `--idle=spin`? Previous `RUSAGE_SELF` context-switch counts combined both threads and startup overhead, which cannot isolate consumer behavior. `CLOCK_THREAD_CPUTIME_ID` and `getrusage(RUSAGE_THREAD)` give per-consumer counters; both full-lifetime and **first-dequeued-frame onward** metrics are now reported. The active definition includes waiting between the first and final consumed frames; it is not processing-only CPU.

## Experimental setup and acceptance

- Two target sender rates, 1,000 and 5,000 packets/s; 600 packets per trial; each mode has three trials, randomized Sleep/Spin order within each pair.
- Native sender uses absolute monotonic deadlines; receive uses `recvmmsg`, `epoll`, sink `none`, consumer delay `0`, SPSC capacity `1024`. Both processes run on the same shared Linux host; CPU pinning and a dedicated isolated core were **not** used.
- Raw records capture achieved sendto rate, receiver counts, sequence events, per-thread active and lifetime CPU time, voluntary/involuntary switches, queue p99 and exit status.
- A pair is accepted only if both runs are complete with no malformed/queue drop/sequence anomaly, each achieved send rate is within ±10% of target and rates differ by no more than 5% of target. All six pairs passed; 12/12 individual trials qualified.
- `SO_RXQ_OVFL` was enabled but a lack of overflow signal is **not** proof of absence of all kernel or wire loss.

## Observed medians (3 runs per mode, all passed)

| Target offered pps | Consumer policy | CPU after first dequeued frame | Voluntary switches after first dequeued frame | Queue residence p99 |
|---|---|---:|---:|---:|
| 1,000 | Sleep 100 μs | 47.16 ms | 3,463 | 277.59 μs |
| 1,000 | Spin | 598.92 ms | 0 | 294.64 μs |
| 5,000 | Sleep 100 μs | 11.02 ms | 668 | 345.90 μs |
| 5,000 | Spin | 119.70 ms | 0 | 379.82 μs |

The Spin consumer incurred roughly 11–13x active thread CPU time in this run. **No stable p99 improvement was observed.** Tail samples vary greatly between trials; 3 repeats per condition cannot establish generalized p99 performance or throughput superiority. Spin has no voluntary switches in these trials, consistent with busy waiting; Sleep voluntarily yields repeatedly. Involuntary switches were small but nonzero for Spin. The effects of scheduling interference, startup timing, cache layout and CPU frequency remain uncontrolled.

## Reproduce

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 scripts/reproduce_consumer_scheduling.py
python3 scripts/audit_consumer_scheduling.py
python3 -m unittest discover -s tests -p test_consumer_scheduling.py -v
```

The re-run writes to `lab-output/consumer-scheduling` and **must not** be expected to reproduce the numeric medians above on a different host. Raw *observed* data are committed to `evidence/consumer-scheduling/local-12-trials.json`; the audit script validates those fixed inputs. The experiment binary was compiled from `cpp/udp_pipeline.cpp` and `cpp/spsc_ring.h` with `g++ -O2 -std=c++17 -Wall -Wextra -Wpedantic -pthread`; the producer was compiled similarly. The SPSC header was checked against the published repository blob (`3550edcadfcca759eb6009ce744fba94a1cdd1a3`).

## Engineering decision

Do not add a hybrid spin/yield/sleep heuristic yet. Per-thread measurements confirm meaningful CPU overhead from busy waiting, not a stable tail-latency benefit. A justified optimization requires a controlled dedicated Linux host, profiler attribution (including producer wakeups and consumer scheduling), and a predeclared before/after comparison with validity gates and correctness checks.
