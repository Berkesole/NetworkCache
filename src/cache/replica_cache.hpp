#pragma once

#include "cache.hpp"
#include "key_namespace.hpp"

#include <cstddef>
#include <cstdint>
#include <list>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace rsync_cache {

// Replica admission policy.
//
//   Bypass     -- never admit a replica. Forwards and delivers normally.
//   Lru        -- admit while the replicated value bytes fit budget_bytes.
//   Unlimited  -- admit everything, no budget, but with the same interfaces and
//                 accounting as Lru so a fair paired comparison is possible.
//
// budget_bytes == 0 with Lru is the same as Bypass, but it is reported with its
// own reason so "budget 0" and "policy bypass" are distinguishable in traces.
// budget 0 must never produce a replica_evict event: nothing was ever resident,
// so nothing can be evicted.
enum class ReplicaPolicy { Bypass, Lru, Unlimited };

const char* replicaPolicyName(ReplicaPolicy policy);
bool parseReplicaPolicy(const std::string& text, ReplicaPolicy& out);

struct ReplicaVictim {
    std::string key;                     // logical key
    std::size_t value_bytes = 0;         // size of THIS victim, not of the new object
    std::uint64_t inserted_mono_ns = 0;
    std::uint64_t last_touch_mono_ns = 0;
    std::uint64_t insert_seq = 0;        // per-store access ordinal
    std::uint64_t touch_seq = 0;         // per-store access ordinal

    // Snapshot taken in evictTail() at the moment THIS victim had been
    // successfully deleted and resident_bytes_ / index_ were updated -- i.e.
    // "after this victim was removed, before any new object is inserted".
    // ReplicaCache only: owner objects are not counted here and never evictable.
    //
    // It is a captured value, not a value derived later from the final state, so
    // it is correct regardless of whether the follow-up insert succeeds, of how
    // many victims there are, and of whether their sizes differ. When several
    // victims are removed in one admission, victim i's snapshot still includes
    // the victims deleted after it (they have not been deleted yet at that
    // point), which is exactly the running state of the eviction sequence.
    std::size_t resident_bytes_after = 0;
    std::size_t resident_objects_after = 0;
    // Capture point of the backend delete completion bookkeeping for this victim
    // (not the event write time, and not a network-level DEL completion time).
    std::uint64_t evicted_mono_ns = 0;
};

enum class AdmitStatus {
    Admitted,
    AlreadyResident,       // same key already resident: no touch, no double count
    BypassedBudgetZero,    // policy bypass, or Lru with budget_bytes == 0
    BypassedTooLarge,      // a single value larger than the whole budget
    Conflict,              // same key, different size(always) or content(opt-in)
    BackendError,          // a PRE-SIDE-EFFECT failure: state is known unchanged
    Indeterminate,         // backend could not confirm the operation; state UNKNOWN
};

struct AdmitResult {
    AdmitStatus status = AdmitStatus::Admitted;
    std::size_t value_bytes = 0;
    std::size_t resident_bytes_after = 0;
    std::size_t resident_objects_after = 0;
    std::vector<ReplicaVictim> victims;
    std::string error;
    std::string failing_op;              // "put" | "evict_delete" | "get"
    std::size_t existing_bytes = 0;      // Conflict detail
    // Key of the victim whose delete result could not be confirmed. It is NOT
    // in victims (which holds only confirmed evictions) and must not be
    // advertised as resident either.
    std::string indeterminate_key;

    bool inserted() const { return status == AdmitStatus::Admitted; }
    bool conflict() const { return status == AdmitStatus::Conflict; }
    bool backend_error() const { return status == AdmitStatus::BackendError; }
    bool indeterminate() const { return status == AdmitStatus::Indeterminate; }
};

// Thrown by ReplicaCache::admit()/use()/peek() when a backend result cannot be
// confirmed. It carries the admission result built so far, so evictions that
// were ALREADY confirmed before the uncertain operation are not lost: the facade
// still finalizes and records them. The exception type itself is what stops the
// run; a caller cannot mistake it for an ordinary backend error.
class IndeterminateAdmission : public BackendIndeterminate {
public:
    IndeterminateAdmission(std::string operation, std::string detail, AdmitResult result)
        : BackendIndeterminate(std::move(operation), std::move(detail)),
          result_(std::move(result)) {}

    const AdmitResult& result() const { return result_; }

private:
    AdmitResult result_;
};

struct UseResult {
    std::optional<Value> value;
    bool touched = false;      // a resident replica was really read and served
    bool divergence = false;   // index said resident, backend had no value
    bool repaired = false;     // divergence was repaired by dropping the index entry
    bool backend_error = false;
    std::string error;
};

// Application-level byte-budget LRU over a LocalCache backend.
//
// Recency rules (design section 5):
//   * a successful admission becomes MRU;
//   * only a real service read (use()) refreshes recency;
//   * resident()/contains()/diagnostics never refresh recency;
//   * receiving duplicate DATA or merely checking "already cached" never
//     refreshes recency (admit() on a resident key is a no-op);
//   * one service read produces at most one touch.
//
// Byte accounting follows the real value length, never the nominal object size.
// Only replica value bytes are counted: owner objects, Redis metadata, the LRU
// structure itself and in-flight DATA are accounted separately (cache_gauge).
class ReplicaCache {
public:
    ReplicaCache(LocalCache* backend, ReplicaPolicy policy, std::size_t budget_bytes,
                 bool namespaced = true, bool verify_content = false);

    AdmitResult admit(const std::string& logical_key, const Value& value);
    UseResult use(const std::string& logical_key);

    // No recency change. `resident` consults the LRU index; `physicalExists`
    // consults the backend and is only used for divergence diagnostics.
    bool resident(const std::string& logical_key) const {
        return index_.find(logical_key) != index_.end();
    }
    bool physicalExists(const std::string& logical_key) const;

