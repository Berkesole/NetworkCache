#include "node_cache.hpp"

#include <cstdlib>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace rsync_cache {
namespace {

bool effectiveNamespaced(const CachePolicyConfig& config) {
    return config.policy != CachePolicy::Legacy && config.namespaced;
}

std::optional<std::string> readEnv(const char* name) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || raw[0] == '\0') {
        return std::nullopt;
    }
    return std::string(raw);
}

bool envEnabled(const char* name) {
    const auto value = readEnv(name);
    return value.has_value() && *value != "0" && *value != "false" && *value != "FALSE";
}

std::uint64_t parseU64(const char* name, const std::string& text) {
    if (text.empty()) {
        throw std::runtime_error(std::string("invalid ") + name + "=''");
    }
    std::size_t consumed = 0;
    unsigned long long parsed = 0;
    try {
        parsed = std::stoull(text, &consumed, 10);
    } catch (const std::exception&) {
        throw std::runtime_error(std::string("invalid ") + name + "='" + text + "'");
    }
    if (consumed != text.size()) {
        throw std::runtime_error(std::string("invalid ") + name + "='" + text + "'");
    }
    return static_cast<std::uint64_t>(parsed);
}

bool parsePolicy(const std::string& text, CachePolicy& out) {
    if (text.empty() || text == "legacy") {
        out = CachePolicy::Legacy;
        return true;
    }
    if (text == "bypass") {
        out = CachePolicy::Bypass;
        return true;
    }
    if (text == "lru") {
        out = CachePolicy::Lru;
        return true;
    }
    if (text == "unlimited") {
        out = CachePolicy::Unlimited;
        return true;
    }
    return false;
}

std::string envOr(const char* name, const char* fallback) {
    const auto value = readEnv(name);
    return value.has_value() ? *value : std::string(fallback);
}

}  // namespace

const char* cacheTierName(CacheTier tier) {
    switch (tier) {
        case CacheTier::Owner: return "owner";
        case CacheTier::Replica: return "replica";
        case CacheTier::Miss: return "miss";
        case CacheTier::Unknown: return "unknown";
    }
    return "unknown";
}

const char* CachePolicyConfig::policyName() const {
    switch (policy) {
        case CachePolicy::Legacy: return "legacy";
        case CachePolicy::Bypass: return "bypass";
        case CachePolicy::Lru: return "lru";
        case CachePolicy::Unlimited: return "unlimited";
    }
    return "unknown";
}

CachePolicyConfig CachePolicyConfig::fromEnv() {
    CachePolicyConfig config;
    const std::string policy_text = readEnv("RSYNC_CACHE_POLICY").value_or("legacy");
    if (!parsePolicy(policy_text, config.policy)) {
        throw std::runtime_error(
            "invalid RSYNC_CACHE_POLICY='" + policy_text +
            "' (expected legacy|bypass|lru|unlimited)");
    }

    const auto bytes_env = readEnv("RSYNC_REPLICA_BUDGET_BYTES");
    const auto objects_env = readEnv("RSYNC_REPLICA_BUDGET_OBJECTS");
    if (bytes_env.has_value() && objects_env.has_value()) {
        throw std::runtime_error(
            "set only one of RSYNC_REPLICA_BUDGET_BYTES / RSYNC_REPLICA_BUDGET_OBJECTS");
    }

    if (config.policy == CachePolicy::Legacy) {
        // Legacy has no budget concept at all; accepting one would silently
        // change nothing and mislead the operator.
        if (bytes_env.has_value() || objects_env.has_value()) {
            throw std::runtime_error(
                "RSYNC_REPLICA_BUDGET_* requires RSYNC_CACHE_POLICY=lru|unlimited|bypass");
        }
    } else if (bytes_env.has_value()) {
        config.budget_bytes = static_cast<std::size_t>(
            parseU64("RSYNC_REPLICA_BUDGET_BYTES", *bytes_env));
    } else if (objects_env.has_value()) {
        const std::uint64_t objects =
            parseU64("RSYNC_REPLICA_BUDGET_OBJECTS", *objects_env);
        const std::uint64_t object_bytes = parseU64(
            "RSYNC_REPLICA_BUDGET_OBJECT_BYTES",
            readEnv("RSYNC_REPLICA_BUDGET_OBJECT_BYTES").value_or("65536"));
        if (object_bytes == 0) {
            throw std::runtime_error("RSYNC_REPLICA_BUDGET_OBJECT_BYTES must be > 0");
        }
        if (objects > std::numeric_limits<std::size_t>::max() / object_bytes) {
            throw std::runtime_error("RSYNC_REPLICA_BUDGET_OBJECTS overflows bytes");
        }
        config.budget_bytes = static_cast<std::size_t>(objects * object_bytes);
    } else if (config.policy == CachePolicy::Lru) {
        throw std::runtime_error(
            "RSYNC_CACHE_POLICY=lru requires RSYNC_REPLICA_BUDGET_BYTES or "
            "RSYNC_REPLICA_BUDGET_OBJECTS");
    }

    // Immutability verification is not optional in the finite-cache modes: the
    // facade compares bytes exactly for every locally verifiable DATA packet, so
    // there is no switch that can silently turn a same-size content conflict
    // into an "already resident" skip. Legacy keeps its historical behaviour
    // (its compatibility fix is a separate item) and so keeps the historical
    // flag meaning.
    if (config.policy == CachePolicy::Legacy) {
        config.verify_replica_content = envEnabled("RSYNC_CACHE_VERIFY_REPLICA_CONTENT");
    } else {
        if (readEnv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT").has_value() &&
            !envEnabled("RSYNC_CACHE_VERIFY_REPLICA_CONTENT")) {
            throw std::runtime_error(
                "RSYNC_CACHE_VERIFY_REPLICA_CONTENT cannot disable immutability "
                "verification in finite-cache modes (lru|bypass|unlimited)");
        }
        config.verify_replica_content = true;
    }
    if (const auto interval = readEnv("RSYNC_CACHE_GAUGE_INTERVAL_MS")) {
        config.gauge_interval_ms = static_cast<std::size_t>(
            parseU64("RSYNC_CACHE_GAUGE_INTERVAL_MS", *interval));
    }
    return config;
}

