# Cache 接口规范

> **版本**: v0.3.0
> **最后更新**: 2026-01-20
> **状态**: 正式版

---

## 1. 概述

LocalCache 是缓存层的抽象接口，定义了 KV 存储的标准方法。所有缓存实现（RedisCache, InMemoryCache）必须实现此接口。

### LocalCache 接口整体视图

```
LocalCache 接口
├── 📡 核心方法（纯虚函数）
│   ├── get(key) .......................... 获取缓存数据
│   ├── put(key, value) ................... 存储 Value 对象
│   ├── del(key) .......................... 删除缓存数据
│   ├── exists(key) ....................... 判断键是否存在
│   └── flush() ........................... 清空所有数据
│
├── 🔧 实现类
│   ├── RedisCache ........................ Redis 缓存（已实现）
│   │   ├── Unix socket 通信
│   │   ├── 持久化支持
│   │   ├── LRU 淘汰（Redis 配置）
│   │   ├── flush 执行 FLUSHDB（需环境变量保护）
│   │   └── 依赖：hiredis, redis++
│   │
│   └── InMemoryCache ..................... 内存缓存（已实现）
│       ├── std::unordered_map 存储
│       ├── 无持久化
│       ├── 无 LRU（需手动实现）
│       └── flush 清空整个 store_
│
├── 📦 Value 类型（opaque byte container）
│   ├── data() ........................... 访问原始字节
│   ├── size() ........................... 获取大小
│   └── empty() .......................... 检查是否为空
│
└── ⚙️ Redis 配置
    ├── unixsocket /run/redis/redis.sock
    ├── maxmemory 256mb
    └── maxmemory-policy allkeys-lru
```

### 快速跳转

