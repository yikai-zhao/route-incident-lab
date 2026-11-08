# Development history and release hygiene

This log is append-only. Git commit history is the canonical source.

- 2026-11-02: Original read-only route-state capture, synthetic incident
  fixtures, vendor text adapters, FRR Containerlab configuration, opt-in
  fault controller and local smoke-test launcher. Historical main:
  2dc4378476cde19291f13570ecf22c348308375a.
- 2026-11-07/08: PR #1 merged: robust HTTP status parsing, offline
  artifact integrity audit, evidence-gated incident Markdown.
  Squash commit 7ab539e9e038cbcf8b94a02d5f25c55fe5e7bf3e.
- 2026-11-08: PR #3 merged: native Linux C++ epoll TCP sampler, raw samples
  and loopback server lifecycle regression. Squash commit
  d6141e243f87edf195e64c60db32d27eba247e81.
  PR #2 was closed as superseded due to stacked-branch merge history.
- 2026-11-08: PR #4 merged: read-only RTNETLINK route/link monitor and
  same-host cross-plane temporal correlation. Squash commit
  5d5595ba3fb4f32aa59de51d90dd47117488a802.
- 2026-11-08: PR #5 merged: bounded native UDP multicast sequenced
  synthetic feed diagnostics. Squash commit
  c0f5dc795aa1fa418f5d27025d2e87b35ca374be.
- 2026-11-08: PR #6 merged: repeated localhost TCP stress and actual
  errno/p99/CPU/kernel queue observation. Squash commit
  5c3a9463961e06d054087f2c7717052451aec8ba.
- 2026-11-08: Core evidence: paired poll/epoll reactor, three actual
  isolated Linux dual-veth static-route fault runs, tests and reproducible
  evidence manifest. One atomic direct-main commit, no feature branch.

## Verification classes

1. Synthetic fixtures test analysis and parsing, not router hardware.
2. Real localhost sockets test user-space TCP/UDP network behavior.
3. Isolated Linux netns tests kernel route/link behavior without touching
   host routing, NOT a production router or FRR dynamic control plane.
4. Full FRRouting/BGP/Containerlab dynamic scenarios are configured but
   **not yet verified end-to-end** in the available runtime.

No GitHub Actions, cloud expenses, background runs or employer-private
captured state are part of this development workflow.

## Branch cleanup

This update intentionally does not create a new remote branch.
Earlier historical feature refs may remain visible. The available GitHub
connector can update refs but cannot delete old branches. Such refs should
be deleted from GitHub's Branches UI after confirming merged and superseded
PR status. Do not rewrite main history just to make stale refs disappear.

- 2026-11-08: Local-only two-thread C++ UDP feed pipeline with bounded
  acquire/release SPSC ring, real epoll/recvmmsg multicast, explicit
  application drop accounting and repeated controlled consumer
  backpressure. No new remote branch; direct main-tree commit.
  The historical record remains append-only.