NodeCache::NodeCache(std::unique_ptr<LocalCache> backend, CachePolicyConfig config,
                     CacheEventSink* sink)
    : backend_(std::move(backend)),
      config_(config),
      sink_(sink),
      owner_store_(backend_.get(), effectiveNamespaced(config), sink),
      replica_cache_(backend_.get(),
                     config.policy == CachePolicy::Lru
                         ? ReplicaPolicy::Lru
                         : (config.policy == CachePolicy::Unlimited
                                ? ReplicaPolicy::Unlimited
                                : ReplicaPolicy::Bypass),
                     config.budget_bytes,
                     effectiveNamespaced(config),
                     config.verify_replica_content) {}

void NodeCache::emit(const std::string& event, FieldList fields) const {
    if (sink_ == nullptr || config_.legacy()) {
        return;  // Legacy must not add new events to frozen trace semantics.
    }
    sink_->emit(event, fields);
}

void NodeCache::requireUsable() const {
    if (unavailable_) {
        throw CacheUnavailable(
            "cache facade is unavailable after an indeterminate backend result; "
            "the process must stop instead of serving or admitting from unknown state");
    }
}

FieldList NodeCache::commonFields(const std::string& logical_key,
                                  const CacheEventContext& context) const {
    return FieldList{
        {"key", logical_key},
        {"request_id", context.request_id},
        {"origin_query_uid", context.origin_query_uid},
        {"app_request_id", context.app_request_id},
        {"phase", context.phase},
        {"source_node", context.source_node},
        {"budget_mode", config_.policyName()},
        {"budget_bytes", std::to_string(config_.budget_bytes)},
        {"cache_policy_version", "finite-cache-v1"},
    };
}

void NodeCache::seedOwner(const std::string& logical_key, const Value& value) {
    requireUsable();
    try {
        owner_store_.seed(logical_key, value);
    } catch (const BackendIndeterminate& e) {
        // The owner object may or may not have been written. Record a fatal
        // backend error, poison the facade and rethrow the typed failure.
        markUnavailable();
        emitBackendError(logical_key,
                         e.operation() == "get" ? "owner_get_indeterminate"
                                                : "owner_put_indeterminate",
                         e.what(), true, {}, BackendErrorCategory::Indeterminate);
        throw;
    }
    directory_.insert(logical_key);
}

void NodeCache::sealOwners() {
    if (owner_store_.sealed()) {
        return;
    }
    owner_store_.seal();
    last_gauge_mono_ns_ = monoNsNow();
}

void NodeCache::maintain() {
    if (pending_repairs_.empty()) {
        return;
    }
    for (const auto& key : pending_repairs_) {
        if (!owner_store_.contains(key)) {
            directory_.erase(key);  // never remove an owner entry
        }
    }
    pending_repairs_.clear();
}

bool NodeCache::exists(const std::string& logical_key) const {
    requireUsable();
    if (config_.legacy()) {
        try {
            return backend_->exists(logical_key);
        } catch (const BackendIndeterminate& e) {
            // The existence result is unknown, not "absent". Poison the facade
            // and rethrow; legacy still emits no cache event by contract.
            markUnavailable();
            emitBackendError(logical_key, "backend_exists_indeterminate", e.what(), true,
                             {}, BackendErrorCategory::Indeterminate);
            throw;
        }
    }
    return owner_store_.contains(logical_key) || replica_cache_.resident(logical_key);
}

