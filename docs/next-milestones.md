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
