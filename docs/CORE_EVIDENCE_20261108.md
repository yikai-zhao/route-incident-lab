# Core systems evidence: 2026-11-08

This is an execution log, not a general claim that the entire project has
passed every possible test. No Ciena or private employer captures were used.

## Actual isolated Linux routing experiment (3/3 runs PASS)

A NEW Linux user, mount, and network namespace contains two network namespaces
(h1 and h2) joined by two independent veth links. h2 serves a TCP listener on
10.10.2.2:8080. h1 routes to that destination over the primary link. The
controller records each command's MONOTONIC start/end and h1's actual route
table, while a native C++ RTNETLINK monitor in h1 observes link and route
notifications and a second native C++ TCP epoll client in h1 continuously
connects to h2.

The operations are local and intentional:
- verify baseline TCP connectivity via primary link;
- bring down h1 eth1: TCP connections fail;
- explicitly replace its static /32 route via h1 eth2: connections succeed;
- replace /32 with a blackhole route: connections fail;
- restore backup route and reconnect successfully;
- bring eth1 up and restore primary route.

Three genuine executions on Linux 6.18.57, x86_64:
- 3/3 runs passed the eight evidence checks in their saved reports.
- 1080 actual TCP connect attempts, 18 RTNETLINK route notifications,
  and 9 link notifications total.
- Run 1: primary-down 38/38 failures, backup 47/47 successes,
  blackhole 43/43 failures and backup recovery 34/34 successes.
- Runs 2 and 3: primary-down 37/37 failures each, backup 47/47
  successes each, blackhole 44/44 failures each and backup recovery
  34/34 successes each.

This is **real kernel FIB/link manipulation**, NOT a three-router FRR/BGP
experiment, not a measured automatic routing convergence time, and not a
wire-latency or packet-loss measurement. Route changes are deliberately
applied by a controller. Samples have user-space observation and command
windows; the test does not know exact packet transition timestamps.

The isolated script exits without modifying host routes. It requires
unprivileged user namespace support, iproute2 and kernel veth. It explicitly
refuses the --inside mode without the expected new user+network namespace.
When a host blocks user namespaces, treat the experiment as unavailable,
never pretend the output passed.

Reproduction on a compatible Linux system:

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/run_isolated_kernel_lab.py \
      --compare build/connect_reactor_compare \
      --netlink build/rtnetlink_monitor \
      --output lab-output/netns-kernel-fault

An opt-in actual integration regression can be run with:

    RIL_RUN_NETNS_LIVE=1 python3 -m unittest discover \
      -s tests -p test_isolated_kernel_lab.py -v

## Matched backend experiment (poll vs epoll)

One C++17 state machine uses identical nonblocking socket creation,
connection issue schedule, in-flight cap, deadline checks, SO_ERROR,
timing and JSONL recording. The only selectable strategy is Linux poll()
versus epoll_wait(). Local orchestration creates fresh listeners for each
trial and randomizes backend order with a fixed recorded seed.

Actually executed five paired trials in each of two workloads (20 real
program invocations in total):
- Accepting localhost TCP listener, 500 attempts/trial, concurrency limit
  32, 0ms interval, 250ms timeout. Both backends completed 2500/2500
  attempts with no failures. Median trial p99 among successful connects:
  poll 0.504776ms, epoll 0.712795ms on this host.
- Deliberately non-accepting localhost listener (backlog pressure),
  180 attempts/trial, concurrency limit 32, 100ms deadline.
  Each backend had 890/900 failed attempts. Successful-only p99
  is not a valid quality metric when this many connections fail.

The p99 values fluctuate considerably across trials and do not establish
that poll is generally faster or that epoll is slower. Same program does
NOT mean perfectly identical kernel scheduling or background conditions
on different trial invocations. No CPU affinity, kernel PMU, exchange
hardware, real Ethernet latency, throughput or statistically significant
winner claim is supported.

Reproduction:

    python3 scripts/benchmark_connect_reactors.py \
      --binary build/connect_reactor_compare \
      --output lab-output/reactor-benchmark --pairs 5 --count 500

All raw samples, CPU time, context switch counts, per-run errors,
execution order and SHA-256 digests are retained in the user's downloadable
verification archive. Raw generated artifacts live in lab-output (gitignored)
to prevent unmaintainable binary/data commits.

## Verification / outstanding blockers

- Five new matched-backend real-socket tests passed.
- Four isolated lab safety tests and one opt-in real namespace
  integration test passed (five tests total).
- Three independent isolated dual-link fault runs passed.
- Live three-router Containerlab/FRRouting/BGP validation remains
  **NOT EXECUTED** here: Docker, Containerlab and FRR are unavailable.
- Full clean-checkout regression of every original repository test
  has NOT been completed here. Do not concatenate targeted test totals
  into an unsupported fresh full-suite claim.
- No GitHub Actions, paid cloud resource or background jobs were added.

## History and branch policy

All source milestones in earlier PRs #1, #3, #4, #5, and #6 were merged
to main before this cycle. The initial #2 was superseded by #3.
This cycle is a single atomic main-tree update (no new remote feature branch).
Historical merged commits are preserved, not rewritten. Older feature branch
references, if still visible, are not part of main history; deletion may
require GitHub's branch UI when deletion access is unavailable through the
connected integration.