CacheTier NodeCache::tierOf(const std::string& logical_key) const {
    if (config_.legacy()) {
        return CacheTier::Unknown;
    }
    if (owner_store_.contains(logical_key)) {
        return CacheTier::Owner;
    }
    if (replica_cache_.resident(logical_key)) {
        return CacheTier::Replica;
    }
    return CacheTier::Miss;
}

LookupResult NodeCache::lookupForService(const std::string& logical_key,
                                         const CacheEventContext& context) {
    requireUsable();
    LookupResult result;
    if (config_.legacy()) {
        // Legacy keeps the original Node behaviour: the backend read is not
        // guarded here. The old code called cache_->get() directly, so a
        // failing get() propagated unchanged; it was never turned into a
        // cache miss. The caller decides how to report the failure. An
        // INDETERMINATE failure additionally poisons the facade.
        result.tier = CacheTier::Unknown;
        try {
            result.value = backend_->get(logical_key);
        } catch (const BackendIndeterminate& e) {
            markUnavailable();
            emitBackendError(logical_key, "backend_get_indeterminate", e.what(), true,
                             context, BackendErrorCategory::Indeterminate);
            throw;
        }
        return result;
    }

    if (owner_store_.contains(logical_key)) {
        std::optional<Value> value;
        try {
            value = owner_store_.get(logical_key);
        } catch (const BackendIndeterminate& e) {
            markUnavailable();
            emitBackendError(logical_key, "owner_get_indeterminate", e.what(), true,
                             context, BackendErrorCategory::Indeterminate);
            throw;
        } catch (const std::exception& e) {
            emitBackendError(logical_key, "owner_get_failed", e.what(), true, context,
                             BackendErrorCategory::PreSideEffect);
            throw std::runtime_error("NodeCache: owner read failed for '" + logical_key +
                                     "': " + e.what());
        }
        if (!value.has_value()) {
            emitBackendError(logical_key, "owner_kv_missing",
                             "owner index entry has no backing value", true, context,
                             BackendErrorCategory::Divergence);
            throw std::runtime_error(
                "NodeCache: owner object missing from the cache backend: '" +
                logical_key + "'");
        }
        result.value = std::move(value);
        result.tier = CacheTier::Owner;  // owner reads never touch replica recency
        return result;
    }

    if (replica_cache_.resident(logical_key)) {
        UseResult used;
        try {
            used = replica_cache_.use(logical_key);
        } catch (const BackendIndeterminate& e) {
            // A read result we cannot confirm must not become an ordinary miss
            // (the caller would then fetch remotely and count that work as
            // normal cache behaviour). Poison and stop.
            markUnavailable();
            emitBackendError(logical_key, "replica_get_indeterminate", e.what(), true,
                             context, BackendErrorCategory::Indeterminate);
            throw;
        }
        if (used.backend_error) {
            emitBackendError(logical_key, "replica_get_failed", used.error, false, context,
                             BackendErrorCategory::PreSideEffect);
            return result;
        }
        if (used.divergence) {
            // The LRU index entry is already gone, so this key can no longer be
            // served. The directory entry is repaired in maintain(), because
            // handleRequest() may be iterating the directory right now.
            pending_repairs_.push_back(logical_key);
            emitBackendError(logical_key, "replica_kv_missing", used.error, false, context,
                             BackendErrorCategory::Divergence);
            result.divergence = true;
            return result;
        }
        if (used.value.has_value()) {
            result.value = std::move(used.value);
            result.tier = CacheTier::Replica;
            result.lru_touched = used.touched;
        }
    }
    return result;
}

// ReplicaCache deletes LRU victims BEFORE it attempts to store the new object,
// so a partially successful admission is possible: every entry in
// result.victims has really been removed from storage and from the LRU index,
// while the new object may not have been stored at all. Those removals are
// committed facts, so they are finalized here whatever the admission outcome:
// the directory entries are dropped and one replica_evict is emitted per
// victim. They are deliberately NOT rolled back to fake atomicity.
//
// Callers emit the final outcome event (replica_admit or cache_backend_error)
// only after this returns, so an eviction always precedes the outcome of the
// admission it belongs to.
void NodeCache::finalizeEvictions(const AdmitResult& result,
                                  const std::string& logical_key,
                                  const CacheEventContext& context) {
    if (result.victims.empty()) {
        return;
    }
    for (const auto& victim : result.victims) {
        // A victim can only come from ReplicaCache, but an owner entry must
        // never be dropped even if that ever changed.
        if (!owner_store_.contains(victim.key)) {
            directory_.erase(victim.key);
        }
    }
    emitEvictions(result, logical_key, context);
}

