# Cache 层说明

本目录存放可插拔的 KV 后端实现：

- `cache.hpp`：最小化 KV 接口（仅 `put/get/del/exists/flush`），不提供枚举/扫描。
- `in_memory_cache.*`：内存实现，单线程友好，默认供 demo 使用。
- `redis_cache.*`：基于 redis-plus-plus/hiredis 的 Redis 实现，通过 Unix socket 连接。

设计原则：
- 纯 KV API：只做精确 key 操作；Bloom Filter / 路由 / SyncTable 逻辑留在协议层。
- 后端错误要暴露（RedisCache 抛异常），不要当成 cache miss。
- RedisCache 直接操作原始 key，不添加前缀；如需命名空间隔离，请在 key 中自行编码。

## RedisCache 使用方式

**⚠️ 重要变更（v0.3.0）**：
- **已移除 prefix 机制**：RedisCache 不再自动添加前缀，直接使用原始 key
- **flush() 语义变更**：现在执行 `FLUSHDB`，清空整个 Redis DB，需要环境变量保护

### 基本用法

```cpp
#include "cache/redis_cache.hpp"

// 方式 1：使用默认路径（推荐，容器内使用）
// 路径优先级：环境变量 REDIS_SOCKET_PATH > 默认路径 /run/redis/redis.sock
RedisCache cache;

// 方式 2：显式指定 Unix socket 路径
RedisCache cache("/run/redis/redis.sock");
```

### flush() 安全保护

`flush()` 方法执行 Redis `FLUSHDB` 命令，会清空整个数据库。为防止误操作，**必须**设置环境变量：

```bash
# 允许执行 flush
export REDIS_CACHE_ALLOW_FLUSHDB=1
./your_application

# 或在代码启动前设置
setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);
cache.flush();  // 现在可以执行
```

**⚠️ 警告**：`flush()` 会删除当前 DB 的所有 key，请确保：
- 测试/生产环境 Redis **不与其他服务共享同一 DB**
- 只在测试代码中使用 `flush()`
- 生产环境禁止设置 `REDIS_CACHE_ALLOW_FLUSHDB=1`

### 命名空间隔离（如需）

如果需要节点/业务隔离，请在 key 设计中自行编码：

```cpp
RedisCache cache;

// 节点隔离：在 key 中包含节点 ID
std::string node_id = "node1";
cache.put(node_id + ":key1", value);

// 业务隔离：在 key 中包含业务前缀
cache.put("business_a:key1", value);
cache.put("business_b:key1", value);
```

### 环境变量覆盖

可通过环境变量 `REDIS_SOCKET_PATH` 覆盖默认 Unix socket 路径：

```bash
# 通过环境变量覆盖默认路径
export REDIS_SOCKET_PATH=/tmp/redis.sock
./your_application
```

**注意**：环境变量仅覆盖默认路径，显式指定的路径不受影响。

### 权限要求

- Unix socket 文件通常位于 `/run/redis/redis.sock`
- 需要确保应用程序有权限访问该 socket 文件（通常需要加入 `redis` 组）
- 容器化部署时，Redis 和应用在同一容器内通过 Unix socket 通信，性能最优

## 容器化部署

项目提供了单容器多进程部署方案（Redis + Router 同容器）：

### 配置架构

容器采用**单一环境变量驱动**的配置方式，`REDIS_SOCKET_PATH` 作为单一事实来源（SSOT）：

```
┌─────────────────────────────────────────────────────────┐
│  REDIS_SOCKET_PATH=/run/redis/redis.sock (默认)         │
│                        ↓                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ entrypoint  │→ │ redis.conf  │  │ healthcheck │     │
│  │   渲染      │  │  (模板)     │  │   (读env)   │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
│                        ↓                                │
│              Redis 服务端 + 客户端 + 健康检查           │
│                   共享同一路径                          │
└─────────────────────────────────────────────────────────┘
```

**优势**：
- 一处配置，处处生效
- 避免服务端/客户端路径不一致的问题
- 支持运行时动态配置

### 构建镜像

```bash
cd /path/to/rsync
./docker/build.sh
```

### 运行容器

```bash
# 使用默认路径
./docker/run.sh

# 或自定义 socket 路径
docker run -d --name rsync-router \
    -e REDIS_SOCKET_PATH=/custom/path/redis.sock \
    rsync-router
```

### 手动运行

```bash
# 构建镜像
docker build -t rsync-router .

# 运行容器（使用默认路径）
docker run -d --name rsync-router rsync-router

# 运行容器（自定义路径）
docker run -d --name rsync-router \
    -e REDIS_SOCKET_PATH=/tmp/redis.sock \
    rsync-router

# 查看日志
docker logs -f rsync-router

# 健康检查
docker exec rsync-router redis-cli -s "$REDIS_SOCKET_PATH" ping
```

