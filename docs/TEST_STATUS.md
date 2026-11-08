# Evidence and test status (updated 2026-11-07)

## Implemented in source

- Read-only router-state capture and state diff (Linux, FRRouting).
- Conservative Junos/IOS/EOS structured text parsers for recognized BGP peers, IPv4 routes and interfaces. Versions and unknown output lines are not treated as confirmed good state.
- Three synthetic scenario fixtures, Python unit tests and C++ nonblocking TCP probe.
- Three-router BGP Containerlab topology, a read-only all-peer BGP/HTTP smoke check and opt-in local fault controller.
- Bounded continuous HTTP sampling during test faults, with sampled failure spans and possible outage-window bounds.

## Not yet independently validated

- Fresh public checkout: test suite has not been executed by the assistant here; earlier 26-test pass result refers to a previous source revision, not this revision.
- Real Docker/Containerlab: no live three-router execution or end-to-end scenario run in this environment, which lacks Docker/Containerlab.
- Hardware: Junos, IOS and EOS text adapters have synthetic parser tests, NOT actual vendor-hardware validation.
- The stream does not measure exact BGP convergence, true packet loss, or microsecond forwarding latency.

## Verification on an authorized local Linux machine

~~~bash
python3 -m unittest discover -s tests -v
containerlab validate -t labs/three-router/topology.clab.yml
cd labs/three-router
docker build -t routeincident-endpoint:local -f Dockerfile.endpoint .
sudo containerlab deploy -t topology.clab.yml
cd ../..
PYTHONPATH=src python3 scripts/lab_smoke.py --output lab-output/smoke.json
PYTHONPATH=src python3 scripts/lab_run.py --scenario neighbor_outage --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario backup_failover --allow-faults
PYTHONPATH=src python3 scripts/lab_run.py --scenario route_ok_blackhole --allow-faults
~~~

Review each live report and the recorded continuous HTTP samples before making recovery claims.
There are no scheduled jobs, paid services or GitHub Actions workflows.

## 2026-11-07 evidence note

- The newly added HTTP status parser and artifact audit have **10 new unit/real-loopback-socket tests passing in an isolated local checkout**. This is not a claim that the entire repository test suite ran successfully; the full revised suite still needs fresh checkout validation.
- The modified HTTP probe was run against a temporary real loopback HTTP server for five requests: 5/5 received HTTP 200. This validates only localhost HTTP parsing, not BGP/Containerlab execution.
- Full live FRR/Containerlab fault injection is **still unverified here** because Docker and Containerlab are unavailable. No real convergence or failover timing result is claimed.
- Completed local lab runs now produce `incident.md` and `audit.json` automatically, with independent offline recheck via `python3 scripts/audit_run.py --run-dir lab-output/<scenario>`.
- No GitHub Actions workflows have been added; no paid cloud compute is required for the test path.

## 2026-11-08 Linux epoll systems exercise

- New `cpp/epoll_connect_sampler.cpp`: bounded Linux nonblocking socket + epoll client, individual connect observations, errno, monotonic timestamps, per-attempt timeouts and p50/p95/p99 on success samples only.
- Compiled with `g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic` in an isolated local Linux workspace.
- Ran one actual localhost listener stop/restart against the C++ sampler: 240 attempts total; healthy 61 success / 0 fail, listener down 0 success / 60 fail, recovered 119 success / 0 fail. This is not BGP/network-fault evidence.
- Ran **7/7 newly added Python real-socket and input-validation tests** successfully, including a non-accepting listener backlog that exercised actual parallel in-flight sockets bounded by the configured limit.
- Docker and Containerlab are absent here. Neither the full repository test suite nor live FRR scenarios were executed in this iteration. The preceding stage's 10 tests and historical 26-count should not be counted as a fresh full-suite result.
- The demo outputs remain local; no GitHub Actions jobs or paid cloud services added.

## 2026-11-08 read-only kernel cross-plane milestone

- Added C++ Linux NETLINK_ROUTE socket monitor: real RTM_GETROUTE IPv4 route
  snapshot, RTMGRP_IPV4_ROUTE/RTMGRP_LINK notifications, read-only JSONL,
  explicit 0..120000 ms watch cap and monotonic receive timestamps.
- Added same-host offline timeline correlator with input checks, SHA-256 input
  digests, event-window failure proportions and explicit no-causality status.
- Compiled with g++ and actually observed 7 Linux IPv4 kernel routes.
- Ran integrated real localhost Socket lifecycle: 240 TCP attempts (180
  success / 60 fail), seven kernel route snapshots and 0 route/link notifications.
  This is NOT a live routing incident or measured routing convergence.
- Verified **10/10 new targeted tests** and **7/7 existing epoll tests** in the
  same local runtime. GitHub blob SHAs match the locally tested source bytes.
- Still outstanding: full clean-clone repository suite and actual
  Docker/Containerlab three-router fault tests. No cloud CI or paid resources.

## 2026-11-08 multicast sequencing milestone

- Implemented Linux C++17 epoll + recvmmsg UDP receiver with strict synthetic
  16-byte binary frame, bounded 32 streams, recent 4096 sequence IDs each,
  optional SO_TIMESTAMPNS/SO_RXQ_OVFL auxiliary metadata, and JSONL observations.
- Actual local Linux multicast loopback delivered 10/10 controlled synthetic
  packets: 2 forward jumps, 2 out-of-order and 1 duplicate as intended.
  The skips were deliberately generated by the publisher, NOT packet loss.
- Six targeted actual UDP socket tests passed (real multicast, unicast,
  independent streams, malformed frames, bounds and missing input).
- The local three-module subset now totals 23/23 tests passed on the same
  Linux host (7 epoll, 10 RTNETLINK/correlation, 6 multicast). No claim
  that all other existing repository tests passed from a clean clone.
- Full local verification script added; it was not run over a fresh complete
  GitHub checkout in this environment. No hosted GitHub Actions or cloud use.

## 2026-11-08 repeated Socket load observations

- Added local-only six-trial real Socket stress harness with per-attempt
  raw JSONL, successful-only p99, errno distribution, sampler CPU time,
  wall time and Linux TcpExt listen queue counter deltas when available.
- Actually ran six new 2000-connect trials (three burst and three paced);
  most completed with no failures. Other preliminary burst runs
  had 36 or 64 ETIMEDOUT connect attempts, showing variability.
- No fair performance superiority claim is supported because the two
  workload types have different offered loads and scheduling.
- Local stress harness is not part of the regular 23-unit-test count.
  No full clean-clone regression claim or live FRR test claim is made.
