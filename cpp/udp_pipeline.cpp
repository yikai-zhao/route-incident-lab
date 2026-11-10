#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "spsc_ring.h"
#include <arpa/inet.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <deque>
#include <unordered_set>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <algorithm>
#include <cmath>

using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;
struct Packet {
  uint32_t stream_id;
  uint64_t seq;
  uint64_t received_ns;
};
struct Fd {
  int fd;
  explicit Fd(int value) : fd(value) {}
  ~Fd() { if (fd >= 0) ::close(fd); }
  Fd(const Fd&) = delete;
  Fd& operator=(const Fd&) = delete;
};
uint32_t get_u32(const unsigned char* ptr) {
  uint32_t n; std::memcpy(&n, ptr, 4); return ntohl(n);
}
uint64_t get_u64(const unsigned char* ptr) {
  return (static_cast<uint64_t>(get_u32(ptr)) << 32) | get_u32(ptr + 4);
}
long long cpu_ns(clockid_t clock) {
  timespec ts{};
  if (::clock_gettime(clock, &ts) < 0) throw std::runtime_error("clock_gettime failed");
  return static_cast<long long>(ts.tv_sec)*1000000000LL+ts.tv_nsec;
}
long long monotonic_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
long parse(const char* s, long lo, long hi) {
  try { std::string x(s); size_t used=0; long value=std::stol(x,&used);
    if (used!=x.size() || value<lo || value>hi) throw std::invalid_argument("bounds");
    return value;
  } catch (const std::exception&) { throw std::invalid_argument("invalid number or range"); }
}

double percentile(const std::vector<double>& values, double p) {
  const auto x=(values.size()-1)*p;
  const size_t lo=static_cast<size_t>(std::floor(x)), hi=static_cast<size_t>(std::ceil(x));
  return values[lo]+(values[hi]-values[lo])*(x-lo);
}
struct StreamState {
  uint64_t highest=0;
  bool initialized=false;
  std::deque<uint64_t> recent;
  std::unordered_set<uint64_t> known;
  void remember(uint64_t seq) {
    recent.push_back(seq); known.insert(seq);
    if (recent.size()>4096) {known.erase(recent.front());recent.pop_front();}
  }
};
struct ConsumerStats {
  uint64_t processed=0, first=0, in_order=0, duplicate=0, forward_gap=0, reordering=0;
  uint64_t missing_candidates=0;
  std::vector<double> queue_delay_us;
  std::map<uint32_t,StreamState> streams;
};

