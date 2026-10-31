# Table 接口规范

> **版本**: v0.2.0
> **最后更新**: 2026-01-16
> **状态**: 正式版

---

## 1. 概述

本文档定义两个状态表的接口：
- **QueryTable**: 跟踪待处理的请求（类似 NDN 的 PIT）
- **SyncTable**: 记录数据位置和路径（类似路由表 + FIB）

### 状态表整体视图

```
状态表系统
│
├── 📊 QueryTable（请求跟踪表）
│   ├── 数据结构
│   │   └── QueryEntry {request_id, filename, downstream_node, timeout}
│   │
│   ├── 核心方法
│   │   ├── addPending(req_id, node_id) .... 记录待处理请求
│   │   ├── getAndRemove(req_id) ........... 查询并移除请求
│   │   └── cleanup() ...................... 清理超时请求（500ms）
│   │
│   └── 设计特点
│       ├── 单源（每个请求只记录一个下游节点）
│       └── 超时自动清理（500ms）
│
└── 📈 SyncTable（数据位置表）
    ├── 数据结构
    │   └── SyncEntry {filename, node_ids[], last_access}
    │
    ├── 核心方法
    │   ├── set(node_id, filename) ......... 记录文件位置
    │   ├── lookup(filename) ............... 查询文件位置（单个节点）
    │   ├── lookupAll(filename) ............ 查询所有节点
    │   └── evictLRU() ..................... LRU 淘汰
    │
    └── 设计特点
        ├── 精确匹配（存储精确文件名，非 BloomFilter）
        ├── 多路径（支持记录多个节点）
        └── LRU 淘汰（预分配容量 1000）
```

### 快速跳转

