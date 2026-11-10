# Controlled UDP application-backpressure case study

This experiment uses the **existing** C++17 `udp_pipeline` receiver and
`udp_publisher`, not an alternative toy implementation. It is a deliberately
simple investigation of what happens when a bounded SPSC consumer cannot keep
up with a paced producer. No Linux routing or physical NIC is involved.

## Protocol

- Six real Linux loopback Socket trials, three randomized control/pressure pairs.
- Each publisher attempts 600 UDP datagrams at a **measured** ~1,000 pps.
- Control: 4,096-slot SPSC queue, consumer delay = 0.
- Pressure: 64-slot queue, forced 5,000 us sleep after each consumed packet.
- Receiver's batch UDP path is identical; only consumer service rate and queue
  capacity are manipulated. Raw stdout summaries are saved separately.
- Reject a trial when the receiver is incomplete, frames are malformed,
  publisher misses its offered rate by more than 10%, kernel overflow is
  *reported*, or accounting does not close. Reject a pair if achieved sender
  rates differ by more than 5% of the target.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 scripts/verify_udp_backpressure.py --receiver build/udp_pipeline \
    --sender build/udp_publisher --output lab-output/backpressure
python3 -m unittest discover -s tests -p 'test_backpressure_evidence.py' -v
```

## Observed on one shared Linux host (2026-11-09)

| Pair | Control drops | Pressured application drops | Control sendto pps | Pressured sendto pps |
|---|---:|---:|---:|---:|
| 0 | 0 | 418 | 1,000.43 | 1,000.14 |
| 1 | 0 | 418 | 1,000.12 | 1,000.05 |
| 2 | 0 | 418 | 1,000.04 | 1,000.09 |

All **3/3** pairs met the declared receiver-completeness, load-comparability,
queue-accounting and absence-of-*reported*-socket-overflow checks. For each
pressured run, all 600 datagrams were received by the process; 182 were
consumed and **418 were rejected by the bounded application queue**. This
isolates application backpressure in this local experiment, rather than
asserting physical wire loss or an exchange-feed loss rate.

The result is an intentionally induced failure, **not a performance advantage**.
Observed near-identical drop counts reflect this particular service rate,
queue capacity and scheduler. Do not extrapolate to the throughput limit of a
NIC, actual trading feeds, physical microsecond latency, or loss behaviour
in different CPU contention conditions.

`SO_RXQ_OVFL` reports counts via control messages on *later delivered packets*;
zero observed counters alone cannot prove a kernel never dropped any packet.
In these trials, complete application receive counts independently support
that the transmitted test frames were received at the socket API. The
instrumented queue's explicit accounting is `valid = processed + app_drops`.

The raw receiver/publisher summaries, `report.json`, source hashes and test
log are retained in the matching local evidence archive. The repository ships
reproducible code and this small result table, not a generated dashboard.
