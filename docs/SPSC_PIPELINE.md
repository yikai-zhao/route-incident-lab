# Bounded C++ Linux multicast SPSC processing pipeline

This module extends the existing single-threaded synthetic UDP feed monitor with
a real two-thread producer/consumer network datapath. It is not an exchange
feed decoder, throughput record, kernel-bypass experiment or evidence of wire
packet loss.

## Architecture

- **Receive/producer thread:** a nonblocking Linux IPv4 UDP socket, joined
  specifically to loopback multicast 239.255.42.99 (or "-" loopback unicast),
  uses epoll and recvmmsg batches of up to 16 datagrams. Frames are the existing
  synthetic RIL1 16-byte big-endian format (magic, 32-bit stream ID,
  64-bit sequence ID).
- **Bounded SPSC queue:** cpp/spsc_ring.h uses fixed-capacity preallocated
  storage and atomics. Producer publishes a constructed slot with release
  ordering, and consumer reads it with acquire ordering. The symmetric
  acquire/release operations guard slot reuse. Only one producer and one
  consumer are supported. The capacity is a power-of-two 2..65536.
- **Processing/consumer thread:** validates per-stream sequence progress,
  tracks 4096 recent IDs, and distinguishes forward jumps, late arrival
  and known duplicates. It writes JSONL with monotonic receive and processing
  timestamps and an observed queue-residence duration.
- **Explicit loss-source boundaries:** increments application_queue_drops
  ONLY when a *valid* datagram was received but rejected by a full SPSC
  ring. SO_RXQ_OVFL is inspected separately when the kernel supplies its
  ancillary control message. A sequence-number gap is never automatically
  classified as packet loss.

## Run without Docker, CI, cloud or paid APIs

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    ctest --test-dir build --output-on-failure
    python3 -m unittest discover -s tests -p test_udp_pipeline.py -v
    python3 scripts/demo_spsc_pipeline.py --binary build/udp_pipeline \
      --output lab-output/spsc-udp

This starts only short-lived test sockets on 127.0.0.1 and creates ignored
local JSONL and report.json files. Does not use sudo, change host routes,
start scheduled actions, or contact any remote destination.

## Actual Linux verification (2026-11-08)

- Compiled C++17 -O2 -Wall -Wextra -Wpedantic -pthread on Linux x86_64.
- The native SPSC queue completed **200,000 strictly ordered transfers**
  between two threads and passed bounded-full/recovery assertions.
- The SPSC test also ran with **ThreadSanitizer**, and separately with
  **AddressSanitizer + UndefinedBehaviorSanitizer**: both passed in this
  isolated local execution.
- **Seven** real socket regression tests passed in standard and
  ASan/UBSan builds: multicast, explicit queue backpressure, known duplicate
  vs out-of-order, synthetic publisher sequence skips, malformed input,
  two streams and invalid/no-data cases.
- An independent four-experiment real multicast demo recorded:
  * baseline 64 datagrams, 64 processed, 0 application ring drops;
  * three pressured trials each sent 160 and received 160 valid datagrams;
    the tiny 4-slot queue with a 7ms delayed consumer processed 29, 29,
    and 30 messages; its **application** drops were 131, 131 and 130;
  * the queue peaked at depth 4, and each trial satisfied
    valid_frames == processed + application_queue_drops.
- The pressured trials observed queue-residence p99 ~28ms on this host.
  That does **not** represent an optimized feed latency. The slow
  consumer was deliberate. No SO_RXQ_OVFL control-message evidence
  was observed; it would be incorrect to assert kernel socket overflow.

## What this does not prove

- The user-space queue is SPSC, not general MPMC, and cannot be used
  concurrently by multiple producers or consumers.
- Producer and consumer operate on a shared, preallocated ring, but
  the socket syscalls, JSONL output, data structures and sleep calls
  are NOT lock-free or hard real-time.
- CPU scheduling and file I/O affect queue residence measurement.
  No CPU affinity, NIC hardware timestamps, NUMA tests, cache
  benchmarks, io_uring, AF_XDP, DPDK or production kernel-bypass.
- Per-stream duplicate history retains 4096 IDs; an old repeated ID
  outside that window can be classified as late rather than duplicate.
- A publisher can skip IDs. A skipped ID can be dropped by the app ring.
  Neither a forward jump nor SO_TIMESTAMPNS alone establishes wire loss.
- Kernel socket-buffer drops may occur without a control message
  being observed in this short experiment.
- Real FRR/BGP and cross-machine multicast delivery are not validated.

## Next experiment worth doing

First improve confidence in the measurement itself: isolate consumer
JSONL cost with a selectable no-output mode, measure sustained real
recvmsg vs recvmmsg throughput with fixed offered load, CPU affinity
and explicit kernel/app drops, repeat trials, and retain raw data.
Only then optimize memory layout or claim any throughput difference.

GitHub Actions and all paid services remain disabled/not configured.
