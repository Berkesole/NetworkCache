# NetworkCache 同步协议（rsync）

> 面向数据中心命名对象的 cache-to-cache 同步协议 C++20 原型。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
![C++20](https://img.shields.io/badge/C++-20-blue.svg)
![Status](https://img.shields.io/badge/Status-Research_Prototype-orange)

---

## 项目概览

`rsync` 是 **NetworkCache** 的实现原型。NetworkCache 的定位是一个分布式缓存的
miss-path 同步底座：应用仍然使用熟悉的本地缓存语义，本地命中立即返回；本地未命中时，
由邻近的 NetworkCache 节点接管发现、转发、聚合、回填等逻辑，而不是让每个应用自己维护
分片规则、目录查询、重试和 cache-fill 代码。

这个原型把对象名、key 或带版本的标识符视为稳定的分布式 handle。发生 miss 时，节点会传播
表示 pending demand 的 Bloom Filter 摘要，在相同出口路径上聚合重叠请求，从返回的 DATA
中学习软性的 location hint，并在返回路径上逐跳物化对象副本。设计目标是运行在普通以太网
和“智能端主机 + 简单网络”的部署假设上，而不是依赖 lossless RDMA 网络。

本仓库仍然是研究原型：它适合验证协议机制、跑受控场景和支撑论文实验，不是生产级缓存服务。

---

## 设计目标

### 1. 解耦对象身份与数据位置

NetworkCache 将几类状态拆开处理：

- **对象身份**：REQUEST 包中用 Bloom Filter 表示命名 key。
- **待满足需求**：QueryTable 记录哪些下游节点可能还在等待匹配对象。
- **位置提示**：SyncTable 从返回的 DATA 包中学习邻居/位置 hint。
- **临时聚合**：UnionWindow 只作为短期转发优化，降低同一出口上的请求压力。

这样，应用看到的仍然是以对象名为中心的缓存 API，而数据位置可以由同步层学习和优化。

### 2. 把 cache miss 视为共享同步事件

一次 miss 不只是单独的远程读取，它会暴露有用的系统状态：

- 多个节点可能正在等待重叠对象。
- 返回的数据可以同时满足本地需求和下游需求。
- DATA 的返回路径可以教会节点“下一次可能该往哪里找”。
- 本地缓存返回数据后，也自然成为重复 DATA fan-out 的隐式过滤器。

### 3. 用有界冗余换取灵活路径

当前协议允许机会式多路径检索，但通过以下机制约束放大：

- 有限的 `hop_count` / `max_hop_count`，
- 转发时排除上一跳，
- REQUEST 只允许单层聚合，
- QueryTable 项是短生命周期状态，
- 依赖本地 cache 抑制重复 DATA 继续扩散。

目标不是完全消灭重复包，而是在保持路径多样性和 warm retrieval 收益的同时保证最终收敛。

---

## 当前架构

```
Node
├── NetworkInterface
│   ├── MockNetwork     # 进程内拓扑模拟
│   └── TcpNetwork      # length-prefixed TCP 包传输
├── LocalCache
│   ├── InMemoryCache   # 默认后端，无外部依赖
│   └── RedisCache      # 可选 Redis 后端，基于 redis-plus-plus
├── QueryTable          # 原始 pending demand 状态
├── SyncTable           # 学到的邻居/位置 hint
├── UnionWindow         # 每个出口的单层 REQUEST 聚合窗口
└── BloomFilter         # 近似对象名摘要
```

### Packet 类型

| 类型 | 用途 | 主要负载 |
|------|------|----------|
| `REQUEST` | 传播一个或多个 key 的需求 | Bloom Filter |
| `DATA` | 返回具体 key/value 对象 | key + value |

`SYNC` 不再属于当前活跃包路径。现在邻居知识主要来自 DATA 返回和本地 SyncTable 更新；周期性
BF gossip 仍是后续工作。

### 运行入口

| 目标 | 用途 |
|------|------|
| `rsync-router` | 场景驱动的 mock 模拟器，`make run` 使用它 |
| `rsync-node-dev` | 单节点运行时，支持 mock 或 TCP transport |
| `test_core` | 无外部依赖的核心数据结构测试 |
| `test_mesh_demo` | 8x8 mock mesh 场景 |
| `test_redis_cache_contract` | 可选 Redis 缓存契约测试 |

---

## 当前已实现

- 核心协议状态：
  - `Node`
  - `Packet`
  - `BloomFilter`
  - `QueryTable`
  - `SyncTable`
  - `UnionWindow`
- REQUEST 路径：
  - 转发前先查本地缓存，
  - 创建 Bloom-filter REQUEST，
  - SyncTable miss 时 broadcast fallback，
  - 学到 hint 后可按 SyncTable 定向 unicast，
  - 排除上一跳，
  - hop-count 有界传播，
  - 每个出口进行单层聚合。
- DATA 路径：
  - 第一次收到 DATA 时写入本地 cache，
  - 从 DATA source 学习 SyncTable hint，
  - 根据 QueryTable 向下游 fan-out，
  - 请求发起者收到数据后通知应用层，
  - 依靠本地 cache 抑制重复 DATA。
- 传输层：
  - `MockNetwork` 用于确定性的进程内场景，
  - TCP transport 支持拓扑文件 endpoint 和运行时 override。
- 缓存后端：
  - 默认 InMemoryCache，无外部依赖，
  - 可选 RedisCache，通过 Unix socket 连接 Redis。
- 场景与验证：
  - YAML-like 拓扑/场景解析器，
  - 默认三节点 demo，
  - 8x8 mock mesh demo，
  - Docker bridge acceptance 脚本，
  - Kathara 场景脚本。

---

## 仍在推进中

- 事件驱动运行时：
  - `processOne()` 仍由外层 tick/loop 驱动，
  - TCP polling 集成在 `receive()` 中，还不是独立 reactor。
- Bloom Filter 工程化：
  - 当前 BF 每个 key 只使用一个 `std::hash` 位置，
  - 还没有 counting BF、generation 管理、饱和检测或可配置误报目标。
- SyncTable 策略：
  - route lookup 和 learned hint 已存在，
  - LRU 淘汰、TTL 清理集成、自适应权重、失败反馈还没有完整接入运行路径。
- Evaluation：
  - 当前测试主要验证机制和 smoke 场景，
  - 延迟、放大、冗余、可扩展性等测量仍属于论文实验工作。
- Producer-side 语义：
  - 当前原型面向 immutable、versioned 或 epoch-named value，
  - 强一致、写入驱动 freshness、allocation ownership 和 free 语义不在当前 consumer-side 设计范围内。

---

## 构建与运行

### 依赖

- GCC 10+ 或 Clang 12+，需要 C++20 支持
- CMake 3.15+
- Make
- 可选：Docker、Kathara、Redis 7+、hiredis、redis-plus-plus

默认关闭 Redis 支持，因此核心原型可以在离线环境中直接构建。

### 常用命令

```bash
make              # 配置并构建默认目标
make run          # 构建并运行默认三节点 mock 场景
make test-core    # 构建并运行无外部依赖的核心测试
make run_mesh     # 构建并运行 8x8 mock mesh demo
make test-redis   # 启用 Redis 支持并运行 Redis 测试
make clean        # 清理 CMake target 输出
make distclean    # 删除 build/
```

### 手动 CMake

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
./build/rsync-router
```

### 默认 mock 场景

默认拓扑是 `config/3-node-topology.yaml`：

```
Node 1 <-> Node 2 <-> Node 3
```

Node 3 初始持有 `redis_key_a=VALUE_OF_A`，Node 1 请求 `redis_key_a`。成功运行时可以看到
REQUEST 经 Node 2 转发到 Node 3，DATA 再经 Node 2 返回 Node 1。

```bash
make run
```

关键输出大致如下：

```text
[Node 1] Broadcast REQUEST for 1 key(s) (no SyncTable hit)
[Node 2] flush AGGREGATE to Node 3
[Node 3] Sending DATA for key=redis_key_a to Node 2
[Node 2] Forwarding DATA for key=redis_key_a to Node 1
[Node 1] Received DATA for key=redis_key_a
[App] Received data for key=redis_key_a, size=10, content="VALUE_OF_A"
```

### 单节点运行时

`rsync-node-dev` 可以通过 CLI 参数或环境变量配置。

```bash
./build/rsync-node-dev \
  --node-id 1 \
  --transport tcp \
  --topology config/manual/3-node-tcp-local.yaml \
  --iterations 100 \
  --sleep-ms 50
```

常用参数：

- `--node-id <id>`
- `--transport <mock|tcp>`
- `--topology <path>`
- `--iterations <count>`
- `--sleep-ms <milliseconds>`
- `--seed <k=v,k=v>`
- `--request-keys <k1,k2>`
- `--tcp-self <host:port>`
- `--tcp-peer <id=host:port>`

对应环境变量包括 `RSYNC_NODE_ID`、`RSYNC_TRANSPORT`、`RSYNC_TOPOLOGY_CONFIG`、
`RSYNC_LOOP_ITERATIONS`、`RSYNC_LOOP_SLEEP_MS`、`RSYNC_SEED_DATA`、`RSYNC_REQUEST_KEYS`、
`RSYNC_TCP_SELF` 和 `RSYNC_TCP_PEERS`。

Docker、TCP、拓扑配置和 Kathara 的手动操作说明见
[`docs/manual-runtime-guide.zh-CN.md`](./docs/manual-runtime-guide.zh-CN.md)。

---

## Redis 支持

Redis 支持是构建期可选项：

```bash
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=ON
cmake --build build -j
```

`RedisCache` 通过 Unix socket 连接 Redis。默认路径是 `/run/redis/redis.sock`，可以用
`REDIS_SOCKET_PATH` 覆盖。

```bash
REDIS_SOCKET_PATH=/tmp/redis.sock ./build/rsync-node-dev ...
```

运行 Redis 测试套件：

```bash
make test-redis
```

如果使用 FetchContent 构建出来的动态库，运行时可能需要：

```bash
LD_LIBRARY_PATH=build/_deps/hiredis-build:build/_deps/redis_plus_plus-build make test-redis
```

---

## 目录结构

```
rsync/
├── CMakeLists.txt
├── Makefile
├── main.cpp                         # rsync-router mock 场景入口
├── app/
│   └── rsync_node_main.cpp          # rsync-node-dev 运行时入口
├── src/
│   ├── node.hpp / node.cpp          # 协议状态机
│   ├── packet.hpp / packet.cpp      # REQUEST/DATA 包模型
│   ├── bloomfilter.hpp              # 近似 key 摘要
│   ├── query_table.hpp              # pending demand 表
│   ├── sync_table.hpp               # 学到的位置 hint
│   ├── union_window.hpp             # 请求聚合窗口
│   ├── cache/                       # LocalCache 后端
│   └── network/                     # mock/TCP 传输与拓扑解析
├── config/
│   ├── *.yaml                       # mock 场景
│   ├── manual/                      # 手动运行示例
│   └── acceptance/                  # bridge/Kathara 验收输入
├── tests/
│   ├── test_core.cpp
│   ├── mesh.cpp
│   └── redis/
├── scripts/
│   ├── acceptance/
│   ├── kathara/
│   └── tests/
├── docker/
├── kathara/
└── docs/
```

---

## 文档索引

- [`docs/manual-runtime-guide.zh-CN.md`](./docs/manual-runtime-guide.zh-CN.md)：
  Docker、TCP、拓扑和 Kathara 手动运行指南。
- [`docs/cache-architecture.md`](./docs/cache-architecture.md)：
  缓存接口与后端关系。
- [`docs/src-structure.md`](./docs/src-structure.md)：
  预期源码分层视图。
- [`docs/interfaces/`](./docs/interfaces/)：
  cache、node、network、table 和 global types 的接口说明。
- [`src/cache/README.zh-CN.md`](./src/cache/README.zh-CN.md)：
  cache 模块说明。
- [`src/network/MIGRATION.md`](./src/network/MIGRATION.md)：
  network 层迁移说明。

---

## 开发路线图

近期工程工作：

- 用事件驱动 runtime 替代外层 polling loop，
- 改进 BloomFilter hash 与饱和控制，
- 将 SyncTable 清理、路径质量和失败反馈接入运行路径，
- 扩展 packet codec 覆盖和 malformed-frame 测试，
- 增加聚合、重复抑制、learned warm retrieval 等协议级测试，
- 收集 cold retrieval、warm retrieval、聚合、冗余/时延权衡和 dense topology amplification 的实验数据。

更长期的研究工作：

- producer-side 命名与 allocation 语义，
- 可变对象的 freshness/coherence 模型，
- 更大规模拓扑实验，
- 与应用层 sharding、directory lookup、RDMA-like 部署假设进行对比。

---

## 贡献方式

1. Fork 仓库。
2. 创建功能分支。
3. 保持 commit 聚焦，并在 commit message 中写清上下文。
4. 运行相关本地测试或 smoke 脚本。
5. 打开 pull request。

代码风格：

- 语言：C++20。
- 优先沿用项目已有模式，不轻易引入新抽象。
- 协议注释保持准确；代码标识符保持英文。
- 除非任务需要，不要默认启用 Redis 或网络依赖下载。

---

## License

MIT License。详见 [LICENSE](./LICENSE)。

Copyright (c) 2025 Network Cache Research Project

---

**最后更新**：2026-06-25

**项目状态**：包含 mock、TCP、in-memory 和可选 Redis 路径的研究原型

**代码名**：`rsync`；论文/系统名：`NetworkCache`
