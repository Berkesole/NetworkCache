# Network 抽象层迁移指南

> **发布日期**：2026-01-15
> **版本**：1.0
> **状态**：待其他开发人员执行

---

## 概述

本指南帮助开发人员将现有代码迁移到新的 `NetworkInterface` 抽象层。

### 核心变更

| 变更项 | 旧设计 | 新设计 |
|--------|--------|--------|
| **发送接口** | `send(pkt)` | `send_direct(dst, pkt)` / `broadcast(pkt)` |
| **邻居管理** | Node 持有 `neighbors_` 向量 | NetworkInterface 管理 |
| **拓扑配置** | 手动调用 `addNeighbor()` | 配置文件驱动 |
| **数据包入队** | `neighbor->enqueuePacket()` | `network_->send_direct()` |

---

## 新接口说明

### NetworkInterface

```cpp
class NetworkInterface {
public:
    // 单播发送到指定节点
    virtual void send_direct(uint32_t dst_node_id, std::unique_ptr<Packet> pkt) = 0;

    // 广播到所有邻居
    virtual void broadcast(std::unique_ptr<Packet> pkt) = 0;

    // 从本地队列接收
    virtual std::unique_ptr<Packet> receive() = 0;

    // 加载拓扑配置
    virtual void configure_topology(const std::string& config_file, uint32_t my_node_id) = 0;

    // 获取邻居列表（调试用）
    virtual std::vector<uint32_t> get_neighbors() const = 0;

    virtual ~NetworkInterface() = default;
};
```

---

## 迁移步骤

### 步骤 1：更新 Node 构造函数

**旧代码**：
```cpp
Node::Node(uint32_t id, std::unique_ptr<NetworkInterface> network,
           std::unique_ptr<LocalCache> cache)
    : id_(id), network_(std::move(network)), cache_(std::move(cache)) {
    if (!cache_) { cache_ = std::make_unique<InMemoryCache>(); }
    if (!network_) { network_ = std::make_unique<MockNetwork>(); }
}
```

**新代码**：
```cpp
Node::Node(uint32_t id, std::unique_ptr<NetworkInterface> network,
           std::unique_ptr<LocalCache> cache)
    : id_(id), network_(std::move(network)), cache_(std::move(cache)) {
    if (!cache_) { cache_ = std::make_unique<InMemoryCache>(); }
    // 注意：network_ 必须在外部创建并配置拓扑
    // MockNetwork 构造函数需要 node_id 参数
}
```

### 步骤 2：移除 neighbors_ 成员变量

**旧代码**（node.hpp）：
```cpp
class Node {
private:
    std::vector<Node*> neighbors_;  // 删除此行
    // ...
};
```

**新代码**：
```cpp
class Node {
private:
    // neighbors_ 已移除，由 NetworkInterface 管理
    // ...
};
```

### 步骤 3：移除 addNeighbor 方法

**旧代码**（node.hpp/cpp）：
```cpp
void Node::addNeighbor(Node* neighbor) {
    neighbors_.push_back(neighbor);
}
```

**新代码**：
```cpp
// 删除 addNeighbor 方法
// 邻居关系通过配置文件定义
```

### 步骤 4：替换广播逻辑

**旧代码**（node.cpp:47-53）：
```cpp
for (auto* neighbor : neighbors_) {
    auto pkt_copy = std::make_unique<Packet>(*pkt);
    pkt_copy->dest_node_id = neighbor->id_;
    neighbor->enqueuePacket(std::move(pkt_copy));
}
```

**新代码**：
```cpp
network_->broadcast(std::move(pkt));
```

### 步骤 5：替换单播逻辑

**旧代码**（node.cpp:111-115）：
```cpp
for (auto* n : neighbors_) {
    if (n->id_ == pkt->src_node_id) {
        n->enqueuePacket(std::move(response));
    }
}
```

**新代码**：
```cpp
network_->send_direct(pkt->src_node_id, std::move(response));
```

### 步骤 6：替换 handleData 中的转发逻辑

**旧代码**（node.cpp:147）：
```cpp
network_->send(std::move(pkt), node_id);  // 旧接口不存在
```

**新代码**：
```cpp
network_->send_direct(node_id, std::move(pkt));
```