### 容器内配置

- **Redis Unix socket**：由 `REDIS_SOCKET_PATH` 环境变量控制（默认 `/run/redis/redis.sock`）
- **配置渲染**：entrypoint.sh 在启动时将模板渲染为实际配置
- **进程管理**：supervisord（Redis priority=10, Router priority=20）
- **健康检查**：每 5 秒检查 Redis 连接（使用环境变量）
- **自动重启**：Redis 和 Router 进程异常退出时自动重启

### 文件说明

| 文件 | 说明 |
|------|------|
| `docker/entrypoint.sh` | 入口脚本，创建目录并渲染配置 |
| `docker/redis.conf.template` | Redis 配置模板 |
| `docker/supervisord.conf` | 进程管理配置 |
| `Dockerfile` | 镜像构建文件 |

## 迁移指南

### v3.x → v4.0（移除 RedisEndpoint）

**破坏性变更**：移除 `RedisEndpoint` 结构体，简化 API

#### 旧代码（v3.x）

```cpp
#include "cache/redis_cache.hpp"
#include "cache/redis_endpoint.hpp"

// 使用 RedisEndpoint（已移除）
rsync::RedisEndpoint endpoint;
RedisCache cache("node1:", endpoint);

// 或显式指定路径
auto endpoint = rsync::RedisEndpoint::Unix("/run/redis/redis.sock");
RedisCache cache("node1:", endpoint);
```

#### 新代码（v4.0+）

```cpp
#include "cache/redis_cache.hpp"

// 使用默认路径（推荐）
RedisCache cache("node1:");

// 或显式指定路径
RedisCache cache("node1:", "/run/redis/redis.sock");
```

#### 迁移步骤

1. **删除 include**：移除 `#include "cache/redis_endpoint.hpp"`
2. **简化构造**：
   - `RedisCache(prefix, endpoint)` → `RedisCache(prefix)` 或 `RedisCache(prefix, socket_path)`
   - 删除所有 `rsync::RedisEndpoint` 相关代码
3. **测试验证**：运行测试确保连接正常

---

### v0.2.x → v0.3.0（移除 prefix 机制）

**破坏性变更**：移除自动前缀机制，`flush()` 改为 FLUSHDB

#### 旧代码（v0.2.x）

```cpp
// 构造时提供 prefix
RedisCache cache("node1:");  // 或 RedisCache("node1:", socket_path)

// put/get 时自动添加前缀
cache.put("key1", value);  // 实际存储为 "node1:key1"
auto v = cache.get("key1");  // 实际查询 "node1:key1"

// flush 只清空当前 prefix 命名空间
cache.flush();  // SCAN "node1:*" + UNLINK
```

#### 新代码（v0.3.0+）

```cpp
// 构造时不再需要 prefix
RedisCache cache;  // 或 RedisCache(socket_path)

// 如需命名空间隔离，在 key 中自行编码
std::string node_id = "node1";
cache.put(node_id + ":key1", value);  // 存储为 "node1:key1"
auto v = cache.get(node_id + ":key1");  // 查询 "node1:key1"

// flush 清空整个 Redis DB（需要环境变量保护）
setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);
cache.flush();  // 执行 FLUSHDB
```

#### 迁移步骤

1. **移除 prefix 参数**：
   - `RedisCache("node1:")` → `RedisCache()`
   - `RedisCache("node1:", socket_path)` → `RedisCache(socket_path)`

2. **更新 key 编码**（如需隔离）：
   - 在 `put/get/del/exists` 调用中手动添加前缀
   - 示例：`cache.put(prefix + ":" + key, value)`

3. **更新 flush 调用**：
   - **测试代码**：添加 `setenv("REDIS_CACHE_ALLOW_FLUSHDB", "1", 1);`
   - **生产代码**：删除 `flush()` 调用或改用 `del()` 逐个删除

4. **测试验证**：
   - 编译：`cmake --build build --target test_redis`
   - 运行完整测试套件：`./scripts/tests/run_redis_suite.sh ./build/test_redis_cache_contract ./build/rsync-node-dev`
   - 仅运行缓存契约测试：`TEST_REDIS_SOCKET=/path/to/redis.sock ./build/test_redis_cache_contract`

## 构建提示

- 默认 demo 只编译 `in_memory_cache`。若要启用 RedisCache，请在构建中加入 `src/cache/redis_cache.cpp` 并链接 `redis++` / `hiredis`，或通过 vcpkg 配置依赖。
- 本代码不会启动 Redis 进程：需要你自行先启动 Redis（本机 `redis-server`，或容器/Compose/K8s 服务），然后在 `RedisCache` 里指定对应的 Unix socket 路径。
- 容器化部署时，Redis 和 Router 在同一容器内通过 Unix socket 通信，性能最优。
