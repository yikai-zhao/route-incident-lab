// Linux-only, bounded event-driven TCP connection sampler.
// Measures client connect completion only: NOT RTT, application delivery, or wire latency.
#include <arpa/inet.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>
#include <cerrno>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;

class Fd {
public:
    explicit Fd(int fd = -1) noexcept : fd_(fd) {}
    ~Fd() { if (fd_ >= 0) ::close(fd_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    Fd(Fd&& o) noexcept : fd_(o.fd_) { o.fd_ = -1; }
    Fd& operator=(Fd&& o) noexcept {
        if (this != &o) { if (fd_ >= 0) ::close(fd_); fd_ = o.fd_; o.fd_ = -1; }
        return *this;
    }
    int get() const noexcept { return fd_; }
private:
    int fd_;
};
struct Config {
    std::string host;
    int port = 0, count = 0, concurrency = 0, interval_ms = 0, timeout_ms = 0;
    std::string output;
    sockaddr_in target{};
};
struct Attempt {
    int seq;
    Time start, deadline;
    Fd socket;
};
struct Sample {
    int seq, error;
    bool ok;
    long long start_ns, finish_ns;
    double connect_ms;
};

long parse(const char* s, long lo, long hi, const char* name) {
    try {
        size_t pos = 0;
        const std::string input(s);
        long value = std::stol(input, &pos, 10);
        if (pos != input.size() || value < lo || value > hi)
            throw std::invalid_argument("outside permitted range");
        return value;
    } catch (const std::exception&) {
        throw std::invalid_argument(std::string("invalid ") + name);
    }
}
Config args(int argc, char** argv) {
    if (argc != 7 && argc != 8)
        throw std::invalid_argument("usage: epoll_connect_sampler IPv4 PORT COUNT CONCURRENCY INTERVAL_MS TIMEOUT_MS [SAMPLES.jsonl]");
    Config c;
    c.host = argv[1];
    c.port = static_cast<int>(parse(argv[2], 1, 65535, "port"));
    c.count = static_cast<int>(parse(argv[3], 1, 10000, "count"));
    c.concurrency = static_cast<int>(parse(argv[4], 1, 256, "concurrency"));
    c.interval_ms = static_cast<int>(parse(argv[5], 0, 1000, "interval_ms"));
    c.timeout_ms = static_cast<int>(parse(argv[6], 10, 10000, "timeout_ms"));
    if (argc == 8) c.output = argv[7];
    c.target.sin_family = AF_INET;
    c.target.sin_port = htons(static_cast<uint16_t>(c.port));
    if (::inet_pton(AF_INET, c.host.c_str(), &c.target.sin_addr) != 1)
        throw std::invalid_argument("host must be numeric IPv4; DNS excluded from measurement");
    return c;
}
long long ns(Time t) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(t.time_since_epoch()).count();
}
double ms(Time a, Time b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}
double percentile(const std::vector<double>& sorted, double q) {
    const double pos = (sorted.size() - 1) * q;
    const size_t lo = static_cast<size_t>(std::floor(pos));
    const size_t hi = static_cast<size_t>(std::ceil(pos));
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
void print_metric(const std::vector<double>& values, double q) {
    if (values.empty()) std::cout << "null";
    else std::cout << percentile(values, q);
}

int run(const Config& c) {
    Fd epoll(::epoll_create1(EPOLL_CLOEXEC));
    if (epoll.get() < 0) throw std::runtime_error("epoll_create1 failed");
    std::unordered_map<int, Attempt> active;
    std::vector<Sample> completed;
    completed.reserve(static_cast<size_t>(c.count));
    int issued = 0, peak = 0;
    const Time run_start = Clock::now();
    Time next_issue = run_start;

    auto record = [&](int seq, Time start, Time done, int error) {
        completed.push_back({seq, error, error == 0, ns(start), ns(done), ms(start, done)});
    };
    auto finish = [&](int fd, int error) {
        auto it = active.find(fd);
        if (it == active.end()) return;
        record(it->second.seq, it->second.start, Clock::now(), error);
        ::epoll_ctl(epoll.get(), EPOLL_CTL_DEL, fd, nullptr);
        active.erase(it); // Fd closes socket even on failure.
    };
    auto start_one = [&] {
        const Time started = Clock::now();
        const int seq = issued++;
        Fd sock(::socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
        if (sock.get() < 0) { record(seq, started, Clock::now(), errno); return; }
        int rc = ::connect(sock.get(), reinterpret_cast<const sockaddr*>(&c.target), sizeof(c.target));
        if (rc == 0) { record(seq, started, Clock::now(), 0); return; }
        const int error = errno;
        if (error != EINPROGRESS) { record(seq, started, Clock::now(), error); return; }
        epoll_event event{};
        event.events = EPOLLOUT | EPOLLERR | EPOLLHUP;
        event.data.fd = sock.get();
        if (::epoll_ctl(epoll.get(), EPOLL_CTL_ADD, sock.get(), &event) < 0) {
            record(seq, started, Clock::now(), errno);
            return;
        }
        int fd = sock.get();
        active.emplace(fd, Attempt{seq, started, started + std::chrono::milliseconds(c.timeout_ms), std::move(sock)});
        peak = std::max(peak, static_cast<int>(active.size()));
    };

    while (static_cast<int>(completed.size()) < c.count) {
        Time now = Clock::now();
        std::vector<int> expired;
        for (const auto& [fd, a] : active)
            if (now >= a.deadline) expired.push_back(fd);
        for (int fd : expired) finish(fd, ETIMEDOUT);
        now = Clock::now();
        while (issued < c.count && static_cast<int>(active.size()) < c.concurrency && now >= next_issue) {
            start_one();
            now = Clock::now();
            // Do not burst to catch up after backpressure/concurrency saturation.
            next_issue = std::max(next_issue + std::chrono::milliseconds(c.interval_ms), now);
        }
        if (static_cast<int>(completed.size()) == c.count) break;
        Time wake = now + std::chrono::seconds(1);
        if (issued < c.count && static_cast<int>(active.size()) < c.concurrency)
            wake = std::min(wake, next_issue);
        for (const auto& [fd, a] : active) {
            (void)fd;
            wake = std::min(wake, a.deadline);
        }
        const auto wait_us = std::chrono::duration_cast<std::chrono::microseconds>(wake - Clock::now()).count();
        // Round up, avoiding a hot-spin on a future deadline below 1ms.
        const int wait_ms = wait_us <= 0 ? 0 : static_cast<int>(std::min<long long>(1000LL, (wait_us + 999) / 1000));
        epoll_event events[256]{};
        int ready = ::epoll_wait(epoll.get(), events, 256, wait_ms);
        if (ready < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("epoll_wait failed");
        }
        for (int i = 0; i < ready; ++i) {
            const int fd = events[i].data.fd;
            if (!active.count(fd)) continue;
            int err = 0;
            socklen_t len = sizeof(err);
            if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) < 0) err = errno;
            finish(fd, err);
        }
    }
    const Time end = Clock::now();
    std::sort(completed.begin(), completed.end(), [](const Sample& a, const Sample& b) {
        return a.seq < b.seq;
    });
    std::vector<double> successes;
    for (const auto& s : completed) if (s.ok) successes.push_back(s.connect_ms);
    std::sort(successes.begin(), successes.end());
    if (!c.output.empty()) {
        std::ofstream out(c.output, std::ios::trunc);
        if (!out) throw std::runtime_error("cannot open output file");
        out << std::fixed << std::setprecision(6);
        for (const auto& s : completed)
            out << "{\"seq\":" << s.seq << ",\"ok\":" << (s.ok ? "true" : "false")
                << ",\"error_errno\":" << s.error << ",\"start_monotonic_ns\":" << s.start_ns
                << ",\"end_monotonic_ns\":" << s.finish_ns
                << ",\"connect_ms\":" << s.connect_ms << "}\n";
        if (!out) throw std::runtime_error("failed to write output");
    }
    std::cout << std::fixed << std::setprecision(6)
              << "{\"engine\":\"linux_epoll\",\"measurement\":\"client_tcp_connect_completion\""
              << ",\"target\":\"" << c.host << ':' << c.port << "\",\"attempts\":" << c.count
              << ",\"successes\":" << successes.size()
              << ",\"failures\":" << c.count - successes.size()
              << ",\"concurrency_limit\":" << c.concurrency << ",\"peak_inflight\":" << peak
              << ",\"interval_ms\":" << c.interval_ms << ",\"timeout_ms\":" << c.timeout_ms
              << ",\"elapsed_ms\":" << ms(run_start, end) << ",\"p50_ms\":";
    print_metric(successes, .50);
    std::cout << ",\"p95_ms\":";
    print_metric(successes, .95);
    std::cout << ",\"p99_ms\":";
    print_metric(successes, .99);
    std::cout << ",\"connect_failure_pct\":" << 100.0 * (c.count - successes.size()) / c.count
              << ",\"limitations\":\"client socket connect only; no server response, no wire latency or network convergence\"}\n";
    return 0; // Failed attempts are measurements, not a sampler execution error.
}
} // namespace

int main(int argc, char** argv) {
    try { return run(args(argc, argv)); }
    catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 2;
    }
}
