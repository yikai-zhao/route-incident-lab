# UDP hot-path sample-buffer allocation audit

**Question.** Does the per-packet queue-residence sample vector reallocate while the
SPSC consumer is processing network datagrams?

**Finding.** Yes, the previous implementation used `push_back()` without
reserving capacity. This can trigger vector reallocation and copying on the
consumer's hot path. We now call `reserve(target)` **before** starting the
consumer thread, and expose `queue_sample_capacity_grows` in the receiver's
existing JSON summary. This changes no queue, socket, classification, latency
sampling or consumer wake-up policy.

**Controlled local demonstration (2026-11-10).** An otherwise identical
instrumented baseline, differing only by removal of the `reserve(target)` line,
was compiled separately from the same code. Three alternating-order paired
trials sent 200 synthetic 16-byte datagrams each on real Linux IPv4 loopback,
with a 600 pps target and the same publisher binary. All six achieved rates
were 599.97–600.08 pps and each passed the existing publisher, receive,
sequence and queue-loss checks.

| Variant | Number of paired trials | Per-trial sample-buffer growth events |
|---|---:|---:|
| Before preallocation | 3 | 9, 9, 9 |
| After preallocation | 3 | 0, 0, 0 |

See [`evidence/hotpath-storage-summary.json`](../evidence/hotpath-storage-summary.json)
for the measured CPU/p99 results **including unfavorable results** and the
source-specific receiver binary SHA-256. Targeted tests compile the current
source, send real UDP packets under scalar/batch and sleep/spin variants, and
assert complete delivery and zero sample-vector growth.

Reproduce the optimized version:

```bash
python3 -m unittest discover -s tests -p test_hotpath_storage.py -v
```

To reproduce a *before* instrumented binary, copy `cpp/udp_pipeline.cpp` to
a temporary path, delete only `consumer.queue_delay_us.reserve(target);`, and
compile with the same `g++ -O2 -std=c++17 -pthread` options. Do not add this
obsolete variant to the regular build.

**Boundaries.** This removes one source of dynamically growing memory on the
consumer hot path. It does not eliminate allocations from stream tracking or
JSONL output, and may reserve up to `target*sizeof(double)` bytes (at most
1.6 MiB for this CLI's 200,000-frame limit). This small, contention-sensitive
experiment **does not establish a CPU time or latency speedup** and contains
no NIC, hardware timestamp, cache-miss, PMU or exchange data.
