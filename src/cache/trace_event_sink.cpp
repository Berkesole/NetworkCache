#include "trace_event_sink.hpp"

#include "event_trace.hpp"

#include <utility>

namespace rsync_cache {
namespace {

class TraceCacheEventSink : public CacheEventSink {
public:
    explicit TraceCacheEventSink(std::uint32_t node_id) : node_id_(node_id) {}

    void emit(const std::string& event, const FieldList& fields) override {
        rsync_trace::record(node_id_, event, fields);
    }

private:
    std::uint32_t node_id_;
};

}  // namespace

std::unique_ptr<CacheEventSink> makeTraceCacheEventSink(std::uint32_t node_id) {
    return std::make_unique<TraceCacheEventSink>(node_id);
}

}  // namespace rsync_cache