int run(int argc, char** argv) {
  if (argc < 8 || argc > 11) throw std::invalid_argument(
    "usage: udp_pipeline GROUP_OR_DASH PORT DATAGRAM_COUNT TIMEOUT_MS CAPACITY CONSUMER_DELAY_US OUTPUT.jsonl [--rx=batch|scalar] [--sink=jsonl|none] [--idle=sleep|spin]");
  bool batch=true, write_jsonl=true, spin_wait=false;
  for (int i=8;i<argc;++i) {
    const std::string flag=argv[i];
    if(flag=="--rx=batch") batch=true;
    else if(flag=="--rx=scalar") batch=false;
    else if(flag=="--sink=jsonl") write_jsonl=true;
    else if(flag=="--sink=none") write_jsonl=false;
    else if(flag=="--idle=sleep") spin_wait=false;
    else if(flag=="--idle=spin") spin_wait=true;
    else throw std::invalid_argument("unknown receive/sink option: "+flag);
  }
  const std::string group=argv[1];
  const int port=static_cast<int>(parse(argv[2],1,65535));
  const size_t target=static_cast<size_t>(parse(argv[3],1,200000));
  const int timeout_ms=static_cast<int>(parse(argv[4],100,60000));
  const size_t capacity=static_cast<size_t>(parse(argv[5],2,65536));
  const int consumer_delay_us=static_cast<int>(parse(argv[6],0,100000));
  SpscRing<Packet> queue(capacity);
  std::ofstream output;
  if (write_jsonl) {
    output.open(argv[7],std::ios::trunc);
    if (!output) throw std::runtime_error("cannot open output");
  }
  Fd socket_fd(::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0));
  if (socket_fd.fd<0) throw std::runtime_error("UDP socket failed");
  int yes=1;
  ::setsockopt(socket_fd.fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
  const bool ts_enabled=::setsockopt(socket_fd.fd,SOL_SOCKET,SO_TIMESTAMPNS,&yes,sizeof(yes)) == 0;
  const bool overflow_enabled=::setsockopt(socket_fd.fd,SOL_SOCKET,SO_RXQ_OVFL,&yes,sizeof(yes)) == 0;
  sockaddr_in bind_to{}; bind_to.sin_family=AF_INET;
  bind_to.sin_port=htons(static_cast<uint16_t>(port));
  bind_to.sin_addr.s_addr=group=="-" ? htonl(INADDR_LOOPBACK) : htonl(INADDR_ANY);
  if (::bind(socket_fd.fd,reinterpret_cast<sockaddr*>(&bind_to),sizeof(bind_to))<0)
    throw std::runtime_error("UDP bind failed");
  if (group!="-") {
    in_addr address{};
    if (::inet_pton(AF_INET,group.c_str(),&address)!=1 || (ntohl(address.s_addr)&0xf0000000u)!=0xe0000000u)
      throw std::invalid_argument("numeric multicast IPv4 group required or - for unicast");
    ip_mreq membership{};
    membership.imr_multiaddr=address;
    ::inet_pton(AF_INET,"127.0.0.1",&membership.imr_interface);
    if (::setsockopt(socket_fd.fd,IPPROTO_IP,IP_ADD_MEMBERSHIP,&membership,sizeof(membership))<0)
      throw std::runtime_error("loopback multicast join failed");
  }
  Fd epoll_fd(::epoll_create1(EPOLL_CLOEXEC));
  if (epoll_fd.fd<0) throw std::runtime_error("epoll create failed");
  epoll_event interest{}; interest.events=EPOLLIN; interest.data.fd=socket_fd.fd;
  if (::epoll_ctl(epoll_fd.fd,EPOLL_CTL_ADD,socket_fd.fd,&interest)<0)
    throw std::runtime_error("epoll add failed");

  std::atomic<bool> completed{false};
  ConsumerStats consumer;
  std::exception_ptr worker_error;
  long long worker_cpu_ns=0;
  std::thread worker([&]{
    try {
      const auto cpu_start=cpu_ns(CLOCK_THREAD_CPUTIME_ID);
      Packet packet{};
      while (!completed.load(std::memory_order_acquire) || queue.size_approx()>0) {
        if (!queue.try_pop(packet)) {
          if (!spin_wait) std::this_thread::sleep_for(std::chrono::microseconds(100));
          continue;
        }
        const auto now=monotonic_ns();
        const double delay=std::max<double>(0,static_cast<double>(now-packet.received_ns)/1000.0);
        consumer.queue_delay_us.push_back(delay);
        std::string kind="first";
        if (consumer.streams.size() >= 64 && !consumer.streams.count(packet.stream_id))
          throw std::runtime_error("more than 64 streams; refusing unbounded stream state");
        auto& state=consumer.streams[packet.stream_id];
        if(!state.initialized) { state.initialized=true;state.highest=packet.seq;
          state.remember(packet.seq);++consumer.first; }
        else if(state.known.count(packet.seq)) { kind="duplicate"; ++consumer.duplicate; }
        else if(packet.seq < state.highest) { kind="out_of_order"; ++consumer.reordering;
          state.remember(packet.seq); }
        else { const uint64_t diff=packet.seq-state.highest;
          state.highest=packet.seq;state.remember(packet.seq);
          if(diff==1) { kind="in_order"; ++consumer.in_order; }
          else { kind="forward_jump"; ++consumer.forward_gap;
            consumer.missing_candidates+=diff-1; }
        }
        if (write_jsonl) {
          output<<"{\"stream_id\":"<<packet.stream_id<<",\"seq\":"<<packet.seq
                <<",\"classification\":\""<<kind<<"\",\"received_monotonic_ns\":"<<packet.received_ns
                <<",\"processed_monotonic_ns\":"<<now<<",\"queue_delay_us\":"<<std::fixed
                <<std::setprecision(3)<<delay<<"}\n";
        }
        ++consumer.processed;
        if (write_jsonl && !output) throw std::runtime_error("consumer JSONL write failed");
        if(consumer_delay_us) std::this_thread::sleep_for(std::chrono::microseconds(consumer_delay_us));
      }
      worker_cpu_ns=cpu_ns(CLOCK_THREAD_CPUTIME_ID)-cpu_start;
    } catch (...) {worker_error=std::current_exception();}
  });

  uint64_t received=0, valid=0, malformed=0, queue_dropped=0;
  size_t queue_highwater=0;
  uint32_t overflow_cumulative=0;
  bool overflow_reported=false;
  uint64_t successful_rx_syscalls=0, rx_datagrams_consumed_by_syscalls=0, first_receive_ns=0, last_receive_ns=0;
  long long first_receive_cpu_ns=0;
  const auto cpu_start=cpu_ns(CLOCK_PROCESS_CPUTIME_ID);
  const auto deadline=Clock::now()+std::chrono::milliseconds(timeout_ms);
  constexpr int RX_BATCH_MAX=16;
  unsigned char data[RX_BATCH_MAX][2048]{};
  alignas(cmsghdr) unsigned char ancillary[RX_BATCH_MAX][256]{};
  sockaddr_in peer[RX_BATCH_MAX]{};
  iovec buffers[RX_BATCH_MAX]{};
  mmsghdr messages[RX_BATCH_MAX]{};
  const int slots=batch ? RX_BATCH_MAX : 1;
  for (int i=0;i<slots;++i) {
    buffers[i].iov_base=data[i]; buffers[i].iov_len=sizeof(data[i]);
    messages[i].msg_hdr.msg_iov=&buffers[i]; messages[i].msg_hdr.msg_iovlen=1;
    messages[i].msg_hdr.msg_name=&peer[i];
    messages[i].msg_hdr.msg_control=ancillary[i];
  }
  try {
    while(received<target && Clock::now()<deadline) {
      const auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
      epoll_event ready{};
      int n=::epoll_wait(epoll_fd.fd,&ready,1,static_cast<int>(std::max<long long>(remaining,0)));
      if(n<0) {if(errno==EINTR)continue;throw std::runtime_error("epoll wait failed");}
      if(n==0)break;
      if(!(ready.events&EPOLLIN))throw std::runtime_error("epoll receive not readable");
      for(int i=0;i<slots;++i) {
        messages[i].msg_hdr.msg_namelen=sizeof(peer[i]);
        messages[i].msg_hdr.msg_controllen=sizeof(ancillary[i]);
        messages[i].msg_hdr.msg_flags=0;
        messages[i].msg_len=0;
      }
      int got=0;
      if (batch) {
        // A batch must not consume more datagrams than the target remainder.
        // Previously, the final recvmmsg could silently drain extra frames.
        const unsigned int vlen = static_cast<unsigned int>(
            std::min<size_t>(static_cast<size_t>(slots), target-received));
        got=::recvmmsg(socket_fd.fd,messages,vlen,MSG_DONTWAIT,nullptr);
      } else {
        const int bytes=::recvmsg(socket_fd.fd,&messages[0].msg_hdr,MSG_DONTWAIT);
        if(bytes>=0) {messages[0].msg_len=static_cast<unsigned int>(bytes);got=1;}
        else got=-1;
      }
      if(got<0){if(errno==EAGAIN||errno==EINTR)continue;throw std::runtime_error("UDP receive failed");}
      if(got>0) {
        ++successful_rx_syscalls;
        rx_datagrams_consumed_by_syscalls += static_cast<uint64_t>(got);
      }
      for(int i=0;i<got;++i){
        ++received;
        const uint64_t received_at=static_cast<uint64_t>(monotonic_ns());
        if(!first_receive_ns) { first_receive_ns=received_at; first_receive_cpu_ns=cpu_ns(CLOCK_PROCESS_CPUTIME_ID); }
        last_receive_ns=received_at;
        const auto& msg=messages[i];
        if ((msg.msg_hdr.msg_flags&(MSG_TRUNC|MSG_CTRUNC)) || msg.msg_len != 16 ||
            get_u32(data[i])!=0x52494c31u) {++malformed;continue;}
        ++valid;
        Packet sample{get_u32(data[i]+4),get_u64(data[i]+8),received_at};
        if(!queue.try_push(sample))++queue_dropped;
        else queue_highwater=std::max(queue_highwater,queue.size_approx());
        for(auto* c=CMSG_FIRSTHDR(const_cast<msghdr*>(&msg.msg_hdr));c;
                 c=CMSG_NXTHDR(const_cast<msghdr*>(&msg.msg_hdr),c)){
          if(c->cmsg_level==SOL_SOCKET && c->cmsg_type==SO_RXQ_OVFL && c->cmsg_len>=CMSG_LEN(sizeof(uint32_t))) {
            std::memcpy(&overflow_cumulative,CMSG_DATA(c),sizeof(uint32_t));overflow_reported=true;
          }
        }
      }
    }
  } catch (...) { completed.store(true,std::memory_order_release);worker.join();throw; }
  completed.store(true,std::memory_order_release);
  worker.join();
  if(worker_error)std::rethrow_exception(worker_error);
  if(write_jsonl) { output.flush();if(!output)throw std::runtime_error("output flush failed"); }
  const auto total_cpu_ns=cpu_ns(CLOCK_PROCESS_CPUTIME_ID)-cpu_start;
  const auto startup_cpu_ns=first_receive_cpu_ns ? first_receive_cpu_ns-cpu_start : total_cpu_ns;
  const auto active_cpu_ns=total_cpu_ns-startup_cpu_ns;
  rusage usage{};
  if (::getrusage(RUSAGE_SELF,&usage)<0) throw std::runtime_error("getrusage failed");
  if (rx_datagrams_consumed_by_syscalls != received)
    throw std::runtime_error("inconsistent syscall-versus-datagram accounting");
  if (valid != consumer.processed+queue_dropped) throw std::runtime_error("inconsistent queue accounting");
  std::sort(consumer.queue_delay_us.begin(),consumer.queue_delay_us.end());
  std::cout<<"{\"engine\":\"spsc_epoll_udp\",\"receive_mode\":\""<<(batch?"batch":"scalar")
           <<"\",\"sink\":\""<<(write_jsonl?"jsonl":"none")
           <<"\",\"consumer_idle_strategy\":\""<<(spin_wait?"spin":"sleep100us")
           <<"\",\"rx_successful_syscalls\":"<<successful_rx_syscalls
           <<",\"rx_datagrams_consumed_by_syscalls\":"<<rx_datagrams_consumed_by_syscalls
           <<",\"rx_datagrams_per_successful_call\":"<<(successful_rx_syscalls ? static_cast<double>(received)/successful_rx_syscalls : 0.)
           <<",\"receiver_active_window_ns\":"<<(first_receive_ns ? last_receive_ns-first_receive_ns : 0)
           <<",\"process_cpu_ns\":"<<total_cpu_ns
           <<",\"process_cpu_startup_ns\":"<<startup_cpu_ns
           <<",\"process_cpu_active_ns\":"<<active_cpu_ns
           <<",\"consumer_thread_cpu_ns\":"<<worker_cpu_ns
           <<",\"voluntary_context_switches\":"<<usage.ru_nvcsw
           <<",\"involuntary_context_switches\":"<<usage.ru_nivcsw
           <<",\"max_rss_kib\":"<<usage.ru_maxrss
           <<",\"received_datagrams\":"<<received
           <<",\"target\":"<<target<<",\"valid_frames\":"<<valid
           <<",\"malformed_frames\":"<<malformed<<",\"processed\":"<<consumer.processed
           <<",\"application_queue_drops\":"<<queue_dropped
           <<",\"ring_capacity\":"<<capacity
           <<",\"queue_depth_highwater\":"<<queue_highwater
           <<",\"consumer_delay_us\":"<<consumer_delay_us
           <<",\"queue_p50_us\":";
  if(consumer.queue_delay_us.empty()) std::cout<<"null";else std::cout<<percentile(consumer.queue_delay_us,.5);
  std::cout<<",\"queue_p99_us\":";
  if(consumer.queue_delay_us.empty()) std::cout<<"null";else std::cout<<percentile(consumer.queue_delay_us,.99);
  std::cout<<",\"streams\":"<<consumer.streams.size()
           <<",\"duplicate_events\":"<<consumer.duplicate
           <<",\"reordered_events\":"<<consumer.reordering
           <<",\"forward_gap_events\":"<<consumer.forward_gap
           <<",\"forward_missing_candidates\":"<<consumer.missing_candidates
           <<",\"kernel_rx_timestamp_enabled\":"<<(ts_enabled?"true":"false")
           <<",\"kernel_rx_overflow_monitor_enabled\":"<<(overflow_enabled?"true":"false")
           <<",\"kernel_rx_overflow_reported\":"<<(overflow_reported?"true":"false")
           <<",\"kernel_rx_overflow_cumulative\":"<<overflow_cumulative
           <<",\"complete\":"<<(received>=target?"true":"false")
           <<",\"limitations\":\"local UDP pipeline; feed sequence gaps are not proven wire loss\"}\n";
  return received>=target?0:1;
}
int main(int argc,char** argv) {
  try{return run(argc,argv);}catch(const std::exception& e){std::cerr<<"error: "<<e.what()<<'\n';return 2;}
}
