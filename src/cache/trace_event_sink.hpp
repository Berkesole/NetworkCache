#pragma once

#include "cache_events.hpp"

#include <cstdint>
#include <memory>

namespace rsync_cache {

// Adapter that forwards cache lifecycle events into the structured event trace
// (rsync_trace). Kept in its own translation unit so the cache core has no
// dependency on the trace writer and unit tests can use a recording sink.
std::unique_ptr<CacheEventSink> makeTraceCacheEventSink(std::uint32_t node_id);

}  // namespace rsync_cache