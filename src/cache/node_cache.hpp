#pragma once

#include "cache.hpp"
#include "cache_events.hpp"
#include "key_namespace.hpp"
#include "owner_store.hpp"
#include "replica_cache.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace rsync_cache {

// Cache policy selected by RSYNC_CACHE_POLICY.
//
//   Legacy     -- default. One flat key space, no prefixes, no budget, no LRU,
//                 no cache events. Byte-for-byte the behaviour the frozen
//                 capacity/slides campaigns ran, so an unchanged configuration
//                 does not change behaviour.
//   Bypass     -- owner/replica namespaces, replica admission disabled.
//   Lru        -- owner/replica namespaces plus the byte-budget LRU.
//   Unlimited  -- same interfaces and accounting as Lru with no budget, so a
//                 finite group has a fair paired control on the same code path.
enum class CachePolicy { Legacy, Bypass, Lru, Unlimited };

struct CachePolicyConfig {
    CachePolicy policy = CachePolicy::Legacy;
    std::size_t budget_bytes = 0;
    bool namespaced = true;
    bool verify_replica_content = false;
    std::size_t gauge_interval_ms = 100;

    // Reads RSYNC_CACHE_POLICY, RSYNC_REPLICA_BUDGET_BYTES,
    // RSYNC_REPLICA_BUDGET_OBJECTS, RSYNC_REPLICA_BUDGET_OBJECT_BYTES,
    // RSYNC_CACHE_VERIFY_REPLICA_CONTENT and RSYNC_CACHE_GAUGE_INTERVAL_MS.
    // Throws std::runtime_error on an unparseable value: a mistyped budget must
    // fail the run at startup, never fall back to a silent default.
    static CachePolicyConfig fromEnv();

    const char* policyName() const;
    bool legacy() const { return policy == CachePolicy::Legacy; }
    bool budgetUnlimited() const { return policy == CachePolicy::Unlimited; }
};

enum class CacheTier { Owner, Replica, Miss, Unknown };

const char* cacheTierName(CacheTier tier);

// Request identity available at the call site. Fields that cannot be
// correlated uniquely are left empty on purpose: the contract emits an explicit
// empty string rather than inventing one request id.
struct CacheEventContext {
    std::string request_id;
    std::string origin_query_uid;
    std::string app_request_id;
    std::string phase;
    std::string source_node;
};

struct LookupResult {
    std::optional<Value> value;
    CacheTier tier = CacheTier::Miss;
    bool lru_touched = false;
    bool divergence = false;
};

struct GaugeSnapshot {
    std::size_t owner_objects = 0;
    std::size_t owner_bytes = 0;
    std::size_t replica_objects = 0;
    std::size_t replica_bytes = 0;
    std::size_t budget_bytes = 0;
    bool unlimited = false;
    std::uint64_t admits = 0;
    std::uint64_t evictions = 0;
    std::uint64_t evicted_bytes = 0;
    std::uint64_t bypasses_budget_zero = 0;
    std::uint64_t bypasses_too_large = 0;
    std::uint64_t skipped_already_resident = 0;
    std::uint64_t conflicts = 0;
    std::uint64_t backend_errors = 0;
    std::uint64_t serves = 0;
    std::uint64_t touches = 0;
};

// ---------------------------------------------------------------------------
// Immutable-object integrity (finite-cache modes: lru / bypass / unlimited)
// ---------------------------------------------------------------------------
//
// In the finite-cache modes one logical key denotes an immutable object:
// a DATA packet for a key this node can locally verify must carry exactly the
// same bytes as the trusted local copy. Different size or different bytes is a
// conflict.
//
// What can be verified locally, and what cannot:
//
//   A. this node has the owner object   -> exact byte comparison
//   B. this node has the key resident    -> exact byte comparison
//   C. neither                           -> NOT verifiable. The packet is the
//      first copy this node sees; its correctness cannot be judged from the
//      logical key and value alone. This is a declared boundary, not a
//      guarantee. There is no global object manifest and no key->hash history.
//   D. the replica was already evicted   -> NOT verifiable. No historical
//      fingerprint is retained (that would need an unbounded key->hash table);
//      a conflict with a past, since-evicted copy cannot be detected.
//
// Comparison is exact byte equality on the stored value, not a hash, so there
// is no collision boundary to argue about. The application-layer expected
// size/hash check is unchanged and still the authority for delivery.
enum class IntegrityVerdict {
    NotApplicable,  // no trustworthy local reference (boundary C/D)
    Verified,       // a local owner/replica holds byte-identical content
    Conflict,       // same key, different size or different bytes (throws)
    Unavailable,    // the local reference exists but could not be read
};

