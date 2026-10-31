# Cache layer

This folder hosts pluggable KV backends for the sync protocol:

- `cache.hpp`: minimal KV interface (`put/get/del/exists/flush`), no scanning/enumeration.
- `in_memory_cache.*`: default in-memory implementation, single-thread friendly.
- `redis_cache.*`: Redis-backed implementation (requires redis-plus-plus/hiredis), connects via Unix socket.

Design principles:
- Pure KV API: exact-key operations only; Bloom Filter / routing / SyncTable logic stays in the protocol layer.
- Backend failures are surfaced (exceptions in RedisCache) instead of being treated as cache misses.
- RedisCache operates on raw keys without automatic prefixing; if namespace isolation is needed, encode it in your keys.

## RedisCache Usage

**⚠️ Breaking Changes (v0.3.0)**:
- **Removed prefix mechanism**: RedisCache no longer automatically adds prefixes, uses raw keys
- **flush() semantics changed**: Now executes `FLUSHDB`, clearing the entire Redis DB, requires environment variable protection

### Basic Usage

```cpp
#include "cache/redis_cache.hpp"

// Method 1: Use default path (recommended for containers)
// Priority: REDIS_SOCKET_PATH env var > default path /run/redis/redis.sock
RedisCache cache;

// Method 2: Explicitly specify Unix socket path
RedisCache cache("/run/redis/redis.sock");
```

### flush() Safety Protection

The `flush()` method executes Redis `FLUSHDB` command, clearing the entire database. To prevent accidental data loss, you **must** set an environment variable:

```bash
# Allow flush execution
export REDIS_CACHE_ALLOW_FLUSHDB=1
./your_application

# Or set before calling flush() in code
setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);
cache.flush();  // Now allowed
```

**⚠️ Warning**: `flush()` deletes all keys in the current DB. Ensure:
- Test/production Redis **does not share the same DB with other services**
- Only use `flush()` in test code
- Never set `REDIS_CACHE_ALLOW_FLUSHDB=1` in production

### Namespace Isolation (If Needed)

If node/service isolation is required, encode it in your keys:

```cpp
RedisCache cache;

// Node isolation: include node ID in key
std::string node_id = "node1";
cache.put(node_id + ":key1", value);

// Service isolation: include service prefix in key
cache.put("service_a:key1", value);
cache.put("service_b:key1", value);
```

## Migration Guide

### v0.2.x → v0.3.0 (Removing prefix mechanism)

**Breaking changes**: Removed automatic prefix mechanism, `flush()` changed to FLUSHDB

#### Old code (v0.2.x)

```cpp
// Provide prefix on construction
RedisCache cache("node1:");  // or RedisCache("node1:", socket_path)

// put/get automatically adds prefix
cache.put("key1", value);  // Actually stores "node1:key1"
auto v = cache.get("key1");  // Actually queries "node1:key1"

// flush only clears current prefix namespace
cache.flush();  // SCAN "node1:*" + UNLINK
```

#### New code (v0.3.0+)

```cpp
// No prefix needed on construction
RedisCache cache;  // or RedisCache(socket_path)

// If namespace isolation needed, encode in keys
std::string node_id = "node1";
cache.put(node_id + ":key1", value);  // Stores "node1:key1"
auto v = cache.get(node_id + ":key1");  // Queries "node1:key1"

// flush clears entire Redis DB (requires environment variable)
setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);
cache.flush();  // Executes FLUSHDB
```

#### Migration Steps

1. **Remove prefix parameter**:
   - `RedisCache("node1:")` → `RedisCache()`
   - `RedisCache("node1:", socket_path)` → `RedisCache(socket_path)`

2. **Update key encoding** (if isolation needed):
   - Manually add prefix in `put/get/del/exists` calls
   - Example: `cache.put(prefix + ":" + key, value)`

3. **Update flush calls**:
   - **Test code**: Add `setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);`
   - **Production code**: Remove `flush()` calls or replace with individual `del()` operations

4. **Verify**:
   - Build: `cmake --build build --target test_redis`
   - Run full suite: `./scripts/tests/run_redis_suite.sh ./build/test_redis_cache_contract ./build/rsync-node-dev`
   - Run cache contract only: `TEST_REDIS_SOCKET=/path/to/redis.sock ./build/test_redis_cache_contract`

Building:
- The demo only links `in_memory_cache` by default. To use RedisCache, add `src/cache/redis_cache.cpp` to the build and link `redis++` / `hiredis`, or configure via vcpkg.
- Redis is not started by this code: you must run a Redis server yourself (local `redis-server`, or a container/Compose/K8s service) and point `RedisCache` at its Unix socket.
