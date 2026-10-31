#include "redis_cache.hpp"
#include <stdexcept>
#include <string>
#include <cstdlib>
#include <chrono>

// 默认连接参数
static constexpr std::size_t DEFAULT_POOL_SIZE = 1;
static constexpr auto DEFAULT_TIMEOUT = std::chrono::milliseconds(100);

// 构造函数：使用默认路径（支持环境变量覆盖）
RedisCache::RedisCache() {
    // 优先使用环境变量，否则使用默认路径
    std::string socket_path = DEFAULT_SOCKET_PATH;
    const char* env_path = std::getenv("REDIS_SOCKET_PATH");
    if (env_path != nullptr && env_path[0] != '\0') {
        socket_path = env_path;
    }

    init(socket_path);
}

// 构造函数：显式指定路径（不受环境变量影响）
RedisCache::RedisCache(const std::string& socket_path) {
    init(socket_path);
}

// 校验 socket 路径
void RedisCache::validatePath(const std::string& path) {
    if (path.empty()) {
        throw std::invalid_argument("RedisCache: socket path cannot be empty");
    }

    if (path[0] != '/') {
        throw std::invalid_argument(
            "RedisCache: socket path must be absolute, got: " + path);
    }

    // Unix socket 路径长度限制（通常 108 字节，保守使用 107）
    if (path.length() > 107) {
        throw std::invalid_argument(
            "RedisCache: socket path too long (max 107 chars), got: " +
            std::to_string(path.length()));
    }
}

// 内部初始化方法
void RedisCache::init(const std::string& socket_path) {
    validatePath(socket_path);

    try {
        sw::redis::ConnectionOptions conn_opts;
        conn_opts.socket_timeout = DEFAULT_TIMEOUT;
        conn_opts.connect_timeout = DEFAULT_TIMEOUT;
        conn_opts.type = sw::redis::ConnectionType::UNIX;
        conn_opts.path = socket_path;

        sw::redis::ConnectionPoolOptions pool_opts;
        pool_opts.size = DEFAULT_POOL_SIZE;
        pool_opts.wait_timeout = DEFAULT_TIMEOUT;

        redis_ = std::make_unique<sw::redis::Redis>(conn_opts, pool_opts);
        redis_->ping();
    } catch (const std::exception& e) {
        std::string error_info = "UNIX(" + socket_path + ")";
        error_info += " (可通过 REDIS_SOCKET_PATH 环境变量覆盖)";
        throw std::runtime_error("RedisCache init failed [" + error_info + "]: " + e.what());
    }
}

void RedisCache::put(const std::string& key, const Value& value) {
    try {
        // 直接存储 value.data() 到 Redis
        std::string str_data(value.data().begin(), value.data().end());
        redis_->set(key, str_data);
    } catch (const sw::redis::Error& e) {
        throw std::runtime_error(std::string("[RedisCache] put() failed: ") + e.what());
    }
}

std::optional<Value> RedisCache::get(const std::string& key) const {
    try {
        auto str = redis_->get(key);
        if (!str) return std::nullopt;
        // 从 Redis 读取字节流，构造 Value
        return Value(std::vector<uint8_t>(str->begin(), str->end()));
    } catch (const sw::redis::Error& e) {
        throw std::runtime_error(std::string("[RedisCache] get() failed: ") + e.what());
    }
}

void RedisCache::del(const std::string& key) {
    try {
        redis_->del(key);
    } catch (const sw::redis::Error& e) {
        throw std::runtime_error(std::string("[RedisCache] del() failed: ") + e.what());
    }
}

bool RedisCache::exists(const std::string& key) const {
    try {
        return redis_->exists(key) > 0;
    } catch (const sw::redis::Error& e) {
        throw std::runtime_error(std::string("[RedisCache] exists() failed: ") + e.what());
    }
}

void RedisCache::flush() {
    const char* allow = std::getenv("REDIS_CACHE_ALLOW_FLUSHDB");
    if (allow == nullptr || std::string(allow) != "1") {
        throw std::runtime_error(
            "[RedisCache] flush() refused: set REDIS_CACHE_ALLOW_FLUSHDB=1 to allow FLUSHDB");
    }

    try {
        redis_->flushdb(false);
    } catch (const sw::redis::Error& e) {
        throw std::runtime_error(std::string("[RedisCache] flush() failed: ") + e.what());
    }
}
