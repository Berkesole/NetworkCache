#pragma once

#include <string>

namespace rsync_cache {

// Physical key namespaces for the finite-replica-cache version.
//
// Owner objects and en-route replicas share one Redis instance but never share
// a key space. The encoding is injective by construction: the physical key is
// prefix(namespace) + logical_key, and the owner and replica prefixes differ,
// so
//
//     physical(a, k1) == physical(b, k2)  =>  a == b  and  k1 == k2
//
// A logical key that itself starts with "owner:" or "replica:" therefore
// cannot collide across the two namespaces; cache_namespace tests assert this
// over a key set that includes such keys, the empty key, prefix-only keys, and
// the boundary where one logical key is a prefix of another.
//
// CacheNamespace::Legacy is the empty prefix. It exists only so the default
// (unchanged) configuration keeps writing bare logical keys exactly as the
// frozen capacity/slides campaigns did. Legacy is never mixed with Owner or
// Replica inside one store.
enum class CacheNamespace { Owner, Replica, Legacy };

inline const char* cacheNamespacePrefix(CacheNamespace ns) {
    switch (ns) {
        case CacheNamespace::Owner: return "owner:";
        case CacheNamespace::Replica: return "replica:";
        case CacheNamespace::Legacy: return "";
    }
    return "";
}

inline const char* cacheNamespaceName(CacheNamespace ns) {
    switch (ns) {
        case CacheNamespace::Owner: return "owner";
        case CacheNamespace::Replica: return "replica";
        case CacheNamespace::Legacy: return "legacy";
    }
    return "unknown";
}

inline std::string physicalKey(CacheNamespace ns, const std::string& logical_key) {
    return std::string(cacheNamespacePrefix(ns)) + logical_key;
}

// Inverse of physicalKey. Returns false when `physical` does not carry the
// namespace prefix (which is a namespace violation, not a logical key).
inline bool logicalKeyFromPhysical(CacheNamespace ns, const std::string& physical,
                                   std::string& logical_out) {
    const std::string prefix(cacheNamespacePrefix(ns));
    if (prefix.empty()) {
        logical_out = physical;
        return true;
    }
    if (physical.size() < prefix.size() ||
        physical.compare(0, prefix.size(), prefix) != 0) {
        return false;
    }
    logical_out = physical.substr(prefix.size());
    return true;
}

}  // namespace rsync_cache