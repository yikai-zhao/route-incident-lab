# Paired real UDP receiver-mode experiment (2026-11-10)

**Question:** Does switching between `recvmsg`/`recvmmsg` and sleeping/busy-spinning the SPSC consumer cause reproducible changes in CPU costs and software queue residence, under matched *actual* packet rates?

**Environment:** Local Linux IPv4 loopback, C++17 `udp_pipeline` + `udp_publisher` built with g++ `-O2 -std=c++17`, real 16-byte UDP datagrams, no per-message JSONL. Receive path is the unmodified current `main` source (including the audited final-batch accounting fix). `spsc_ring.h` was verified against remote blob SHA. **No NIC, PMU/perf, hardware clock or real exchange feed.**

Two campaigns ran independently, each with 3 randomized 4-mode groups at each of 1,000 and 5,000 targeted publisher pps (600 sent frames per mode per group). A group counts only if **all four runs** finish without missed sends, receives, sequence/queue/kernel-drop anomalies, have achieved rates within ±10% of target, and have max/min achieved rates within 5% of target. **12/12 groups accepted across both campaigns; 48/48 mode-trials.** All results were recorded rather than selecting a favorable run.

| Target | Receive/wait policy | Campaign A p99 (μs) | Campaign B p99 (μs) | Campaign A active CPU μs/frame | Campaign B active CPU μs/frame |
|---|---|---:|---:|---:|---:|
| 1,000 | batch+sleep | 242.35 | 174.80 | 76.27 | 81.27 |
| 1,000 | scalar+sleep | 178.12 | 203.53 | 87.12 | 78.81 |
| 1,000 | batch+spin | 230.19 | 210.31 | 1009.53 | 1014.89 |
| 1,000 | scalar+spin | 992.57 | 251.50 | 1013.50 | 1014.08 |
| 5,000 | batch+sleep | 165.13 | 226.60 | 18.61 | 20.67 |
| 5,000 | scalar+sleep | 298.51 | 178.15 | 18.38 | 19.24 |
| 5,000 | batch+spin | 69.70 | 2.84 | 210.16 | 209.48 |
| 5,000 | scalar+spin | 82.47 | 185.11 | 205.78 | 203.91 |

These are *per-campaign medians of three trials*, **not** a pooled confidence interval. One particularly low 2.84 μs p99 in campaign B is not a deployable guarantee. At these low paced rates, `recvmmsg`'s successful syscalls per frame were typically ~1, so the experiment **did not meaningfully exercise batching**. Report it as a negative finding, not "batching is faster". Spin's large CPU cost appears consistently; its p99 ordering depends heavily on scheduling conditions. The major uncertainty is uncontrolled shared-host scheduling; no saturation throughput or causal optimization has been demonstrated.

## Reproduce

```bash
g++ -O2 -std=c++17 -pthread -Wall -Wextra -Wpedantic \
  cpp/udp_pipeline.cpp -o build/udp_pipeline
g++ -O2 -std=c++17 -Wall -Wextra -Wpedantic \
  cpp/udp_publisher.cpp -o build/udp_publisher
python3 scripts/profile_native_rx.py --receiver build/udp_pipeline \
  --sender build/udp_publisher --rates 1000 5000 --count 600 --repeats 3
python3 -m unittest discover -s tests -p test_native_rx_profile.py -v
```

Every trial writes its actual achieved rate, CPU and queue statistics, source binary hashes, raw Socket return counts and explicit rejection reasons. See `evidence/native-rx/two-campaign-summary.json` for two original campaign summaries and exact SHA-256 of the full reports. Raw experiment reports are in the accompanying downloadable audit archive; they do not include employer or confidential market data.

**Next serious milestone:** Dedicated-host `perf`/eBPF scheduler profiling, receive batch occupancy histograms and paced load sweeps approaching sustainable capacity. A negative controlled result is more valuable than an unsupported microsecond improvement claim.