### 步骤 7：更新 main.cpp

**旧代码**：
```cpp
Node server1(1);
Node server2(2);
Node server3(3);

server1.addNeighbor(&server2);
server2.addNeighbor(&server1);
server2.addNeighbor(&server3);
server3.addNeighbor(&server2);
```

**新代码**：
```cpp
// 创建 MockNetwork 实例
auto net1 = std::make_unique<MockNetwork>(1);
auto net2 = std::make_unique<MockNetwork>(2);
auto net3 = std::make_unique<MockNetwork>(3);

// 配置拓扑
net1->configure_topology("config/3-node-topology.yaml", 1);
net2->configure_topology("config/3-node-topology.yaml", 2);
net3->configure_topology("config/3-node-topology.yaml", 3);

// 创建 Node 实例
Node server1(1, std::move(net1), std::make_unique<InMemoryCache>());
Node server2(2, std::move(net2), std::make_unique<InMemoryCache>());
Node server3(3, std::move(net3), std::make_unique<InMemoryCache>());
```

---

## 配置文件格式

### 示例：3 节点拓扑

```yaml
# config/3-node-topology.yaml
nodes:
- id: 1
  neighbors: [2]

- id: 2
  neighbors: [1, 3]

- id: 3
  neighbors: [2]
```

### 示例：8×8 Mesh 拓扑

参见 `config/8x8-mesh-topology.yaml`

---

## 常见问题

### Q1: 编译错误 "send" 未定义

**原因**：旧的 `send(pkt)` 接口已移除。

**解决**：根据语义替换为 `send_direct(dst, pkt)` 或 `broadcast(pkt)`。

### Q2: 编译错误 "neighbors_" 未定义

**原因**：`neighbors_` 成员变量已移除。

**解决**：使用 `network_->get_neighbors()` 获取邻居列表（如果需要）。

### Q3: 运行时错误 "dst not found"

**原因**：目标节点的 MockNetwork 实例未创建或未配置拓扑。

**解决**：确保所有节点的 MockNetwork 实例都已创建并调用 `configure_topology()`。

### Q4: 数据包未送达

**原因**：可能是拓扑配置错误或邻居关系不正确。

**解决**：
1. 检查配置文件中的邻居列表
2. 使用 `get_neighbors()` 验证邻居关系
3. 确保双向邻居关系（如果需要）

---

## 测试验证

### 运行单元测试

```bash
cd /home/ifzzh/Documents/NetOS/meeting-records/design/code/rsync
./build/test_mock_network_standalone
```

### 预期输出

```
=== MockNetwork 独立单元测试 ===

Running construction_and_registration... PASSED
Running destruction_and_unregistration... PASSED
Running configure_topology... PASSED
Running send_direct... PASSED
Running broadcast... PASSED
Running receive_empty... PASSED
Running send_to_nonexistent_node... PASSED
Running invalid_config_file... PASSED
Running packet_copy_independence... PASSED

=== 所有测试通过 ===
```

---

## 文件清单

### 已修改的文件

| 文件 | 变更 |
|------|------|
| `src/network.hpp` | 新增 `send_direct`、`broadcast`、`configure_topology`、`get_neighbors` |
| `src/mocknetwork.hpp` | 完整实现新接口，静态注册表机制 |

### 新增的文件

| 文件 | 用途 |
|------|------|
| `src/network/topology_parser.hpp` | 拓扑配置解析器 |
| `config/3-node-topology.yaml` | 3 节点拓扑示例 |
| `config/8x8-mesh-topology.yaml` | 8×8 Mesh 拓扑模板 |
| `tests/test_mock_network_standalone.cpp` | 独立单元测试 |
| `src/network/MIGRATION.md` | 本迁移指南 |

### 需要修改的文件（由其他开发人员执行）

| 文件 | 变更 |
|------|------|
| `src/node.hpp` | 移除 `neighbors_`、`addNeighbor()` |
| `src/node.cpp` | 使用新接口替换旧逻辑 |
| `main.cpp` | 使用配置文件初始化拓扑 |

---

## 联系方式

如有问题，请联系 Network 模块负责人。

---

**最后更新**：2026-01-15