// Emits one replica_evict per victim, in the order the victims were actually
// deleted. resident_bytes / resident_objects come from the snapshot ReplicaCache
// took immediately after that victim's delete (see ReplicaVictim): the
// replica-only resident state after this victim was removed and before the new
// object is inserted. They are NOT derived from the final state, NOT copied from
// the final cache_gauge, and do not depend on whether the admission ultimately
// succeeded or on the other victims' sizes.
//
// Timestamps: the record's ts_mono_ns is the time the record was written (all
// victims of one admission are written after admit() returned). The backend
// delete completion bookkeeping is timestamped separately in evicted_mono_ns,
// and idle_age_ns is measured against that point rather than the write time.
// Victim order is the authoritative ordering guarantee.
void NodeCache::emitEvictions(const AdmitResult& result, const std::string& logical_key,
                              const CacheEventContext& context) const {
    for (const auto& victim : result.victims) {
        FieldList fields = commonFields(victim.key, context);
        fields.push_back({"value_bytes", std::to_string(victim.value_bytes)});
        fields.push_back({"resident_bytes", std::to_string(victim.resident_bytes_after)});
        fields.push_back({"resident_objects", std::to_string(victim.resident_objects_after)});
        fields.push_back({"inserted_mono_ns", std::to_string(victim.inserted_mono_ns)});
        fields.push_back({"last_touch_mono_ns", std::to_string(victim.last_touch_mono_ns)});
        fields.push_back({"evicted_mono_ns", std::to_string(victim.evicted_mono_ns)});
        fields.push_back({"idle_age_ns",
                          std::to_string(victim.evicted_mono_ns >= victim.last_touch_mono_ns
                                             ? victim.evicted_mono_ns -
                                                   victim.last_touch_mono_ns
                                             : 0)});
        fields.push_back({"insert_seq", std::to_string(victim.insert_seq)});
        fields.push_back({"touch_seq", std::to_string(victim.touch_seq)});
        fields.push_back({"cause_key", logical_key});
        fields.push_back({"cache_contract_revision", kCacheContractRevision});
        fields.push_back({"reason", "budget"});
        emit(event::kReplicaEvict, fields);
    }
}

