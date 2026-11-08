// Linux-only matched TCP connect experiments: shared state machine, poll or epoll readiness.
// This measures userspace TCP connect-completion (not packet loss, wire latency or throughput).
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <fstream>
#include <algorithm>
#include <map>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/epoll.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;
struct Fd {
  int fd;
  explicit Fd(int n = -1):fd(n){}
  ~Fd(){if(fd>=0) ::close(fd);}
  Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete;
  Fd(Fd&& o) noexcept:fd(o.fd){o.fd=-1;}
  Fd& operator=(Fd&& o) noexcept{if(this!=&o){if(fd>=0)::close(fd);fd=o.fd;o.fd=-1;}return *this;}
};
struct Sample {int seq, err; Time start, end;};
struct Pending {int seq; Time start, expires; Fd fd;};
long parse(const char* in,long lo,long hi){
  try{std::size_t pos=0;std::string text(in);long x=std::stol(text,&pos);
    if(pos!=text.size()||x<lo||x>hi) throw std::invalid_argument("range");
    return x;
  }catch(const std::exception&){throw std::invalid_argument("invalid numeric parameter");}
}
long long nanosec(Time t){return std::chrono::duration_cast<std::chrono::nanoseconds>(t.time_since_epoch()).count();}
double duration_ms(Time a, Time b){return std::chrono::duration<double,std::milli>(b-a).count();}
double quantile(const std::vector<double>& data,double q){
  const auto position=(data.size()-1)*q;
  auto left=static_cast<std::size_t>(std::floor(position));
  auto right=static_cast<std::size_t>(std::ceil(position));
  return data[left]+(data[right]-data[left])*(position-left);
}
void qprint(const std::vector<double>& data,double q){if(data.empty())std::cout<<"null";else std::cout<<quantile(data,q);}
long long cpu_us(const timeval& x){return static_cast<long long>(x.tv_sec)*1000000LL+x.tv_usec;}
void json_quote(std::ostream& stream,const std::string& s){
  stream<<'"';for(char c:s){if(c=='"'||c=='\\')stream<<'\\';stream<<c;}stream<<'"';
}
int run(int argc,char** argv){
  if(argc!=9)throw std::invalid_argument(
    "usage: connect_reactor_compare poll|epoll IPv4 PORT COUNT CONCURRENCY INTERVAL_MS TIMEOUT_MS SAMPLES.jsonl");
  const std::string backend=argv[1];
  if(backend!="poll"&&backend!="epoll")throw std::invalid_argument("backend must be poll or epoll");
  const std::string host=argv[2];
  const int port=static_cast<int>(parse(argv[3],1,65535));
  const int count=static_cast<int>(parse(argv[4],1,20000));
  const int concurrency=static_cast<int>(parse(argv[5],1,256));
  const int interval=static_cast<int>(parse(argv[6],0,2000));
  const int timeout=static_cast<int>(parse(argv[7],10,10000));
  sockaddr_in dest{};dest.sin_family=AF_INET;dest.sin_port=htons(static_cast<uint16_t>(port));
  if(::inet_pton(AF_INET,host.c_str(),&dest.sin_addr)!=1)
    throw std::invalid_argument("numeric IPv4 required (DNS excluded)");
  std::ofstream raw(argv[8],std::ios::trunc);
  if(!raw)throw std::runtime_error("cannot open samples file");
  Fd epoll(backend=="epoll" ? ::epoll_create1(EPOLL_CLOEXEC) : -1);
  if(backend=="epoll"&&epoll.fd<0)throw std::runtime_error("epoll_create1 failed");
  std::map<int,Pending> inflight;
  std::vector<Sample> samples;samples.reserve(static_cast<std::size_t>(count));
  long wakeups=0, ready_events=0, max_inflight=0, timeouts=0;
  int issued=0;
  rusage before{},after{};
  ::getrusage(RUSAGE_SELF,&before);
  const Time start=Clock::now();
  Time next=start;
  auto record=[&](int id, Time begin, int error){
    samples.push_back({id,error,begin,Clock::now()});
    if(error==ETIMEDOUT)++timeouts;
  };
  auto finish=[&](int fd,int error){
    auto it=inflight.find(fd);if(it==inflight.end())return;
    record(it->second.seq,it->second.start,error);
    if(backend=="epoll")::epoll_ctl(epoll.fd,EPOLL_CTL_DEL,fd,nullptr);
    inflight.erase(it);
  };
  auto launch=[&](){
    auto began=Clock::now();int id=issued++;
    Fd sock(::socket(AF_INET,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0));
    if(sock.fd<0){record(id,began,errno);return;}
    int rc=::connect(sock.fd,reinterpret_cast<const sockaddr*>(&dest),sizeof(dest));
    if(rc==0){record(id,began,0);return;}
    int err=errno;
    if(err!=EINPROGRESS){record(id,began,err);return;}
    if(backend=="epoll"){
      epoll_event ev{};ev.events=EPOLLOUT|EPOLLERR|EPOLLHUP;ev.data.fd=sock.fd;
      if(::epoll_ctl(epoll.fd,EPOLL_CTL_ADD,sock.fd,&ev)<0){record(id,began,errno);return;}
    }
    int fd=sock.fd;
    inflight.emplace(fd,Pending{id,began,began+std::chrono::milliseconds(timeout),std::move(sock)});
    max_inflight=std::max(max_inflight,static_cast<long>(inflight.size()));
  };
  while(static_cast<int>(samples.size())<count){
    auto now=Clock::now();std::vector<int> expired;
    for(const auto& p:inflight)if(now>=p.second.expires)expired.push_back(p.first);
    for(int fd:expired)finish(fd,ETIMEDOUT);
    now=Clock::now();
    while(issued<count&&static_cast<int>(inflight.size())<concurrency&&now>=next){
      launch();now=Clock::now();
      // Matched no-catch-up scheduling: stalled attempts do not create queued bursts.
      next=std::max(next+std::chrono::milliseconds(interval),now);
    }
    if(static_cast<int>(samples.size())>=count)break;
    Time deadline=now+std::chrono::seconds(1);
    if(issued<count&&static_cast<int>(inflight.size())<concurrency)deadline=std::min(deadline,next);
    for(const auto& p:inflight)deadline=std::min(deadline,p.second.expires);
    const auto us=std::chrono::duration_cast<std::chrono::microseconds>(deadline-Clock::now()).count();
    const int wait_ms=us<=0?0:static_cast<int>(std::min<long long>(1000LL,(us+999)/1000));
    std::vector<int> ready;
    int n=0;
    if(backend=="epoll"){
      epoll_event events[256]{};n=::epoll_wait(epoll.fd,events,256,wait_ms);
      if(n>0)for(int i=0;i<n;++i)ready.push_back(events[i].data.fd);
    }else{
      std::vector<pollfd> descriptors;descriptors.reserve(inflight.size());
      for(const auto& p:inflight)descriptors.push_back({p.first,static_cast<short>(POLLOUT|POLLERR|POLLHUP),0});
      // poll with nfds=0 acts as a timed sleep for interval-limited scheduling.
      n=::poll(descriptors.data(),descriptors.size(),wait_ms);
      if(n>0)for(const auto& d:descriptors)if(d.revents)ready.push_back(d.fd);
    }
    ++wakeups;
    if(n<0){if(errno==EINTR)continue;throw std::runtime_error("reactor wait failed");}
    ready_events+=static_cast<long>(ready.size());
    for(int fd:ready){
      if(inflight.find(fd)==inflight.end())continue;
      int err=0;socklen_t len=sizeof(err);
      if(::getsockopt(fd,SOL_SOCKET,SO_ERROR,&err,&len)<0)err=errno;
      finish(fd,err);
    }
  }
  const auto end=Clock::now();
  ::getrusage(RUSAGE_SELF,&after);
  std::sort(samples.begin(),samples.end(),[](const Sample& a,const Sample& b){return a.seq<b.seq;});
  if(samples.size()!=static_cast<std::size_t>(count))throw std::runtime_error("incomplete sampling");
  raw<<std::fixed<<std::setprecision(6);
  std::vector<double> timings;timings.reserve(samples.size());
  std::size_t failures=0;
  for(const auto& s:samples){
    if(s.err==0)timings.push_back(duration_ms(s.start,s.end));else ++failures;
    raw<<"{\"seq\":"<<s.seq<<",\"ok\":"<<(s.err==0?"true":"false")
       <<",\"error_errno\":"<<s.err
       <<",\"start_monotonic_ns\":"<<nanosec(s.start)
       <<",\"end_monotonic_ns\":"<<nanosec(s.end)
       <<",\"connect_ms\":"<<duration_ms(s.start,s.end)<<"}\n";
  }
  raw.flush();if(!raw)throw std::runtime_error("failed writing samples");
  std::sort(timings.begin(),timings.end());
  std::cout<<std::fixed<<std::setprecision(6)<<"{\"engine\":\"matched_connect_reactor\",\"backend\":";
  json_quote(std::cout,backend);
  std::cout<<",\"target\":";json_quote(std::cout,host+":"+std::to_string(port));
  std::cout<<",\"attempts\":"<<count<<",\"successes\":"<<timings.size()
           <<",\"failures\":"<<failures<<",\"timeout_failures\":"<<timeouts
           <<",\"concurrency_limit\":"<<concurrency<<",\"peak_inflight\":"<<max_inflight
           <<",\"interval_ms\":"<<interval<<",\"timeout_ms\":"<<timeout
           <<",\"wakeups\":"<<wakeups<<",\"ready_events\":"<<ready_events
           <<",\"wall_ms\":"<<duration_ms(start,end)
           <<",\"user_cpu_ms\":"<<(cpu_us(after.ru_utime)-cpu_us(before.ru_utime))/1000.0
           <<",\"system_cpu_ms\":"<<(cpu_us(after.ru_stime)-cpu_us(before.ru_stime))/1000.0
           <<",\"voluntary_context_switches\":"<<(after.ru_nvcsw-before.ru_nvcsw)
           <<",\"involuntary_context_switches\":"<<(after.ru_nivcsw-before.ru_nivcsw)
           <<",\"p50_ms\":";
  qprint(timings,.50);std::cout<<",\"p95_ms\":";qprint(timings,.95);
  std::cout<<",\"p99_ms\":";qprint(timings,.99);
  std::cout<<",\"failure_pct\":"<<100.0*failures/count
           <<",\"limitation\":\"client TCP connect only; not wire, HTTP, or exchange latency\"}\n";
  return 0;
}
}
int main(int argc,char** argv){try{return run(argc,argv);}catch(const std::exception& e){
  std::cerr<<"error: "<<e.what()<<'\n';return 2;
}}