| 类别 | 方法/实现 | 跳转 |
|------|----------|------|
| **核心方法** | get() | [→ 查看](#21-get) |
| | put() | [→ 查看](#22-put) |
| | del() | [→ 查看](#23-del) |
| | exists() | [→ 查看](#24-exists) |
| | flush() | [→ 查看](#25-flush) |
| **实现类** | RedisCache | [→ 查看](#31-rediscache) |
| | InMemoryCache | [→ 查看](#32-inmemorycache) |
| **Value 类型** | Value 类 | [→ 查看](#41-value-类) |
| **使用示例** | RedisCache 示例 | [→ 查看](#51-rediscache-完整示例) |
| | InMemoryCache 示例 | [→ 查看](#52-inmemorycache-完整示例) |
| **常见问题** | FAQ | [→ 查看](#6-常见问题) |

---

## 2. 接口定义

### 2.1 get()

**功能**: 获取缓存中的数据

**签名**:
```cpp
virtual std::optional<Value> get(const std::string& key) = 0;
```

**参数**:
- `key`: 键名（Redis key）

**返回值**:
- 如果找到，返回 `std::optional<Value>`（包含数据）
- 如果未找到，返回 `std::nullopt`

**前置条件**:
- 缓存接口已初始化

**后置条件**:
- 无副作用（只读操作）

**使用示例**:
```cpp
LocalCache* cache = new RedisCache("/run/redis/redis.sock");
auto value = cache->get("redis_key_a");
if (value) {
    // Value 是 opaque 的，应用层负责解析
    std::cout << "Value size: " << value->size() << " bytes" << std::endl;

    // 应用层解析（示例：假设是字符串）
    std::string str(value->data().begin(), value->data().end());
    std::cout << "Value: " << str << std::endl;
}
```

**实现要点**:
- **RedisCache**: 调用 Redis GET 命令
- **InMemoryCache**: 查询 `std::unordered_map`

**变更历史**:
- v0.1.0 (2025-12-01): 初始定义

---

### 2.2 put()

**功能**: 存储 Value 对象到缓存

**签名**:
```cpp
virtual void put(const std::string& key, const Value& value) = 0;
```

**参数**:
- `key`: 键名（Redis key）
- `value`: Value 对象（opaque byte container）

**前置条件**:
- 缓存接口已初始化
- `key` 不为空

**后置条件**:
- 数据已存入缓存
- 可以通过 `get(key)` 查询

**使用示例**:
```cpp
LocalCache* cache = new RedisCache("/run/redis/redis.sock");

// 从字节流创建 Value（应用层逻辑）
std::string str = "Hello, World!";
Value v(std::vector<uint8_t>(str.begin(), str.end()));

// 直接存储 Value 对象
cache->put("redis_key_a", v);
```

**实现要点**:
- **RedisCache**: 调用 Redis SET 命令，存储 `value.data()`
- **InMemoryCache**: 存储到 `std::unordered_map`

**变更历史**:
- v0.2.0 (2026-01-19): 新增 put() 方法，替代 putRaw()
- v0.1.0 (2025-12-01): 初始定义（putRaw）

---

### 2.3 del()

**功能**: 删除缓存中的数据

**签名**:
```cpp
virtual void del(const std::string& key) = 0;
```

**参数**:
- `key`: 键名（Redis key）

**前置条件**:
- 缓存接口已初始化

**后置条件**:
- 数据已从缓存中删除
- `get(key)` 返回 `std::nullopt`

**使用示例**:
```cpp
LocalCache* cache = new RedisCache("/run/redis/redis.sock");
cache->del("redis_key_a");
```

**实现要点**:
- **RedisCache**: 调用 Redis DEL 命令
- **InMemoryCache**: 从 `std::unordered_map` 删除

**变更历史**:
- v0.2.0 (2026-01-19): 重命名为 del()（原 remove()）
- v0.1.0 (2025-12-01): 初始定义

---

### 2.4 exists()

**功能**: 判断键是否存在

**签名**:
```cpp
virtual bool exists(const std::string& key) const = 0;
```

**参数**:
- `key`: 键名（Redis key）

**返回值**:
- `true`: 键存在
- `false`: 键不存在

**前置条件**:
- 缓存接口已初始化

**后置条件**:
- 无副作用（只读操作）

**使用示例**:
```cpp
LocalCache* cache = new RedisCache("/run/redis/redis.sock");
if (cache->exists("redis_key_a")) {
    std::cout << "Key exists" << std::endl;
}
```

**实现要点**:
- **RedisCache**: 调用 Redis EXISTS 命令
- **InMemoryCache**: 查询 `std::unordered_map`

**变更历史**:
- v0.2.0 (2026-01-19): 新增 exists() 方法

---

### 2.5 flush()

**功能**: 清空缓存所有数据

**签名**:
```cpp
virtual void flush() = 0;
```

**前置条件**:
- 缓存接口已初始化
- **RedisCache**: 必须设置环境变量 `REDIS_CACHE_ALLOW_FLUSHDB=1`

**后置条件**:
- **RedisCache**: 清空整个 Redis DB 的所有 key（执行 `FLUSHDB`）
- **InMemoryCache**: 清空整个 `store_`

**⚠️ 重要警告**:
- **RedisCache.flush()** 会删除当前 DB 的所有 key，请确保：
  - 测试/生产环境 Redis **不与其他服务共享同一 DB**
  - 只在测试代码中使用 `flush()`
  - 生产环境禁止设置 `REDIS_CACHE_ALLOW_FLUSHDB=1`

**使用示例**:
```cpp
// InMemoryCache：直接调用
InMemoryCache cache;
cache.put("key1", value1);
cache.flush();  // 清空所有数据
assert(!cache.exists("key1"));

// RedisCache：需要环境变量保护
RedisCache redis_cache("/run/redis/redis.sock");
setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);  // 允许 flush
redis_cache.flush();  // 执行 FLUSHDB
unsetenv("REDIS_CACHE_ALLOW_FLUSHDB");  // 清除环境变量

// 没有环境变量时会抛出异常
RedisCache redis_cache2("/run/redis/redis.sock");
try {
    redis_cache2.flush();  // ❌ 抛出 std::runtime_error
} catch (const std::runtime_error& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    // Error: [RedisCache] flush() refused: set REDIS_CACHE_ALLOW_FLUSHDB=1 to allow FLUSHDB
}
```

**实现要点**:
- **RedisCache**: 检查环境变量，然后调用 Redis `FLUSHDB` 命令
- **InMemoryCache**: 调用 `store_.clear()`

**变更历史**:
- v0.3.0 (2026-01-20): 新增 flush() 方法

---

## 3. 实现类

### 3.1 RedisCache

**用途**: 基于 Redis 的缓存实现

**特点**:
- 使用 Unix socket 与 Redis 通信
- 支持持久化
- 支持 LRU 淘汰（Redis 配置）

**实现文件**: `src/cache/redis_cache.hpp`, `src/cache/redis_cache.cpp`

**依赖**:
- hiredis: Redis C 客户端库
- redis++: Redis C++ 客户端库

**构造函数**:
```cpp
// 使用默认 Unix socket 路径（支持环境变量覆盖）
RedisCache();

// 显式指定 Unix socket 路径
RedisCache(const std::string& socket_path);
```

**使用示例**:
```cpp
#include "src/cache/redis_cache.hpp"

int main() {
    // 使用默认路径（推荐）
    RedisCache cache;

    // 存储数据（从字符串创建 Value）
    std::string str = "Hello, World!";
    Value v(std::vector<uint8_t>(str.begin(), str.end()));
    cache.put("key1", v);

    // 获取数据
    auto value = cache.get("key1");
    if (value) {
        // 应用层解析（转换为字符串）
        std::string result(value->data().begin(), value->data().end());
        std::cout << "Value: " << result << std::endl;
    }

    return 0;
}

**配置**:
```bash
# Redis 配置文件（redis.conf）
unixsocket /run/redis/redis.sock
unixsocketperm 777
maxmemory 256mb
maxmemory-policy allkeys-lru
```

---

### 3.2 InMemoryCache

**用途**: 基于内存的缓存实现（用于测试）

**特点**:
- 使用 `std::unordered_map` 存储
- 无持久化
- 无 LRU 淘汰（需要手动实现）

**实现文件**: `src/cache/in_memory_cache.hpp`, `src/cache/in_memory_cache.cpp`

**构造函数**:
```cpp
InMemoryCache();
```

**使用示例**:
```cpp
#include "src/cache/in_memory_cache.hpp"

int main() {
    InMemoryCache cache;

    // 存储数据（从字符串创建 Value）
    std::string str = "Hello, World!";
    Value v(std::vector<uint8_t>(str.begin(), str.end()));
    cache.put("key1", v);

    // 获取数据
    auto value = cache.get("key1");
    if (value) {
        // 应用层解析（转换为字符串）
        std::string result(value->data().begin(), value->data().end());
        std::cout << "Value: " << result << std::endl;
    }

    return 0;
}
```

---

## 4. Value 类型

### 4.1 概述

Value 是 opaque byte container（不透明字节容器），用于在缓存层传递数据。

**实现文件**: `src/payload.hpp`

**设计原则**:
- **Opaque byte container**: Value 是不透明的字节容器
- **Node 不解析**: Node 层不解析 Value 内容，只做透传/转发/存储
- **应用层解析**: 数据解析职责在应用层
- **最小化设计**: 不包含 fromString/toString/serialize/deserialize 方法

### 4.2 构造函数

**签名**:
```cpp
Value() = default;
explicit Value(std::vector<uint8_t> data);
```

**使用示例**:
```cpp
// 从字节流创建 Value
std::vector<uint8_t> bytes = {0x01, 0x02, 0x03};
Value v(bytes);

// 从字符串创建 Value（应用层逻辑）
std::string str = "Hello, World!";
Value v2(std::vector<uint8_t>(str.begin(), str.end()));
```

### 4.3 data()

**功能**: 访问原始字节流

**签名**:
```cpp
const std::vector<uint8_t>& data() const;
std::vector<uint8_t>& data();
```

**使用示例**:
```cpp
Value v(std::vector<uint8_t>{0x01, 0x02, 0x03});
const auto& bytes = v.data();  // 访问原始字节
```

### 4.4 size() / empty()

**功能**: 获取大小和检查是否为空

**签名**:
```cpp
size_t size() const;
bool empty() const;
```

**使用示例**:
```cpp
Value v(std::vector<uint8_t>{0x01, 0x02, 0x03});
std::cout << "Size: " << v.size() << " bytes" << std::endl;
if (!v.empty()) {
    std::cout << "Value is not empty" << std::endl;
}
```

---

## 5. 使用示例

### 5.1 RedisCache 完整示例

```cpp
#include "src/cache/redis_cache.hpp"
#include "src/payload.hpp"
#include <iostream>

int main() {
    // 创建 RedisCache 实例
    RedisCache cache("/run/redis/redis.sock");

    // 存储数据（从字符串创建 Value）
    std::string str = "VALUE_OF_A";
    Value v1(std::vector<uint8_t>(str.begin(), str.end()));
    cache.put("redis_key_a", v1);

    // 获取数据
    auto value = cache.get("redis_key_a");
    if (value) {
        // 应用层解析（转换为字符串）
        std::string result(value->data().begin(), value->data().end());
        std::cout << "Value: " << result << std::endl;
    }
    // 输出: Value: VALUE_OF_A

    // 删除数据
    cache.del("redis_key_a");

    // 验证删除
    auto value2 = cache.get("redis_key_a");
    assert(!value2.has_value());

    return 0;
}
```

### 5.2 InMemoryCache 完整示例

```cpp
#include "src/cache/in_memory_cache.hpp"
#include "src/payload.hpp"
#include <iostream>

int main() {
    // 创建 InMemoryCache 实例
    InMemoryCache cache;

    // 存储数据（从字符串创建 Value）
    std::string str1 = "VALUE_OF_A";
    Value v1(std::vector<uint8_t>(str1.begin(), str1.end()));
    cache.put("key1", v1);

    std::string str2 = "VALUE_OF_B";
    Value v2(std::vector<uint8_t>(str2.begin(), str2.end()));
    cache.put("key2", v2);

    // 获取数据
    auto value1 = cache.get("key1");
    auto value2 = cache.get("key2");

    if (value1 && value2) {
        // 应用层解析（转换为字符串）
        std::string result1(value1->data().begin(), value1->data().end());
        std::string result2(value2->data().begin(), value2->data().end());
        std::cout << "Value1: " << result1 << std::endl;
        std::cout << "Value2: " << result2 << std::endl;
    }
    // 输出:
    // Value1: VALUE_OF_A
    // Value2: VALUE_OF_B

    // 判断键是否存在
    if (cache.exists("key1")) {
        std::cout << "key1 exists" << std::endl;
    }

    // 删除数据
    cache.del("key1");

    // 验证删除
    auto value3 = cache.get("key1");
    assert(!value3.has_value());

    return 0;
}
```

---

## 6. 常见问题

### Q1: 为什么使用 Unix socket 而非 TCP？

**A**: Unix socket 性能更好（无需网络协议栈），适合同机通信。延迟约 10-20μs，而 TCP loopback 约 50-100μs。

### Q2: 如何配置 Redis？

**A**: 见 `docker/redis.conf.template`，关键配置：
- `unixsocket /run/redis/redis.sock`
- `maxmemory 256mb`
- `maxmemory-policy allkeys-lru`

### Q3: InMemoryCache 是否支持 LRU？

**A**: 当前不支持。如果需要，可以参考 SyncTable 的 LRU 实现。

### Q4: 如何处理 Redis 连接失败？

**A**: RedisCache 构造函数会抛出异常。建议捕获异常并回退到 InMemoryCache。

### Q5: 如何测试缓存层？

**A**: 使用 InMemoryCache 进行单元测试，使用 RedisCache 进行集成测试。

---

## 7. 未来改进

### 7.1 批量操作

**当前**: 单个 get/put

**未来**: 批量 get/put

```cpp
// 未来接口（草案）
virtual std::vector<std::optional<Value>> getBatch(const std::vector<std::string>& keys) = 0;
virtual void putBatch(const std::vector<std::pair<std::string, std::vector<uint8_t>>>& items) = 0;
```

### 7.2 TTL 支持

**当前**: 无 TTL

**未来**: 支持 TTL（Time To Live）

```cpp
// 未来接口（草案）
virtual void put(const std::string& key, const Value& value, std::chrono::seconds ttl) = 0;
```

### 7.3 异步 API

**当前**: 同步接口

**未来**: 异步接口 + Future/Promise

```cpp
// 未来接口（草案）
virtual std::future<std::optional<Value>> getAsync(const std::string& key) = 0;
virtual std::future<void> putAsync(const std::string& key, const Value& value) = 0;
```

---

## 8. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v0.3.0 | 2026-01-20 | **重大架构变更**：移除 prefix 机制，RedisCache 直接操作原始 key；新增 flush() 方法（RedisCache 执行 FLUSHDB，需环境变量保护）；更新所有示例代码 |
| v0.2.0 | 2026-01-19 | **重大架构变更**：移除 putRaw/getRaw，统一使用 Value 接口；重命名 remove() 为 del()；新增 exists() 方法；更新所有示例代码 |
| v0.1.0 | 2025-12-01 | 初始定义 |

---

**下一步**: 阅读 [global-types.md](./global-types.md) 了解全局类型定义
