# 全局类型定义

> **版本**: v0.3.0
> **最后更新**: 2026-01-19
> **状态**: 正式版

---

## 1. 概述

本文档定义 rsync 项目中的全局类型、常量和枚举。所有模块必须遵循这些定义。

### 全局类型整体视图

```
全局类型系统
│
├── 📦 数据包类型
│   ├── PacketType 枚举 ................... REQUEST, DATA（SYNC 已废弃）
│   ├── Packet 基类 ....................... 数据包基类（继承设计）
│   │   ├── type, request_id, src_node_id, dest_node_id
│   │   └── virtual ~Packet() = default
│   │
│   ├── RequestPacket 子类 ................ REQUEST 数据包
│   │   ├── BloomFilter bf ................ Bloom Filter 内容发现
│   │   └── 工厂方法：create()
│   │
│   ├── DataPacket 子类 ................... DATA 数据包
│   │   ├── std::string key ............... 文件名（NDN 风格）
│   │   ├── Value value ................... 数据载荷（opaque）
│   │   └── 工厂方法：create()
│   │
│   └── 数据包序列化
│       └── [4B length][Packet data]
│
├── 🔍 Bloom Filter
│   ├── BloomFilter 类
│   │   ├── add(item) ..................... 添加元素
│   │   ├── batch_add(items) .............. 批量添加
│   │   ├── contains(item) ................ 检查元素
│   │   └── merge(other) .................. 合并 BF
│   │
│   └── 参数
│       ├── size: 1024（默认）
│       └── num_hashes: 3（默认）
│
├── 💾 Payload / Value
│   ├── Value 类（opaque byte container）
│   │   ├── data() ........................ 访问原始字节
│   │   ├── size() ........................ 获取大小
│   │   └── empty() ....................... 检查是否为空
│   │
│   └── 设计原则
│       ├── Value 对 Node 层是 opaque 的
│       ├── Node 不解析 Value 内容
│       └── 数据解析职责在应用层
│
├── 🔢 全局常量
│   ├── DEFAULT_BF_SIZE = 1024
│   ├── DEFAULT_NUM_HASHES = 3
│   ├── DEFAULT_QUERY_TIMEOUT_MS = 500
│   ├── DEFAULT_SYNC_TABLE_CAPACITY = 1000
│   ├── MAX_PACKET_SIZE = 65536
│   ├── INVALID_NODE_ID = 0
│   └── BROADCAST_NODE_ID = 0xFFFFFFFF
│
├── ⚠️ 错误码
│   └── ErrorCode 枚举
│       ├── SUCCESS, INVALID_PACKET, TIMEOUT
│       └── NOT_FOUND, NETWORK_ERROR, CACHE_ERROR
│
└── ⚙️ 配置参数
    └── Config 结构
        ├── bf_size, num_hashes
        ├── query_timeout_ms, sync_table_capacity
        ├── config_file_path
        └── redis_socket_path
```

### 快速跳转