    // Size of a resident replica as recorded at admission, without touching
    // recency. nullopt when the key is not resident.
    std::optional<std::size_t> residentSize(const std::string& logical_key) const;

    // Non-touching read used only for immutability verification: returns the
    // stored bytes without changing recency, counters, the index or the
    // directory, and without repairing divergence. It is const on purpose, so a
    // verification can never be mistaken for a service read.
    struct PeekResult {
        std::optional<Value> value;
        bool backend_error = false;
        std::string error;
    };
    PeekResult peek(const std::string& logical_key) const;

    std::size_t residentBytes() const { return resident_bytes_; }
    std::size_t residentObjects() const { return index_.size(); }
    std::size_t budgetBytes() const { return budget_bytes_; }
    ReplicaPolicy policy() const { return policy_; }
    bool namespaced() const { return namespaced_; }
    bool verifyContent() const { return verify_content_; }
    void setVerifyContent(bool value) { verify_content_ = value; }
    // False once an indeterminate backend result poisoned this store. Serving
    // and admission will then throw CacheUnavailable.
    bool available() const { return !unavailable_; }

    // Diagnostics only: never touches recency.
    std::vector<std::string> residentKeysLruOrder() const;
    const std::unordered_map<std::string, std::uint64_t>& lastTouchTimes() const {
        return last_touch_;
    }

    std::uint64_t admits() const { return admits_; }
    std::uint64_t evictions() const { return evictions_; }
    std::uint64_t bypassesBudgetZero() const { return bypasses_budget_zero_; }
    std::uint64_t bypassesTooLarge() const { return bypasses_too_large_; }
    std::uint64_t skippedAlreadyResident() const { return skipped_already_resident_; }
    std::uint64_t conflicts() const { return conflicts_; }
    std::uint64_t backendErrors() const { return backend_errors_; }
    std::uint64_t serves() const { return serves_; }
    std::uint64_t touches() const { return touches_; }
    std::uint64_t evictedBytes() const { return evicted_bytes_; }
    std::uint64_t divergencesRepaired() const { return divergences_repaired_; }
    void resetRepairCounters() { divergences_repaired_ = 0; }

    std::string physicalKeyFor(const std::string& logical_key) const {
        return physicalKey(namespaced_ ? CacheNamespace::Replica : CacheNamespace::Legacy,
                           logical_key);
    }

private:
    struct Entry {
        std::size_t bytes = 0;
        std::uint64_t inserted_mono_ns = 0;
        std::uint64_t last_touch_mono_ns = 0;
        std::uint64_t insert_seq = 0;
        std::uint64_t touch_seq = 0;
        std::list<std::string>::iterator pos;
    };

    // Removes the LRU tail entry.
    //
    //   Evicted       -- the delete was confirmed; entry removed and counted.
    //   NotApplied    -- the delete failed BEFORE any side effect; the entry is
    //                    kept and no eviction is counted (state is known).
    //   Indeterminate -- the delete may or may not have been applied; the entry
    //                    is left untouched, the eviction is NOT counted, and the
    //                    store is poisoned. The old code called this case
    //                    "delete failed, victim still resident", which is exactly
    //                    the assumption a lost Redis response breaks.
    enum class EvictOutcome { Evicted, NotApplied, Indeterminate };
    EvictOutcome evictTail(ReplicaVictim& victim_out);

    // Once an indeterminate result has poisoned the store, every serving and
    // admission entry point throws CacheUnavailable instead of guessing at the
    // state. Diagnostics (gauge, LRU order) stay readable.
    void poison() { unavailable_ = true; }
    void requireUsable() const;

    // Shared implementation of the admission policy.
    //
    // `resident_content_verified` is true only when the caller has ALREADY read
    // the resident replica under this exact logical key and compared it
    // byte-for-byte with this exact incoming value (NodeCache's DATA path does
    // that in checkImmutability()). It suppresses the otherwise redundant second
    // backend read in the already-resident same-size branch; every other
    // decision (size conflict, budget, eviction, put failure, counters,
    // recency) is unchanged. It is not a general "trust me" switch: the only
    // caller is admitWithVerifiedResident() below, which is private and
    // reachable only through the friend NodeCache, and NodeCache reaches it only
    // with a single-use proof bound to the same key and bytes.
    AdmitResult admitInternal(const std::string& logical_key, const Value& value,
                              bool resident_content_verified);
    AdmitResult admitWithVerifiedResident(const std::string& logical_key,
                                          const Value& value);

    friend class NodeCache;

    LocalCache* backend_;
    ReplicaPolicy policy_;
    std::size_t budget_bytes_;
    bool namespaced_;
    bool verify_content_;
    // peek() is const (a verification must never look like a service read), so
    // the poison flag is mutable. It only ever moves false -> true.
    mutable bool unavailable_ = false;

    std::list<std::string> lru_;  // front = MRU, back = LRU
    std::unordered_map<std::string, Entry> index_;
    std::unordered_map<std::string, std::uint64_t> last_touch_;
    std::size_t resident_bytes_ = 0;
    std::uint64_t access_seq_ = 0;

    std::uint64_t admits_ = 0;
    std::uint64_t evictions_ = 0;
    std::uint64_t evicted_bytes_ = 0;
    std::uint64_t bypasses_budget_zero_ = 0;
    std::uint64_t bypasses_too_large_ = 0;
    std::uint64_t skipped_already_resident_ = 0;
    std::uint64_t conflicts_ = 0;
    std::uint64_t backend_errors_ = 0;
    std::uint64_t serves_ = 0;
    std::uint64_t touches_ = 0;
    std::uint64_t divergences_repaired_ = 0;
};

}  // namespace rsync_cache