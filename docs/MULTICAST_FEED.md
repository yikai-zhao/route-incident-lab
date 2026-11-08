# Synthetic UDP multicast feed diagnostics (Linux/C++17)

## What is implemented

The native Linux udp_feed_monitor is a bounded C++17 receiver using
nonblocking UDP sockets, epoll, and a batch of up to 16 messages per recvmmsg
call. An explicit synthetic 16-byte big-endian message format is used:

    Offset  Size  Meaning
    0       4     0x52494c31 ("RIL1" magic)
    4       4     unsigned stream ID
    8       8     unsigned monotonically increasing sequence number

The receiver accepts one IPv4 multicast group on the loopback interface
or "-" for local-only unicast. All samples, classified per stream, are
saved as JSONL. Configured limits: 1..10000 received packets,
100..60000 ms deadline, 32 streams max, recent sequence memory of 4096
entries per stream, 16-message kernel receive batch.

Each packet is classified as first, in_order, forward_jump, out_of_order,
duplicate, outside_reorder_window, or large_jump_unknown.

The forward_missing_candidates count is only how many sequence numbers
were skipped by a forward jump. Late arrivals may fill those candidates.
Even permanently absent sequence IDs do NOT independently establish network
packet loss: the publisher itself may have skipped the numbers.

Optional SO_TIMESTAMPNS ancillary timestamps use CLOCK_REALTIME and are
reported separately from client monotonic observations. Optional
SO_RXQ_OVFL control data can report a kernel socket queue overflow counter
when such a notification is supplied. Neither is NIC hardware timestamping.

## Reproduce with actual loopback multicast sockets

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j2
    python3 scripts/demo_multicast_feed.py \
      --binary build/udp_feed_monitor \
      --output lab-output/multicast-feed
    python3 -m unittest discover -s tests -p test_udp_feed_monitor.py -v

The local publisher sends this intentional sequence:
1,2,4,3,4,5,9,10,8,11. It does not generate exchange messages
or market prices. The receiver should classify two forward jumps,
two late out-of-order messages, and one duplicate.

The demo uses 239.255.42.99 on 127.0.0.1, not a company multicast network.
No root, external market feed, cloud service, or paid data source is needed.
The produced feed.jsonl and report.json show exact evidence and checks.

The receiver command itself is:

    ./build/udp_feed_monitor GROUP_OR_DASH PORT COUNT TIMEOUT_MS OUTPUT.jsonl

On "-" mode the receiver binds to 127.0.0.1 for unicast tests.
A multicast group joins specifically on loopback 127.0.0.1.
Do not infer any ability to join multicast groups on unrelated interfaces.

## Real evidence (2026-11-08)

- C++ compiled with g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic.
- 10 real multicast UDP packets were received using the Linux loopback network.
- Classified 1 first, 4 in-order, 2 forward jumps, 2 out-of-order and
  1 duplicate. Four candidate skipped sequence IDs are publisher-generated.
- Kernel receive timestamps were enabled; SO_RXQ_OVFL tracking was enabled,
  but no overflow-control notification was observed.
- All 6 new UDP socket tests passed. The local workspace reran 23/23 total
  tests across epoll, RTNETLINK and UDP modules. This is NOT a fresh execution
  of every Python test in the full GitHub checkout.
- Does not prove real packet loss, exchange latency, sustained high throughput,
  kernel bypass, PIM-SM network convergence, or FRR fault recovery.

## What makes this relevant to systems engineering

Actual event-driven socket code, bounded resources, batching, strict binary
parsing, per-stream state machines, explicit timestamp clock domains,
observable queue-overflow signals, and accurate operational semantics are
transferable to live-feed systems and network diagnostics.

Higher-level market-data decoding, gap recovery and hardware accelerated
packet ingestion are deliberately out of scope until those capabilities are
implemented and independently tested. No claim of production feed support.

## Next deeper experiments

1. Sustained feed burst testing with sender sequence exhaustiveness,
   backpressure and genuine observed SO_RXQ_OVFL evidence.
2. Matched recvmsg/recvmmsg throughput and CPU profile comparisons,
   multiple real trials, process scheduling affinity and host metadata.
3. Netns or Containerlab routed multicast topology with authorized PIM-SM,
   actual route/link events and separately measured application effects.
4. Correct per-stream recovery of missing candidates and optional feed
   reordering latency under a documented bounded window.
