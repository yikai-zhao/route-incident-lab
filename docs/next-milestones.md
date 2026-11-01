# Next tests to run

These are still open. The network topology and controller scripts exist, but the Docker/FRR lab has not been executed in this environment.

1. **Deploy the local Containerlab topology.** Verify FRR BGP neighbor sessions and r1's route to `10.10.2.0/24`. Confirm h1 receives an actual HTTP response from h2 before injecting faults.
2. **Run and repeat all three scenarios.** `neighbor_outage` should withdraw routes and stop HTTP; `backup_failover` should change next hop to r3 and recover the direct path; `route_ok_blackhole` should leave RIB and main FIB intact while an RPDB rule blocks delivery. Collect at least three runs per scenario before making reliability claims.
3. **Inspect failures, not only passed assertions.** Inspect live `vtysh`, `ip -j route`, `ip -j rule`, and TCP/HTTP probe output. Determine whether any false positives arise from sequential snapshot timing, FRR JSON differences, or the probe server itself.
4. **Only then measure recovery.** Add a bounded continuous time-series sampler, record the fault-command completion timestamp and the collector observation window, and report intervals or uncertainty rather than exact network event timing. Measure sample rates and overhead.
5. **Consider other vendors later.** Introduce explicit, versioned parsers only when test fixtures and permitted real outputs are available. Raw CLI capture is not the same as structured diagnosis.

Do not commit Ciena configurations, logs, credentials, or proprietary source. No cloud spending, scheduled GitHub Actions or hosted test jobs are required.
