#include "../../cpp/spsc_ring.h"
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <thread>

// Deliberately NOT assert(): CMake Release builds define NDEBUG and must still
// execute every correctness check. Side-effecting queue operations must never
// disappear in an optimized test build.
void require(bool ok, const char* message) {
  if (!ok) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}

int main() {
  SpscRing<uint64_t> ring(64);
  std::atomic<bool> ready{false};
  constexpr uint64_t N=200000;
  uint64_t consumed=0;
  std::thread writer([&]{
    ready.store(true,std::memory_order_release);
    for(uint64_t n=1;n<=N;++n) {
      while(!ring.try_push(n)) std::this_thread::yield();
    }
  });
  while(!ready.load(std::memory_order_acquire)){}
  for(uint64_t n=1;n<=N;++n) {
    uint64_t value=0;
    while(!ring.try_pop(value))std::this_thread::yield();
    require(value==n,"out-of-order SPSC transfer");
    ++consumed;
  }
  writer.join();
  require(consumed==N,"incorrect transfer count");
  require(ring.size_approx()==0,"ring not empty");
  SpscRing<int> backpressure(2);
  require(backpressure.try_push(1),"first push failed");
  require(backpressure.try_push(2),"second push failed");
  require(!backpressure.try_push(3),"full queue accepted third item");
  int a=0;
  require(backpressure.try_pop(a) && a==1,"pop first item failed");
  require(backpressure.try_push(3),"push after drain failed");
  require(backpressure.try_pop(a) && a==2,"pop second item failed");
  require(backpressure.try_pop(a) && a==3,"pop third item failed");
  require(!backpressure.try_pop(a),"empty queue pop succeeded");
  std::cout<<"PASS 200000 ordered concurrent ring transfers + overflow/recovery checks\n";
}