AdmitResult NodeCache::admitReplica(const std::string& logical_key, const Value& value,
                                    const CacheEventContext& context) {
    requireUsable();
    sealOwners();

    // A verification receipt is valid for exactly one admission. Consume it here
    // first, whatever path this call takes, so it can never leak into a later
    // packet (for example when the owner short-circuit below returns early).
    const auto verification = std::move(resident_verification_);
    resident_verification_.reset();

    if (config_.legacy()) {
        // Legacy keeps the original Node behaviour exactly: one exists() probe,
        // then one put() when the key is absent. A backend failure propagates
        // unchanged instead of being converted into an admission status, and
        // the directory entry is added only after a successful put -- so a
        // failed write leaves no directory entry and no success accounting.
        AdmitResult result;
        result.value_bytes = value.size();
        bool probed_absent = false;
        try {
            if (backend_->exists(logical_key)) {
                result.status = AdmitStatus::AlreadyResident;
                return result;
            }
            probed_absent = true;
            backend_->put(logical_key, value);
        } catch (const BackendIndeterminate& e) {
            markUnavailable();
            emitBackendError(logical_key,
                             probed_absent ? "replica_put_indeterminate"
                                           : "replica_get_indeterminate",
                             e.what(), true, context, BackendErrorCategory::Indeterminate);
            throw;
        }
        directory_.insert(logical_key);
        result.status = AdmitStatus::Admitted;
        return result;
    }

    if (owner_store_.contains(logical_key)) {
        // Requirement A.7: a node that already owns the key must not create a
        // redundant replica of it.
        AdmitResult result;
        result.status = AdmitStatus::AlreadyResident;
        result.value_bytes = value.size();
        result.existing_bytes = value.size();
        result.resident_bytes_after = replica_cache_.residentBytes();
        result.resident_objects_after = replica_cache_.residentObjects();
        FieldList fields = commonFields(logical_key, context);
        fields.push_back({"value_bytes", std::to_string(value.size())});
        fields.push_back({"resident_bytes", std::to_string(replica_cache_.residentBytes())});
        fields.push_back({"resident_objects", std::to_string(replica_cache_.residentObjects())});
        fields.push_back({"reason", "owner_resident"});
        emit(event::kDataAdmitSkipped, fields);
        return result;
    }

    // Reuse the mandatory integrity read instead of issuing a second one. The
    // receipt is only accepted when it names this key and holds these exact
    // bytes; otherwise the store's own full verification still runs.
    const bool verified_resident =
        verification.has_value() && verification->key == logical_key &&
        verification->content.data() == value.data();
    AdmitResult result;
    try {
        result = verified_resident
                     ? replica_cache_.admitWithVerifiedResident(logical_key, value)
                     : replica_cache_.admit(logical_key, value);
    } catch (const IndeterminateAdmission& e) {
        // Evictions confirmed before the uncertain operation are committed
        // facts and are still finalized and recorded. The uncertain operation
        // itself is NOT recorded as a success; the store is poisoned and the
        // typed failure is rethrown so the run stops on unknown state.
        markUnavailable();
        finalizeEvictions(e.result(), logical_key, context);
        // The uncertain victim must not stay in the serviceable directory: it
        // is neither a confirmed eviction event nor a resident object.
        if (!e.result().indeterminate_key.empty() &&
            !owner_store_.contains(e.result().indeterminate_key)) {
            directory_.erase(e.result().indeterminate_key);
        }
        const bool evict_phase = e.result().failing_op == "evict_delete";
        emitBackendError(logical_key,
                         evict_phase ? "replica_evict_indeterminate"
                                     : "replica_put_indeterminate",
                         e.what(), true, context, BackendErrorCategory::Indeterminate);
        throw;
    } catch (const BackendIndeterminate& e) {
        // A plain BackendIndeterminate can also escape ReplicaCache::admit():
        // the redundant content re-read of an already-resident replica (when no
        // integrity receipt is available) poisons the replica store and
        // rethrows. The facade must be poisoned too, and exactly one error must
        // be recorded; there is no confirmed victim to finalize here, so this
        // must not touch the IndeterminateAdmission finalization path above.
        markUnavailable();
        emitBackendError(logical_key,
                         e.operation() == "get" ? "replica_get_indeterminate"
                                                : "replica_indeterminate",
                         e.what(), true, context, BackendErrorCategory::Indeterminate);
        throw;
    }

    // Evictions that already happened are finalized for EVERY outcome, before
    // the outcome event is emitted. Only the success case additionally adds the
    // new object, so a victim is cleaned up and recorded exactly once.
    finalizeEvictions(result, logical_key, context);

    switch (result.status) {
        case AdmitStatus::Admitted: {
            directory_.insert(logical_key);
            FieldList fields = commonFields(logical_key, context);
            fields.push_back({"value_bytes", std::to_string(value.size())});
            fields.push_back({"resident_bytes", std::to_string(result.resident_bytes_after)});
            fields.push_back({"resident_objects", std::to_string(result.resident_objects_after)});
            fields.push_back({"evicted_count", std::to_string(result.victims.size())});
            std::size_t evicted_bytes = 0;
            for (const auto& victim : result.victims) {
                evicted_bytes += victim.value_bytes;
            }
            fields.push_back({"evicted_bytes", std::to_string(evicted_bytes)});
            fields.push_back({"reason", "admitted"});
            emit(event::kReplicaAdmit, fields);
            break;
        }
        case AdmitStatus::AlreadyResident: {
            FieldList fields = commonFields(logical_key, context);
            fields.push_back({"value_bytes", std::to_string(value.size())});
            fields.push_back({"resident_bytes", std::to_string(result.resident_bytes_after)});
            fields.push_back({"resident_objects", std::to_string(result.resident_objects_after)});
            fields.push_back({"reason", "already_cached"});
            emit(event::kDataAdmitSkipped, fields);
            break;
        }
        case AdmitStatus::BypassedBudgetZero: {
            FieldList fields = commonFields(logical_key, context);
            fields.push_back({"value_bytes", std::to_string(value.size())});
            fields.push_back({"resident_bytes", std::to_string(result.resident_bytes_after)});
            fields.push_back({"resident_objects", std::to_string(result.resident_objects_after)});
            fields.push_back({"reason", config_.policy == CachePolicy::Bypass
                                            ? "policy_bypass"
                                            : "budget_zero"});
            emit(event::kReplicaBypass, fields);
            break;
        }
        case AdmitStatus::BypassedTooLarge: {
            FieldList fields = commonFields(logical_key, context);
            fields.push_back({"value_bytes", std::to_string(value.size())});
            fields.push_back({"resident_bytes", std::to_string(result.resident_bytes_after)});
            fields.push_back({"resident_objects", std::to_string(result.resident_objects_after)});
            fields.push_back({"reason", "object_larger_than_budget"});
            emit(event::kReplicaBypass, fields);
            break;
        }
        case AdmitStatus::Conflict: {
            // Same event shape as an immutability conflict detected on the DATA
            // path, built from what this admission attempt knows.
            IntegrityCheck check;
            check.tier = CacheTier::Replica;
            check.incoming_bytes = value.size();
            check.existing_bytes = result.existing_bytes;
            check.reason = result.existing_bytes != value.size() ? "size_mismatch"
                                                                 : "content_mismatch";
            emitKeyConflict(logical_key, check, context);
            break;
        }
        case AdmitStatus::BackendError: {
            const std::string reason = result.failing_op == "evict_delete"
                                           ? "replica_evict_failed"
                                           : (result.failing_op == "get" ? "replica_get_failed"
                                                                         : "replica_put_failed");
            emitBackendError(logical_key, reason, result.error, false, context,
                             BackendErrorCategory::PreSideEffect);
            break;
        }
        case AdmitStatus::Indeterminate: {
            // Defensive: ReplicaCache throws IndeterminateAdmission for this
            // status, so the normal path never returns it. Fail closed anyway
            // rather than let a caller treat an unknown state as a result.
            markUnavailable();
            emitBackendError(logical_key, "replica_indeterminate", result.error, true,
                             context, BackendErrorCategory::Indeterminate);
            throw BackendIndeterminate("admission", result.error);
        }
    }
    return result;
}

