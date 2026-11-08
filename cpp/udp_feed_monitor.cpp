// Bounded Linux multicast UDP receiver for a documented synthetic 16-byte feed.
// Sequence gaps are not proof of packet loss; kernel timestamps use REALTIME.
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/epoll.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
struct Fd {
    int fd;
    explicit Fd(int value): fd(value) {}
    ~Fd() { if (fd >= 0) ::close(fd); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
long parse(const char* value, long lo, long hi) {
    try { size_t used=0; long v=std::stol(value,&used);
        if (used != std::strlen(value) || v<lo || v>hi) throw std::invalid_argument("bounds");
        return v;
    } catch (const std::exception&) { throw std::invalid_argument("invalid numeric parameter"); }
}
uint32_t be32(const unsigned char* b) {
    uint32_t v; std::memcpy(&v,b,4); return ntohl(v);
}
uint64_t be64(const unsigned char* b) {
    return (static_cast<uint64_t>(be32(b)) << 32) | be32(b+4);
}
long long monotonic_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
struct Stream {
    bool started = false;
    uint64_t highest = 0;
    std::deque<uint64_t> recent;
    std::unordered_set<uint64_t> seen;
    void remember(uint64_t seq) {
        recent.push_back(seq); seen.insert(seq);
        if (recent.size() > 4096) { seen.erase(recent.front()); recent.pop_front(); }
    }
    std::pair<std::string,uint64_t> process(uint64_t seq) {
        if (!started) { started = true; highest = seq; remember(seq); return {"first",0}; }
        if (seen.count(seq)) return {"duplicate",0};
        if (seq < highest) {
            if (highest - seq > 4096) return {"outside_reorder_window",0};
            remember(seq); return {"out_of_order",0};
        }
        if (seq == highest) return {"outside_reorder_window",0};
        uint64_t distance = seq - highest;
        highest = seq; remember(seq);
        if (distance == 1) return {"in_order",0};
        if (distance > 1'000'000) return {"large_jump_unknown",0};
        return {"forward_jump",distance-1};
    }
};
struct Stats {
    size_t received=0, malformed=0, first=0, in_order=0, forward_jump=0;
    size_t out_of_order=0, duplicate=0, outside=0, large_jump=0;
    uint64_t gap_candidates=0;
    uint32_t last_kernel_overflow=0;
    bool overflow_reported=false;
};
constexpr uint32_t MAGIC = 0x52494c31; // ASCII RIL1
const char* classification_name = "synthetic_sequence_event";
int run(int argc, char** argv) {
    if (argc != 6) throw std::invalid_argument(
        "usage: udp_feed_monitor GROUP_OR_DASH PORT COUNT TIMEOUT_MS OUTPUT.jsonl");
    const std::string group = argv[1];
    const int port = static_cast<int>(parse(argv[2], 1, 65535));
    const size_t target = static_cast<size_t>(parse(argv[3], 1, 10000));
    const int timeout_ms = static_cast<int>(parse(argv[4], 100, 60000));
    std::ofstream out(argv[5], std::ios::trunc);
    if (!out) throw std::runtime_error("cannot open output");
    Fd s(::socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
    if (s.fd < 0) throw std::runtime_error("UDP socket failed");
    int yes = 1;
    if (::setsockopt(s.fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes)) < 0)
        throw std::runtime_error("SO_REUSEADDR failed");
    bool rx_timestamps = ::setsockopt(s.fd, SOL_SOCKET, SO_TIMESTAMPNS, &yes, sizeof(yes)) == 0;
    bool rx_overflow = ::setsockopt(s.fd, SOL_SOCKET, SO_RXQ_OVFL, &yes, sizeof(yes)) == 0;
    sockaddr_in bind_to{};
    bind_to.sin_family = AF_INET;
    bind_to.sin_port = htons(static_cast<uint16_t>(port));
    bind_to.sin_addr.s_addr = group == "-" ? htonl(INADDR_LOOPBACK) : htonl(INADDR_ANY);
    if (::bind(s.fd, reinterpret_cast<sockaddr*>(&bind_to), sizeof(bind_to)) < 0)
        throw std::runtime_error("UDP bind failed");
    if (group != "-") {
        in_addr addr{};
        if (::inet_pton(AF_INET, group.c_str(), &addr) != 1 ||
            (ntohl(addr.s_addr) & 0xf0000000u) != 0xe0000000u)
            throw std::invalid_argument("group must be multicast numeric IPv4 or - for loopback unicast");
        ip_mreq request{};
        request.imr_multiaddr = addr;
        if (::inet_pton(AF_INET, "127.0.0.1", &request.imr_interface) != 1)
            throw std::runtime_error("invalid local interface");
        if (::setsockopt(s.fd, IPPROTO_IP, IP_ADD_MEMBERSHIP, &request, sizeof(request)) < 0)
            throw std::runtime_error("multicast join failed; loopback multicast unavailable");
    }
    Fd epoll(::epoll_create1(EPOLL_CLOEXEC));
    if (epoll.fd < 0) throw std::runtime_error("epoll create failed");
    epoll_event event{};
    event.events = EPOLLIN;
    event.data.fd = s.fd;
    if (::epoll_ctl(epoll.fd, EPOLL_CTL_ADD, s.fd, &event) < 0)
        throw std::runtime_error("epoll add failed");
    std::unordered_map<uint32_t, Stream> streams;
    Stats stats;
    const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
    constexpr int BATCH = 16;
    while (stats.received < target && Clock::now() < deadline) {
        auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
        if (remaining < 0) remaining = 0;
        epoll_event ready{};
        int n = ::epoll_wait(epoll.fd, &ready, 1, static_cast<int>(remaining));
        if (n < 0) { if (errno == EINTR) continue; throw std::runtime_error("epoll wait failed"); }
        if (n == 0) break;
        if ((ready.events & EPOLLIN) == 0) throw std::runtime_error("epoll receive error");
        unsigned char data[BATCH][2048]{};
        alignas(cmsghdr) unsigned char controls[BATCH][256]{};
        sockaddr_in senders[BATCH]{};
        iovec vec[BATCH]{};
        mmsghdr messages[BATCH]{};
        for (int i=0;i<BATCH;++i) {
            vec[i].iov_base = data[i]; vec[i].iov_len = sizeof(data[i]);
            messages[i].msg_hdr.msg_iov = &vec[i];
            messages[i].msg_hdr.msg_iovlen = 1;
            messages[i].msg_hdr.msg_name = &senders[i];
            messages[i].msg_hdr.msg_namelen = sizeof(senders[i]);
            messages[i].msg_hdr.msg_control = controls[i];
            messages[i].msg_hdr.msg_controllen = sizeof(controls[i]);
        }
        int got = ::recvmmsg(s.fd, messages, BATCH, MSG_DONTWAIT, nullptr);
        if (got < 0) { if (errno == EAGAIN || errno == EINTR) continue; throw std::runtime_error("recvmmsg failed"); }
        for (int i=0;i<got && stats.received < target;++i) {
            const auto& msg = messages[i];
            if ((msg.msg_hdr.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0 ||
                msg.msg_len != 16 || be32(data[i]) != MAGIC) { ++stats.malformed; continue; }
            uint32_t stream_id = be32(data[i]+4);
            uint64_t seq = be64(data[i]+8);
            if (!streams.count(stream_id) && streams.size() >= 32) { ++stats.malformed; continue; }
            auto [kind, candidates] = streams[stream_id].process(seq);
            stats.gap_candidates += candidates;
            if (kind == "first") ++stats.first;
            else if (kind == "in_order") ++stats.in_order;
            else if (kind == "forward_jump") ++stats.forward_jump;
            else if (kind == "out_of_order") ++stats.out_of_order;
            else if (kind == "duplicate") ++stats.duplicate;
            else if (kind == "outside_reorder_window") ++stats.outside;
            else if (kind == "large_jump_unknown") ++stats.large_jump;
            long long kernel_realtime_ns = -1;
            uint32_t kernel_overflow = 0;
            bool has_overflow = false;
            for (auto* c = CMSG_FIRSTHDR(&messages[i].msg_hdr); c != nullptr;
                 c = CMSG_NXTHDR(const_cast<msghdr*>(&messages[i].msg_hdr), c)) {
                if (c->cmsg_level != SOL_SOCKET) continue;
                if (c->cmsg_type == SCM_TIMESTAMPNS && c->cmsg_len >= CMSG_LEN(sizeof(timespec))) {
                    timespec ts{}; std::memcpy(&ts,CMSG_DATA(c),sizeof(ts));
                    kernel_realtime_ns = static_cast<long long>(ts.tv_sec)*1000000000LL + ts.tv_nsec;
                }
                if (c->cmsg_type == SO_RXQ_OVFL && c->cmsg_len >= CMSG_LEN(sizeof(uint32_t))) {
                    std::memcpy(&kernel_overflow,CMSG_DATA(c),sizeof(kernel_overflow));
                    has_overflow = true;
                }
            }
            if (has_overflow) { stats.overflow_reported = true; stats.last_kernel_overflow = kernel_overflow; }
            out << "{\"record_type\":\"" << classification_name
                << "\",\"stream_id\":" << stream_id
                << ",\"seq\":" << seq
                << ",\"classification\":\"" << kind << "\""
                << ",\"forward_missing_candidates\":" << candidates
                << ",\"observed_monotonic_ns\":" << monotonic_ns()
                << ",\"kernel_receive_realtime_ns\":";
            if (kernel_realtime_ns >= 0) out << kernel_realtime_ns; else out << "null";
            out << ",\"rxq_ovfl_cumulative\":";
            if (has_overflow) out << kernel_overflow; else out << "null";
            out << "}\n";
            ++stats.received;
        }
        if (!out) throw std::runtime_error("failed writing feed records");
    }
    out.flush();
    if (!out) throw std::runtime_error("failed flushing feed output");
    std::cout << "{\"engine\":\"linux_epoll_recvmmsg\",\"received\":" << stats.received
        << ",\"target\":" << target << ",\"malformed\":" << stats.malformed
        << ",\"streams\":" << streams.size()
        << ",\"first\":" << stats.first << ",\"in_order\":" << stats.in_order
        << ",\"forward_jump\":" << stats.forward_jump
        << ",\"out_of_order\":" << stats.out_of_order
        << ",\"duplicate\":" << stats.duplicate
        << ",\"outside_reorder_window\":" << stats.outside
        << ",\"large_jump_unknown\":" << stats.large_jump
        << ",\"forward_missing_candidates\":" << stats.gap_candidates
        << ",\"kernel_receive_timestamp_enabled\":" << (rx_timestamps ? "true":"false")
        << ",\"rxq_overflow_monitor_enabled\":" << (rx_overflow ? "true":"false")
        << ",\"rxq_overflow_seen\":" << (stats.overflow_reported ? "true":"false")
        << ",\"rxq_last_cumulative\":" << stats.last_kernel_overflow
        << ",\"complete\":" << (stats.received == target ? "true":"false")
        << ",\"limitation\":\"sequence jumps are not proof of network loss; timestamps use different clocks\"}\n";
    return stats.received == target ? 0 : 1;
}
} // namespace
int main(int argc,char** argv) {
    try { return run(argc,argv); }
    catch (const std::exception& e) { std::cerr << "error: " << e.what() << '\n'; return 2; }
}
