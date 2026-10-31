cmake_minimum_required(VERSION 3.15)
project(SimpleDCNSync CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_BUILD_RPATH_USE_ORIGIN ON)

include_directories(src)
find_package(Threads REQUIRED)

# 加载依赖管理模块
# Load dependency management module
include(cmake/Dependencies.cmake)

# ============================================================================
# 依赖设置
# Dependency setup
# ============================================================================
setup_redis_dependencies()

# ============================================================================
# 主程序：rsync-router（包含所有 cache 实现）
# Main program: rsync-router (includes all cache implementations)
# ============================================================================
# Finite replica cache: owner index, byte-budget LRU, facade, trace adapter.
# Every target that compiles src/node.cpp needs these.
set(RSYNC_FINITE_CACHE_SOURCES
    src/cache/owner_store.cpp
    src/cache/replica_cache.cpp
    src/cache/node_cache.cpp
    src/cache/trace_event_sink.cpp
    src/cache/redis_acceptance.cpp
)

set(RSYNC_RUNTIME_SOURCES
    src/node.cpp
    src/event_trace.cpp
    src/packet.cpp
    src/cache/in_memory_cache.cpp
    ${RSYNC_FINITE_CACHE_SOURCES}
)

set(RSYNC_OPTIONAL_CACHE_SOURCES)

# 如果 Redis 可用，添加 RedisCache
# Add RedisCache if Redis is available
if(REDIS_AVAILABLE)
    list(APPEND RSYNC_OPTIONAL_CACHE_SOURCES src/cache/redis_cache.cpp)
endif()

function(configure_rsync_runtime target_name entrypoint)
    add_executable(${target_name}
        ${RSYNC_RUNTIME_SOURCES}
        ${RSYNC_OPTIONAL_CACHE_SOURCES}
        ${ARGN}
        ${entrypoint}
    )
    target_link_libraries(${target_name} PRIVATE Threads::Threads)

    if(REDIS_AVAILABLE)
        target_compile_definitions(${target_name} PRIVATE ENABLE_REDIS_CACHE)
        if(TARGET redis++ AND TARGET hiredis)
            target_link_libraries(${target_name} PRIVATE redis++ hiredis)
        else()
            target_include_directories(${target_name} PRIVATE ${REDIS_INCLUDE_DIRS})
            target_link_libraries(${target_name} PRIVATE ${REDIS_LIBRARIES})
        endif()
    endif()
endfunction()

configure_rsync_runtime(rsync-router main.cpp)
configure_rsync_runtime(rsync-node-dev app/rsync_node_main.cpp
    src/network/tcp_network.cpp
    src/network/packet_codec.cpp
)

# The oracle fetcher is an optional experiment helper. Its source is absent from
# the frozen simple-station lineage, so the target is guarded: without this
# guard the whole project cannot be configured (and therefore no new image can
# be built for the finite-cache version). No behaviour changes when the file is
# present.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/experiments/content_discovery/delay_find_content/oracle_direct_fetch.cpp")
    add_executable(oracle-direct-fetch
        experiments/content_discovery/delay_find_content/oracle_direct_fetch.cpp
    )
    target_link_libraries(oracle-direct-fetch PRIVATE Threads::Threads)
    message(STATUS "oracle-direct-fetch: enabled")
else()
    message(STATUS "oracle-direct-fetch: skipped (source absent from this lineage)")
endif()

if(REDIS_AVAILABLE)
    message(STATUS "rsync-router: Redis support enabled")
    message(STATUS "rsync-node-dev: Redis support enabled")
else()
    message(STATUS "rsync-router: Redis support disabled (InMemoryCache only)")
    message(STATUS "rsync-node-dev: Redis support disabled (InMemoryCache only)")
endif()

# 兼容旧名称
# Compatibility alias for old name
add_custom_target(demo DEPENDS rsync-router)

# ============================================================================
# 测试程序
# Test programs
# ============================================================================

# 核心测试（无依赖，总是编译）
# Core tests (no dependencies, always compiled)
add_executable(test_core
    tests/test_core.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_core PRIVATE Threads::Threads)
message(STATUS "test_core: enabled (no external dependencies)")

add_executable(test_packet_codec
    tests/test_packet_codec.cpp
    src/packet.cpp
    src/network/packet_codec.cpp
)
target_link_libraries(test_packet_codec PRIVATE Threads::Threads)
message(STATUS "test_packet_codec: enabled (wire v1/v2 compatibility)")

add_executable(test_query_table_fallback
    tests/test_query_table_fallback.cpp
)
target_link_libraries(test_query_table_fallback PRIVATE Threads::Threads)
message(STATUS "test_query_table_fallback: enabled (staged fallback state)")

add_executable(test_node_staged_fallback
    tests/test_node_staged_fallback.cpp
    src/node.cpp
    src/event_trace.cpp
    src/packet.cpp
    src/network/packet_codec.cpp
    src/cache/in_memory_cache.cpp
    ${RSYNC_FINITE_CACHE_SOURCES}
)
target_link_libraries(test_node_staged_fallback PRIVATE Threads::Threads)
message(STATUS "test_node_staged_fallback: enabled (staged fallback Node integration)")