struct IntegrityCheck {
    IntegrityVerdict verdict = IntegrityVerdict::NotApplicable;
    CacheTier tier = CacheTier::Miss;
    std::size_t incoming_bytes = 0;
    std::size_t existing_bytes = 0;
    // "size_mismatch" | "content_mismatch" | "read_failed" | "" (not applicable)
    std::string reason;
};

// Thrown when incoming DATA contradicts an immutable local object. This is an
// unrecoverable correctness error: the packet must not be cached, forwarded or
// delivered, and the run must stop rather than continue with a corrupted view.
class ImmutabilityViolation : public std::runtime_error {
public:
    ImmutabilityViolation(std::string logical_key, std::string reason,
                          std::string tier, std::size_t incoming_bytes,
                          std::size_t existing_bytes)
        : std::runtime_error("immutable object conflict for key '" + logical_key +
                             "' (" + reason + ", tier=" + tier + ", incoming=" +
                             std::to_string(incoming_bytes) + "B, existing=" +
                             std::to_string(existing_bytes) + "B)"),
          logical_key_(std::move(logical_key)),
          reason_(std::move(reason)),
          tier_(std::move(tier)),
          incoming_bytes_(incoming_bytes),
          existing_bytes_(existing_bytes) {}

    const std::string& logicalKey() const { return logical_key_; }
    const std::string& reason() const { return reason_; }
    const std::string& tier() const { return tier_; }
    std::size_t incomingBytes() const { return incoming_bytes_; }
    std::size_t existingBytes() const { return existing_bytes_; }

private:
    std::string logical_key_;
    std::string reason_;
    std::string tier_;
    std::size_t incoming_bytes_ = 0;
    std::size_t existing_bytes_ = 0;
};

// The single cache facade the Node uses. It owns the backend, the owner index,
// the replica LRU and the serviceable-key directory, so the directory cannot
// disagree with what the store can actually serve:
//
//     directory == owner keys  U  resident replica keys
//
// Replica eviction removes the directory entry; owner entries are never
// removed. Cache lifecycle events are emitted here so the admission decision
// and the emitted event cannot disagree.
class NodeCache {
public:
    NodeCache(std::unique_ptr<LocalCache> backend, CachePolicyConfig config,
              CacheEventSink* sink = nullptr);

    LocalCache* backend() const { return backend_.get(); }
    const CachePolicyConfig& config() const { return config_; }
    void setEventSink(CacheEventSink* sink) { sink_ = sink; }
    bool legacy() const { return config_.policy == CachePolicy::Legacy; }
    // Effective namespace usage. Legacy always writes bare logical keys, even if
    // a config object happens to carry namespaced=true, so an unchanged
    // configuration keeps the frozen flat key space.
    bool namespaced() const {
        return config_.policy != CachePolicy::Legacy && config_.namespaced;
    }
    // False once an indeterminate backend result has poisoned the facade. Every
    // serving/admission entry point then throws CacheUnavailable; diagnostics
    // (gauge, directory) stay readable for the fatal record.
    bool available() const { return !unavailable_; }

    // ---- owner lifecycle -------------------------------------------------
    void seedOwner(const std::string& logical_key, const Value& value);
    // Idempotent. Called explicitly after initialization; lookups and
    // admissions also seal lazily so an unsealed store cannot accept owner
    // writes on the serving path.
    void sealOwners();
    bool ownersSealed() const { return owner_store_.sealed(); }
    bool isOwner(const std::string& logical_key) const {
        return owner_store_.contains(logical_key);
    }

    // ---- service reads ---------------------------------------------------
    // Owner first, then replica. Only a replica that is really read refreshes
    // recency. Never emits a hit event: Node emits provider_hit /
    // local_cache_hit with tier and lru_touched from this result.
    LookupResult lookupForService(const std::string& logical_key,
                                  const CacheEventContext& context = {});

    // No recency change. In Legacy mode this is the backend probe the old code
    // used (hasLocalData); in the tiered modes it is the directory.
    bool exists(const std::string& logical_key) const;

    CacheTier tierOf(const std::string& logical_key) const;

    // ---- immutability ----------------------------------------------------
    // Must be called BEFORE any protocol state is consumed and before any cache
    // mutation, for every DATA packet in a finite-cache mode. Reads the trusted
    // local reference (owner first, then resident replica) and compares bytes
    // exactly. Never touches replica recency, never admits, never evicts, never
    // creates a redundant owner replica, and never repairs divergence.
    //
    // Emits exactly one cache_key_conflict and then throws ImmutabilityViolation
    // when the value contradicts the local object, so a caller cannot silently
    // ignore a conflict. Returns:
    //   Verified      -- byte-identical local copy exists; the caller may treat
    //                    this packet as a normal duplicate;
    //   NotApplicable -- nothing locally verifiable (boundary C/D); the packet
    //                    is the first copy and is accepted as before;
    //   Unavailable   -- the local reference could not be read; a
    //                    cache_backend_error was emitted and the caller must NOT
    //                    deliver, forward or cache this packet as verified data.
    IntegrityCheck checkImmutability(const std::string& logical_key, const Value& value,
                                     const CacheEventContext& context = {});

