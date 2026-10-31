# NetworkInterface 接口规范

> **版本**: v0.2.0
> **最后更新**: 2026-01-16
> **状态**: 正式版

---

## 1. 概述

NetworkInterface 是网络层的抽象接口，定义了数据包发送和接收的标准方法。所有网络实现（MockNetwork, TCPNetwork, UDPNetwork）必须实现此接口。

### NetworkInterface 接口整体视图

```
NetworkInterface 接口
├── 📡 核心方法（纯虚函数）
│   ├── receive() ......................... 接收数据包（非阻塞）
│   ├── send_direct(dest_id, pkt) ......... 发送给指定节点
│   ├── broadcast(pkt) .................... 广播给所有邻居
│   ├── configure_topology(config, id) .... 配置网络拓扑
│   └── get_neighbors() ................... 获取邻居列表
│
├── 🔧 实现类
│   ├── MockNetwork ....................... 进程内模拟（已实现）
│   │   ├── 静态注册表 all_mock_networks_
│   │   └── 直接调用 enqueuePacket()
│   │
│   ├── TCPNetwork ........................ TCP 网络（待实现）
│   │   ├── 连接池管理
│   │   ├── 数据包序列化/反序列化
│   │   └── 错误处理（超时、重连）
│   │
│   └── UDPNetwork ........................ UDP 网络（待实现）
│       ├── 无连接通信
│       └── 应用层重传（可选）
│
└── 📄 配置文件格式
    ├── 基本格式（MockNetwork）
    │   └── nodes: [id, neighbors]
    │
    └── 扩展格式（TCPNetwork）
        └── nodes: [id, ip, port, neighbors]
```

### 快速跳转

