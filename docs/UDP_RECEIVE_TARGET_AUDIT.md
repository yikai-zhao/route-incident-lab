# UDP batch receive target accounting — 2026-11-10

**Scope:** C++17 `udp_pipeline` real Linux socket receive loop. This is an accounting correctness fix, **not** a speedup or trading-network performance claim.

## Finding

The previous `recvmmsg(fd, messages, 16, ...)` call could remove 16 UDP packets from the kernel socket with only one logical frame left before the requested target. The subsequent `for (i < got && received < target)` counted only the first returned element. Remaining packets had been consumed by the syscall but disappeared from all receiver-side counters. A clean report of `received == target` thus could not establish complete syscall accounting.

A diagnostic built from the previous committed C++ source (Git blob `ac87a950420ac04e8ce92c519e74e00e84c74a7a`) counted `got - (target-received)` before logical processing. On this host, when 64 localhost datagrams were burst to a receiver with target 1, **18/18 trials observed 15 consumed-but-unreported datagrams**. The test reproduces a mismatch between syscall consumption and application counters; it does not imply any physical network loss.

## Fix and invariants

The receiver now bounds each batch `vlen = min(16, target - received)` and processes **all** elements returned by the syscall. A separate `rx_datagrams_consumed_by_syscalls` counter must equal `received_datagrams`; this is checked at runtime. Normal and malformed frames still contribute to `received`; validated frames must equal `processed + application_queue_drops`.

Real socket regression `tests/test_udp_receive_target_accounting.py` exercises `target = 1, 3, 15, 16, 17, 31` with 64 offered packets and both scalar and batched receive. No new traffic to external hosts, privileged network changes, or GitHub Actions.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 -m unittest discover -s tests -p test_udp_receive_target_accounting.py -v
python3 -m unittest discover -s tests -p test_udp_pipeline.py -v
python3 -m unittest discover -s tests -p test_native_sender.py -v
ctest --test-dir build --output-on-failure
```

A targeted ASan/UBSan real-socket 80-packet run and the Release SPSC 200,000-transfer test also passed in the local audit. The full clean-clone repository regression, Docker/Containerlab FRR BGP test and dedicated-NIC latency/profile measurements remain unverified. An exact-target receiver legitimately leaves later datagrams pending/unread: **do not infer that it consumed or accounted for all externally offered datagrams**.