| 类别 | 类型/常量 | 跳转 |
|------|----------|------|
| **数据包类型** | PacketType 枚举 | [→ 查看](#21-packettype-枚举) |
| | Packet 基类 | [→ 查看](#31-packet-基类) |
| | RequestPacket 子类 | [→ 查看](#32-requestpacket-子类) |
| | DataPacket 子类 | [→ 查看](#33-datapacket-子类) |
| **Bloom Filter** | BloomFilter 类 | [→ 查看](#41-bloomfilter-类) |
| | add() | [→ 查看](#42-bloomfilter-方法) |
| | contains() | [→ 查看](#42-bloomfilter-方法) |
| | merge() | [→ 查看](#42-bloomfilter-方法) |
| **Payload/Value** | Value 类 | [→ 查看](#51-value-类) |
| | data() | [→ 查看](#51-value-类) |
| | size() / empty() | [→ 查看](#51-value-类) |
| **全局常量** | 网络参数 | [→ 查看](#61-网络参数) |
| | 节点 ID | [→ 查看](#62-节点-id) |
| | 请求 ID | [→ 查看](#63-请求-id) |
| **错误码** | ErrorCode 枚举 | [→ 查看](#71-errorcode-枚举) |
| **配置参数** | Config 结构 | [→ 查看](#81-config-结构) |
| **类型别名** | 常用类型别名 | [→ 查看](#91-常用类型别名) |

---

## 2. 数据包类型

### 2.1 PacketType 枚举

**定义**:
```cpp
enum class PacketType : uint8_t {
    REQUEST = 1,  // 请求数据包
    DATA = 2,     // 数据数据包
    // SYNC = 3,  // 已废弃（2026-01-08）
};
```

**说明**:
- **REQUEST**: 查询数据可用性，携带 Bloom Filter
- **DATA**: 传递实际数据，携带 Key-Value 对
- ~~**SYNC**~~: 已废弃（2026-01-08 会议决策）

**使用示例**:
```cpp
// 基于 type 字段进行类型转换（使用 static_cast）
std::unique_ptr<Packet> pkt = /* 接收到的数据包 */;

switch (pkt->type) {
    case PacketType::REQUEST: {
        auto* req_pkt = static_cast<RequestPacket*>(pkt.get());
        handleRequest(req_pkt);
        break;
    }
    case PacketType::DATA: {
        auto* data_pkt = static_cast<DataPacket*>(pkt.get());
        handleData(data_pkt);
        break;
    }
    default:
        std::cerr << "Unknown packet type" << std::endl;
        break;
}
```

**设计说明**:
- 使用 `static_cast` 而非 `dynamic_cast`，避免 RTTI 开销
- type 字段用于序列化和类型判断
- 只有虚析构函数，无其他虚函数（最小化 vptr 开销）

**变更历史**:
- v0.2.0 (2026-01-08): 废弃 SYNC 类型
- v0.1.0 (2025-12-01): 初始定义

---

## 3. 数据包结构

### 3.1 Packet 基类

**定义**:
```cpp
class Packet {
public:
    PacketType type;                      // 数据包类型
    uint64_t request_id;                  // 请求 ID（全局唯一）
    uint32_t src_node_id;                 // 源节点 ID
    uint32_t dest_node_id;                // 目标节点 ID
    uint32_t hop_count = 0;               // 跳数计数

    virtual ~Packet() = default;          // 虚析构函数

protected:
    Packet(PacketType t) : type(t), request_id(0), src_node_id(0), dest_node_id(0) {}
};
```

**字段说明**:
- `type`: 数据包类型（REQUEST / DATA），用于序列化和类型判断
- `request_id`: 请求 ID，用于关联请求和响应
- `src_node_id`: 源节点 ID
- `dest_node_id`: 目标节点 ID（广播时为 0）
- `hop_count`: 跳数计数（用于 TTL 控制）

**设计原则**:
- **继承设计**: RequestPacket 和 DataPacket 继承自 Packet
- **最小虚函数**: 只有虚析构函数，无其他虚函数（最小化开销）
- **type 字段保留**: 用于序列化和 static_cast 类型判断
- **性能考虑**: vptr 开销仅 8 字节，使用 static_cast 避免 RTTI

**变更历��**:
- v0.3.0 (2026-01-19): 改为继承设计（基类 + RequestPacket + DataPacket）
- v0.2.0 (2026-01-08): 移除 SYNC 包相关字段
- v0.1.0 (2025-12-01): 初始定义

---

### 3.2 RequestPacket 子类

**定义**:
```cpp
class RequestPacket : public Packet {
public:
    BloomFilter bf;                       // Bloom Filter（内容发现）

    RequestPacket() : Packet(PacketType::REQUEST) {}

    // 工厂方法
    static std::unique_ptr<RequestPacket> create(
        uint32_t src_node_id,
        const std::vector<std::string>& keys
    );
};
```

**字段说明**:
- `bf`: Bloom Filter，用于内容发现（查询哪些文件）

**工厂方法**:
```cpp
std::unique_ptr<RequestPacket> RequestPacket::create(
    uint32_t src_node_id,
    const std::vector<std::string>& keys
) {
    auto pkt = std::make_unique<RequestPacket>();
    pkt->request_id = generateRequestId();  // 生成唯一 ID
    pkt->src_node_id = src_node_id;
    pkt->dest_node_id = 0;  // 广播

    // 创建 Bloom Filter
    pkt->bf = BloomFilter(1024);
    pkt->bf.batch_add(keys);

    return pkt;
}
```

**使用示例**:
```cpp
// 创建 REQUEST 包
auto req_pkt = RequestPacket::create(1, {"redis_key_a", "redis_key_b"});

// 访问 Bloom Filter
if (req_pkt->bf.contains("redis_key_a")) {
    std::cout << "Key may exist" << std::endl;
}
```

**变更历史**:
- v0.3.0 (2026-01-19): 新增子类，从 Packet struct 拆分
- v0.2.0 (2026-01-08): 移除 SYNC 包相关字段
- v0.1.0 (2025-12-01): 初始定义

---

### 3.3 DataPacket 子类

**定义**:
```cpp
class DataPacket : public Packet {
public:
    std::string key;                      // 文件名（NDN 风格命名）
    Value value;                          // 数据载荷（opaque）

    DataPacket() : Packet(PacketType::DATA) {}

    // 工厂方法
    static std::unique_ptr<DataPacket> create(
        uint64_t request_id,
        uint32_t dest_node_id,
        const std::string& key,
        Value value
    );
};
```

**字段说明**:
- `key`: 文件名（NDN 风格命名，如 `/app/data/file1`）
- `value`: 数据载荷（opaque byte container，Node 不解析内容）

**工厂方法**:
```cpp
std::unique_ptr<DataPacket> DataPacket::create(
    uint64_t request_id,
    uint32_t dest_node_id,
    const std::string& key,
    Value value
) {
    auto pkt = std::make_unique<DataPacket>();
    pkt->request_id = request_id;
    pkt->dest_node_id = dest_node_id;
    pkt->key = key;
    pkt->value = std::move(value);

    return pkt;
}
```

**使用示例**:
```cpp
// 创建 DATA 包
Value v(std::vector<uint8_t>{0x01, 0x02, 0x03});
auto data_pkt = DataPacket::create(12345, 2, "redis_key_a", std::move(v));

// 访问数据
std::cout << "Key: " << data_pkt->key << std::endl;
std::cout << "Value size: " << data_pkt->value.size() << " bytes" << std::endl;
```

**架构原则**:
- **Value 是 opaque 的**: Node 层不解析 Value 内容
- **Node 的三种操作**: 供给上层应用、转发给其他节点、存储到 Redis
- **数据解析职责**: 在应用层，而非 Node 层

**变更历史**:
- v0.3.0 (2026-01-19): 新增子类，从 Packet struct 拆分；明确 Value 是 opaque 的
- v0.2.0 (2026-01-08): 移除 SYNC 包相关字段
- v0.1.0 (2025-12-01): 初始定义

---

## 4. Bloom Filter

### 4.1 BloomFilter 类

**定义**:
```cpp
class BloomFilter {
public:
    BloomFilter(size_t size);

    void add(const std::string& item);
    void batch_add(const std::vector<std::string>& items);
    bool contains(const std::string& item) const;
    BloomFilter merge(const BloomFilter& other) const;

private:
    std::vector<bool> bits_;
    size_t size_;
    size_t num_hashes_;
};
```

**字段说明**:
- `bits_`: 位数组
- `size_`: 位数组大小（建议 1024）
- `num_hashes_`: 哈希函数数量（建议 3）

**使用示例**:
```cpp
BloomFilter bf(1024);
bf.add("redis_key_a");
bf.add("redis_key_b");

if (bf.contains("redis_key_a")) {
    std::cout << "Key found (maybe)" << std::endl;
}

// 合并两个 BF
BloomFilter bf2(1024);
bf2.add("redis_key_c");
BloomFilter merged = bf.merge(bf2);
```

**变更历史**:
- v0.1.0 (2025-12-01): 初始定义

---

### 4.2 BloomFilter 方法

#### add()

**功能**: 添加元素到 Bloom Filter

**签名**:
```cpp
void add(const std::string& item);
```

**实现要点**:
```cpp
void BloomFilter::add(const std::string& item) {
    for (size_t i = 0; i < num_hashes_; ++i) {
        size_t hash = hash_function(item, i) % size_;
        bits_[hash] = true;
    }
}
```

---

#### batch_add()

**功能**: 批量添加元素

**签名**:
```cpp
void batch_add(const std::vector<std::string>& items);
```

**实现要点**:
```cpp
void BloomFilter::batch_add(const std::vector<std::string>& items) {
    for (const auto& item : items) {
        add(item);
    }
}
```

---

#### contains()

**功能**: 检查元素是否存在

**签名**:
```cpp
bool contains(const std::string& item) const;
```

**返回值**:
- `true`: 元素可能存在（有误判）
- `false`: 元素一定不存在

**实现要点**:
```cpp
bool BloomFilter::contains(const std::string& item) const {
    for (size_t i = 0; i < num_hashes_; ++i) {
        size_t hash = hash_function(item, i) % size_;
        if (!bits_[hash]) {
            return false;  // 一定不存在
        }
    }
    return true;  // 可能存在
}
```

---

#### merge()

**功能**: 合并两个 Bloom Filter

**签名**:
```cpp
BloomFilter merge(const BloomFilter& other) const;
```

**返回值**:
- 合并后的 Bloom Filter

**实现要点**:
```cpp
BloomFilter BloomFilter::merge(const BloomFilter& other) const {
    assert(size_ == other.size_);  // 大小必须相同

    BloomFilter result(size_);
    for (size_t i = 0; i < size_; ++i) {
        result.bits_[i] = bits_[i] || other.bits_[i];  // 按位或
    }
    return result;
}
```

---

## 5. Payload / Value

### 5.1 Value 类

**定义**:
```cpp
class Value {
public:
    Value() = default;
    explicit Value(std::vector<uint8_t> data) : data_(std::move(data)) {}

    // 访问原始字节
    const std::vector<uint8_t>& data() const { return data_; }
    std::vector<uint8_t>& data() { return data_; }

    // 大小和状态查询
    size_t size() const { return data_.size(); }
    bool empty() const { return data_.empty(); }

private:
    std::vector<uint8_t> data_;  // 原始字节流
};
```

**字段说明**:
- `data_`: 原始字节流（opaque，不假设内容类型）

**设计原则**:
- **Opaque byte container**: Value 是不透明的字节容器
- **Node 不解析**: Node 层不解析 Value 内容，只做透传/转发/存储
- **应用层解析**: 数据解析职责在应用层
- **最小化设计**: 不包含 fromString/toString/serialize/deserialize 方法
- **未来可扩展**: 可以在应用层添加转换逻辑

**使用示例**:

**Demo/测试代码中的转换**:
```cpp
// Demo 代码：从字符串创建 Value（应用层逻辑）
std::string str = "Hello, World!";
Value v(std::vector<uint8_t>(str.begin(), str.end()));

// Demo 代码：转换为字符串（应用层逻辑）
std::string result(v.data().begin(), v.data().end());
std::cout << result << std::endl;
```

**Node 层使用**:
```cpp
// Node 存储到 Redis（不解析内容）
void Node::storeToCache(const std::string& key, const Value& value) {
    cache_->put(key, value);  // 直接存储 Value 对象
}

// Node 转发数据包（不解析内容）
void Node::forwardData(std::unique_ptr<DataPacket> pkt) {
    network_->send_direct(pkt->dest_node_id, std::move(pkt));
}

// Node 供给应用层（不解析内容）
void Node::supplyToApp(const Value& value) {
    app_callback_(value);  // 应用层负责解析
}
```

**应用层使用**:
```cpp
// 应用层：解析 Value 内容
void ApplicationLayer::onDataReceived(const Value& value) {
    // 应用层知道数据格式，可以解析
    if (isJsonFormat(value)) {
        auto json = parseJson(value.data());
        // 处理 JSON 数据
    } else if (isBinaryFormat(value)) {
        auto tensor = parseTensor(value.data());
        // 处理 Tensor 数据
    }
}
```

**Redis 存储**:
```cpp
// Redis 支持二进制安全存储（最大 512MB）
// Value 可以直接存储到 Redis String 类型
redis_client.set(key, std::string(value.data().begin(), value.data().end()));
```

**变更历史**:
- v0.3.0 (2026-01-19): 简化为 opaque byte container，移除 fromString/toString/serialize/deserialize
- v0.1.0 (2025-12-01): 初始定义

---

## 6. 全局常量

### 6.1 网络参数

```cpp
// 默认 Bloom Filter 大小
constexpr size_t DEFAULT_BF_SIZE = 1024;

// 默认哈希函数数量
constexpr size_t DEFAULT_NUM_HASHES = 3;

// 默认 QueryTable 超时时间（毫秒）
constexpr uint64_t DEFAULT_QUERY_TIMEOUT_MS = 500;

// 默认 SyncTable 容量
constexpr size_t DEFAULT_SYNC_TABLE_CAPACITY = 1000;

// 最大数据包大小（字节）
constexpr size_t MAX_PACKET_SIZE = 65536;  // 64 KB
```

### 6.2 节点 ID

```cpp
// 无效节点 ID
constexpr uint32_t INVALID_NODE_ID = 0;

// 广播节点 ID
constexpr uint32_t BROADCAST_NODE_ID = 0xFFFFFFFF;
```

### 6.3 请求 ID

```cpp
// 无效请求 ID
constexpr uint64_t INVALID_REQUEST_ID = 0;

// 请求 ID 生成器（原子递增）
inline uint64_t generateRequestId() {
    static std::atomic<uint64_t> counter{1};
    return counter.fetch_add(1, std::memory_order_relaxed);
}
```

---

## 7. 错误码

### 7.1 ErrorCode 枚举

```cpp
enum class ErrorCode : int {
    SUCCESS = 0,
    INVALID_PACKET = 1,
    INVALID_NODE_ID = 2,
    INVALID_REQUEST_ID = 3,
    TIMEOUT = 4,
    NOT_FOUND = 5,
    NETWORK_ERROR = 6,
    CACHE_ERROR = 7,
    UNKNOWN_ERROR = 99,
};
```

**使用示例**:
```cpp
ErrorCode err = processPacket(pkt);
if (err != ErrorCode::SUCCESS) {
    std::cerr << "Error: " << static_cast<int>(err) << std::endl;
}
```

---

## 8. 配置参数

### 8.1 Config 结构

```cpp
struct Config {
    // Bloom Filter 配置
    size_t bf_size = DEFAULT_BF_SIZE;
    size_t num_hashes = DEFAULT_NUM_HASHES;

    // QueryTable 配置
    uint64_t query_timeout_ms = DEFAULT_QUERY_TIMEOUT_MS;

    // SyncTable 配置
    size_t sync_table_capacity = DEFAULT_SYNC_TABLE_CAPACITY;

    // 网络配置
    std::string config_file_path = "config/3-node-topology.yaml";

    // Redis 配置
    std::string redis_socket_path = "/run/redis/redis.sock";
};
```

**使用示例**:
```cpp
Config cfg;
cfg.bf_size = 2048;
cfg.query_timeout_ms = 1000;

// 传递给 Node 构造函数
Node node(1, nullptr, nullptr, cfg);
```

---

## 9. 类型别名

### 9.1 常用类型别名

```cpp
// 节点 ID 类型
using NodeId = uint32_t;

// 请求 ID 类型
using RequestId = uint64_t;

// 文件名类型
using FileName = std::string;

// 数据载荷类型
using Payload = std::vector<uint8_t>;
```

**使用示例**:
```cpp
NodeId node_id = 1;
RequestId request_id = generateRequestId();
FileName filename = "redis_key_a";
Payload payload = {0x01, 0x02, 0x03};
```

---

## 10. 常见问题

### Q1: 为什么 Bloom Filter 大小是 1024？

**A**: 基于经验值。1024 位可以存储约 100 个元素，误判率约 10^-3。如果需要更低的误判率，可以增加大小。

### Q2: 为什么 QueryTable 超时时间是 500ms？

**A**: 基于经验值。如果数据在 500ms 内未返回，可能是网络故障或数据不存在。

### Q3: 如何生成唯一的请求 ID？

**A**: 使用 `generateRequestId()` 函数，内部使用原子递增计数器。

### Q4: 如何处理数据包大小超过 64 KB？

**A**: 当前不支持。未来可以实现分片机制（类似 IP 分片）。

### Q5: 如何扩展配置参数？

**A**: 在 `Config` 结构中添加新字段，并更新配置文件解析逻辑。

---

## 11. 未来改进

### 11.1 动态 Bloom Filter 大小

**当前**: 固定大小 1024

**未来**: 根据元素数量动态调整

```cpp
// 未来接口（草案）
BloomFilter(size_t expected_elements, double false_positive_rate);
```

### 11.2 配置文件支持

**当前**: 硬编码常量

**未来**: 从配置文件加载

```cpp
// 未来接口（草案）
Config Config::loadFromFile(const std::string& path);
```

### 11.3 错误处理增强

**当前**: 简单的错误码

**未来**: 异常 + 错误码 + 错误消息

```cpp
// 未来接口（草案）
class RsyncException : public std::exception {
public:
    RsyncException(ErrorCode code, const std::string& message);
    ErrorCode code() const;
    const char* what() const noexcept override;
};
```

---

## 12. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v0.3.0 | 2026-01-19 | **重大架构变更**：Packet 改为继承设计（基类 + RequestPacket + DataPacket）；Value 简化为 opaque byte container；明确 Node 不解析 Value 内容的架构原则 |
| v0.2.0 | 2026-01-08 | 废弃 SYNC 包类型，移除相关字段 |
| v0.1.0 | 2025-12-01 | 初始定义 |

---

**完成**: 所有接口文档已创建，请查阅 [README.md](./README.md) 了解文档索引