| 类别 | 方法/实现 | 跳转 |
|------|----------|------|
| **核心方法** | receive() | [→ 查看](#21-receive) |
| | send_direct() | [→ 查看](#22-send_direct) |
| | broadcast() | [→ 查看](#23-broadcast) |
| | configure_topology() | [→ 查看](#24-configure_topology) |
| | get_neighbors() | [→ 查看](#25-get_neighbors) |
| **实现类** | MockNetwork | [→ 查看](#31-mocknetwork) |
| | TCPNetwork（待实现） | [→ 查看](#32-tcpnetwork待实现) |
| | UDPNetwork（待实现） | [→ 查看](#33-udpnetwork待实现) |
| **数据包序列化** | 序列化格式 | [→ 查看](#41-序列化格式) |
| **配置文件** | 基本格式 | [→ 查看](#51-基本格式mocknetwork) |
| | 扩展格式 | [→ 查看](#52-扩展格式tcpnetwork) |
| **使用示例** | MockNetwork 使用 | [→ 查看](#61-mocknetwork-使用) |

---

## 2. 接口定义

### 2.1 receive()

**功能**: 接收一个数据包（非阻塞）

**签名**:
```cpp
virtual std::unique_ptr<Packet> receive() = 0;
```

**返回值**:
- 如果有数据包，返回 `std::unique_ptr<Packet>`
- 如果没有数据包，返回 `nullptr`

**前置条件**:
- 网络接口已初始化

**后置条件**:
- 数据包从接收队列中移除

**使用示例**:
```cpp
auto pkt = network->receive();
if (pkt) {
    // 处理数据包
}
```

**实现要点**:
- **MockNetwork**: 从 `inbox_` 队列取出数据包
- **TCPNetwork**: 从 socket 读取数据包（非阻塞）
- **UDPNetwork**: 从 socket 读取数据包（非阻塞）

**变更历史**:
- v0.1.0 (2025-12-01): 初始定义

---

### 2.2 send_direct()

**功能**: 发送数据包给指定节点

**签名**:
```cpp
virtual void send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt) = 0;
```

**参数**:
- `dest_node_id`: 目标节点 ID
- `pkt`: 要发送的数据包

**前置条件**:
- 网络接口已配置（调用过 `configure_topology()`）
- `dest_node_id` 必须是有效的节点 ID

**后置条件**:
- 数据包已发送给目标节点
- `pkt` 的所有权已转移

**异常情况**:
- 如果目标节点不存在，行为未定义（建议输出错误日志）

**使用示例**:
```cpp
auto pkt = Packet::createDataPkt(request_id, dest_node_id, key);
network->send_direct(dest_node_id, std::move(pkt));
```

**实现要点**:
- **MockNetwork**: 查找目标节点的 MockNetwork 实例，调用其 `enqueuePacket()`
- **TCPNetwork**: 通过 TCP socket 发送序列化的数据包
- **UDPNetwork**: 通过 UDP socket 发送序列化的数据包

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，替代直接访问 `neighbors_`

---

### 2.3 broadcast()

**功能**: 广播数据包给所有邻居

**签名**:
```cpp
virtual void broadcast(std::unique_ptr<Packet> pkt) = 0;
```

**参数**:
- `pkt`: 要广播的数据包

**前置条件**:
- 网络接口已配置（调用过 `configure_topology()`）

**后置条件**:
- 数据包已发送给所有邻居
- `pkt` 的所有权已转移

**使用示例**:
```cpp
auto pkt = Packet::createRequestPkt(my_id, {key});
network->broadcast(std::move(pkt));
```

**实现要点**:
- **MockNetwork**: 遍历 `neighbors_`，对每个邻居调用 `send_direct()`
- **TCPNetwork**: 遍历邻居列表，通过 TCP socket 发送
- **UDPNetwork**: 遍历邻居列表，通过 UDP socket 发送

**注意**: 广播需要复制数据包（除了最后一个邻居）

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，替代遍历 `neighbors_`

---

### 2.4 configure_topology()

**功能**: 配置网络拓扑（从配置文件加载邻居关系）

**签名**:
```cpp
virtual void configure_topology(const std::string& config_file_path, uint32_t my_node_id) = 0;
```

**参数**:
- `config_file_path`: 配置文件路径（如 `config/3-node-topology.yaml`）
- `my_node_id`: 当前节点 ID

**前置条件**:
- 配置文件存在且格式正确

**后置条件**:
- 邻居列表已加载
- 网络接口已就绪

**异常情况**:
- 如果配置文件不存在或格式错误，抛出异常或输出错误日志

**使用示例**:
```cpp
auto net = std::make_unique<MockNetwork>(1);
net->configure_topology("config/3-node-topology.yaml", 1);
```

**配置文件格式**:
```yaml
nodes:
  - id: 1
    neighbors: [2]
  - id: 2
    neighbors: [1, 3]
  - id: 3
    neighbors: [2]
```

**实现要点**:
- 使用 YAML 解析库（如 yaml-cpp）解析配置文件
- 提取当前节点的邻居列表
- 存储到 `neighbors_` 成员变量

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，支持配置驱动的拓扑

---

### 2.5 get_neighbors()

**功能**: 获取邻居节点 ID 列表

**签名**:
```cpp
virtual std::vector<uint32_t> get_neighbors() const = 0;
```

**返回值**:
- 邻居节点 ID 列表

**前置条件**:
- 网络接口已配置（调用过 `configure_topology()`）

**后置条件**:
- 无副作用

**使用示例**:
```cpp
auto neighbors = network->get_neighbors();
for (uint32_t neighbor_id : neighbors) {
    std::cout << "Neighbor: " << neighbor_id << std::endl;
}
```

**实现要点**:
- 返回 `neighbors_` 成员变量的副本

**变更历史**:
- v0.2.0 (2026-01-16): 新增方法，提供邻居列表查询

---

## 3. 实现类

### 3.1 MockNetwork

**用途**: 进程内网络模拟（用于单元测试和 Demo）

**特点**:
- 使用静态注册表 `all_mock_networks_` 存储所有节点
- 通过节点 ID 查找目标节点并直接调用 `enqueuePacket()`
- 无需真实网络通信

**实现文件**: `src/network/mock_network.hpp`, `src/network/mock_network.cpp`

**使用场景**:
- 单元测试
- Demo 应用（main.cpp）
- 快速原型验证

---

### 3.2 TCPNetwork（待实现）

**用途**: 基于 TCP 的真实网络通信

**特点**:
- 使用 POSIX socket API
- 支持容器间通信（Kathara 测试）
- 可靠传输（TCP 保证顺序和完整性）

**实现文件**: `src/network/tcp_network.hpp`, `src/network/tcp_network.cpp`（待创建）

**使用场景**:
- Kathara 测试环境
- 多机部署
- 生产环境

**实现要点**:
- 监听端口，接受连接
- 维护连接池（`std::map<uint32_t, int fd>`）
- 数据包序列化/反序列化
- 错误处理（超时、断线重连）

---

### 3.3 UDPNetwork（待实现）

**用途**: 基于 UDP 的低延迟网络通信

**特点**:
- 使用 POSIX socket API
- 无连接，低延迟
- 需要应用层处理丢包和乱序

**实现文件**: `src/network/udp_network.hpp`, `src/network/udp_network.cpp`（待创建）

**使用场景**:
- 性能优化阶段
- 低延迟要求（<100μs）

**实现要点**:
- 绑定端口，发送/接收数据报
- 数据包序列化/反序列化
- 应用层重传机制（可选）

---

## 4. 数据包序列化

### 4.1 序列化格式

**TCP/UDP 网络需要序列化数据包**，建议格式：

```
[4 字节：包长度][Packet 数据]
```

**Packet 数据格式**（TLV 风格）：
```
[1 字节：PacketType]
[8 字节：request_id]
[4 字节：src_node_id]
[4 字节：dest_node_id]
[可变长度：payload]
```

### 4.2 序列化方法

```cpp
// 序列化
std::vector<uint8_t> serialize(const Packet& pkt);

// 反序列化
std::unique_ptr<Packet> deserialize(const std::vector<uint8_t>& data);
```

**实现要点**:
- 使用网络字节序（大端序）
- 处理可变长度字段（如 payload）
- 添加校验和（可选）

---

## 5. 配置文件格式

### 5.1 基本格式（MockNetwork）

```yaml
nodes:
  - id: 1
    neighbors: [2]
  - id: 2
    neighbors: [1, 3]
  - id: 3
    neighbors: [2]
```

### 5.2 扩展格式（TCPNetwork）

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

### 5.3 解析库

**推荐**: yaml-cpp

**安装**:
```bash
# Ubuntu/Debian
sudo apt-get install libyaml-cpp-dev

# CMake FetchContent
FetchContent_Declare(
  yaml-cpp
  GIT_REPOSITORY https://github.com/jbeder/yaml-cpp.git
  GIT_TAG yaml-cpp-0.7.0
)
```

---

## 6. 使用示例

### 6.1 MockNetwork 使用

```cpp
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

    // 发送数据包
    auto pkt = Packet::createRequestPkt(1, {"key"});
    net1->broadcast(std::move(pkt));

    // 接收数据包
    auto received = net2->receive();
    if (received) {
        std::cout << "Received packet from node " << received->src_node_id << std::endl;
    }

    return 0;
}
```

### 6.2 TCPNetwork 使用（草案）

```cpp
#include "src/network/tcp_network.hpp"

int main() {
    // 创建 TCPNetwork 实例（监听端口 9001）
    auto net = std::make_unique<TCPNetwork>(1, 9001);

    // 配置拓扑
    net->configure_topology("config/3-node-tcp-topology.yaml", 1);

    // 发送数据包
    auto pkt = Packet::createRequestPkt(1, {"key"});
    net->send_direct(2, std::move(pkt));

    // 接收数据包（阻塞等待）
    auto received = net->receive();
    if (received) {
        std::cout << "Received packet from node " << received->src_node_id << std::endl;
    }

    return 0;
}
```

---

## 7. 常见问题

### Q1: 为什么需要 NetworkInterface 抽象？

**A**: 为了解耦协议层和网络层，支持多种网络实现（MockNetwork, TCPNetwork, UDPNetwork），便于测试和扩展。

### Q2: broadcast() 如何实现？

**A**: 遍历邻居列表，对每个邻居调用 `send_direct()`。注意需要复制数据包（除了最后一个邻居）。

### Q3: 如何处理网络错误？

**A**:
- **MockNetwork**: 无网络错误
- **TCPNetwork**: 捕获 socket 错误，输出日志，尝试重连
- **UDPNetwork**: 捕获 socket 错误，输出日志，丢弃数据包

### Q4: 如何支持 RDMA？

**A**: 实现 `RDMANetwork` 类，继承 `NetworkInterface`，使用 RDMA verbs API。

### Q5: 如何测试网络层？

**A**: 使用 MockNetwork 进行单元测试，使用 TCPNetwork 进行集成测试（Kathara）。

---

## 8. 未来改进

### 8.1 异步 API

**当前**: 同步接口（`receive()` 非阻塞）

**未来**: 异步接口 + 回调

```cpp
// 未来接口（草案）
virtual void receive_async(std::function<void(std::unique_ptr<Packet>)> callback) = 0;
```

### 8.2 多播支持

**当前**: 广播通过遍历邻居实现

**未来**: 使用 IP 多播（UDP multicast）

```cpp
// 未来接口（草案）
virtual void multicast(std::unique_ptr<Packet> pkt, const std::string& group) = 0;
```

### 8.3 QoS 支持

**当前**: 无 QoS

**未来**: 支持优先级队列

```cpp
// 未来接口（草案）
virtual void send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt, Priority priority) = 0;
```

---

## 9. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v0.2.0 | 2026-01-16 | 新增 `send_direct()`, `broadcast()`, `configure_topology()`, `get_neighbors()` 方法 |
| v0.1.0 | 2025-12-01 | 初始定义（仅 `receive()` 方法） |

---

**下一步**: 阅读 [table-interface.md](./table-interface.md) 了解状态表接口
