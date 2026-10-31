# Node 接口规范

> **版本**: v0.3.0
> **最后更新**: 2026-01-19
> **状态**: 正式版

---

## 1. 概述

Node 是协议层的核心类，负责：
- 处理数据包（REQUEST / DATA）
- 维护状态表（QueryTable / SyncTable）
- 与网络层和缓存层交互

### 架构原则

**Node 是数据透传层**：
- **Value 对 Node 是 opaque 的**：Node 不解析 Value 内容，只做字节流处理
- **Node 的三种操作**：
  1. **供给上层应用**：将数据回调给应用层，由应用层解析
  2. **转发给其他节点**：通过网络接口转发数据包
  3. **存储到 Redis**：将字节流直接存入缓存
- **数据解析职责在应用层**：只有应用层知道数据格式（JSON、Tensor 等）

**设计理由**：
- 保持 Node 层简单，专注于路由和缓存
- 支持多种数据格式，无需修改 Node 代码
- 符合关注点分离原则

### Node 类整体视图

```
Node 类
├── 📌 构造函数
│   └── Node(id, network, cache)
│
├── 🌐 公共接口（应用层调用）
│   ├── requestFile(const std::vector<std::string>& keys) .................. 发起文件请求 （改成vector）
│   ├── processOne() ...................... 处理一个数据包
│   ├── addLocalData(key, value) .......... 添加本地数据
│   └── enqueuePacket(pkt) ................ 接收数据包（网络层调用）
│
├── 🔒 私有接口（内部状态机）
│   ├── handleRequest(pkt) ................ 处理 REQUEST 包
│   ├── handleData(pkt) ................... 处理 DATA 包
│   ├── sendDirect(pkt, dest_id) .......... 发送给指定节点
│   ├── sendBroadcast(pkt) ................ 广播给所有邻居
│   ├── setQueryTable(req_id, node_id) .... 记录请求
│   ├── searchSyncTable(filename) ......... 查询文件位置
│   └── getLocalData(key) ................. 查询本地缓存
│
└── 💾 成员变量
    ├── id_ ............................... 节点 ID
    ├── inbox_ ............................ 接收队列
    ├── qt_ ............................... QueryTable（请求跟踪）
    ├── st_ ............................... SyncTable（数据位置）
    ├── network_ .......................... NetworkInterface（网络接口）
    └── cache_ ............................ LocalCache（缓存接口）
```

### 快速跳转

