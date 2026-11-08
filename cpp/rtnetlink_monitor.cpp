// Read-only Linux RTNETLINK IPv4 route snapshots and route/link notifications.
// Receipt timestamps are userspace observations, not exact kernel transition times.
#include <arpa/inet.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Ns = std::chrono::nanoseconds;
struct Fd {
    int fd = -1;
    explicit Fd(int x) : fd(x) {}
    ~Fd() { if (fd >= 0) ::close(fd); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
std::string quoted(const std::string& str) {
    std::string result = "\"";
    for (unsigned char c : str) {
        if (c == '"' || c == '\\') { result += '\\'; result += c; }
        else if (c >= 32 && c < 127) result += c;
        else result += '?';
    }
    return result + '"';
}
long long timestamp() {
    return std::chrono::duration_cast<Ns>(Clock::now().time_since_epoch()).count();
}
int number(const char* v, int lo, int hi) {
    try {
        size_t used = 0;
        auto n = std::stol(v, &used);
        if (used != std::strlen(v) || n < lo || n > hi) throw std::invalid_argument("range");
        return static_cast<int>(n);
    } catch (const std::exception&) { throw std::invalid_argument("invalid watch-ms"); }
}
std::string ipv4(const void* bytes) {
    char b[INET_ADDRSTRLEN]{};
    if (!inet_ntop(AF_INET, bytes, b, sizeof(b))) throw std::runtime_error("inet_ntop failed");
    return b;
}
struct Record {
    std::string json;
    bool route = false, link = false;
};
std::optional<Record> decode(const nlmsghdr* header, long long observed, bool snapshot) {
    const size_t len = header->nlmsg_len;
    if (len < sizeof(nlmsghdr)) throw std::runtime_error("short netlink header");
    if (header->nlmsg_type == RTM_NEWROUTE || header->nlmsg_type == RTM_DELROUTE) {
        if (len < NLMSG_LENGTH(sizeof(rtmsg))) throw std::runtime_error("short rtmsg");
        const auto* route = reinterpret_cast<const rtmsg*>(NLMSG_DATA(header));
        if (route->rtm_family != AF_INET) return std::nullopt;
        std::string dst = "0.0.0.0", gateway = "", oif = "";
        unsigned table = route->rtm_table;
        int attr_bytes = static_cast<int>(RTM_PAYLOAD(header));
        for (auto* attr = RTM_RTA(route); RTA_OK(attr, attr_bytes); attr = RTA_NEXT(attr, attr_bytes)) {
            if (attr->rta_type == RTA_DST && RTA_PAYLOAD(attr) == 4) dst = ipv4(RTA_DATA(attr));
            if (attr->rta_type == RTA_GATEWAY && RTA_PAYLOAD(attr) == 4) gateway = ipv4(RTA_DATA(attr));
            if (attr->rta_type == RTA_OIF && RTA_PAYLOAD(attr) >= sizeof(int)) {
                int index = 0;
                std::memcpy(&index, RTA_DATA(attr), sizeof(index));
                char name[IF_NAMESIZE]{};
                oif = if_indextoname(static_cast<unsigned>(index), name) ? name : std::to_string(index);
            }
            if (attr->rta_type == RTA_TABLE && RTA_PAYLOAD(attr) >= sizeof(uint32_t)) {
                uint32_t value = 0;
                std::memcpy(&value, RTA_DATA(attr), sizeof(value));
                table = value;
            }
        }
        if (attr_bytes != 0) throw std::runtime_error("malformed route attributes");
        std::string type = snapshot ? "snapshot_route" : "kernel_route_event";
        std::string op = header->nlmsg_type == RTM_NEWROUTE ? "newroute" : "delroute";
        std::string json = "{\"record_type\":" + quoted(type) + ",\"operation\":" + quoted(op)
            + ",\"observed_monotonic_ns\":" + std::to_string(observed)
            + ",\"prefix\":" + quoted(dst + "/" + std::to_string(route->rtm_dst_len))
            + ",\"gateway\":" + (gateway.empty() ? "null" : quoted(gateway))
            + ",\"out_interface\":" + (oif.empty() ? "null" : quoted(oif))
            + ",\"table\":" + std::to_string(table)
            + ",\"protocol\":" + std::to_string(route->rtm_protocol)
            + ",\"scope\":" + std::to_string(route->rtm_scope)
            + ",\"route_type\":" + std::to_string(route->rtm_type) + "}";
        return Record{std::move(json), true, false};
    }
    if (!snapshot && (header->nlmsg_type == RTM_NEWLINK || header->nlmsg_type == RTM_DELLINK)) {
        if (len < NLMSG_LENGTH(sizeof(ifinfomsg))) throw std::runtime_error("short ifinfomsg");
        const auto* msg = reinterpret_cast<const ifinfomsg*>(NLMSG_DATA(header));
        int attr_bytes = static_cast<int>(IFLA_PAYLOAD(header));
        std::string name;
        for (auto* attr = IFLA_RTA(msg); RTA_OK(attr, attr_bytes); attr = RTA_NEXT(attr, attr_bytes)) {
            if (attr->rta_type == IFLA_IFNAME && RTA_PAYLOAD(attr)) {
                const auto* bytes = static_cast<const char*>(RTA_DATA(attr));
                size_t amount = strnlen(bytes, RTA_PAYLOAD(attr));
                name.assign(bytes, amount);
            }
        }
        if (attr_bytes != 0) throw std::runtime_error("malformed link attributes");
        return Record{"{\"record_type\":\"kernel_link_event\",\"operation\":"
            + quoted(header->nlmsg_type == RTM_NEWLINK ? "newlink" : "dellink")
            + ",\"observed_monotonic_ns\":" + std::to_string(observed)
            + ",\"interface\":" + quoted(name) + ",\"interface_index\":" + std::to_string(msg->ifi_index)
            + ",\"flags\":" + std::to_string(msg->ifi_flags) + "}", false, true};
    }
    return std::nullopt;
}

int self_test() {
    alignas(nlmsghdr) unsigned char buffer[256]{};
    auto* h = reinterpret_cast<nlmsghdr*>(buffer);
    h->nlmsg_type = RTM_NEWROUTE;
    h->nlmsg_len = NLMSG_LENGTH(sizeof(rtmsg));
    auto* msg = reinterpret_cast<rtmsg*>(NLMSG_DATA(h));
    msg->rtm_family = AF_INET;
    msg->rtm_dst_len = 24;
    msg->rtm_table = RT_TABLE_MAIN;
    auto* a = reinterpret_cast<rtattr*>(buffer + NLMSG_ALIGN(h->nlmsg_len));
    a->rta_type = RTA_DST;
    a->rta_len = RTA_LENGTH(4);
    in_addr ip{};
    inet_pton(AF_INET, "198.51.100.0", &ip);
    std::memcpy(RTA_DATA(a), &ip, 4);
    h->nlmsg_len = NLMSG_ALIGN(h->nlmsg_len) + RTA_ALIGN(a->rta_len);
    auto value = decode(h, 123, false);
    if (!value || value->json.find("198.51.100.0/24") == std::string::npos || !value->route)
        throw std::runtime_error("synthetic route parser test failed");
    // Invalid length must fail rather than emit a misleading event.
    h->nlmsg_len = NLMSG_LENGTH(1);
    try { (void)decode(h, 123, false); }
    catch (const std::runtime_error&) { std::cout << "synthetic_parser_self_test PASS\n"; return 0; }
    throw std::runtime_error("short route message was accepted");
}

void emit(const Record& record, std::ostream& out, unsigned& routes, unsigned& links, unsigned& snapshots) {
    out << record.json << '\n';
    if (!out) throw std::runtime_error("failed to write netlink record");
    if (record.route && record.json.find("snapshot_route") != std::string::npos) ++snapshots;
    else if (record.route) ++routes;
    if (record.link) ++links;
}
int main_impl(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") return self_test();
    if (argc != 5 || std::string(argv[1]) != "--watch-ms" || std::string(argv[3]) != "--output")
        throw std::invalid_argument("usage: rtnetlink_monitor --watch-ms 0..120000 --output events.jsonl [or --self-test]");
    const int watch_ms = number(argv[2], 0, 120000);
    const std::string output = argv[4];
    if (output.empty()) throw std::invalid_argument("output path required");
    std::ofstream out(output, std::ios::trunc);
    if (!out) throw std::runtime_error("cannot open output file");
    Fd sock(::socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, NETLINK_ROUTE));
    if (sock.fd < 0) throw std::runtime_error(std::string("netlink socket: ") + std::strerror(errno));
    sockaddr_nl address{};
    address.nl_family = AF_NETLINK;
    address.nl_groups = RTMGRP_IPV4_ROUTE | RTMGRP_LINK;
    if (::bind(sock.fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0)
        throw std::runtime_error(std::string("netlink bind: ") + std::strerror(errno));
    struct { nlmsghdr h; rtmsg route; } request{};
    request.h.nlmsg_len = NLMSG_LENGTH(sizeof(rtmsg));
    request.h.nlmsg_type = RTM_GETROUTE;
    request.h.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
    request.h.nlmsg_seq = 20261108;
    request.route.rtm_family = AF_INET;
    sockaddr_nl kernel{};
    kernel.nl_family = AF_NETLINK;
    iovec vec{&request, sizeof(request)};
    msghdr message{};
    message.msg_name = &kernel;
    message.msg_namelen = sizeof(kernel);
    message.msg_iov = &vec;
    message.msg_iovlen = 1;
    if (::sendmsg(sock.fd, &message, 0) < 0) throw std::runtime_error("RTM_GETROUTE send failed");
    const auto dump_deadline = Clock::now() + std::chrono::seconds(5);
    const auto watch_begin = Clock::now();
    bool dump_done = false;
    unsigned routes = 0, links = 0, snapshots = 0;
    alignas(nlmsghdr) char bytes[1 << 16];
    while (!dump_done || (watch_ms > 0 && Clock::now() < watch_begin + std::chrono::milliseconds(watch_ms))) {
        auto now = Clock::now();
        if (!dump_done && now >= dump_deadline) throw std::runtime_error("RTM_GETROUTE dump timed out");
        auto deadline = !dump_done ? dump_deadline : watch_begin + std::chrono::milliseconds(watch_ms);
        auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count();
        if (left < 0) left = 0;
        pollfd pfd{sock.fd, POLLIN, 0};
        int rv = ::poll(&pfd, 1, static_cast<int>(left));
        if (rv < 0) { if (errno == EINTR) continue; throw std::runtime_error("poll(netlink) failed"); }
        if (rv == 0) continue;
        if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))
            throw std::runtime_error("netlink socket readiness error");
        iovec data{bytes, sizeof(bytes)};
        sockaddr_nl sender{};
        msghdr reply{};
        reply.msg_name = &sender;
        reply.msg_namelen = sizeof(sender);
        reply.msg_iov = &data;
        reply.msg_iovlen = 1;
        ssize_t got = ::recvmsg(sock.fd, &reply, 0);
        if (got < 0) { if (errno == EINTR || errno == EAGAIN) continue; throw std::runtime_error("recvmsg(netlink) failed"); }
        if (got == 0 || reply.msg_flags & MSG_TRUNC) throw std::runtime_error("truncated netlink receive");
        if (sender.nl_pid != 0) continue; // only kernel messages
        const auto observed = timestamp();
        int remain = static_cast<int>(got);
        for (auto* h = reinterpret_cast<nlmsghdr*>(bytes); NLMSG_OK(h, remain); h = NLMSG_NEXT(h, remain)) {
            const bool dump = h->nlmsg_seq == request.h.nlmsg_seq;
            if (h->nlmsg_type == NLMSG_DONE && dump) { dump_done = true; continue; }
            if (h->nlmsg_type == NLMSG_ERROR) {
                if (h->nlmsg_len < NLMSG_LENGTH(sizeof(nlmsgerr))) throw std::runtime_error("short NLMSG_ERROR");
                const auto* e = reinterpret_cast<const nlmsgerr*>(NLMSG_DATA(h));
                if (e->error != 0) throw std::runtime_error("netlink kernel error " + std::to_string(-e->error));
                continue;
            }
            if (auto record = decode(h, observed, dump)) emit(*record, out, routes, links, snapshots);
        }
        if (remain != 0) throw std::runtime_error("malformed netlink multipart envelope");
    }
    out.flush();
    if (!out) throw std::runtime_error("netlink output flush failed");
    std::cout << "{\"engine\":\"rtnetlink_read_only\",\"snapshot_routes\":" << snapshots
              << ",\"route_notifications\":" << routes << ",\"link_notifications\":" << links
              << ",\"watch_ms\":" << watch_ms
              << ",\"limitation\":\"user-space observation timestamps, not exact kernel update times\"}\n";
    return 0;
}
} // namespace
int main(int argc, char** argv) {
    try { return main_impl(argc, argv); }
    catch (const std::exception& e) { std::cerr << "error: " << e.what() << '\n'; return 2; }
}