| 类别 | 方法/特性 | 跳转 |
|------|----------|------|
| **QueryTable** | 概述 | [→ 查看](#2-querytable-接口) |
| | addPending() | [→ 查看](#23-addpending) |
| | getAndRemove() | [→ 查看](#24-getandremove) |
| | cleanup() | [→ 查看](#25-cleanup) |
| **SyncTable** | 概述 | [→ 查看](#3-synctable-接口) |
| | set() | [→ 查看](#33-set) |
| | lookup() | [→ 查看](#34-lookup) |
| | lookupAll() | [→ 查看](#35-lookupall) |
| | evictLRU() | [→ 查看](#36-evictlru) |
| **使用示例** | QueryTable 示例 | [→ 查看](#41-querytable-完整示例) |
| | SyncTable 示例 | [→ 查看](#42-synctable-完整示例) |
| **常见问题** | FAQ | [→ 查看](#5-常见问题) |

---

## 2. QueryTable 接口

### 2.1 概述

QueryTable 用于跟踪待处理的请求，记录"谁在等这个数据"。

**核心功能**:
- 记录请求 ID 和下游节点
- 查询并移除请求
- 超时自动清理（500ms）

### 2.2 数据结构

```cpp
struct QueryEntry {
    uint64_t request_id;                      // 请求 ID
    std::string filename;                     // 请求的文件名
    uint32_t downstream_node;                 // 下游节点 ID（单源）
    std::chrono::milliseconds timeout{500};   // 超时时间
};
```

**设计决策**:
- **单源**: 每个请求只记录一个下游节点（2026-01-08 会议决策）
- **超时**: 500ms 后自动清理，避免内存泄漏

---

### 2.3 addPending()

**功能**: 记录待处理请求

**签名**:
```cpp
void addPending(uint64_t request_id, uint32_t downstream_node);
```

**参数**:
- `request_id`: 请求 ID（全局唯一）
- `downstream_node`: 下游节点 ID

**前置条件**:
- `request_id` 不为 0
- `downstream_node` 是有效的节点 ID

**后置条件**:
- 请求已记录到 QueryTable
- 超时计时器已启动

**使用示例**:
```cpp
QueryTable qt;
qt.addPending(12345, 2);  // 记录请求 12345，下游节点是 2
```

**实现要点**:
```cpp
void QueryTable::addPending(uint64_t request_id, uint32_t downstream_node) {
    QueryEntry entry;
    entry.request_id = request_id;
    entry.downstream_node = downstream_node;
    entry.timeout = std::chrono::milliseconds(500);

    pending_requests_[request_id] = entry;
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 改为单源（单个 `downstream_node`）
- v0.1.0 (2025-12-01): 初始定义（多源）

---

### 2.4 getAndRemove()

**功能**: 查询并移除请求

**签名**:
```cpp
std::vector<uint32_t> getAndRemove(uint64_t request_id);
```

**参数**:
- `request_id`: 请求 ID

**返回值**:
- 下游节点 ID 列表（单源情况下只有一个元素）
- 如果请求不存在，返回空列表

**前置条件**:
- 无

**后置条件**:
- 请求已从 QueryTable 移除

**使用示例**:
```cpp
QueryTable qt;
qt.addPending(12345, 2);

auto downstream = qt.getAndRemove(12345);
for (uint32_t node_id : downstream) {
    std::cout << "Downstream node: " << node_id << std::endl;
}
// 输出: Downstream node: 2
```

**实现要点**:
```cpp
std::vector<uint32_t> QueryTable::getAndRemove(uint64_t request_id) {
    auto it = pending_requests_.find(request_id);
    if (it == pending_requests_.end()) {
        return {};  // 请求不存在
    }

    std::vector<uint32_t> result = {it->second.downstream_node};
    pending_requests_.erase(it);
    return result;
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 返回单元素列表（单源）
- v0.1.0 (2025-12-01): 初始定义（返回多元素列表）

---

### 2.5 cleanup()

**功能**: 清理超时的请求

**签名**:
```cpp
void cleanup();
```

**前置条件**:
- 无

**后置条件**:
- 超时的请求已被移除

**使用示例**:
```cpp
QueryTable qt;
qt.addPending(12345, 2);

// 等待 600ms
std::this_thread::sleep_for(std::chrono::milliseconds(600));

qt.cleanup();  // 移除超时的请求
```

**实现要点**:
```cpp
void QueryTable::cleanup() {
    auto now = std::chrono::steady_clock::now();

    for (auto it = pending_requests_.begin(); it != pending_requests_.end();) {
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            now - it->second.timestamp
        );

        if (elapsed > it->second.timeout) {
            it = pending_requests_.erase(it);
        } else {
            ++it;
        }
    }
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 新增方法，支持超时清理

---

## 3. SyncTable 接口

### 3.1 概述

SyncTable 用于记录数据位置，类似路由表但存储精确文件名（非 Bloom Filter）。

**核心功能**:
- 记录文件位置（文件名 → 节点 ID 列表）
- 查询文件位置
- LRU 淘汰（预分配容量 1000）

### 3.2 数据结构

```cpp
struct SyncEntry {
    std::string filename;                     // 文件名
    std::vector<uint32_t> node_ids;           // 拥有该文件的节点列表
    std::chrono::steady_clock::time_point last_access;  // 最后访问时间
};
```

**设计决策**:
- **精确匹配**: 存储精确文件名，避免 BF 误判（2026-01-08 会议决策）
- **多路径**: 支持记录多个节点拥有同一文件
- **LRU 淘汰**: 预分配容量 1000，满时淘汰最旧条目

---

### 3.3 set()

**功能**: 记录文件位置

**签名**:
```cpp
void set(uint32_t node_id, const std::string& filename);
```

**参数**:
- `node_id`: 拥有该文件的节点 ID
- `filename`: 文件名

**前置条件**:
- `node_id` 是有效的节点 ID
- `filename` 不为空

**后置条件**:
- 文件位置已记录到 SyncTable
- 如果容量已满，最旧的条目被淘汰

**使用示例**:
```cpp
SyncTable st;
st.set(3, "redis_key_a");  // 节点 3 拥有 redis_key_a
```

**实现要点**:
```cpp
void SyncTable::set(uint32_t node_id, const std::string& filename) {
    // 如果容量已满，淘汰最旧条目
    if (file_to_nodes_.size() >= max_capacity_) {
        evictLRU();
    }

    // 添加或更新条目
    auto& nodes = file_to_nodes_[filename];
    if (std::find(nodes.begin(), nodes.end(), node_id) == nodes.end()) {
        nodes.push_back(node_id);
    }

    // 更新访问时间
    last_access_[filename] = std::chrono::steady_clock::now();
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 改为存储精确文件名（非 BloomFilter）
- v0.1.0 (2025-12-01): 初始定义（存储 BloomFilter）

---

### 3.4 lookup()

**功能**: 查询文件位置

**签名**:
```cpp
std::optional<uint32_t> lookup(const std::string& filename);
```

**参数**:
- `filename`: 文件名

**返回值**:
- 如果找到，返回拥有该文件的节点 ID（多个节点时返回第一个）
- 如果未找到，返回 `std::nullopt`

**前置条件**:
- 无

**后置条件**:
- 访问时间已更新（LRU）

**使用示例**:
```cpp
SyncTable st;
st.set(3, "redis_key_a");

auto node_id = st.lookup("redis_key_a");
if (node_id) {
    std::cout << "File found on node " << *node_id << std::endl;
}
// 输出: File found on node 3
```

**实现要点**:
```cpp
std::optional<uint32_t> SyncTable::lookup(const std::string& filename) {
    auto it = file_to_nodes_.find(filename);
    if (it == file_to_nodes_.end() || it->second.empty()) {
        return std::nullopt;
    }

    // 更新访问时间
    last_access_[filename] = std::chrono::steady_clock::now();

    // 返回第一个节点 ID
    return it->second[0];
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 改为查询精确文件名
- v0.1.0 (2025-12-01): 初始定义（查询 BloomFilter）

---

### 3.5 lookupAll()

**功能**: 查询所有拥有该文件的节点

**签名**:
```cpp
std::vector<uint32_t> lookupAll(const std::string& filename);
```

**参数**:
- `filename`: 文件名

**返回值**:
- 拥有该文件的所有节点 ID 列表
- 如果未找到，返回空列表

**前置条件**:
- 无

**后置条件**:
- 访问时间已更新（LRU）

**使用示例**:
```cpp
SyncTable st;
st.set(2, "redis_key_a");
st.set(3, "redis_key_a");

auto nodes = st.lookupAll("redis_key_a");
for (uint32_t node_id : nodes) {
    std::cout << "Node " << node_id << " has the file" << std::endl;
}
// 输出:
// Node 2 has the file
// Node 3 has the file
```

**实现要点**:
```cpp
std::vector<uint32_t> SyncTable::lookupAll(const std::string& filename) {
    auto it = file_to_nodes_.find(filename);
    if (it == file_to_nodes_.end()) {
        return {};
    }

    // 更新访问时间
    last_access_[filename] = std::chrono::steady_clock::now();

    return it->second;
}
```

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，支持多路径查询

---

### 3.6 evictLRU()

**功能**: 淘汰最旧的条目（LRU）

**签名**:
```cpp
void evictLRU();
```

**前置条件**:
- SyncTable 不为空

**后置条件**:
- 最旧的条目已被移除

**使用示例**:
```cpp
SyncTable st(1000);  // 容量 1000

// 插入 1001 个条目
for (int i = 0; i < 1001; ++i) {
    st.set(1, "file_" + std::to_string(i));
}

// 最旧的条目（file_0）已被淘汰
auto node_id = st.lookup("file_0");
assert(!node_id.has_value());  // file_0 已被淘汰
```

**实现要点**:
```cpp
void SyncTable::evictLRU() {
    if (file_to_nodes_.empty()) {
        return;
    }

    // 找到最旧的条目
    auto oldest_it = std::min_element(
        last_access_.begin(),
        last_access_.end(),
        [](const auto& a, const auto& b) {
            return a.second < b.second;
        }
    );

    // 移除最旧的条目
    file_to_nodes_.erase(oldest_it->first);
    last_access_.erase(oldest_it);
}
```

**变更历史**:
- v0.2.0 (2026-01-08): 新增方法，支持 LRU 淘汰

---

## 4. 使用示例

### 4.1 QueryTable 完整示例

```cpp
#include "src/query_table.hpp"
#include <iostream>

int main() {
    QueryTable qt;

    // 记录请求
    qt.addPending(12345, 2);
    qt.addPending(67890, 3);

    // 查询并移除
    auto downstream = qt.getAndRemove(12345);
    for (uint32_t node_id : downstream) {
        std::cout << "Send data to node " << node_id << std::endl;
    }
    // 输出: Send data to node 2

    // 清理超时请求
    std::this_thread::sleep_for(std::chrono::milliseconds(600));
    qt.cleanup();

    // 请求 67890 已超时
    auto downstream2 = qt.getAndRemove(67890);
    assert(downstream2.empty());  // 已被清理

    return 0;
}
```

### 4.2 SyncTable 完整示例

```cpp
#include "src/sync_table.hpp"
#include <iostream>

int main() {
    SyncTable st(1000);  // 容量 1000

    // 记录文件位置
    st.set(2, "redis_key_a");
    st.set(3, "redis_key_a");
    st.set(3, "redis_key_b");

    // 查询文件位置
    auto node_id = st.lookup("redis_key_a");
    if (node_id) {
        std::cout << "File found on node " << *node_id << std::endl;
    }
    // 输出: File found on node 2

    // 查询所有节点
    auto nodes = st.lookupAll("redis_key_a");
    for (uint32_t id : nodes) {
        std::cout << "Node " << id << " has the file" << std::endl;
    }
    // 输出:
    // Node 2 has the file
    // Node 3 has the file

    // LRU 淘汰测试
    for (int i = 0; i < 1001; ++i) {
        st.set(1, "file_" + std::to_string(i));
    }

    // redis_key_a 已被淘汰
    auto node_id2 = st.lookup("redis_key_a");
    assert(!node_id2.has_value());

    return 0;
}
```

---

## 5. 常见问题

### Q1: 为什么 QueryTable 是单源？

**A**: 见 [architecture.md](./architecture.md) 第 4.4 节。简而言之，为了简化逻辑、避免重复发送。

### Q2: 为什么 SyncTable 存储精确文件名而非 BloomFilter？

**A**: 见 [architecture.md](./architecture.md) 第 4.3 节。简而言之，为了避免 BF 误判、支持 LRU 淘汰。

### Q3: SyncTable 容量满了怎么办？

**A**: 自动淘汰最旧的条目（LRU）。预分配容量建议 1000。

### Q4: QueryTable 超时时间为什么是 500ms？

**A**: 基于经验值。如果数据在 500ms 内未返回，可能是网络故障或数据不存在。

### Q5: 如何处理 SyncTable 的多路径？

**A**: 使用 `lookupAll()` 获取所有节点，然后选择最优路径（如最近的节点、负载最低的节点）。

---

## 6. 未来改进

### 6.1 QueryTable 优先级队列

**当前**: 无优先级

**未来**: 支持优先级队列（高优先级请求优先处理）

```cpp
// 未来接口（草案）
void addPending(uint64_t request_id, uint32_t downstream_node, Priority priority);
```

### 6.2 SyncTable 路径选择策略

**当前**: 返回第一个节点

**未来**: 支持多种策略（最近节点、负载均衡、随机选择）

```cpp
// 未来接口（草案）
std::optional<uint32_t> lookup(const std::string& filename, SelectionStrategy strategy);
```

### 6.3 SyncTable 持久化

**当前**: 内存存储

**未来**: 支持持久化到磁盘（如 RocksDB）

```cpp
// 未来接口（草案）
void save(const std::string& path);
void load(const std::string& path);
```

---

## 7. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v0.2.0 | 2026-01-16 | 新增 `lookupAll()`, `evictLRU()`, `cleanup()` 方法 |
| v0.2.0 | 2026-01-08 | QueryTable 改为单源，SyncTable 改为存储精确文件名 |
| v0.1.0 | 2025-12-01 | 初始定义 |

---

**下一步**: 阅读 [cache-interface.md](./cache-interface.md) 了解缓存层接口
