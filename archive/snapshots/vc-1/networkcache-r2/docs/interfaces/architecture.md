# rsync 系统架构设计

> **版本**: v0.2.0
> **最后更新**: 2026-01-16
> **状态**: 正式版

---

## 1. 系统概述

rsync 是一个基于 Bloom Filter 的分布式 KV 缓存同步协议，目标是在普通以太网上实现 <400μs 的数据访问延迟。

### 1.1 核心目标

- **数据与位置解耦**：Key 标识数据，SyncTable 记录位置
- **内容发现**：通过 Bloom Filter 实现空间高效的内容发现
- **缓存到缓存传输**：动态路径选择，支持多路径并行传输

### 1.2 性能指标

| 指标 | 目标 | 当前状态 |
|------|------|---------|
| 数据访问延迟 | <400μs | 待测试 |
| BF 误判率 | 10^-3 ~ 10^-4 | 待测试 |
| 网络开销 | <5% | 待测试 |
| 可扩展性 | 1000+ 节点 | 支持 |

---

## 2. 系统架构

### 2.1 层次结构

```
┌─────────────────────────────────────────────────────────┐
│                      应用层                              │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │  main.cpp    │  │  测试程序     │                     │
│  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    协议层 (Node)                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  状态机：handleRequest / handleData              │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  状态表：QueryTable / SyncTable                  │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  网络抽象层 (NetworkInterface)           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ MockNetwork  │  │ TCPNetwork   │  │ UDPNetwork   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  缓存层 (LocalCache)                     │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │ RedisCache   │  │InMemoryCache │                     │
│  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| **Node** | 协议核心逻辑，状态机处理 | NetworkInterface, LocalCache, SyncTable, QueryTable |
| **NetworkInterface** | 网络抽象，发送/接收数据包 | Packet |
| **LocalCache** | 缓存抽象，存储 KV 数据 | 无 |
| **SyncTable** | 记录数据位置，路径缓存 | BloomFilter |
| **QueryTable** | 跟踪待处理请求 | 无 |
| **Packet** | 数据包定义 | BloomFilter, Payload |
| **BloomFilter** | 内容发现元数据 | 无 |

---

## 3. 数据流

### 3.1 请求流程

```
客户端 → Node::requestFile()
         ↓
      创建 REQUEST 包（包含 BF）
         ↓
      记录到 QueryTable
         ↓
      network_->broadcast(pkt)
         ↓
      所有邻居收到 REQUEST
         ↓
      Node::handleRequest()
         ↓
      检查本地缓存
         ↓
   ┌─────┴─────┐
   │           │
 找到        未找到
   │           │
   ↓           ↓
创建 DATA   转发 REQUEST
   │           │
   ↓           ↓
send_direct  broadcast
```

### 3.2 数据返回流程

```
Node::handleData()
    ↓
存入本地缓存
    ↓
更新 SyncTable
    ↓
查询 QueryTable（谁在等这个数据？）
    ↓
