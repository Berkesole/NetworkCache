#include "replica_cache.hpp"

#include "cache_events.hpp"

#include <utility>

namespace rsync_cache {

const char* replicaPolicyName(ReplicaPolicy policy) {
    switch (policy) {
        case ReplicaPolicy::Bypass: return "bypass";
        case ReplicaPolicy::Lru: return "lru";
        case ReplicaPolicy::Unlimited: return "unlimited";
    }
    return "unknown";
}

bool parseReplicaPolicy(const std::string& text, ReplicaPolicy& out) {
    if (text == "bypass") {
        out = ReplicaPolicy::Bypass;
        return true;
    }
    if (text == "lru") {
        out = ReplicaPolicy::Lru;
        return true;
    }
    if (text == "unlimited") {
        out = ReplicaPolicy::Unlimited;
        return true;
    }
    return false;
}

ReplicaCache::ReplicaCache(LocalCache* backend, ReplicaPolicy policy,
                           std::size_t budget_bytes, bool namespaced,
                           bool verify_content)
    : backend_(backend),
      policy_(policy),
      budget_bytes_(budget_bytes),
      namespaced_(namespaced),
      verify_content_(verify_content) {}

bool ReplicaCache::physicalExists(const std::string& logical_key) const {
    try {
        return backend_->exists(physicalKeyFor(logical_key));
    } catch (...) {
        return false;
    }
}

std::vector<std::string> ReplicaCache::residentKeysLruOrder() const {
    return std::vector<std::string>(lru_.begin(), lru_.end());
}

std::optional<std::size_t> ReplicaCache::residentSize(const std::string& logical_key) const {
    const auto it = index_.find(logical_key);
    if (it == index_.end()) {
        return std::nullopt;
    }
    return it->second.bytes;
}

void ReplicaCache::requireUsable() const {
    if (unavailable_) {
        throw CacheUnavailable(
            "replica cache is unavailable after an indeterminate backend result; "
            "the process must stop instead of serving or admitting from unknown state");
    }
}

ReplicaCache::PeekResult ReplicaCache::peek(const std::string& logical_key) const {
    requireUsable();
    PeekResult result;
    if (index_.find(logical_key) == index_.end()) {
        return result;  // not resident: nothing to compare against
    }
    try {
        result.value = backend_->get(physicalKeyFor(logical_key));
    } catch (const BackendIndeterminate&) {
        // The read result is unknown, not "absent". Poison and rethrow the
        // original typed failure so the caller cannot treat it as a miss.
        unavailable_ = true;
        throw;
    } catch (const std::exception& e) {
        result.backend_error = true;
        result.error = e.what();
    }
    return result;
}

ReplicaCache::EvictOutcome ReplicaCache::evictTail(ReplicaVictim& victim_out) {
    if (lru_.empty()) {
        return EvictOutcome::NotApplied;
    }
    const std::string victim = lru_.back();
    const auto it = index_.find(victim);
    if (it == index_.end()) {
        // Index/LRU disagreement: drop the orphan LRU node, change nothing else.
        lru_.pop_back();
        return EvictOutcome::NotApplied;
    }
    try {
        backend_->del(physicalKeyFor(victim));
    } catch (const BackendIndeterminate&) {
        // Redis may already have deleted this victim even though the response
        // was lost. Neither claim is allowed: it is not counted as a successful
        // eviction (no event, no evicted_bytes) AND it is no longer advertised
        // as resident (removed from the index/LRU). The store is poisoned and
        // the confirmed victims deleted before it stay in result.victims. The
        // final accounting intentionally does not close, because the outcome is
        // unknown and the run is invalid.
        unavailable_ = true;
        victim_out = ReplicaVictim{};
        victim_out.key = victim;
        victim_out.value_bytes = it->second.bytes;
        victim_out.inserted_mono_ns = it->second.inserted_mono_ns;
        victim_out.last_touch_mono_ns = it->second.last_touch_mono_ns;
        resident_bytes_ -= it->second.bytes;
        last_touch_.erase(victim);
        lru_.erase(it->second.pos);
        index_.erase(it);
        return EvictOutcome::Indeterminate;
    } catch (...) {
        // The value is still stored, so the entry stays resident and no
        // eviction is counted: accounting cannot diverge from storage.
        ++backend_errors_;
        victim_out = ReplicaVictim{};
        victim_out.key = victim;
        victim_out.value_bytes = it->second.bytes;
        return EvictOutcome::NotApplied;
    }
    victim_out.key = victim;
    victim_out.value_bytes = it->second.bytes;
    victim_out.inserted_mono_ns = it->second.inserted_mono_ns;
    victim_out.last_touch_mono_ns = it->second.last_touch_mono_ns;
    victim_out.insert_seq = it->second.insert_seq;
    victim_out.touch_seq = it->second.touch_seq;

    resident_bytes_ -= it->second.bytes;
    last_touch_.erase(victim);
    lru_.erase(it->second.pos);
    index_.erase(it);
    ++evictions_;
    evicted_bytes_ += victim_out.value_bytes;
    // Snapshot the state this eviction produced, right here: the delete already
    // succeeded and resident_bytes_/index_ have been updated, so this is
    // "after this victim was removed". Capturing it here (instead of deriving it
    // from the final AdmitResult later) keeps it correct whether or not the
    // follow-up insert succeeds and whatever the other victims' sizes are.
    victim_out.resident_bytes_after = resident_bytes_;
    victim_out.resident_objects_after = index_.size();
    victim_out.evicted_mono_ns = monoNsNow();
    return EvictOutcome::Evicted;
}

AdmitResult ReplicaCache::admit(const std::string& logical_key, const Value& value) {
    // Public, fully self-contained admission: when verify_content_ is set, an
    // already-resident same-size key is re-read from the backend and compared
    // here. Direct callers keep that protection.
    return admitInternal(logical_key, value, /*resident_content_verified=*/false);
}

AdmitResult ReplicaCache::admitWithVerifiedResident(const std::string& logical_key,
                                                    const Value& value) {
    // Private fast path for NodeCache: the caller has already read the resident
    // replica for this exact key and confirmed these exact bytes, so the second
    // read would only repeat that work after protocol state was consumed.
    return admitInternal(logical_key, value, /*resident_content_verified=*/true);
}

AdmitResult ReplicaCache::admitInternal(const std::string& logical_key,
                                        const Value& value,
                                        bool resident_content_verified) {
    requireUsable();
    AdmitResult result;
    result.value_bytes = value.size();

    auto existing = index_.find(logical_key);
    if (existing != index_.end()) {
        // Already resident: no put, no recency change, no double counting.
        // A duplicate DATA arrival must not keep a replica alive.
        bool conflict = existing->second.bytes != value.size();
        // The content read below is the "second read": skip it only when the
        // caller's proof already covered this exact resident object. A size
        // difference is still a conflict and is decided without any read.
        if (!conflict && verify_content_ && !resident_content_verified) {
            std::optional<Value> current;
            try {
                current = backend_->get(physicalKeyFor(logical_key));
            } catch (const BackendIndeterminate&) {
                unavailable_ = true;
                throw;
            } catch (const std::exception& e) {
                result.status = AdmitStatus::BackendError;
                result.error = e.what();
                result.failing_op = "get";
                ++backend_errors_;
                result.resident_bytes_after = resident_bytes_;
                result.resident_objects_after = index_.size();
                return result;
            }
            if (!current.has_value()) {
                // Divergence, not a content conflict: the index advertised an
                // object the backend does not have. Repair it and fall through
                // to a fresh admission.
                resident_bytes_ -= existing->second.bytes;
                last_touch_.erase(logical_key);
                lru_.erase(existing->second.pos);
                index_.erase(existing);
                ++divergences_repaired_;
                existing = index_.end();
            } else {
                conflict = current->data() != value.data();
            }
        }
        if (existing != index_.end()) {
            if (conflict) {
                result.status = AdmitStatus::Conflict;
                result.existing_bytes = existing->second.bytes;
                ++conflicts_;
            } else {
                result.status = AdmitStatus::AlreadyResident;
                result.existing_bytes = existing->second.bytes;
                ++skipped_already_resident_;
            }
            result.resident_bytes_after = resident_bytes_;
            result.resident_objects_after = index_.size();
            return result;
        }
    }

    // Bypass means "never admit". budget_bytes == 0 is a distinct reason and
    // applies to Lru only: Unlimited is an explicit independent policy value and
    // must never be gated by a byte budget.
    if (policy_ == ReplicaPolicy::Bypass ||
        (policy_ == ReplicaPolicy::Lru && budget_bytes_ == 0)) {
        result.status = AdmitStatus::BypassedBudgetZero;
        ++bypasses_budget_zero_;
        result.resident_bytes_after = resident_bytes_;
        result.resident_objects_after = index_.size();
        return result;
    }

    if (policy_ == ReplicaPolicy::Lru && value.size() > budget_bytes_) {
        result.status = AdmitStatus::BypassedTooLarge;
        ++bypasses_too_large_;
        result.resident_bytes_after = resident_bytes_;
        result.resident_objects_after = index_.size();
        return result;
    }

    // Choose victims before inserting so the final resident size never exceeds
    // the budget (design section 5.8).
    if (policy_ == ReplicaPolicy::Lru) {
        while (resident_bytes_ + value.size() > budget_bytes_ && !lru_.empty()) {
            ReplicaVictim victim;
            const EvictOutcome outcome = evictTail(victim);
            if (outcome == EvictOutcome::Evicted) {
                result.victims.push_back(std::move(victim));
                continue;
            }
            if (outcome == EvictOutcome::Indeterminate) {
                // The delete may already have happened. The confirmed victims
                // already in result.victims stay there and are finalized by the
                // facade; this victim is NOT counted and NOT reported as still
                // resident. Throw the typed indeterminate failure so the run
                // stops instead of admitting on unknown state.
                result.status = AdmitStatus::Indeterminate;
                result.error = "replica eviction delete result is indeterminate for key " +
                               victim.key;
                result.failing_op = "evict_delete";
                result.indeterminate_key = victim.key;
                result.resident_bytes_after = resident_bytes_;
                result.resident_objects_after = index_.size();
                throw IndeterminateAdmission(result.failing_op, result.error, result);
            }
            if (!victim.key.empty()) {
                // Delete failed before its side effect: the victim is still
                // resident. Stop and report the backend error instead of
                // pretending we made room.
                result.status = AdmitStatus::BackendError;
                result.error = "replica eviction delete failed for key " + victim.key;
                result.failing_op = "evict_delete";
                result.resident_bytes_after = resident_bytes_;
                result.resident_objects_after = index_.size();
                return result;
            }
            // Orphan LRU node dropped; loop again.
        }
        if (resident_bytes_ + value.size() > budget_bytes_) {
            result.status = AdmitStatus::BypassedTooLarge;
            ++bypasses_too_large_;
            result.resident_bytes_after = resident_bytes_;
            result.resident_objects_after = index_.size();
            return result;
        }
    }

    try {
        backend_->put(physicalKeyFor(logical_key), value);
    } catch (const BackendIndeterminate& e) {
        // The write may already have been applied. Nothing is recorded as
        // admitted and the index is not updated, but we must not describe this
        // as "the object was not stored": poison the store and throw the typed
        // failure so the process stops before producing more data.
        unavailable_ = true;
        ++backend_errors_;
        result.status = AdmitStatus::Indeterminate;
        result.error = e.what();
        result.failing_op = "put";
        result.resident_bytes_after = resident_bytes_;
        result.resident_objects_after = index_.size();
        throw IndeterminateAdmission("put", e.what(), result);
    } catch (const std::exception& e) {
        // Proved pre-side-effect failure: nothing is recorded as admitted. The
        // LRU index and the byte counter are unchanged, so storage, index and
        // counters cannot silently diverge.
        result.status = AdmitStatus::BackendError;
        result.error = e.what();
        result.failing_op = "put";
        ++backend_errors_;
        result.resident_bytes_after = resident_bytes_;
        result.resident_objects_after = index_.size();
        return result;
    }

    Entry entry;
    entry.bytes = value.size();
    entry.inserted_mono_ns = monoNsNow();
    entry.last_touch_mono_ns = entry.inserted_mono_ns;
    entry.insert_seq = ++access_seq_;
    entry.touch_seq = entry.insert_seq;
    lru_.push_front(logical_key);
    entry.pos = lru_.begin();
    index_.emplace(logical_key, entry);
    last_touch_[logical_key] = entry.last_touch_mono_ns;
    resident_bytes_ += entry.bytes;
    ++admits_;

    result.status = AdmitStatus::Admitted;
    result.resident_bytes_after = resident_bytes_;
    result.resident_objects_after = index_.size();
    return result;
}

UseResult ReplicaCache::use(const std::string& logical_key) {
    requireUsable();
    UseResult result;
    const auto it = index_.find(logical_key);
    if (it == index_.end()) {
        return result;  // not resident: nothing to serve, nothing to touch
    }

    std::optional<Value> current;
    try {
        current = backend_->get(physicalKeyFor(logical_key));
    } catch (const BackendIndeterminate&) {
        // A read does not modify storage, but a lost response means we cannot
        // say whether this key is servable. Poison and rethrow: the caller may
        // not turn this into an ordinary miss plus a remote fallback, because
        // that extra work would be counted as normal cache performance.
        unavailable_ = true;
        throw;
    } catch (const std::exception& e) {
        result.backend_error = true;
        result.error = e.what();
        ++backend_errors_;
        return result;
    }

    if (!current.has_value()) {
        // The index claimed residency but the backend has no value. Report it
        // and repair by dropping the index entry, so the LRU accounting and the
        // directory stop advertising an object that cannot be served.
        result.divergence = true;
        result.repaired = true;
        result.error = "replica index entry has no backing value";
        resident_bytes_ -= it->second.bytes;
        last_touch_.erase(logical_key);
        lru_.erase(it->second.pos);
        index_.erase(it);
        ++divergences_repaired_;
        return result;
    }

    // Real service: the only place that refreshes recency.
    lru_.splice(lru_.begin(), lru_, it->second.pos);
    it->second.last_touch_mono_ns = monoNsNow();
    it->second.touch_seq = ++access_seq_;
    last_touch_[logical_key] = it->second.last_touch_mono_ns;
    ++serves_;
    ++touches_;
    result.value = std::move(current);
    result.touched = true;
    return result;
}

}  // namespace rsync_cache