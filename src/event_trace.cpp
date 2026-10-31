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
#include <vector>

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

// Field names every record has carried since the frozen capacity/slides
// campaigns. They are always emitted, in this order, so existing key-based
// consumers are unaffected. Fields supplied by a caller that are not in this
// list are appended verbatim after them; that is how the finite-replica-cache
// contract adds tier / lru_touched / resident_bytes / budget_bytes without
// maintaining a second whitelist.
const std::vector<std::string>& legacyFieldOrder() {
    static const std::vector<std::string> fields = {
        "request_id", "key", "src", "dest", "packet_type", "hop_count", "via",
        "tick", "value_bytes", "phase", "reason", "neighbors",
        "signature_count", "deadline_ms", "frame_bytes", "fallback"};
    return fields;
}

// Trace schema version. Bumped when the record layout changes; consumers can
// read it instead of guessing from the presence of keys.
std::string schema_version_from_env() {
    const std::string configured = getenv_string("RSYNC_EVENT_TRACE_SCHEMA_VERSION");
    return configured.empty() ? std::string("2") : configured;
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
          schema_version_(schema_version_from_env()),
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

        std::unordered_map<std::string, std::string> values;
        for (const auto& name : legacyFieldOrder()) {
            values.emplace(name, std::string());
        }
        std::vector<std::pair<std::string, std::string>> extras;
        std::unordered_map<std::string, std::size_t> extra_positions;
        for (const auto& [key, value] : fields) {
            const auto known = values.find(key);
            if (known != values.end()) {
                known->second = value;
                continue;
            }
            const auto seen = extra_positions.find(key);
            if (seen != extra_positions.end()) {
                extras[seen->second].second = value;
                continue;
            }
            extra_positions.emplace(key, extras.size());
            extras.emplace_back(key, value);
        }

        std::lock_guard<std::mutex> lock(mutex_);
        stream_ << "{\"ts_epoch_ns\":" << epoch_ns
                << ",\"ts_mono_ns\":" << mono_ns
                << ",\"run_id\":\"" << json_escape(run_id_) << "\""
                << ",\"node\":" << node_id
                << ",\"event\":\"" << json_escape(event) << "\""
                << ",\"schema_version\":\"" << json_escape(schema_version_) << "\"";

        for (const auto& key : legacyFieldOrder()) {
            stream_ << ",\"" << key << "\":\"" << json_escape(values[key]) << "\"";
        }
        for (const auto& [key, value] : extras) {
            stream_ << ",\"" << json_escape(key) << "\":\"" << json_escape(value) << "\"";
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
    std::string schema_version_;
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
