// Measures user-space TCP connect completion time, not wire one-way latency.
// Build: cmake -S . -B build && cmake --build build
// Usage: ./build/tcp_connect_probe 127.0.0.1 8000 100
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <netdb.h>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <algorithm>

namespace {
class Fd {
public:
    explicit Fd(int fd = -1) : fd_(fd) {}
    ~Fd() { if (fd_ >= 0) ::close(fd_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    int get() const { return fd_; }
private:
    int fd_;
};

struct AddrInfo {
    addrinfo* head = nullptr;
    ~AddrInfo() { if (head) ::freeaddrinfo(head); }
    AddrInfo(const AddrInfo&) = delete;
    AddrInfo& operator=(const AddrInfo&) = delete;
    AddrInfo(const char* host, const char* port) {
        addrinfo hints{};
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;
        hints.ai_protocol = IPPROTO_TCP;
        const int rc = ::getaddrinfo(host, port, &hints, &head);
        if (rc != 0) throw std::runtime_error(::gai_strerror(rc));
    }
};

bool connect_once(const addrinfo* addr, int timeout_ms, double& elapsed_ms) {
    using Clock = std::chrono::steady_clock;
    Fd sock(::socket(addr->ai_family, addr->ai_socktype | SOCK_CLOEXEC, addr->ai_protocol));
    if (sock.get() < 0) return false;
    const int flags = ::fcntl(sock.get(), F_GETFL, 0);
    if (flags < 0 || ::fcntl(sock.get(), F_SETFL, flags | O_NONBLOCK) < 0) return false;

    const auto start = Clock::now();
    int rc = ::connect(sock.get(), addr->ai_addr, addr->ai_addrlen);
    if (rc == -1 && errno == EINPROGRESS) {
        pollfd pfd{};
        pfd.fd = sock.get();
        pfd.events = POLLOUT;
        do {
            rc = ::poll(&pfd, 1, timeout_ms);
        } while (rc == -1 && errno == EINTR);
        if (rc <= 0) return false;
        int connect_error = 0;
        socklen_t len = sizeof(connect_error);
        if (::getsockopt(sock.get(), SOL_SOCKET, SO_ERROR, &connect_error, &len) < 0 || connect_error != 0)
            return false;
    } else if (rc != 0) {
        return false;
    }
    elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    return true;
}

// Try all DNS results (e.g. IPv6 localhost then IPv4 localhost) under
// one overall timeout. Count fallback attempts in the total measurement.
bool connect_any(const addrinfo* head, int timeout_ms, double& elapsed_ms) {
    using Clock = std::chrono::steady_clock;
    const auto start = Clock::now();
    const auto deadline = start + std::chrono::milliseconds(timeout_ms);
    for (auto* entry = head; entry; entry = entry->ai_next) {
        const auto now = Clock::now();
        if (now >= deadline) return false;
        const int remaining = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
        if (remaining <= 0) return false;
        double ignored_ms = 0;
        if (connect_once(entry, remaining, ignored_ms)) {
            elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
            return true;
        }
    }
    return false;
}

double quantile(const std::vector<double>& xs, double q) {
    if (xs.empty()) return 0.0;
    double pos = (xs.size() - 1) * q;
    const auto lo = static_cast<size_t>(std::floor(pos));
    const auto hi = static_cast<size_t>(std::ceil(pos));
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo);
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "Usage: tcp_connect_probe HOST PORT COUNT (1..10000)\n";
        return 2;
    }
    try {
        size_t port_pos = 0, count_pos = 0;
        const long port = std::stol(argv[2], &port_pos);
        const long count = std::stol(argv[3], &count_pos);
        if (port_pos != std::strlen(argv[2]) || count_pos != std::strlen(argv[3]) ||
            port < 1 || port > 65535 || count < 1 || count > 10000)
            throw std::invalid_argument("invalid port or count");
        AddrInfo address(argv[1], argv[2]);
        if (!address.head) throw std::runtime_error("empty address result");
        std::vector<double> samples;
        samples.reserve(static_cast<size_t>(count));
        long failures = 0;
        for (long i = 0; i < count; ++i) {
            double elapsed_ms = 0;
            if (connect_any(address.head, 1000, elapsed_ms)) samples.push_back(elapsed_ms);
            else ++failures;
            if (i + 1 < count) std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        std::sort(samples.begin(), samples.end());
        std::cout.setf(std::ios::fixed);
        std::cout.precision(3);
        std::cout << "{\"engine\":\"cpp\",\"target\":\"" << argv[1] << ':' << port
                  << "\",\"attempts\":" << count << ",\"successes\":" << samples.size()
                  << ",\"failures\":" << failures << ",\"connect_failure_pct\":" << (100.0 * failures / count)
                  << ",\"p50_ms\":";
        if (samples.empty()) std::cout << "null";
        else std::cout << quantile(samples, .50);
        std::cout << ",\"p95_ms\":";
        if (samples.empty()) std::cout << "null";
        else std::cout << quantile(samples, .95);
        std::cout << ",\"p99_ms\":";
        if (samples.empty()) std::cout << "null";
        else std::cout << quantile(samples, .99);
        std::cout << "}\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 2;
    }
}
