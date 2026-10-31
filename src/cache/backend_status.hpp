#pragma once

#include <stdexcept>
#include <string>
#include <utility>

namespace rsync_cache {

// How a backend failure relates to the stored state. The distinction is the
// whole point of this header: a real Redis client cannot always tell whether a
// command reached the server before the response was lost, so a thrown
// exception must never be silently read as "the operation did not happen".
//
//   PreSideEffect -- the backend threw before applying anything. A deterministic
//                    test backend proves this; the stored state is known.
//   Indeterminate -- the backend could not confirm whether the operation was
//                    applied (connection reset, socket timeout, response lost).
//                    The stored state is UNKNOWN: it is neither applied nor
//                    not-applied, and no further cache decision may be based on
//                    it.
//   Divergence    -- the cache detected that its index and the backend disagree
//                    (an index entry with no backing value) without an
//                    operation failure.
enum class BackendErrorCategory {
    PreSideEffect,
    Indeterminate,
    Divergence,
};

inline const char* backendErrorCategoryName(BackendErrorCategory category) {
    switch (category) {
        case BackendErrorCategory::PreSideEffect: return "pre_side_effect";
        case BackendErrorCategory::Indeterminate: return "indeterminate";
        case BackendErrorCategory::Divergence: return "divergence";
    }
    return "unknown";
}

// Thrown by a LocalCache implementation when it cannot confirm whether an
// operation reached and was applied by the server. Carrying the operation name
// (set/get/del/exists) keeps the diagnostic actionable, and the type is what
// lets the cache layer classify the failure instead of guessing.
class BackendIndeterminate : public std::runtime_error {
public:
    BackendIndeterminate(std::string operation, std::string detail)
        : std::runtime_error(std::move(detail)), operation_(std::move(operation)) {}

    const std::string& operation() const { return operation_; }

private:
    std::string operation_;
};

// Thrown by every cache entry point once a store has been poisoned by an
// indeterminate backend result. This is deliberately not a boolean a caller can
// forget: any further serving or admission attempt throws, so a process cannot
// quietly keep producing data from a store whose state is unknown.
class CacheUnavailable : public std::runtime_error {
public:
    explicit CacheUnavailable(std::string detail) : std::runtime_error(std::move(detail)) {}
};

}  // namespace rsync_cache