add_executable(test_runtime_neighbor
    tests/test_runtime_neighbor.cpp
    src/event_trace.cpp
    src/packet.cpp
    src/network/packet_codec.cpp
    src/network/tcp_network.cpp
)
target_link_libraries(test_runtime_neighbor PRIVATE Threads::Threads)
message(STATUS "test_runtime_neighbor: enabled (runtime-local federation bootstrap)")

# Redis 测试（可选，需要 Redis）
# Redis tests (optional, requires Redis)
if(REDIS_AVAILABLE)
    add_executable(test_redis_cache_contract
        tests/redis/cache_contract.cpp
        src/cache/redis_cache.cpp
        src/cache/redis_acceptance.cpp
    )
    if(TARGET redis++ AND TARGET hiredis)
        target_link_libraries(test_redis_cache_contract PRIVATE redis++ hiredis Threads::Threads)
    else()
        target_include_directories(test_redis_cache_contract PRIVATE ${REDIS_INCLUDE_DIRS})
        target_link_libraries(test_redis_cache_contract PRIVATE ${REDIS_LIBRARIES} Threads::Threads)
    endif()
    message(STATUS "Redis cache contract test: enabled")

    # Live acceptance driver: real RedisCache + NodeCache over a Unix socket.
    # Does not depend on the trace writer (a null event sink is used), so it
    # links only the cache sources it needs.
    add_executable(test_redis_acceptance_live
        tests/redis/redis_acceptance_live.cpp
        src/cache/node_cache.cpp
        src/cache/owner_store.cpp
        src/cache/replica_cache.cpp
        src/cache/redis_acceptance.cpp
        src/cache/redis_cache.cpp
    )
    target_compile_definitions(test_redis_acceptance_live PRIVATE ENABLE_REDIS_CACHE)
    if(TARGET redis++ AND TARGET hiredis)
        target_link_libraries(test_redis_acceptance_live PRIVATE redis++ hiredis Threads::Threads)
    else()
        target_include_directories(test_redis_acceptance_live PRIVATE ${REDIS_INCLUDE_DIRS})
        target_link_libraries(test_redis_acceptance_live PRIVATE ${REDIS_LIBRARIES} Threads::Threads)
    endif()
    message(STATUS "Redis live acceptance driver: enabled")

    add_custom_target(test_redis DEPENDS test_redis_cache_contract test_redis_acceptance_live rsync-node-dev)
    add_custom_target(test_redis_cache DEPENDS test_redis)
else()
    message(WARNING "Redis test suite: disabled (Redis dependencies not available)")
endif()

# 8x8 mesh 拓扑 demo（基于 MockNetwork + InMemoryCache）
# 仅依赖本地代码，无需 Redis / 网络
add_executable(test_mesh_demo
    tests/mesh.cpp
    src/node.cpp
    src/event_trace.cpp
    src/packet.cpp
    src/cache/in_memory_cache.cpp
    ${RSYNC_FINITE_CACHE_SOURCES}
)
target_link_libraries(test_mesh_demo PRIVATE Threads::Threads)
message(STATUS "test_mesh_demo: enabled (8x8 mesh demo)")

# ============================================================================
# 有限副本缓存 + 应用层 LRU（finite replica cache）
# Finite replica cache with application-level LRU.
# All of these run in-process on InMemoryCache; no Redis, no Kathara lab.
# ============================================================================
add_executable(test_cache_namespace
    tests/test_cache_namespace.cpp
    src/cache/owner_store.cpp
    src/cache/replica_cache.cpp
    src/cache/node_cache.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_cache_namespace PRIVATE Threads::Threads)
message(STATUS "test_cache_namespace: enabled (namespaces, owner store, owner protection)")

add_executable(test_replica_cache
    tests/test_replica_cache.cpp
    src/cache/replica_cache.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_replica_cache PRIVATE Threads::Threads)
message(STATUS "test_replica_cache: enabled (byte-budget LRU)")

add_executable(test_node_cache_events
    tests/test_node_cache_events.cpp
    src/cache/owner_store.cpp
    src/cache/replica_cache.cpp
    src/cache/node_cache.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_node_cache_events PRIVATE Threads::Threads)
message(STATUS "test_node_cache_events: enabled (cache event contract)")

add_executable(test_node_finite_cache
    tests/test_node_finite_cache.cpp
    src/node.cpp
    src/event_trace.cpp
    src/packet.cpp
    src/network/packet_codec.cpp
    src/cache/in_memory_cache.cpp
    ${RSYNC_FINITE_CACHE_SOURCES}
)
target_link_libraries(test_node_finite_cache PRIVATE Threads::Threads)
message(STATUS "test_node_finite_cache: enabled (Node integration, DATA admission, eviction)")

add_executable(test_cache_trace_schema
    tests/test_cache_trace_schema.cpp
    src/event_trace.cpp
    src/cache/in_memory_cache.cpp
    src/cache/owner_store.cpp
    src/cache/replica_cache.cpp
    src/cache/node_cache.cpp
    src/cache/trace_event_sink.cpp
)
target_link_libraries(test_cache_trace_schema PRIVATE Threads::Threads)
message(STATUS "test_cache_trace_schema: enabled (structured trace schema versioning)")

add_executable(test_redis_acceptance
    tests/test_redis_acceptance.cpp
    src/cache/redis_acceptance.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_redis_acceptance PRIVATE Threads::Threads)
message(STATUS "test_redis_acceptance: enabled (Redis startup/monitor rules, no server)")
