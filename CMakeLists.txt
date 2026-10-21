cmake_minimum_required(VERSION 3.15)
project(NetworkCache CXX)

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
set(RSYNC_RUNTIME_SOURCES
    src/node.cpp
    src/packet.cpp
    src/cache/in_memory_cache.cpp
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

# Redis 测试（可选，需要 Redis）
# Redis tests (optional, requires Redis)
if(REDIS_AVAILABLE)
    add_executable(test_redis_cache_contract
        tests/redis/cache_contract.cpp
        src/cache/redis_cache.cpp
    )
    if(TARGET redis++ AND TARGET hiredis)
        target_link_libraries(test_redis_cache_contract PRIVATE redis++ hiredis Threads::Threads)
    else()
        target_include_directories(test_redis_cache_contract PRIVATE ${REDIS_INCLUDE_DIRS})
        target_link_libraries(test_redis_cache_contract PRIVATE ${REDIS_LIBRARIES} Threads::Threads)
    endif()
    message(STATUS "Redis cache contract test: enabled")

    add_custom_target(test_redis DEPENDS test_redis_cache_contract rsync-node-dev)
    add_custom_target(test_redis_cache DEPENDS test_redis)
else()
    message(WARNING "Redis test suite: disabled (Redis dependencies not available)")
endif()

# 8x8 mesh 拓扑 demo（基于 MockNetwork + InMemoryCache）
# 仅依赖本地代码，无需 Redis / 网络
add_executable(test_mesh_demo
    tests/mesh.cpp
    src/node.cpp
    src/packet.cpp
    src/cache/in_memory_cache.cpp
)
target_link_libraries(test_mesh_demo PRIVATE Threads::Threads)
message(STATUS "test_mesh_demo: enabled (8x8 mesh demo)")

# Artifact checks: leave the runtime implementation unchanged.
# The original core tests use assert(); retain them even in Release builds.
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(test_core PRIVATE -UNDEBUG)
endif()
include(CTest)
if(BUILD_TESTING)
    add_test(NAME core COMMAND test_core)
    find_package(Python3 COMPONENTS Interpreter QUIET)
    if(Python3_Interpreter_FOUND)
        add_test(NAME mock_delivery
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/verify_local.py
            --build-dir ${CMAKE_CURRENT_BINARY_DIR} --case mock)
        if(NOT REDIS_AVAILABLE)
            add_test(NAME tcp_delivery
                COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/verify_local.py
                --build-dir ${CMAKE_CURRENT_BINARY_DIR} --case tcp)
            set_tests_properties(tcp_delivery PROPERTIES TIMEOUT 30)
        endif()
        set_tests_properties(mock_delivery PROPERTIES TIMEOUT 30)
    else()
        message(STATUS "Python3 not found: only the core check is registered with CTest")
    endif()
endif()
