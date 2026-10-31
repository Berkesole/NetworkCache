#include "redis_cache.hpp"
#include <stdexcept>
#include <string>
#include <cstdlib>
#include <chrono>

// 默认连接参数
static constexpr std::size_t DEFAULT_POOL_SIZE = 1;
static constexpr auto DEFAULT_TIMEOUT = std::chrono::milliseconds(100);

std::chrono::milliseconds configured_timeout() {
    const char* raw = std::getenv("RSYNC_REDIS_TIMEOUT_MS");
    if (raw == nullptr || raw[0] == '\0') return DEFAULT_TIMEOUT;
    try {
        const long value = std::stol(raw);
        if (value <= 0 || value > 60000) throw std::out_of_range("timeout");
        return std::chrono::milliseconds(value);
    } catch (const std::exception&) {
        throw std::runtime_error("RSYNC_REDIS_TIMEOUT_MS must be in 1..60000");
    }
}

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
    socket_path_ = socket_path;

    try {
        sw::redis::ConnectionOptions conn_opts;
        const auto timeout = configured_timeout();
        conn_opts.socket_timeout = timeout;
        conn_opts.connect_timeout = timeout;
        conn_opts.type = sw::redis::ConnectionType::UNIX;
        conn_opts.path = socket_path;

        sw::redis::ConnectionPoolOptions pool_opts;
        pool_opts.size = DEFAULT_POOL_SIZE;
        pool_opts.wait_timeout = timeout;

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
        // The command may have reached the server before the response was lost.
        // This is an INDETERMINATE result, not proof that the SET did not run.
        throw rsync_cache::BackendIndeterminate(
            "set", std::string("[RedisCache] set() result unknown: ") + e.what());
    }
}

std::optional<Value> RedisCache::get(const std::string& key) const {
    try {
        auto str = redis_->get(key);
        if (!str) return std::nullopt;
        // 从 Redis 读取字节流，构造 Value
        return Value(std::vector<uint8_t>(str->begin(), str->end()));
    } catch (const sw::redis::Error& e) {
        throw rsync_cache::BackendIndeterminate(
            "get", std::string("[RedisCache] get() result unknown: ") + e.what());
    }
}

void RedisCache::del(const std::string& key) {
    try {
        redis_->del(key);
    } catch (const sw::redis::Error& e) {
        // The DEL may already have removed the key even though the response was
        // lost; the caller must not assume the value is still resident.
        throw rsync_cache::BackendIndeterminate(
            "del", std::string("[RedisCache] del() result unknown: ") + e.what());
    }
}

bool RedisCache::exists(const std::string& key) const {
    try {
        return redis_->exists(key) > 0;
    } catch (const sw::redis::Error& e) {
        throw rsync_cache::BackendIndeterminate(
            "exists", std::string("[RedisCache] exists() result unknown: ") + e.what());
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

rsync_cache::RedisInstanceInfo RedisCache::readInstanceInfo(const std::string& identity) {
    rsync_cache::RedisInstanceInfo info;
    info.socket_path = socket_path_;
    info.identity = identity;
    try {
        // redis++'s command() defaults to a void output; ask explicitly for the
        // optional single-bulk reply. A lost/empty response leaves info_readable
        // false, which the caller reports as info_unreadable.
        const sw::redis::OptionalString raw =
            redis_->command<sw::redis::OptionalString>("INFO");
        if (!raw) {
            return info;
        }
        info = rsync_cache::parseRedisInfo(*raw, socket_path_, identity);
        try {
            info.db_keys = static_cast<long long>(redis_->dbsize());
        } catch (const sw::redis::Error&) {
            info.info_readable = false;
        }
        return info;
    } catch (const sw::redis::Error&) {
        // info_readable stays false; callers must reject, not assume healthy.
        return info;
    }
}