    // ---- replica admission (DATA arrival) --------------------------------
    // Emits exactly one of replica_admit / replica_bypass /
    // data_admit_skipped / cache_key_conflict / cache_backend_error, plus one
    // replica_evict per victim. A duplicate DATA arrival for a resident key
    // does not refresh recency.
    AdmitResult admitReplica(const std::string& logical_key, const Value& value,
                             const CacheEventContext& context = {});

    // ---- directory -------------------------------------------------------
    const std::unordered_set<std::string>& serviceableKeys() const {
        return directory_;
    }
    // Applies deferred directory repairs. A replica whose backing value has
    // disappeared is removed from the LRU index immediately (so it can never be
    // served) but only removed from the directory here, because the directory
    // may be under iteration by handleRequest(). Safe to call at any time and a
    // no-op when nothing is pending.
    void maintain();

    // ---- observability ---------------------------------------------------
    GaugeSnapshot gauge() const;
    void emitConfigEvent() const;
    void emitGaugeEvent() const;
    // Emits cache_gauge when gauge_interval_ms has elapsed. Cheap: counters
    // only, no backend round trip.
    void maybeEmitGauge();
    void emitBackendError(const std::string& logical_key, const std::string& reason,
                          const std::string& detail, bool fatal,
                          const CacheEventContext& context = {},
                          BackendErrorCategory category =
                              BackendErrorCategory::PreSideEffect) const;
    // Exactly one cache_key_conflict for a confirmed conflict: logical key,
    // owner/replica tier, incoming and existing sizes, reason and the request
    // identity available at the call site. No value bytes are logged.
    void emitKeyConflict(const std::string& logical_key, const IntegrityCheck& check,
                         const CacheEventContext& context) const;

private:
    FieldList commonFields(const std::string& logical_key,
                           const CacheEventContext& context) const;
    void emit(const std::string& event, FieldList fields) const;
    // Poison the facade after an indeterminate backend result and refuse every
    // later serving/admission call. The state must not be guessed at. Const so
    // that the const entry point exists() can poison too.
    void markUnavailable() const { unavailable_ = true; }
    void requireUsable() const;
    void emitEvictions(const AdmitResult& result, const std::string& logical_key,
                       const CacheEventContext& context) const;
    // Finalizes evictions that really happened, whatever the admission outcome:
    // drops the victims from the serviceable directory (never an owner entry)
    // and emits exactly one replica_evict per victim, in deletion order, using
    // the per-victim snapshot ReplicaCache captured when each delete completed.
    // Never rolls back a delete that already succeeded.
    void finalizeEvictions(const AdmitResult& result, const std::string& logical_key,
                           const CacheEventContext& context);

    std::unique_ptr<LocalCache> backend_;
    CachePolicyConfig config_;
    CacheEventSink* sink_ = nullptr;
    OwnerStore owner_store_;
    ReplicaCache replica_cache_;
    std::unordered_set<std::string> directory_;
    std::vector<std::string> pending_repairs_;
    std::uint64_t last_gauge_mono_ns_ = 0;
    bool gauge_emitted_ = false;
    // Set when any backend result could not be confirmed. Guards every
    // serving/admission entry point. Mutable so const entry points can poison.
    mutable bool unavailable_ = false;

    // Single-use proof that checkImmutability() read the resident replica under
    // `key` and found it byte-identical to `content`.
    //
    // It exists only to remove one redundant backend read inside a single DATA
    // handling: NodeCache's caller checks immutability first (that read is
    // mandatory and must complete before any protocol state is consumed), and
    // ReplicaCache::admit() would otherwise read the same resident object again
    // -- after state was already consumed. The proof is:
    //   * private and never caller-supplied, so there is no passable
    //     "verified=true" that could skip verification;
    //   * bound to the exact logical key and the exact verified bytes, so a
    //     receipt from one key/value cannot be applied to another;
    //   * consumed and cleared at the start of every admitReplica() call, so it
    //     can never outlive the DATA handling that produced it.
    // It is not a fingerprint table and holds no history.
    struct ResidentReplicaVerification {
        std::string key;
        Value content;
    };
    std::optional<ResidentReplicaVerification> resident_verification_;
};

}  // namespace rsync_cache