void NodeCache::emitKeyConflict(const std::string& logical_key, const IntegrityCheck& check,
                               const CacheEventContext& context) const {
    FieldList fields = commonFields(logical_key, context);
    fields.push_back({"tier", cacheTierName(check.tier)});
    fields.push_back({"value_bytes", std::to_string(check.incoming_bytes)});
    fields.push_back({"incoming_bytes", std::to_string(check.incoming_bytes)});
    fields.push_back({"existing_bytes", std::to_string(check.existing_bytes)});
    const CacheNamespace physical =
        namespaced() ? (check.tier == CacheTier::Owner ? CacheNamespace::Owner
                                                       : CacheNamespace::Replica)
                     : CacheNamespace::Legacy;
    fields.push_back({"namespace", cacheNamespaceName(physical)});
    fields.push_back({"reason", check.reason});
    // The conflict event's semantics changed in contract revision 3 (tier field
    // and size_mismatch/content_mismatch reasons), so it declares the revision
    // on every event, like replica_evict does.
    fields.push_back({"cache_contract_revision", kCacheContractRevision});
    // No value bytes are ever logged. Immutability is decided by exact byte
    // comparison, so no diagnostic hash is needed here either.
    emit(event::kCacheKeyConflict, fields);
}

IntegrityCheck NodeCache::checkImmutability(const std::string& logical_key,
                                            const Value& value,
                                            const CacheEventContext& context) {
    requireUsable();
    IntegrityCheck check;
    // Each integrity check starts with no receipt: a failed or non-applicable
    // check must not leave an earlier proof behind.
    resident_verification_.reset();
    check.incoming_bytes = value.size();
    if (config_.legacy()) {
        // The legacy path's full immutability handling is a separate item; this
        // must not change the default legacy behaviour.
        return check;
    }
    sealOwners();

    // A. trusted owner object: exact byte comparison.
    if (owner_store_.contains(logical_key)) {
        check.tier = CacheTier::Owner;
        const auto known_size = owner_store_.sizeOf(logical_key);
        if (known_size.has_value()) {
            check.existing_bytes = *known_size;
        }
        // Size conflicts are decided from the in-memory owner index: no backend
        // read and, above all, no admission or eviction may happen before this.
        if (known_size.has_value() && *known_size != value.size()) {
            check.verdict = IntegrityVerdict::Conflict;
            check.reason = "size_mismatch";
            emitKeyConflict(logical_key, check, context);
            throw ImmutabilityViolation(logical_key, check.reason,
                                        cacheTierName(check.tier),
                                        check.incoming_bytes, check.existing_bytes);
        }
        std::optional<Value> existing;
        try {
            existing = owner_store_.get(logical_key);
        } catch (const BackendIndeterminate& e) {
            markUnavailable();
            emitBackendError(logical_key, "owner_get_indeterminate", e.what(), true,
                             context, BackendErrorCategory::Indeterminate);
            throw;
        } catch (const std::exception& e) {
            emitBackendError(logical_key, "owner_get_failed", e.what(), true, context,
                             BackendErrorCategory::PreSideEffect);
            check.verdict = IntegrityVerdict::Unavailable;
            check.reason = "read_failed";
            return check;
        }
        if (!existing.has_value()) {
            emitBackendError(logical_key, "owner_kv_missing",
                             "owner index entry has no backing value", true, context,
                             BackendErrorCategory::Divergence);
            throw std::runtime_error(
                "NodeCache: owner object missing from the cache backend while "
                "verifying incoming DATA: '" + logical_key + "'");
        }
        check.existing_bytes = existing->size();
        if (existing->data() != value.data()) {
            check.verdict = IntegrityVerdict::Conflict;
            check.reason = "content_mismatch";
            emitKeyConflict(logical_key, check, context);
            throw ImmutabilityViolation(logical_key, check.reason,
                                        cacheTierName(check.tier),
                                        check.incoming_bytes, check.existing_bytes);
        }
        check.verdict = IntegrityVerdict::Verified;
        return check;
    }

    // B. resident replica: exact byte comparison, without touching recency.
    if (replica_cache_.resident(logical_key)) {
        check.tier = CacheTier::Replica;
        const auto known_size = replica_cache_.residentSize(logical_key);
        if (known_size.has_value()) {
            check.existing_bytes = *known_size;
        }
        if (known_size.has_value() && *known_size != value.size()) {
            check.verdict = IntegrityVerdict::Conflict;
            check.reason = "size_mismatch";
            emitKeyConflict(logical_key, check, context);
            throw ImmutabilityViolation(logical_key, check.reason,
                                        cacheTierName(check.tier),
                                        check.incoming_bytes, check.existing_bytes);
        }
        ReplicaCache::PeekResult peeked;
        try {
            peeked = replica_cache_.peek(logical_key);
        } catch (const BackendIndeterminate& e) {
            markUnavailable();
            emitBackendError(logical_key, "replica_get_indeterminate", e.what(), true,
                             context, BackendErrorCategory::Indeterminate);
            throw;
        }
        if (peeked.backend_error) {
            emitBackendError(logical_key, "replica_get_failed", peeked.error, false, context,
                             BackendErrorCategory::PreSideEffect);
            check.verdict = IntegrityVerdict::Unavailable;
            check.reason = "read_failed";
            return check;
        }
        if (!peeked.value.has_value()) {
            // Index says resident but the backend has no value: the reference
            // cannot be read, so this packet must not be accepted as verified.
            // Repair is left to the normal serving path; nothing is mutated here.
            emitBackendError(logical_key, "replica_kv_missing",
                             "replica index entry has no backing value", false, context,
                             BackendErrorCategory::Divergence);
            check.verdict = IntegrityVerdict::Unavailable;
            check.reason = "read_failed";
            return check;
        }
        check.existing_bytes = peeked.value->size();
        if (peeked.value->data() != value.data()) {
            check.verdict = IntegrityVerdict::Conflict;
            check.reason = "content_mismatch";
            emitKeyConflict(logical_key, check, context);
            throw ImmutabilityViolation(logical_key, check.reason,
                                        cacheTierName(check.tier),
                                        check.incoming_bytes, check.existing_bytes);
        }
        check.verdict = IntegrityVerdict::Verified;
        // Hand the exact bytes this check read to admitReplica(), so the same
        // DATA handling does not read the same resident object a second time
        // after protocol state has been consumed. The proof is bound to this
        // key and these bytes and is consumed by the next admission.
        ResidentReplicaVerification proof;
        proof.key = logical_key;
        proof.content = std::move(*peeked.value);
        resident_verification_ = std::move(proof);
        return check;
    }

    // C/D. nothing trustworthy to compare against: declared boundary, accepted
    // as a first copy exactly as before.
    return check;
}

