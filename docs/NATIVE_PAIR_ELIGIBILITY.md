# Native UDP experiment: valid load comparisons

Why this exists: a receiver can process all 600 datagrams while its *publisher*
misses the requested rate. That trial is not evidence for a p99 comparison at
the nominal offered load. Nor should a 950 pps run be compared with 1,050 pps
simply because both are inside a nominal ±10% target band.

## Independent audit

The read-only `scripts/compare_native_pairs.py` consumes individual raw trial
JSON files from `scripts/benchmark_native_sender.py`, never calculated summary
medians. Each pair is comparable only if:

- both modes (sleep and spin) occur once, and each trial passed receiver integrity;
- source and receiver metrics exist and the sent count matches processed count;
- **both** measured `sendto` rates are within ±10% of the nominal target;
- the two measured rates differ by **no more than 5% of the target**.

Failed pairs remain in the report, with explicit rejection reasons. No p99
median is reported for a target rate that has zero comparable pairs. This is a
necessary, *not sufficient*, control for equivalent experimental conditions.

## Existing captured evidence re-audited

Read-only replay of 18 recorded Linux loopback trials (`native-pinned-v2`,
3 pairs at each nominal rate) yields **7/9 load-comparable pairs**:

| Nominal load | Eligible pairs | Sleep p99 median | Spin p99 median |
|---|---:|---:|---:|
| 1,000 pps | 3/3 | 308.934 μs | 339.522 μs |
| 5,000 pps | 3/3 | 460.679 μs | 110.911 μs |
| 20,000 pps | 1/3 | 222.572 μs | 26.946 μs |

Two 20,000-pps pairs were excluded. One reached only **17,317.1 and 17,034.7 pps**, below 90% attainment. The other attained **18,838.9 and 20,307.9 pps**, both within the individual 10% target band, but differed by over 5% of the target. Complete receive counts do not fix load mismatch.
At 1,000 pps, spin had a *worse* median p99; performance is workload-dependent.
These are archived prior observations; the audit did **not** generate a new
performance benchmark. The Python runner and kernel scheduling are still not a
controlled dedicated-host performance environment.

## Reproduce (no network or remote services)

```bash
python3 scripts/compare_native_pairs.py evidence/native-pacing/archived-pinned-v2-minimal.json
# Fresh runs can instead pass their directory of unmodified per-trial JSON:
python3 scripts/compare_native_pairs.py lab-output/native-pacing \
  --output lab-output/native-pacing/comparability.json
python3 -m unittest discover -s tests -p 'test_native_pair_comparability.py' -v
```

The checked-in replay fixture contains **18 condensed trial summaries** with
SHA-256 references to each original 18-trial JSON record; it is a derived
representation rather than a substitute for original raw data. The accompanying
local evidence package retains the original full records and the complete audit.

**Limits:** successful `sendto()` is not receiver delivery; good offered-rate
matching does not prove equal CPU contention, NIC throughput or production HFT
latency. The audit does not claim causal superiority of either waiting strategy.
