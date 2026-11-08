#pragma once
// Single producer / single consumer, bounded ring. Exactly one producer owns
// tail, exactly one consumer owns head. Stores values without allocation on push.
#include <atomic>
#include <cstddef>
#include <stdexcept>
#include <vector>

template <class T>
class SpscRing {
 public:
  explicit SpscRing(size_t capacity) : slots_(capacity), capacity_(capacity) {
    if (capacity < 2 || capacity > 65536 || (capacity & (capacity - 1)))
      throw std::invalid_argument("ring capacity must be power-of-two in [2,65536]");
  }
  SpscRing(const SpscRing&) = delete;
  SpscRing& operator=(const SpscRing&) = delete;

  bool try_push(const T& value) noexcept {
    const size_t tail = tail_.load(std::memory_order_relaxed);
    if (tail - head_.load(std::memory_order_acquire) == capacity_) return false;
    slots_[tail & (capacity_ - 1)] = value;
    tail_.store(tail + 1, std::memory_order_release);
    return true;
  }
  bool try_pop(T& output) noexcept {
    const size_t head = head_.load(std::memory_order_relaxed);
    if (head == tail_.load(std::memory_order_acquire)) return false;
    output = slots_[head & (capacity_ - 1)];
    head_.store(head + 1, std::memory_order_release);
    return true;
  }
  size_t size_approx() const noexcept {
    return tail_.load(std::memory_order_acquire) - head_.load(std::memory_order_acquire);
  }
  size_t capacity() const noexcept { return capacity_; }
 private:
  std::vector<T> slots_;
  size_t capacity_;
  alignas(64) std::atomic<size_t> head_{0};
  alignas(64) std::atomic<size_t> tail_{0};
};