void NodeCache::emitBackendError(const std::string& logical_key, const std::string& reason,
                                 const std::string& detail, bool fatal,
                                 const CacheEventContext& context,
                                 BackendErrorCategory category) const {
    FieldList fields = commonFields(logical_key, context);
    fields.push_back({"reason", reason});
    fields.push_back({"detail", detail});
    fields.push_back({"fatal", fatal ? "1" : "0"});
    // Error class: pre_side_effect (state known unchanged) / indeterminate
    // (operation result unknown, store poisoned) / divergence (index vs KV).
    fields.push_back({"category", backendErrorCategoryName(category)});
    fields.push_back({"cache_contract_revision", kCacheContractRevision});
    emit(event::kCacheBackendError, fields);
}

GaugeSnapshot NodeCache::gauge() const {
    GaugeSnapshot snapshot;
    snapshot.owner_objects = owner_store_.objectCount();
    snapshot.owner_bytes = owner_store_.valueBytes();
    snapshot.replica_objects = replica_cache_.residentObjects();
    snapshot.replica_bytes = replica_cache_.residentBytes();
    snapshot.budget_bytes = config_.budget_bytes;
    snapshot.unlimited = config_.budgetUnlimited();
    snapshot.admits = replica_cache_.admits();
    snapshot.evictions = replica_cache_.evictions();
    snapshot.evicted_bytes = replica_cache_.evictedBytes();
    snapshot.bypasses_budget_zero = replica_cache_.bypassesBudgetZero();
    snapshot.bypasses_too_large = replica_cache_.bypassesTooLarge();
    snapshot.skipped_already_resident = replica_cache_.skippedAlreadyResident();
    snapshot.conflicts = replica_cache_.conflicts();
    snapshot.backend_errors = replica_cache_.backendErrors();
    snapshot.serves = replica_cache_.serves();
    snapshot.touches = replica_cache_.touches();
    return snapshot;
}

