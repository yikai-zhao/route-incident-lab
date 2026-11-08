# Outstanding validation milestones

1. Fresh-clone Python unit tests: record actual pass count; do not repeat the earlier 26-passed claim for the revised suite without running it.
2. Containerlab validate + deploy + read-only smoke: show all six expected eBGP peer views Established and actual h1-to-h2 HTTP responses.
3. Run the three local fault scenarios explicitly with --allow-faults; save route/BGP snapshots, action records, and continuous HTTP traces; repeat each scenario at least three times.
4. Review timing precision: probe sample windows are bounds, not true route convergence timestamps or packet-loss measurements. Record sampler overhead and verify failure detection without false-positive assertions.
5. Validate version-specific Junos/IOS/EOS CLI text parsers against authorized real device output before advertising hardware-tested multi-vendor support.

No Ciena/private employer data, paid cloud resources, scheduled GitHub Actions, or external traffic targets.

6. Review the new run audit's PASS/FAIL, `incident.md`, and hashed input artifacts; archive actual local run outputs for all three scenarios. Do not present mocked audit tests or loopback-only HTTP observations as live FRR measurements.

7. Bridge the Linux epoll connect-observation timeline with real FRR route events; do not confuse localhost server-stop observations with control-plane convergence.
8. Perform controlled poll/epoll CPU and latency distributions under comparable loads with profiler data before claiming any performance advantage.

9. Validate real route change notifications in an isolated, explicitly
   authorized local netns/Containerlab run; zero notifications in a localhost
   server test is expected and must not be claimed as routing verification.
10. Add local UDP multicast sequenced-feed diagnostics: distinguish
    publisher gaps, packet loss, reordering and duplication; verify with
    real-socket fixtures and explicit timing/measurement boundaries.
11. Benchmark poll/epoll with matched workloads and resource counters before
    claiming lower tail latency; archive machine/CPU/compiler metadata.

12. UDP multicast receiver burst stress and socket overflow evidence under
    repeatable load. Separate publisher gaps from actual queue drops.
13. Compare recvmmsg with one-datagram recvmsg under the same workload.
    Record CPU profiles, repetitions, kernel configuration and p99 variability.

14. Randomized matched-workload poll versus epoll trials with warmup,
    CPU pinning, performance counters where authorized, explicit failure
    distributions, and archived machine/compiler metadata.

15. Completed evidence: three genuine isolated Linux static FIB route/dual-veth
    link failures and recovery runs. FRR's dynamic routing protocol convergence
    remains a separate outstanding experiment requiring Docker/Containerlab.
16. Completed exploratory poll-vs-epoll comparison with matched backend code
    and paired offered workload. **Not** a controlled low-latency advantage
    claim. Next: CPU affinity, perf counters and larger randomized trial sets
    only when the host permits meaningful measurement.

17. Completed native two-thread epoll/recvmmsg SPSC UDP experiment with
    real localhost multicast and explicit user-space queue drop accounting.
    ThreadSanitizer and ASan/UBSan passed for targeted tests.
18. Still outstanding: full original suite from clean checkout; controlled
    matched receiver throughput and CPU profiling without JSONL overhead;
    real Linux kernel RX queue overflow (if safely reproducible); actual
    FRRouting BGP/Containerlab topology with dynamic failover.
19. Separate optional project recommended for AI-infrastructure roles:
    evidence-grounded, read-only cross-layer incident debugging assistant
    with local LLM interface, deterministic baseline, evaluation and
    abstention controls. Do not couple an LLM to latency-critical feed path.

20. Completed single-codepath UDP latency/CPU trade-off: five paired pinned loopback rounds, real synthetic datagrams and fail-closed accounting, normal/sanitized targeted tests. Queue p99 improvement has an explicit CPU cost; not a saturation or wire measurement. See docs/LOW_LATENCY_RECEIVE_BENCHMARK.md.
21. Still outstanding for stronger Quant Systems evidence: clean-clone full regression, repeat on dedicated bare-metal Linux with perf and NIC timestamps, sweep offered load through saturation and record kernel overflow, and live FRR BGP recovery. Do not claim any as completed.
