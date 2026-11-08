#include "../../cpp/spsc_ring.h"
#include <atomic>
#include <cassert>
#include <iostream>
#include <thread>

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
    assert(value==n);
    ++consumed;
  }
  writer.join();
  assert(consumed==N);
  assert(ring.size_approx()==0);
  SpscRing<int> backpressure(2);
  assert(backpressure.try_push(1));
  assert(backpressure.try_push(2));
  assert(!backpressure.try_push(3));
  int a=0;
  assert(backpressure.try_pop(a) && a==1);
  assert(backpressure.try_push(3));
  assert(backpressure.try_pop(a) && a==2);
  assert(backpressure.try_pop(a) && a==3);
  assert(!backpressure.try_pop(a));
  std::cout<<"PASS 200000 ordered concurrent ring transfers + overflow/recovery checks\n";
}