void NodeCache::emitConfigEvent() const {
    if (config_.legacy()) {
        return;
    }
    FieldList fields = commonFields("<run>", {});
    fields.push_back({"namespace_owner", cacheNamespaceName(
                                             namespaced() ? CacheNamespace::Owner
                                                          : CacheNamespace::Legacy)});
    fields.push_back({"namespace_replica", cacheNamespaceName(
                                               namespaced() ? CacheNamespace::Replica
                                                            : CacheNamespace::Legacy)});
    fields.push_back({"owner_store", namespaced() ? "namespaced" : "legacy"});
    fields.push_back({"value_granularity", "value_bytes_only"});
    // Producer revision for the cache event contract: a run without this marker
    // (revision 1) emitted replica_evict resident_* derived from the final state,
    // which must not be read with the revision-2 semantics.
    fields.push_back({"cache_contract_revision", kCacheContractRevision});
    fields.push_back({"verify_replica_content",
                      config_.verify_replica_content ? "1" : "0"});
    // Effective integrity policy of this run: finite-cache modes always compare
    // locally verifiable DATA against the owner/replica bytes exactly.
    fields.push_back({"integrity_verification", "exact_bytes"});
    fields.push_back({"gauge_interval_ms", std::to_string(config_.gauge_interval_ms)});
    fields.push_back({"redis_maxmemory", envOr("RSYNC_REDIS_MAXMEMORY", "<unset>")});
    fields.push_back({"redis_maxmemory_policy",
                      envOr("RSYNC_REDIS_MAXMEMORY_POLICY", "<unset>")});
    fields.push_back({"redis_require_empty", envOr("RSYNC_REDIS_REQUIRE_EMPTY", "<unset>")});
    fields.push_back({"redis_socket_path", envOr("REDIS_SOCKET_PATH", "<unset>")});
    fields.push_back({"reason", "startup"});
    emit(event::kCacheConfig, fields);
}

void NodeCache::emitGaugeEvent() const {
    if (config_.legacy()) {
        return;
    }
    const GaugeSnapshot snapshot = gauge();
    FieldList fields = commonFields("<run>", {});
    fields.push_back({"owner_objects", std::to_string(snapshot.owner_objects)});
    fields.push_back({"owner_bytes", std::to_string(snapshot.owner_bytes)});
    fields.push_back({"replica_objects", std::to_string(snapshot.replica_objects)});
    fields.push_back({"replica_bytes", std::to_string(snapshot.replica_bytes)});
    fields.push_back({"resident_bytes", std::to_string(snapshot.replica_bytes)});
    fields.push_back({"resident_objects", std::to_string(snapshot.replica_objects)});
    fields.push_back({"admits", std::to_string(snapshot.admits)});
    fields.push_back({"evictions", std::to_string(snapshot.evictions)});
    fields.push_back({"evicted_bytes", std::to_string(snapshot.evicted_bytes)});
    fields.push_back({"bypasses_budget_zero", std::to_string(snapshot.bypasses_budget_zero)});
    fields.push_back({"bypasses_too_large", std::to_string(snapshot.bypasses_too_large)});
    fields.push_back({"skipped_already_resident",
                      std::to_string(snapshot.skipped_already_resident)});
    fields.push_back({"conflicts", std::to_string(snapshot.conflicts)});
    fields.push_back({"backend_errors", std::to_string(snapshot.backend_errors)});
    fields.push_back({"serves", std::to_string(snapshot.serves)});
    fields.push_back({"touches", std::to_string(snapshot.touches)});
    fields.push_back({"reason", "periodic"});
    emit(event::kCacheGauge, fields);
}

void NodeCache::maybeEmitGauge() {
    if (config_.legacy() || config_.gauge_interval_ms == 0) {
        return;
    }
    const std::uint64_t now = monoNsNow();
    const std::uint64_t interval_ns =
        static_cast<std::uint64_t>(config_.gauge_interval_ms) * 1000000ULL;
    if (last_gauge_mono_ns_ != 0 && now - last_gauge_mono_ns_ < interval_ns) {
        return;
    }
    last_gauge_mono_ns_ = now;
    emitGaugeEvent();
}

}  // namespace rsync_cache