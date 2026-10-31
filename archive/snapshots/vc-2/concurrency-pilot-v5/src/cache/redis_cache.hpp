#pragma once

#include "cache.hpp"
#include <memory>
#include <string>
#include <sw/redis++/redis.h>

/**
 * Redis 缓存实现（仅支持 Unix socket）
 * Redis cache implementation (Unix socket only)
 *
 * 连接配置：
 * - 默认路径：/run/redis/redis.sock
 * - 环境变量 REDIS_SOCKET_PATH 可覆盖默认路径
 * - 容器化部署时，Redis 和应用在同一容器内通过 Unix socket 通信
 *
 * 示例：
 *   // 使用默认路径（推荐）
 *   RedisCache cache;
 *
 *   // 显式指定路径
 *   RedisCache cache("/tmp/redis.sock");
 */
class RedisCache : public LocalCache {
public:
    static constexpr const char* DEFAULT_SOCKET_PATH = "/run/redis/redis.sock";

    /**
     * 构造函数：使用默认 Unix socket 路径（支持环境变量覆盖）
     *
     * 路径优先级：
     * 1. 环境变量 REDIS_SOCKET_PATH（如果设置）
     * 2. 默认路径 /run/redis/redis.sock
     */
    RedisCache();

    /**
     * 构造函数：显式指定 Unix socket 路径（不受环境变量影响）
     * @param socket_path Unix socket 路径（必须是绝对路径）
     */
    explicit RedisCache(const std::string& socket_path);

    ~RedisCache() override = default;

    void put(const std::string& key, const Value& value) override;
    std::optional<Value> get(const std::string& key) const override;
    void del(const std::string& key) override;
    bool exists(const std::string& key) const override;
    void flush() override;

private:
    // 内部初始化方法
    void init(const std::string& socket_path);

    // 校验 socket 路径
    static void validatePath(const std::string& path);

    std::unique_ptr<sw::redis::Redis> redis_;
};
