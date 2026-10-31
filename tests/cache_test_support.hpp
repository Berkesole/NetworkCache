#pragma once

// Shared helpers for the finite-replica-cache deterministic test suites.
// No Kathara lab, no image, no network: every suite here runs against
// InMemoryCache (and deliberately faulty backends) in-process.

#include "cache/backend_status.hpp"
#include "cache/cache_events.hpp"
#include "cache/in_memory_cache.hpp"
#include "value.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace cache_test {

// ---------------------------------------------------------------------------
// minimal registry-based test harness
// ---------------------------------------------------------------------------
using TestFn = void (*)();

struct Registry {
    static std::vector<std::pair<std::string, TestFn>>& tests() {
        static std::vector<std::pair<std::string, TestFn>> tests;
        return tests;
    }
};

inline bool registerTest(const std::string& name, TestFn fn) {
    Registry::tests().emplace_back(name, fn);
    return true;
}

inline void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

inline int runAll(const std::string& suite) {
    int failed = 0;
    for (const auto& [name, fn] : Registry::tests()) {
        std::cout << "[" << suite << "] " << name << " ... " << std::flush;
        try {
            fn();
            std::cout << "PASS\n";
        } catch (const std::exception& e) {
            std::cout << "FAIL: " << e.what() << "\n";
            ++failed;
        }
    }
    std::cout << "[" << suite << "] " << (Registry::tests().size() - failed) << "/"
              << Registry::tests().size() << " passed\n";
    return failed == 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// value helpers
// ---------------------------------------------------------------------------
inline Value makeValue(const std::string& text) {
    return Value(std::vector<std::uint8_t>(text.begin(), text.end()));
}

inline Value makeValueOfSize(std::size_t bytes, std::uint8_t fill = 'x') {
    return Value(std::vector<std::uint8_t>(bytes, fill));
}

inline std::string valueString(const Value& value) {
    return std::string(value.data().begin(), value.data().end());
}

// ---------------------------------------------------------------------------
// recording event sink
// ---------------------------------------------------------------------------
class RecordingCacheEventSink : public rsync_cache::CacheEventSink {
public:
    struct Event {
        std::string name;
        rsync_cache::FieldList fields;
    };

    void emit(const std::string& event, const rsync_cache::FieldList& fields) override {
        events_.push_back(Event{event, fields});
    }

    const std::vector<Event>& events() const { return events_; }
    void clear() { events_.clear(); }

    std::size_t count(const std::string& name) const {
        std::size_t total = 0;
        for (const auto& event : events_) {
            if (event.name == name) ++total;
        }
        return total;
    }

    std::vector<Event> named(const std::string& name) const {
        std::vector<Event> out;
        for (const auto& event : events_) {
            if (event.name == name) out.push_back(event);
        }
        return out;
    }

    static bool has(const Event& event, const std::string& field) {
        for (const auto& [key, value] : event.fields) {
            if (key == field) return true;
        }
        return false;
    }

    static std::string field(const Event& event, const std::string& field) {
        for (const auto& [key, value] : event.fields) {
            if (key == field) return value;
        }
        throw std::runtime_error("event '" + event.name + "' has no field '" + field + "'");
    }

    static std::uint64_t number(const Event& event, const std::string& field) {
        return std::stoull(field_(event, field));
    }

    // Names of every event emitted, in order. Useful for asserting mutual
    // exclusivity of the admission outcomes.
    std::vector<std::string> names() const {
        std::vector<std::string> out;
        out.reserve(events_.size());
        for (const auto& event : events_) out.push_back(event.name);
        return out;
    }

private:
    static const std::string& field_(const Event& event, const std::string& name) {
        static const std::string missing = "<missing>";
        for (const auto& [key, value] : event.fields) {
            if (key == name) return value;
        }
        return missing;
    }

    std::vector<Event> events_;
};

// ---------------------------------------------------------------------------
// backend that can be told to fail specific operations
//
// Fault semantics (deliberately narrow): a failing put()/del() throws BEFORE the
// backing store is touched, so the failed operation leaves no side effect. This
// models a client-side failure (bad connection, local error) and lets a test
// assert exactly what state a partial failure leaves behind.
//
// It is explicitly NOT a model of a real Redis timeout, where the command may
// have been applied even though the response was lost. Nothing here licenses
// any claim about real backend behaviour under timeout.
// ---------------------------------------------------------------------------

// Identifiable failure thrown by FaultyCache. Tests assert on this exact type
// (and on the operation/key in the message) so "some exception happened" is
// never accepted as evidence that a specific backend error propagated.
class BackendFailure : public std::runtime_error {
public:
    explicit BackendFailure(const std::string& what) : std::runtime_error(what) {}
};

class FaultyCache : public LocalCache {
public:
    explicit FaultyCache(std::size_t fail_after_successes)
        : fail_after_successes_(fail_after_successes) {}

    void failPuts() { fail_put_ = true; }
    void failDeletes() { fail_del_ = true; }
    void failGets() { fail_get_ = true; }
    void failExists() { fail_exists_ = true; }
    // A read that succeeds but finds nothing (no exception). Used to model the
    // old "exists() says present, get() returns nullopt" divergence.
    void nullGets() { null_get_ = true; }

    // Indeterminate ("applied, then response lost") modes. These APPLY the side
    // effect and THEN throw rsync_cache::BackendIndeterminate, modelling a real
    // Redis where the command reached the server but the reply was lost. They
    // deliberately break the old assumptions "delete failed => victim still
    // resident" and "put failed => object absent".
    void applyThenThrowPuts() { indeterminate_put_ = true; }
    void applyThenThrowDeletes() { indeterminate_del_ = true; }
    void applyThenThrowDeleteAtRelative(std::size_t offset) {
        indeterminate_del_at_ = del_calls_ + offset;
    }
    void indeterminateGets() { indeterminate_get_ = true; }
    void applyThenThrowExists() { indeterminate_exists_ = true; }
    void setFailAfterPuts(std::size_t successes) {
        fail_after_put_successes_ = successes;
        fail_put_after_ = true;
    }

    // Relative fault injection: the operation with index `calls_so_far + offset`
    // throws before its side effect. offset 0 is "the next call from now".
    void failPutAtRelative(std::size_t offset) { fail_put_at_ = put_calls_ + offset; }
    void failDeleteAtRelative(std::size_t offset) { fail_del_at_ = del_calls_ + offset; }
    // Same model for reads. It lets a test prove that a specific read was NOT
    // issued: the count of get() calls is asserted directly, and the injected
    // fault at that index must not fire.
    void failGetAtRelative(std::size_t offset) const {
        fail_get_at_ = get_calls_ + offset;
    }

    // Clears injected faults but keeps the call log and the call counters, so
    // relative indices stay meaningful.
    void clearFaults() {
        fail_put_ = fail_del_ = fail_get_ = fail_exists_ = null_get_ = false;
        indeterminate_put_ = indeterminate_del_ = indeterminate_get_ = false;
        indeterminate_exists_ = false;
        indeterminate_del_at_.reset();
        fail_put_after_ = false;
        fail_put_at_.reset();
        fail_del_at_.reset();
        fail_get_at_.reset();
    }

    std::size_t putCalls() const { return put_calls_; }
    std::size_t delCalls() const { return del_calls_; }
    // Number of backend reads issued so far, including the ones that failed.
    std::size_t getCalls() const { return get_calls_; }
    const std::vector<std::string>& calls() const { return calls_; }
    bool sawCall(const std::string& entry) const {
        return std::find(calls_.begin(), calls_.end(), entry) != calls_.end();
    }
    std::size_t countCalls(const std::string& entry) const {
        return static_cast<std::size_t>(
            std::count(calls_.begin(), calls_.end(), entry));
    }

    void put(const std::string& key, const Value& value) override {
        calls_.push_back("put:" + key);
        const std::size_t call_index = put_calls_++;
        if (fail_put_ || (fail_put_at_.has_value() && *fail_put_at_ == call_index)) {
            throw BackendFailure("faulty put (no side effect): " + key);
        }
        if (fail_put_after_ && put_successes_ >= fail_after_put_successes_) {
            throw BackendFailure("faulty put after threshold (no side effect): " + key);
        }
        ++put_successes_;
        inner_.put(key, value);
        if (indeterminate_put_) {
            // The SET was applied; the response was lost. The value IS stored,
            // so the caller must not claim it is absent.
            throw rsync_cache::BackendIndeterminate(
                "set", "faulty set applied then response lost: " + key);
        }
    }
    std::optional<Value> get(const std::string& key) const override {
        calls_.push_back("get:" + key);
        const std::size_t call_index = get_calls_++;
        if (fail_get_ || (fail_get_at_.has_value() && *fail_get_at_ == call_index)) {
            throw BackendFailure("faulty get: " + key);
        }
        if (indeterminate_get_) {
            throw rsync_cache::BackendIndeterminate(
                "get", "faulty get response lost: " + key);
        }
        if (null_get_) return std::nullopt;
        return inner_.get(key);
    }
    void del(const std::string& key) override {
        calls_.push_back("del:" + key);
        const std::size_t call_index = del_calls_++;
        if (fail_del_ || (fail_del_at_.has_value() && *fail_del_at_ == call_index)) {
            throw BackendFailure("faulty del (no side effect): " + key);
        }
        inner_.del(key);
        if (indeterminate_del_ ||
            (indeterminate_del_at_.has_value() && *indeterminate_del_at_ == call_index)) {
            // The DEL was applied; the response was lost. The value is gone, so
            // the caller must not claim the victim is still resident.
            throw rsync_cache::BackendIndeterminate(
                "del", "faulty del applied then response lost: " + key);
        }
    }
    bool exists(const std::string& key) const override {
        calls_.push_back("exists:" + key);
        if (fail_exists_) {
            throw BackendFailure("faulty exists: " + key);
        }
        if (indeterminate_exists_) {
            throw rsync_cache::BackendIndeterminate(
                "exists", "faulty exists response lost: " + key);
        }
        return inner_.exists(key);
    }
    void flush() override { inner_.flush(); }

    // Direct access for divergence tests: mutate the KV behind the cache's back.
    void eraseDirect(const std::string& physical_key) { inner_.del(physical_key); }
    bool hasPhysical(const std::string& physical_key) const {
        return inner_.exists(physical_key);
    }

private:
    std::size_t fail_after_successes_ = 0;
    bool fail_put_ = false;
    bool fail_del_ = false;
    bool fail_get_ = false;
    bool fail_exists_ = false;
    bool null_get_ = false;
    bool indeterminate_put_ = false;
    bool indeterminate_del_ = false;
    bool indeterminate_get_ = false;
    bool indeterminate_exists_ = false;
    std::optional<std::size_t> indeterminate_del_at_;
    bool fail_put_after_ = false;
    std::size_t fail_after_put_successes_ = 0;
    std::size_t put_successes_ = 0;
    std::optional<std::size_t> fail_put_at_;
    std::optional<std::size_t> fail_del_at_;
    // get() is const (LocalCache::get is const), so the read log, the read
    // counter and its relative fault index must be mutable.
    mutable std::optional<std::size_t> fail_get_at_;
    std::size_t put_calls_ = 0;
    std::size_t del_calls_ = 0;
    mutable std::size_t get_calls_ = 0;
    mutable std::vector<std::string> calls_;
    InMemoryCache inner_;
};

}  // namespace cache_test

#define CACHE_TEST(name)                                                       \
    void test_##name();                                                        \
    namespace {                                                                \
    const bool cache_test_reg_##name = cache_test::registerTest(#name, test_##name); \
    }                                                                          \
    void test_##name()

#define CACHE_REQUIRE(condition, message) cache_test::require((condition), (message))