┌───┴───┐
│       │
我是    其他节点
发起者   在等
│       │
↓       ↓
回调    转发
应用层   send_direct
```

---

## 4. 关键设计决策

### 4.1 为什么删除 neighbors_ 成员变量？

**决策**: Node 不再持有 `std::vector<Node*> neighbors_`

**理由**:
1. **解耦**: Node 不应该知道邻居的具体实现（指针）
2. **可测试性**: MockNetwork 可以模拟任意拓扑，无需修改 Node
3. **配置驱动**: 邻居关系由配置文件定义，而非硬编码
4. **真实网络**: TCP/UDP 网络中，邻居是 IP:Port，不是对象指针

**替代方案**:
- 使用 `network_->broadcast(pkt)` 替代遍历 neighbors_
- 使用 `network_->send_direct(node_id, pkt)` 替代 `neighbor->enqueuePacket(pkt)`
- 使用 `network_->get_neighbors()` 获取邻居列表（如果需要）

**影响**:
- `Node::addNeighbor()` 方法被删除
- `main.cpp` 改为通过配置文件定义拓扑
- `handleRequest()` 和 `handleData()` 中的转发逻辑改为调用网络接口

### 4.2 为什么废弃 SYNC 包？

**决策**: 只保留 REQUEST 和 DATA 两种包类型

**理由**:
1. **简化协议**: DATA 包到达时自动更新 SyncTable，无需单独 SYNC 包
2. **降低开销**: 避免额外的 SYNC 包传输
3. **更直接**: 数据传输和状态更新合二为一

**来源**: 2026-01-08 会议决策

### 4.3 SyncTable 存储什么？

**决策**: 存储精确文件名（非 Bloom Filter）

**数据结构**:
```cpp
std::unordered_map<std::string, std::vector<uint32_t>> file_to_nodes_;
// 文件名 → 拥有该文件的邻居节点列表
```

**理由**:
1. **精确匹配**: 避免 BF 误判导致的无效请求
2. **LRU 淘汰**: 预分配容量（如 1000），满时淘汰最旧条目
3. **多路径**: 支持记录多个邻居拥有同一文件

**来源**: 2026-01-08 会议决策

### 4.4 QueryTable 是单源还是多源？

**决策**: 单源（每个请求只记录一个下游节点）

**数据结构**:
```cpp
struct QueryEntry {
    uint64_t request_id;
    std::string filename;
    uint32_t downstream_node;  // 单个节点 ID
    std::chrono::milliseconds timeout{500};
};
```

**理由**:
1. **简化逻辑**: 数据只沿一条路径返回
2. **避免重复**: 防止同一数据被多次发送
3. **超时机制**: 500ms 后自动清理，避免内存泄漏

**来源**: 2026-01-08 会议决策

---

## 5. 模块间依赖关系

### 5.1 依赖图

```
main.cpp
   │
   ├─→ Node
   │    ├─→ NetworkInterface (抽象)
   │    │    ├─→ MockNetwork (实现)
   │    │    ├─→ TCPNetwork (实现，待开发)
   │    │    └─→ UDPNetwork (实现，待开发)
   │    ├─→ LocalCache (抽象)
   │    │    ├─→ RedisCache (实现)
   │    │    └─→ InMemoryCache (实现)
   │    ├─→ SyncTable
   │    │    └─→ BloomFilter
   │    ├─→ QueryTable
   │    └─→ Packet
   │         ├─→ BloomFilter
   │         └─→ Payload
   │
   └─→ 配置文件 (config/*.yaml)
```

### 5.2 接口依赖矩阵

| 模块 | Node | NetworkInterface | LocalCache | SyncTable | QueryTable | Packet |
|------|------|------------------|------------|-----------|------------|--------|
| **Node** | - | ✓ | ✓ | ✓ | ✓ | ✓ |
| **NetworkInterface** | ✗ | - | ✗ | ✗ | ✗ | ✓ |
| **LocalCache** | ✗ | ✗ | - | ✗ | ✗ | ✗ |
| **SyncTable** | ✗ | ✗ | ✗ | - | ✗ | ✗ |
| **QueryTable** | ✗ | ✗ | ✗ | ✗ | - | ✗ |
| **Packet** | ✗ | ✗ | ✗ | ✗ | ✗ | - |

**说明**: ✓ 表示依赖，✗ 表示不依赖

---

## 6. 全局约定

### 6.1 命名规范

- **类名**: `PascalCase`（如 `Node`, `NetworkInterface`）
- **变量名**: `snake_case`（如 `node_id`, `request_id`）
- **成员变量**: 后缀 `_`（如 `id_`, `cache_`）
- **常量**: `UPPER_SNAKE_CASE`（如 `MAX_PACKET_SIZE`）

### 6.2 错误处理

- **返回值**: 使用 `std::optional<T>` 表示可能失败的操作
- **异常**: 仅用于不可恢复的错误（如内存分配失败）
- **日志**: 使用 `std::cout` 输出调试信息（后续改为日志库）

### 6.3 内存管理

- **所有权**: 使用 `std::unique_ptr<T>` 表示独占所有权
- **共享**: 避免使用 `std::shared_ptr<T>`，优先传递引用
- **生命周期**: Packet 在处理后立即销毁，不保留

### 6.4 线程安全

- **当前**: 单线程模型，无需加锁
- **未来**: 引入事件循环后，考虑线程安全

---

## 7. 配置文件格式

### 7.1 拓扑配置 (config/3-node-topology.yaml)

```yaml
nodes:
  - id: 1
    neighbors: [2]
  - id: 2
    neighbors: [1, 3]
  - id: 3
    neighbors: [2]
```

### 7.2 未来扩展 (TCP 网络)

```yaml
nodes:
  - id: 1
    ip: "192.168.1.1"
    port: 9001
    neighbors: [2]
  - id: 2
    ip: "192.168.1.2"
    port: 9002
    neighbors: [1, 3]
  - id: 3
    ip: "192.168.1.3"
    port: 9003
    neighbors: [2]
```

---

## 8. 开发路线图

### 8.1 当前状态 (Phase 1)

- ✅ 核心数据结构
- ✅ 基本协议流程
- ✅ MockNetwork 实现
- ✅ Redis 缓存集成
- ✅ Docker 容器化

### 8.2 进行中 (Phase 2)

- 🔄 删除 neighbors_ 成员变量
- 🔄 接口文档编写
- ⏳ TCPNetwork 实现
- ⏳ 事件驱动架构

### 8.3 计划中 (Phase 3)

- ⏳ Kathara 测试环境
- ⏳ 性能优化
- ⏳ 容错机制

---

## 9. 测试策略

### 9.1 单元测试

- **BloomFilter**: 哈希函数、合并操作、误判率
- **SyncTable**: CRUD 操作、LRU 淘汰
- **QueryTable**: 添加、查询、超时清理
- **Packet**: 序列化、反序列化

### 9.2 集成测试

- **3 节点拓扑**: 基本请求-响应流程
- **Unix Socket**: Redis 通信
- **多跳转发**: 数据包正确转发

### 9.3 性能测试

- **延迟**: 端到端数据访问延迟
- **吞吐量**: 每秒处理请求数
- **误判率**: BF 误判率统计

---

## 10. 参考资料

- **会议记录**: `/home/ifzzh/Documents/NetOS/meeting-records/`
- **设计文档**: `/home/ifzzh/Documents/NetOS/meeting-records/design/README.md`
- **代码实现**: `/home/ifzzh/Documents/NetOS/meeting-records/design/code/rsync/`
- **根级文档**: `/home/ifzzh/Documents/NetOS/meeting-records/CLAUDE.md`

---

**下一步**: 阅读各模块的详细接口文档
