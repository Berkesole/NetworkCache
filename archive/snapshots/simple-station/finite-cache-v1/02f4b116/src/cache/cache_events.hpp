#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace rsync_cache {

// Same shape as rsync_trace::FieldList, repeated here so the cache layer does
// not have to include the trace writer (tests inject a recording sink instead
// of writing files).
using FieldList = std::vector<std::pair<std::string, std::string>>;

// Cache lifecycle event names. These are the structured-trace contract of the
// finite-replica-cache version. Every name below is also mirrored by the
// writer whitelist in src/event_trace.cpp; a name that is not in that whitelist
// would be dropped silently, so event_contract tests assert the whitelist.
namespace event {
inline constexpr const char* kCacheConfig = "cache_config";
inline constexpr const char* kReplicaAdmit = "replica_admit";
inline constexpr const char* kReplicaEvict = "replica_evict";
inline constexpr const char* kReplicaBypass = "replica_bypass";
inline constexpr const char* kDataAdmitSkipped = "data_admit_skipped";
inline constexpr const char* kCacheBackendError = "cache_backend_error";
inline constexpr const char* kCacheGauge = "cache_gauge";
inline constexpr const char* kCacheKeyConflict = "cache_key_conflict";
}  // namespace event

inline constexpr const char* kCacheSchemaVersion = "2";

// Cache event contract revision: the semantics of the cache_* events,
// independent of the trace record layout version above.
//
//   1 (implicit) -- producers before this revision emitted no marker. Their
//                   replica_evict resident_bytes/resident_objects were derived
//                   by adding victims back onto the final resident state, so
//                   they are NOT the post-deletion running state and must not be
//                   reinterpreted with the revision-2 meaning.
//   2            -- replica_evict carries resident_bytes/resident_objects
//                   snapshotted immediately after that victim's successful
//                   delete, plus a real evicted_mono_ns. The marker is emitted
//                   on cache_config (once per run) and on every replica_evict,
//                   so a consumer can classify each event instead of guessing.
//   3            -- cache_key_conflict is now emitted for every confirmed
//                   immutable-object conflict on the DATA path (not only for an
//                   admission attempt), it carries tier + incoming_bytes +
//                   existing_bytes, and its reason values are size_mismatch /
//                   content_mismatch (revision 2 used replica_size_mismatch /
//                   replica_content_mismatch and had no tier field). cache_config
//                   additionally declares integrity_verification.
//   4            -- cache_backend_error carries an explicit `category`
//                   (pre_side_effect | indeterminate | divergence). Before this
//                   revision every backend error implicitly meant "the operation
//                   did not happen"; revision 4 introduces the indeterminate
//                   class, in which the operation may already have been applied
//                   and the store becomes unusable, so the two must not be read
//                   with the same meaning.
inline constexpr const char* kCacheContractRevision = "4";

// Wall-clock-free timestamp source. Steady clock matches rsync_trace's
// ts_mono_ns, so last_touch_mono_ns / inserted_mono_ns / evicted_mono_ns in
// replica_evict are directly comparable with the trace timestamps of the same
// run.
inline std::uint64_t monoNsNow() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

class CacheEventSink {
public:
    virtual ~CacheEventSink() = default;
    virtual void emit(const std::string& event, const FieldList& fields) = 0;
};

}  // namespace rsync_cache