| 类别 | 方法/成员 | 跳转 |
|------|----------|------|
| **构造函数** | Node(id, network, cache) | [→ 查看](#21-构造函数) |
| **公共接口** | requestFile() | [→ 查看](#22-requestfile) |
| | processOne() | [→ 查看](#23-processone) |
| | addLocalData() | [→ 查看](#24-addlocaldata) |
| | enqueuePacket() | [→ 查看](#25-enqueuepacket) |
| **私有接口** | handleRequest() | [→ 查看](#31-handlerequest) |
| | handleData() | [→ 查看](#32-handledata) |
| | sendDirect() | [→ 查看](#33-senddirect) |
| | sendBroadcast() | [→ 查看](#34-sendbroadcast) |
| **成员变量** | 已删除的 neighbors_ | [→ 查看](#41-已删除的成员变量) |
| | 当前成员变量 | [→ 查看](#42-当前成员变量) |
| **依赖关系** | 依赖的接口 | [→ 查看](#51-依赖的接口) |
| **使用示例** | 基本使用 | [→ 查看](#61-基本使用) |

---

## 2. 公共接口

### 2.1 构造函数

**签名**:
```cpp
Node(uint32_t id,
     std::unique_ptr<NetworkInterface> network = nullptr,
     std::unique_ptr<LocalCache> cache = nullptr);
```

**参数**:
- `id`: 节点 ID（全局唯一）
- `network`: 网络接口实现（可选，默认使用 MockNetwork）
- `cache`: 缓存接口实现（可选，默认使用 InMemoryCache）

**前置条件**:
- `id` 必须唯一
- 如果提供 `network`，必须已初始化

**后置条件**:
- Node 对象已创建
- 如果 `network` 为空，自动创建 MockNetwork
- 如果 `cache` 为空，自动创建 InMemoryCache

**使用示例**:
```cpp
// 使用默认实现
Node server1(1);

// 注入自定义网络和缓存
auto net = std::make_unique<MockNetwork>(2);
auto cache = std::make_unique<RedisCache>("/run/redis/redis.sock");
Node server2(2, std::move(net), std::move(cache));
```

**变更历史**:
- v0.2.0 (2026-01-16): 构造函数支持依赖注入
- v0.1.0 (2025-12-01): 初始定义

---

### 2.2 requestFile()

**功能**: 发起文件请求，触发 REQUEST 包广播

**签名**:
```cpp
void requestFile(const std::string& key);
```

**参数**:
- `key`: 请求的文件名（Redis key）

**前置条件**:
- Node 已初始化
- 网络接口已配置（调用过 `configure_topology()`）

**后置条件**:
- REQUEST 包已通过 `network_->broadcast()` 发送给所有邻居
- QueryTable 已记录请求（`request_id` → `my_node_id`）

**异常情况**:
- 如果网络未配置，行为未定义（建议添加断言）

**使用示例**:
```cpp
Node server1(1);
server1.requestFile("redis_key_a");
// 内部流程：
// 1. 创建 REQUEST 包（包含 BF）
// 2. 记录到 QueryTable
// 3. 调用 network_->broadcast(pkt)
```

**实现要点**:
- 使用 `Packet::createRequestPkt(id_, {key})` 创建数据包
- 调用 `setQueryTable(pkt->request_id, id_)` 记录请求
- 调用 `sendBroadcast(std::move(pkt))` 广播

**变更历史**:
- v0.2.0 (2026-01-16): 改为调用 `network_->broadcast()`，不再遍历 `neighbors_`
- v0.1.0 (2025-12-01): 初始定义

---

### 2.3 processOne()

**功能**: 处理 inbox 中的一个数据包

**签名**:
```cpp
void processOne();
```

**前置条件**:
- 无（如果 inbox 为空，直接返回）

**后置条件**:
- 如果 inbox 不为空，一个数据包被处理
- 根据包类型调用对应的处理函数（`handleRequest` / `handleData`）
- 状态表可能被更新

**异常情况**:
- 如果遇到未知包类型，输出错误日志并忽略

**使用示例**:
```cpp
Node server1(1);
// 事件循环
for (int i = 0; i < 10; ++i) {
    server1.processOne();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
}
```

**实现要点**:
```cpp
void Node::processOne() {
    if (inbox_.empty()) return;

    auto pkt = std::move(inbox_.front());
    inbox_.pop();

    // 基于 type 字段进行类型转换（使用 static_cast）
    switch (pkt->type) {
        case PacketType::REQUEST: {
            auto* req_pkt = static_cast<RequestPacket*>(pkt.get());
            handleRequest(req_pkt);  // 传递子类指针
            break;
        }
        case PacketType::DATA: {
            auto* data_pkt = static_cast<DataPacket*>(pkt.get());
            handleData(data_pkt);  // 传递子类指针
            break;
        }
        default:
            std::cerr << "Unknown packet type" << std::endl;
            break;
    }
}
```

**设计说明**:
- 使用 `static_cast` 而非 `dynamic_cast`，避免 RTTI 开销
- 转换逻辑集中在 `processOne()` 中，处理函数类型明确
- 编译器检查类型安全，避免访问错误字段

**变更历史**:
- v0.3.0 (2026-01-19): 更新实现说明，展示 static_cast 转换和子类指针传递
- v0.2.0 (2026-01-08): 移除 SYNC 包处理分支
- v0.1.0 (2025-12-01): 初始定义

---

### 2.4 addLocalData()

**功能**: 向本地缓存添加数据（用于测试）

**签名**:
```cpp
void addLocalData(const std::string& key, const std::string& value);
```

**参数**:
- `key`: 文件名（Redis key）
- `value`: 文件内容（字符串）

**前置条件**:
- 缓存接口已初始化

**后置条件**:
- 数据已存入本地缓存
- 可以通过 `getLocalData(key)` 查询

**使用示例**:
```cpp
Node server3(3);
server3.addLocalData("redis_key_a", "VALUE_OF_A");
```

**实现要点**:
- 将字符串转换为 `Value` 对象
- 调用 `cache_->put(key, value)`

**变更历史**:
- v0.1.0 (2025-12-01): 初始定义

---

### 2.5 enqueuePacket()

**功能**: 将数据包放入 inbox（由网络层调用）

**签名**:
```cpp
void enqueuePacket(std::unique_ptr<Packet> pkt);
```

**参数**:
- `pkt`: 接收到的数据包

**前置条件**:
- `pkt` 不为空

**后置条件**:
- 数据包已加入 `inbox_` 队列
- 等待 `processOne()` 处理

**使用示例**:
```cpp
// 通常由 NetworkInterface 调用
auto pkt = std::make_unique<Packet>();
pkt->type = PacketType::REQUEST;
node->enqueuePacket(std::move(pkt));
```

**变更历史**:
- v0.1.0 (2025-12-01): 初始定义

---

## 3. 私有接口（内部方法）

### 3.1 handleRequest()

**功能**: 处理 REQUEST 包

**签名**:
```cpp
void handleRequest(RequestPacket* pkt);
```

**参数**:
- `pkt`: REQUEST 数据包指针（已通过 static_cast 转换）

**处理流程**:
1. 遍历 Bloom Filter 中可能存在的 key
2. 检查本地缓存是否有对应的文件
3. 如果有，创建 DATA 包并通过 `send_direct()` 返回
4. 如果没有，记录到 QueryTable 并通过 `broadcast()` 转发

**实现要点**:
```cpp
void Node::handleRequest(RequestPacket* pkt) {
    // 遍历本地缓存的所有 key
    for (const auto& key : local_cache_keys) {
        // 检查 BF 是否包含这个 key
        if (pkt->bf.contains(key)) {
            // 查询本地缓存
            auto value = cache_->get(key);
            if (value) {
                // 创建 DATA 包返回
                auto data_pkt = DataPacket::create(
                    pkt->request_id,
                    pkt->src_node_id,
                    key,
                    std::move(*value)
                );
                sendDirect(std::move(data_pkt), pkt->src_node_id);
            }
        }
    }

    // 如果没有找到任何数据，转发请求
    if (!found_any) {
        qt_.addPending(pkt->request_id, pkt->src_node_id);
        sendBroadcast(std::move(pkt));
    }
}
```

**变更历史**:
- v0.3.0 (2026-01-19): 参数改为 `RequestPacket*`，使用子类指针
- v0.2.0 (2026-01-16): 改为调用 `network_->broadcast()`，不再遍历 `neighbors_`
- v0.1.0 (2025-12-01): 初始定义

---

### 3.2 handleData()

**功能**: 处理 DATA 包

**签名**:
```cpp
void handleData(DataPacket* pkt);
```

**参数**:
- `pkt`: DATA 数据包指针（已通过 static_cast 转换）

**处理流程**:
1. 将数据存入本地缓存（**不解析 Value 内容**）
2. 更新 SyncTable（记录数据来源）
3. 查询 QueryTable（谁在等这个数据？）
4. 如果是发起者，回调应用层；否则转发给下游

**实现要点**:
```cpp
void Node::handleData(DataPacket* pkt) {
    // 1. 存储到缓存（不解析 Value 内容）
    cache_->put(pkt->key, pkt->value);

    // 2. 更新 SyncTable
    st_.set(pkt->src_node_id, pkt->key);

    // 3. 查询 QueryTable
    auto downstream_node = qt_.getAndRemove(pkt->request_id);

    if (downstream_node) {
        if (*downstream_node == id_) {
            // 4a. 供给应用层（不解析内容）
            notifyApplication(pkt->key, pkt->value);
        } else {
            // 4b. 转发给下游节点
            sendDirect(std::move(pkt), *downstream_node);
        }
    }
}
```

**架构原则**:
- **Node 不解析 Value**：直接将 Value 对象存入缓存
- **三种操作**：
  - 存储：`cache_->put(key, value)`
  - 转发：`sendDirect(pkt, downstream_node)`
  - 供给应用：`notifyApplication(key, value)`（应用层负责解析）

**变更历史**:
- v0.3.0 (2026-01-19): 参数改为 `DataPacket*`，使用子类指针；强调 Node 不解析 Value 内容
- v0.2.0 (2026-01-16): 改为调用 `network_->send_direct()`，不再遍历 `neighbors_`
- v0.2.0 (2026-01-08): DATA 包到达时自动更新 SyncTable
- v0.1.0 (2025-12-01): 初始定义

---

### 3.3 sendDirect()

**功能**: 发送数据包给指定节点（网络层包装）

**签名**:
```cpp
void sendDirect(std::unique_ptr<Packet> pkt, uint32_t dest_node_id);
```

**参数**:
- `pkt`: 要发送的数据包
- `dest_node_id`: 目标节点 ID

**实现**:
```cpp
void Node::sendDirect(std::unique_ptr<Packet> pkt, const uint32_t dest_node_id) {
    network_->send_direct(std::move(pkt), dest_node_id);
}
```

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，包装 `network_->send_direct()`

---

### 3.4 sendBroadcast()

**功能**: 广播数据包给所有邻居（网络层包装）

**签名**:
```cpp
void sendBroadcast(std::unique_ptr<Packet> pkt);
```

**参数**:
- `pkt`: 要广播的数据包

**实现**:
```cpp
void Node::sendBroadcast(std::unique_ptr<Packet> pkt) {
    network_->send_broadcast(std::move(pkt));
}
```

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，包装 `network_->send_broadcast()`

---

### 3.5 setQueryTable()

**功能**: 记录请求到 QueryTable

**签名**:
```cpp
void setQueryTable(uint64_t request_id, uint32_t downstream_node);
```

**参数**:
- `request_id`: 请求 ID
- `downstream_node`: 下游节点 ID

**实现**:
```cpp
void Node::setQueryTable(uint64_t request_id, uint32_t downstream_node) {
    qt_.addPending(request_id, downstream_node);
}
```

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，包装 QueryTable 操作

---

### 3.6 searchSyncTable()

**功能**: 查询 SyncTable，找到拥有指定文件的节点

**签名**:
```cpp
std::optional<uint32_t> searchSyncTable(const std::string& filename);
```

**参数**:
- `filename`: 文件名

**返回值**:
- 如果找到，返回节点 ID
- 如果未找到，返回 `std::nullopt`

**实现**:
```cpp
std::optional<uint32_t> Node::searchSyncTable(const std::string& filename) {
    return st_.lookup(filename);
}
```

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，包装 SyncTable 操作

---

## 4. 成员变量

### 4.1 已删除的成员变量

**变量**: `std::vector<Node*> neighbors_`

**删除原因**: 见 [architecture.md](./architecture.md) 第 4.1 节

**替代方案**:
- 使用 `network_->broadcast()` 替代遍历 `neighbors_`
- 使用 `network_->send_direct(node_id, pkt)` 替代 `neighbor->enqueuePacket(pkt)`
- 使用 `network_->get_neighbors()` 获取邻居列表（如果需要）

**影响**:
- `addNeighbor()` 方法被删除
- `handleRequest()` 和 `handleData()` 中的转发逻辑改为调用网络接口

---

### 4.2 当前成员变量

| 变量名 | 类型 | 职责 |
|--------|------|------|
| `id_` | `uint32_t` | 节点 ID |
| `inbox_` | `std::queue<std::unique_ptr<Packet>>` | 接收队列 |
| `qt_` | `QueryTable` | 请求跟踪表 |
| `st_` | `SyncTable` | 数据位置表 |
| `network_` | `std::unique_ptr<NetworkInterface>` | 网络接口 |
| `cache_` | `std::unique_ptr<LocalCache>` | 缓存接口 |

---

## 5. 依赖关系

### 5.1 依赖的接口

- **NetworkInterface**: `send_direct()`, `broadcast()`, `receive()`, `configure_topology()`, `get_neighbors()`
- **LocalCache**: `get()`, `put()`, `del()`, `exists()`
- **SyncTable**: `set()`, `lookup()`
- **QueryTable**: `addPending()`, `getAndRemove()`
- **Packet**: `createRequestPkt()`, `createDataPkt()`

### 5.2 被依赖的模块

- **main.cpp**: 创建 Node 实例，调用 `requestFile()` 和 `processOne()`
- **测试程序**: 调用 `addLocalData()` 准备测试数据

---

## 6. 使用示例

### 6.1 基本使用

```cpp
#include "src/node.hpp"
#include "src/network/mock_network.hpp"

int main() {
    // 创建 MockNetwork 实例
    auto net1 = std::make_unique<MockNetwork>(1);
    auto net2 = std::make_unique<MockNetwork>(2);
    auto net3 = std::make_unique<MockNetwork>(3);

    // 配置拓扑
    net1->configure_topology("config/3-node-topology.yaml", 1);
    net2->configure_topology("config/3-node-topology.yaml", 2);
    net3->configure_topology("config/3-node-topology.yaml", 3);

    // 创建节点（注入 NetworkInterface）
    Node server1(1, std::move(net1));
    Node server2(2, std::move(net2));
    Node server3(3, std::move(net3));

    // 准备数据和发起请求
    server3.addLocalData("redis_key_a", "VALUE_OF_A");
    server1.requestFile("redis_key_a");

    // 事件循环
    for (int i = 0; i < 10; ++i) {
        server1.processOne();
        server2.processOne();
        server3.processOne();
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    return 0;
}
```

### 6.2 使用 Redis 缓存

```cpp
#include "src/node.hpp"
#include "src/cache/redis_cache.hpp"

int main() {
    auto net = std::make_unique<MockNetwork>(1);
    auto cache = std::make_unique<RedisCache>("/run/redis/redis.sock");

    Node server1(1, std::move(net), std::move(cache));
    server1.requestFile("redis_key_a");

    return 0;
}
```

---

## 7. 常见问题

### Q1: 为什么删除 neighbors_？

**A**: 见 [architecture.md](./architecture.md) 第 4.1 节。简而言之，为了解耦、可测试性和配置驱动。

### Q2: 如何获取邻居列表？

**A**: 调用 `network_->get_neighbors()`，返回 `std::vector<uint32_t>`。

### Q3: 如何发送数据包给指定节点？

**A**: 调用 `sendDirect(pkt, dest_node_id)` 或直接调用 `network_->send_direct(pkt, dest_node_id)`。

### Q4: 如何广播数据包？

**A**: 调用 `sendBroadcast(pkt)` 或直接调用 `network_->broadcast(pkt)`。

### Q5: processOne() 是否会阻塞？

**A**: 不会。如果 inbox 为空，直接返回。未来改为事件驱动架构后，会使用 epoll/select 等机制。

---

## 8. 未来改进

### 8.1 事件驱动架构

**当前**: 轮询模式（`processOne()` 循环调用）

**未来**: 事件循环 + 回调

```cpp
// 未来接口（草案）
void Node::run() {
    while (running_) {
        auto pkt = network_->receive();  // 阻塞等待
        if (pkt) {
            handlePacket(std::move(pkt));
        }
    }
}
```

### 8.2 异步 API

**当前**: 同步接口

**未来**: 异步接口 + Future/Promise

```cpp
// 未来接口（草案）
std::future<Value> Node::requestFileAsync(const std::string& key);
```

---

## 9. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v0.3.0 | 2026-01-19 | **重大架构变更**：handleRequest/handleData 参数改为子类指针（RequestPacket*/DataPacket*）；补充架构原则（Node 是数据透传层，不解析 Value 内容）；更新 processOne() 实现说明（展示 static_cast 转换） |
| v0.2.0 | 2026-01-16 | 删除 `neighbors_` 成员变量，改为使用 `network_->broadcast()` 和 `network_->send_direct()` |
| v0.2.0 | 2026-01-08 | 移除 SYNC 包处理，DATA 包到达时自动更新 SyncTable |
| v0.1.0 | 2025-12-01 | 初始定义 |

---

**下一步**: 阅读 [network-interface.md](./network-interface.md) 了解网络层接口
