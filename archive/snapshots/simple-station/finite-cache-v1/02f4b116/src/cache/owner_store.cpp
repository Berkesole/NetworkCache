#include "owner_store.hpp"

#include <stdexcept>
#include <string>
#include <utility>

namespace rsync_cache {

OwnerStore::OwnerStore(LocalCache* backend, bool namespaced, CacheEventSink* sink)
    : backend_(backend), namespaced_(namespaced), sink_(sink) {}

void OwnerStore::requireUsable() const {
    if (unavailable_) {
        throw CacheUnavailable(
            "owner store is unavailable after an indeterminate backend result; "
            "the process must stop instead of seeding or reading from unknown state");
    }
}

void OwnerStore::seed(const std::string& logical_key, const Value& value) {
    requireUsable();
    if (sealed_) {
        throw std::logic_error(
            "OwnerStore::seed() called after seal(): owner objects are written "
            "once during initialization and are read-only afterwards");
    }
    const auto existing = owners_.find(logical_key);
    if (existing != owners_.end()) {
        const std::size_t previous_size = sizes_.at(logical_key);
        bool conflict = previous_size != value.size();
        if (!conflict && verify_content_) {
            // Re-seed content comparison reads the backend; an indeterminate
            // read means the comparison (and therefore the conflict verdict)
            // cannot be trusted, so poison the store and rethrow rather than
            // deciding.
            try {
                const auto current = backend_->get(physicalKeyFor(logical_key));
                conflict = !current.has_value() || current->data() != value.data();
            } catch (const BackendIndeterminate&) {
                unavailable_ = true;
                throw;
            }
        }
        if (conflict) {
            if (sink_ != nullptr) {
                sink_->emit(event::kCacheKeyConflict,
                            {{"schema_version", kCacheSchemaVersion},
                             {"key", logical_key},
                             {"namespace", cacheNamespaceName(
                                  namespaced_ ? CacheNamespace::Owner
                                              : CacheNamespace::Legacy)},
                             {"value_bytes", std::to_string(value.size())},
                             {"resident_bytes", std::to_string(previous_size)},
                             {"reason", "owner_content_mismatch"}});
            }
            throw std::runtime_error(
                "OwnerStore: owner object '" + logical_key +
                "' seeded twice with different content (" +
                std::to_string(previous_size) + " vs " +
                std::to_string(value.size()) + " bytes)");
        }
        return;  // identical re-seed: idempotent, no double counting
    }

    try {
        backend_->put(physicalKeyFor(logical_key), value);
    } catch (const BackendIndeterminate&) {
        // The owner object may or may not exist in the backend. Do not add it
        // to the index, poison the store and rethrow the typed failure.
        unavailable_ = true;
        throw;
    }
    owners_.insert(logical_key);
    sizes_.emplace(logical_key, value.size());
    bytes_ += value.size();
}

std::optional<Value> OwnerStore::get(const std::string& logical_key) const {
    requireUsable();
    if (owners_.find(logical_key) == owners_.end()) {
        return std::nullopt;
    }
    try {
        return backend_->get(physicalKeyFor(logical_key));
    } catch (const BackendIndeterminate&) {
        unavailable_ = true;
        throw;
    }
}

}  // namespace rsync_cache