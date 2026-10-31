#include "event_trace.hpp"

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <limits>
#include <sstream>
#include <string>
#include <unordered_map>

namespace rsync_trace {
namespace {

bool truthy_env(const char* value) {
    if (value == nullptr) {
        return false;
    }
    const std::string text(value);
    return !text.empty() && text != "0" && text != "false" && text != "FALSE";
}

std::string getenv_string(const char* name) {
    const char* value = std::getenv(name);
    return value == nullptr ? std::string() : std::string(value);
}

std::size_t flush_every_from_env() {
    const std::string text = getenv_string("RSYNC_EVENT_TRACE_FLUSH_EVERY");
    if (text.empty()) {
        return 64;
    }
    try {
        const auto value = std::stoull(text);
        if (value == 0 || value > std::numeric_limits<std::size_t>::max()) {
            return 64;
        }
        return static_cast<std::size_t>(value);
    } catch (...) {
        return 64;
    }
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (unsigned char ch : value) {
        switch (ch) {
            case '"':
                out << "\\\"";
                break;
            case '\\':
                out << "\\\\";
                break;
            case '\b':
                out << "\\b";
                break;
            case '\f':
                out << "\\f";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (ch < 0x20) {
                    constexpr char hex[] = "0123456789abcdef";
                    out << "\\u00" << hex[(ch >> 4) & 0x0f] << hex[ch & 0x0f];
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

uint64_t epoch_ns_now() {
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

uint64_t mono_ns_now() {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

class Recorder {
public:
    Recorder()
        : enabled_(truthy_env(std::getenv("RSYNC_EVENT_TRACE")) ||
                   !getenv_string("RSYNC_EVENT_TRACE_PATH").empty()),
          path_(getenv_string("RSYNC_EVENT_TRACE_PATH")),
          run_id_(getenv_string("RSYNC_EVENT_TRACE_RUN_ID")),
          flush_every_(flush_every_from_env()) {
        if (!enabled_ || path_.empty()) {
            enabled_ = false;
            return;
        }

        try {
            const auto parent = std::filesystem::path(path_).parent_path();
            if (!parent.empty()) {
                std::filesystem::create_directories(parent);
            }
            stream_.open(path_, std::ios::out | std::ios::app);
            enabled_ = stream_.is_open();
        } catch (...) {
            enabled_ = false;
        }
    }

    bool enabled() const {
        return enabled_;
    }

    ~Recorder() {
        flush();
    }

    void write(uint32_t node_id, const std::string& event, const FieldList& fields) {
        if (!enabled_) {
            return;
        }

        const uint64_t epoch_ns = epoch_ns_now();
        const uint64_t mono_ns = mono_ns_now();

        std::unordered_map<std::string, std::string> values = {
            {"request_id", ""},
            {"key", ""},
            {"src", ""},
            {"dest", ""},
            {"packet_type", ""},
            {"hop_count", ""},
            {"via", ""},
            {"tick", ""},
            {"value_bytes", ""},
            {"phase", ""},
            {"reason", ""},
            {"neighbors", ""},
            {"signature_count", ""},
            {"deadline_ms", ""},
            {"frame_bytes", ""},
            {"fallback", ""},
        };
        for (const auto& [key, value] : fields) {
            values[key] = value;
        }

        std::lock_guard<std::mutex> lock(mutex_);
        stream_ << "{\"ts_epoch_ns\":" << epoch_ns
                << ",\"ts_mono_ns\":" << mono_ns
                << ",\"run_id\":\"" << json_escape(run_id_) << "\""
                << ",\"node\":" << node_id
                << ",\"event\":\"" << json_escape(event) << "\"";

        for (const auto& key : {
                 "request_id", "key", "src", "dest", "packet_type",
                 "hop_count", "via", "tick", "value_bytes", "phase",
                 "reason", "neighbors", "signature_count", "deadline_ms",
                 "frame_bytes", "fallback"}) {
            stream_ << ",\"" << key << "\":\"" << json_escape(values[key]) << "\"";
        }

        stream_ << "}\n";
        ++pending_records_;
        if (pending_records_ >= flush_every_) {
            stream_.flush();
            pending_records_ = 0;
        }
    }

    void flush() {
        if (!enabled_) {
            return;
        }
        std::lock_guard<std::mutex> lock(mutex_);
        stream_.flush();
        pending_records_ = 0;
    }

private:
    bool enabled_ = false;
    std::string path_;
    std::string run_id_;
    std::ofstream stream_;
    std::mutex mutex_;
    std::size_t flush_every_ = 64;
    std::size_t pending_records_ = 0;
};

Recorder& recorder() {
    static Recorder instance;
    return instance;
}

}  // namespace

bool enabled() {
    return recorder().enabled();
}

void record(uint32_t node_id, const std::string& event, const FieldList& fields) {
    recorder().write(node_id, event, fields);
}

void flush() {
    recorder().flush();
}

}  // namespace rsync_trace
