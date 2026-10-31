# 依赖管理模块 - 统一管理所有外部依赖
# Dependency management module - unified management of all external dependencies

# ============================================================================
# Redis 依赖（hiredis + redis++）
# Redis dependencies (hiredis + redis++)
# ============================================================================
function(setup_redis_dependencies)
    # 默认关闭 Redis 支持与自动拉取依赖，便于在离线/内网环境下直接编译核心协议
    # 如需启用 Redis，请在配置时显式指定：
    #   -DENABLE_REDIS=ON -DFETCH_REDIS_DEPS=ON
    option(ENABLE_REDIS "Enable Redis cache support" OFF)
    option(FETCH_REDIS_DEPS "Auto-fetch redis deps if not found" OFF)

    if(FETCH_REDIS_DEPS AND NOT ENABLE_REDIS)
        message(WARNING "FETCH_REDIS_DEPS=ON has no effect unless ENABLE_REDIS=ON")
    endif()

    if(NOT ENABLE_REDIS)
        message(STATUS "Redis support disabled")
        set(REDIS_AVAILABLE FALSE PARENT_SCOPE)
        return()
    endif()

    # 检测系统依赖
    # Detect system dependencies
    find_library(HIREDIS_LIB hiredis)
    find_library(REDIS_PLUS_PLUS_LIB redis++ PATHS /usr/local/lib)
    find_path(REDIS_PLUS_PLUS_HEADER sw/redis++/redis++.h PATHS /usr/local/include)

    set(REDIS_FOUND OFF)
    if(HIREDIS_LIB AND REDIS_PLUS_PLUS_LIB AND REDIS_PLUS_PLUS_HEADER)
        set(REDIS_FOUND ON)
        message(STATUS "Found system Redis dependencies")
    endif()

    # 自动下载
    # Auto-fetch if not found
    if(NOT REDIS_FOUND AND FETCH_REDIS_DEPS)
        include(FetchContent)
        message(STATUS "Fetching Redis dependencies via FetchContent...")

        # hiredis
        set(HIREDIS_BUILD_TESTS OFF CACHE BOOL "" FORCE)
        set(HIREDIS_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
        set(HIREDIS_BUILD_SHARED ON CACHE BOOL "" FORCE)
        set(HIREDIS_BUILD_STATIC OFF CACHE BOOL "" FORCE)
        FetchContent_Declare(hiredis
            GIT_REPOSITORY https://github.com/redis/hiredis.git
            GIT_TAG v1.2.0
        )
        FetchContent_MakeAvailable(hiredis)

        # 创建 hiredis 头文件符号链接
        # Create symbolic link for hiredis headers
        set(HIREDIS_INCLUDE_WRAPPER ${CMAKE_BINARY_DIR}/_hiredis_include)
        file(MAKE_DIRECTORY ${HIREDIS_INCLUDE_WRAPPER})
        if(NOT EXISTS ${HIREDIS_INCLUDE_WRAPPER}/hiredis)
            file(CREATE_LINK ${hiredis_SOURCE_DIR}
                 ${HIREDIS_INCLUDE_WRAPPER}/hiredis SYMBOLIC)
        endif()
        set(HIREDIS_HEADER ${HIREDIS_INCLUDE_WRAPPER} CACHE PATH "" FORCE)
        set(HIREDIS_LIB hiredis CACHE STRING "" FORCE)

        # redis-plus-plus
        set(REDIS_PLUS_PLUS_BUILD_TEST OFF CACHE BOOL "" FORCE)
        set(REDIS_PLUS_PLUS_BUILD_SHARED ON CACHE BOOL "" FORCE)
        set(REDIS_PLUS_PLUS_BUILD_STATIC OFF CACHE BOOL "" FORCE)
        set(REDIS_PLUS_PLUS_ENABLE_TLS OFF CACHE BOOL "" FORCE)
        FetchContent_Declare(redis_plus_plus
            GIT_REPOSITORY https://github.com/sewenew/redis-plus-plus.git
            GIT_TAG 1.3.10
        )
        FetchContent_MakeAvailable(redis_plus_plus)

        # 设置 RPATH 确保运行时找到动态库
        # Set RPATH to find shared libraries at runtime
        list(APPEND CMAKE_BUILD_RPATH
            ${CMAKE_BINARY_DIR}/_deps/hiredis-build
            ${CMAKE_BINARY_DIR}/_deps/redis_plus_plus-build
        )

        set(REDIS_FOUND ON)
    endif()

    # 导出结果到父作用域
    # Export results to parent scope
    set(REDIS_AVAILABLE ${REDIS_FOUND} PARENT_SCOPE)
    if(REDIS_FOUND)
        set(REDIS_LIBRARIES ${REDIS_PLUS_PLUS_LIB} ${HIREDIS_LIB} PARENT_SCOPE)
        set(REDIS_INCLUDE_DIRS ${REDIS_PLUS_PLUS_HEADER} PARENT_SCOPE)
    endif()
endfunction()

# ============================================================================
# 未来可以添加其他依赖，例如：
# Future dependencies can be added here, for example:
# ============================================================================
# function(setup_grpc_dependencies)
#     option(ENABLE_GRPC "Enable gRPC support" ON)
#     option(FETCH_GRPC_DEPS "Auto-fetch gRPC if not found" ON)
#     ...
# endfunction()
#
# function(setup_protobuf_dependencies)
#     ...
# endfunction()
