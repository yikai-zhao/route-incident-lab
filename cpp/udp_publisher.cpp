// Local-only UDP load source. All pacing and measurement use CLOCK_MONOTONIC.
// Successful sendto() does NOT mean a UDP datagram reached the receiver.
#include <arpa/inet.h>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <iostream>
#include <algorithm>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace {
long number(const char* text, long low, long high) {
  std::string s(text); std::size_t used = 0;
  long value = 0;
  try { value = std::stol(s, &used); }
  catch (const std::exception&) { throw std::invalid_argument("invalid integer"); }
  if (used != s.size() || value < low || value > high)
    throw std::invalid_argument("integer out of range");
  return value;
}
uint64_t monotonic_ns(clockid_t clock = CLOCK_MONOTONIC) {
  timespec ts{};
  if (::clock_gettime(clock, &ts) != 0) throw std::runtime_error("clock_gettime failed");
  return uint64_t(ts.tv_sec) * 1000000000ULL + uint64_t(ts.tv_nsec);
}
timespec from_ns(uint64_t ns) {
  return {static_cast<time_t>(ns / 1000000000ULL),
          static_cast<long>(ns % 1000000000ULL)};
}
void wait_until(uint64_t deadline) {
  const auto when = from_ns(deadline);
  int code;
  do { code = ::clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &when, nullptr); }
  while (code == EINTR);
  if (code != 0) throw std::runtime_error("clock_nanosleep failed");
}
void put32(unsigned char* out, uint32_t value) {
  value = htonl(value); std::memcpy(out, &value, 4);
}
void put64(unsigned char* out, uint64_t value) {
  put32(out, static_cast<uint32_t>(value >> 32));
  put32(out + 4, static_cast<uint32_t>(value));
}
double quantile(std::vector<double> values, double probability) {
  if (values.empty()) return 0.;
  std::sort(values.begin(), values.end());
  const double position = (values.size()-1) * probability;
  const auto lo = static_cast<size_t>(std::floor(position));
  const auto hi = static_cast<size_t>(std::ceil(position));
  return values[lo] + (values[hi]-values[lo]) * (position-lo);
}
struct Socket {
  int fd;
  explicit Socket(int value): fd(value) {}
  ~Socket() { if (fd >= 0) ::close(fd); }
  Socket(const Socket&) = delete;
  Socket& operator=(const Socket&) = delete;
};
}

int main(int argc, char** argv) {
  try {
    if (argc < 4 || argc > 5)
      throw std::invalid_argument("usage: udp_publisher PORT COUNT RATE_PPS [STREAM_ID]; 0 pps means unpaced; loopback-only");
    const int port = static_cast<int>(number(argv[1], 1, 65535));
    const uint64_t count = number(argv[2], 1, 200000);
    const uint64_t rate = number(argv[3], 0, 500000);
    const uint32_t stream = argc == 5 ? static_cast<uint32_t>(number(argv[4], 0, 65535)) : 7;
    Socket sock(::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0));
    if (sock.fd < 0) throw std::runtime_error("socket failed");
    sockaddr_in address{}; address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(static_cast<uint16_t>(port));
    unsigned char frame[16]{};
    put32(frame, 0x52494c31); put32(frame+4, stream);
    std::vector<double> late_us, spacing_us;
    late_us.reserve(count); spacing_us.reserve(count > 1 ? count-1 : 0);
    uint64_t successful = 0, errors = 0, previous = 0, first = 0, last = 0;
    const auto cpu_start = monotonic_ns(CLOCK_PROCESS_CPUTIME_ID);
    const auto start = monotonic_ns() + 5000000ULL; // fixed, measured 5ms readiness period
    for (uint64_t i = 0; i < count; ++i) {
      const uint64_t deadline = rate ? start + (i * 1000000000ULL) / rate : start;
      if (rate) wait_until(deadline);
      put64(frame + 8, i + 1);
      const ssize_t n = ::sendto(sock.fd, frame, sizeof(frame), 0,
                                  reinterpret_cast<const sockaddr*>(&address), sizeof(address));
      const auto sent_at = monotonic_ns();
      if (n != static_cast<ssize_t>(sizeof(frame))) { ++errors; continue; }
      ++successful;
      if (!first) first = sent_at;
      if (previous) spacing_us.push_back((sent_at - previous) / 1000.0);
      previous = sent_at; last = sent_at;
      if (rate) late_us.push_back(std::max(0.0, (double(sent_at) - double(deadline))/1000.0));
    }
    const auto cpu_ns = monotonic_ns(CLOCK_PROCESS_CPUTIME_ID) - cpu_start;
    const auto elapsed_ns = first && last > first ? last - first : 0;
    const auto observed_rate = elapsed_ns ? (successful-1) * 1e9 / elapsed_ns : 0.;
    std::cout << "{\"engine\":\"udp_publisher_clock_nanosleep\",\"destination\":\"127.0.0.1\","
              << "\"target_pps\":" << rate << ",\"attempted\":" << count
              << ",\"successful_sendto\":" << successful << ",\"send_errors\":" << errors
              << ",\"achieved_sendto_pps\":" << observed_rate
              << ",\"first_to_last_send_ns\":" << elapsed_ns
              << ",\"sender_cpu_ns\":" << cpu_ns
              << ",\"send_spacing_p50_us\":" << quantile(spacing_us, .5)
              << ",\"send_spacing_p99_us\":" << quantile(spacing_us, .99)
              << ",\"send_lateness_p50_us\":" << (rate ? quantile(late_us,.5) : 0.)
              << ",\"send_lateness_p99_us\":" << (rate ? quantile(late_us,.99) : 0.)
              << ",\"note\":\"sendto success is not receiver delivery; pacing is scheduler-limited\"}\n";
    return errors ? 1 : 0;
  } catch (const std::exception& e) {
    std::cerr << "error: " << e.what() << '\n';
    return 2;
  }
}
