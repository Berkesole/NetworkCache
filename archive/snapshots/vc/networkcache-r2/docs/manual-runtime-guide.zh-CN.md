# Rsync 手动构建 / 手动运行 / 手动拓扑实操手册

> 适用分支：`dev/zzh`
>
> 这份手册只写**仓库当前已经存在、并且我已核实过**的运行链：
> - `rsync-router`：旧 demo 入口（`main.cpp`，单进程模拟）
> - `rsync-node-dev`：当前实验入口（`app/rsync_node_main.cpp`，支持 `mock` / `tcp`）
> - `Dockerfile` / `docker/`：容器打包与单容器启动链
> - `scripts/kathara/*` 与 `scripts/acceptance/*`：仓库里已经验证过的 Kathara / 验收链
>
> **重要边界**：Redis 在本仓库里是 **cache backend**，**不是 control plane**。

---

## 1. 先选你要走的路线

### 路线 A：最省事的单进程 demo
适合：
- 先确认仓库能编译、能跑
- 不关心 TCP、多进程或容器

入口：`rsync-router`

### 路线 B：本机手动多进程 TCP
适合：
- 想自己开 2~3 个终端
- 想手动改拓扑 YAML
- 想看 `rsync-node-dev` 的真实启动参数和日志

入口：`rsync-node-dev`

### 路线 C：手动容器化 TCP
适合：
- 想自己手动打镜像
- 想自己手动起多个容器
- 想用 Docker 网络和容器名作为节点地址

入口：容器内 `rsync-node-dev`

### 路线 D：仓库当前验证过的 5 节点 Kathara 验收链
适合：
- 想复现 phase6 的已验收实验
- 想看 5 节点无环分支 / failure / isolation 场景

入口：`scripts/kathara/*` + `scripts/acceptance/*`

---

## 2. 前置依赖

### 2.1 本机构建依赖

至少需要：
- GCC 10+ 或 Clang 12+
- CMake 3.15+
- git
- make

仓库里当前默认构建命令：

```bash
cmake -S . -B build
cmake --build build -j$(nproc)
```

也可以直接用仓库自带 Makefile：

```bash
make
make run
make test-core
```

### 2.2 容器相关依赖

如果要自己打包 / 跑容器：
- Docker

### 2.3 Kathara 相关依赖

如果要跑仓库现有 5 节点实验：
- Kathara
- Docker

仓库脚本会直接调用：
- `kathara`
- `docker`

---

## 3. 当前入口和它们各自负责什么

### 3.1 `rsync-router`
编译目标来自：
- `main.cpp`

特点：
- 单进程
- 直接在一个进程里创建多个 `MockNetwork`
- 读取一个 topology + protocol + scenario YAML
- 适合跑最老的 demo 路线，以及最小 mock-sim 复现

最简单启动：

```bash
make run
# 或
./build/rsync-router
```

如果你要显式指定 YAML：

```bash
./build/rsync-router config/3-node-topology.yaml
```

### 3.2 `rsync-node-dev`
编译目标来自：
- `app/rsync_node_main.cpp`
- `src/network/tcp_network.cpp`
- `src/network/packet_codec.cpp`

特点：
- 支持 `mock` / `tcp`
- 读 topology 文件
- 读环境变量 / CLI 参数
- 支持 seed data、request keys、TCP endpoint override

它当前的命令行参数（已从源码核实）至少包括：

```text
--node-id <id>
--transport <mock|tcp>
--topology <path>
--iterations <count>
--sleep-ms <milliseconds>
--seed <k=v,k=v>
--request-keys <k1,k2>
--tcp-self <host:port>
--tcp-peer <id=host:port>
--help
```

它当前会读的关键环境变量（已从源码核实）包括：

```text
RSYNC_NODE_ID
RSYNC_TRANSPORT
RSYNC_TOPOLOGY_CONFIG
RSYNC_LOOP_ITERATIONS
RSYNC_LOOP_SLEEP_MS
RSYNC_SEED_DATA
RSYNC_REQUEST_KEYS
RSYNC_TCP_SELF
RSYNC_TCP_PEERS
REDIS_SOCKET_PATH
```

---

## 4. 构建方式

## 4.1 本地默认构建（推荐起手式）

这是当前最稳的起手方式：
- 不要求 Redis 依赖编进二进制
- 适合 demo / 手动 TCP / 容器手动实验

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
make distclean
make
```

或：

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

### 这时的运行特征
- `rsync-node-dev` 会打印：

```text
[rsync-node-dev] cache backend: in-memory (redis support disabled at build time)
```

这是**正常现象**，表示你现在跑的是内存后端。

---

## 4.2 本地 Redis-enabled 构建

如果你要复现仓库里 phase5/phase6 验收链的 Redis-enabled 形态：

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
make distclean
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=ON
cmake --build build -j$(nproc)
```

### 注意
这条链会尝试拉外部依赖（如 `hiredis` / `redis++`）。
如果你的网络不通，可能会在配置或构建阶段失败。

如果你已经提前安装好了依赖，可以尝试：

```bash
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=OFF
cmake --build build -j$(nproc)
```

---

## 4.3 手动构建容器镜像

### 方式 A：在仓库根目录直接构建（推荐）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
docker build -t rsync-router:manual -f Dockerfile .
```

### 方式 B：构建 Redis-enabled 镜像（与 phase6 更接近）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
docker build \
  --build-arg ENABLE_REDIS=ON \
  --build-arg FETCH_REDIS_DEPS=ON \
  -t rsync-router:manual-redis \
  -f Dockerfile .
```

### 关于 `docker/build.sh`
仓库里有 `docker/build.sh`，但它更像早期便捷脚本，手动实验时**不建议把它当主路径**。

原因是你现在要自己明确控制：
- 镜像 tag
- Docker build 参数
- 运行入口
- topology 挂载与节点参数

这时最稳的方式仍然是直接在仓库根目录写显式命令：

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
docker build -t rsync-router:manual -f Dockerfile .
```

如果你只是想快速试脚本，可以自行阅读 `docker/build.sh` / `docker/run.sh`，但本文手册后续都以**显式命令**为准。

---

## 5. 拓扑文件格式

当前 `tcp` 传输依赖 topology 文件。
从 `TopologyParser` 源码看，支持的核心字段是：

```yaml
nodes:
- id: 1
  host: 127.0.0.1
  port: 42201
  neighbors: [2]

- id: 2
  host: 127.0.0.1
  port: 42202
  neighbors: [1, 3]
```

### 字段说明
- `id`：节点 ID，正整数
- `host`：节点监听地址 / 可连接地址
- `port`：节点监听端口
- `neighbors`：邻居节点 ID 列表

### 当前仓库里的相关样例
- `config/3-node-topology.yaml`：只有 `neighbors`，适合老 demo / mock 路线
- `config/acceptance/acyclic-5node-redis.yaml`：带 `host/port/neighbors`，适合 phase6 拓扑
- 本手册新增：
  - `config/manual/single-node.yaml`
  - `config/manual/3-node-tcp-local.yaml`
  - `config/manual/3-node-tcp-docker.yaml`

---

## 6. 手动单节点运行

## 6.1 本机单节点（最小 TCP 启动）

目标：
- 不追求成功取回数据
- 只确认 `rsync-node-dev` 能启动、读 topology、监听 TCP

命令：

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
./build/rsync-node-dev \
  --transport tcp \
  --topology config/manual/single-node.yaml \
  --node-id 1 \
  --seed demo_key=DEMO_VALUE \
  --iterations 20 \
  --sleep-ms 200
```

### 预期关注日志
至少应看到：

```text
[rsync-node-dev] startup configuration
[rsync-node-dev] resolved tcp endpoints
[rsync-node-dev] tcp transport initialized
listening on 127.0.0.1:42201
```

---

## 6.2 单容器单节点（绕过默认 entrypoint，直接跑 `rsync-node-dev`）

适合：
- 你已经用默认方式把镜像打出来
- 你只想在容器里手动跑节点二进制

> 说明：这里默认使用**未启用 Redis 支持**的镜像，即 `rsync-router:manual`。
> 这样 `rsync-node-dev` 会走 in-memory backend，不依赖容器内 Redis socket。

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync

docker run --rm -it \
  --name rsync-single \
  --entrypoint /app/build/rsync-node-dev \
  rsync-router:manual \
  --transport tcp \
  --topology /app/config/manual/single-node.yaml \
  --node-id 1 \
  --seed demo_key=DEMO_VALUE \
  --iterations 20 \
  --sleep-ms 200
```

### `RSYNC_APP_ARGS` 与 `RSYNC_APP_ARGS_FILE`

默认容器入口支持两种显式参数注入方式：

- `RSYNC_APP_ARGS`
  - 用 shell 空白分隔参数
- `RSYNC_APP_ARGS_FILE`
  - 文件内容按**一行一个参数**读取

约束：
- 两者**不能同时设置**
- 这是给默认容器入口传参，不会改变 `rsync-node-dev` / `rsync-router` 本身的 CLI 语义

---

## 7. 本机手动多进程 TCP（推荐给“自己搭拓扑/自己看日志”）

这条链最适合你自己做手工实验。
它不依赖 Kathara，也不要求容器间 DNS。

## 7.1 拓扑文件
直接用仓库里的：
- `config/manual/3-node-tcp-local.yaml`

拓扑结构：
- node1 <-> node2 <-> node3
- 三个节点都监听在 `127.0.0.1`，端口不同

## 7.2 开三个终端

### 终端 A：先起 node3（持有数据）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
./build/rsync-node-dev \
  --transport tcp \
  --topology config/manual/3-node-tcp-local.yaml \
  --node-id 3 \
  --seed demo_key=VALUE_FROM_NODE3 \
  --iterations 1000000 \
  --sleep-ms 200 \
  | tee /tmp/rsync-node3.log
```

### 终端 B：起 node2（中间节点）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
./build/rsync-node-dev \
  --transport tcp \
  --topology config/manual/3-node-tcp-local.yaml \
  --node-id 2 \
  --iterations 1000000 \
  --sleep-ms 200 \
  | tee /tmp/rsync-node2.log
```

### 终端 C：最后起 node1（发起请求）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
./build/rsync-node-dev \
  --transport tcp \
  --topology config/manual/3-node-tcp-local.yaml \
  --node-id 1 \
  --request-keys demo_key \
  --iterations 400 \
  --sleep-ms 200 \
  | tee /tmp/rsync-node1.log
```

## 7.3 你应该检查什么

### node1
```bash
grep -E 'startup configuration|sent REQUEST|received DATA|Received data|listening on' /tmp/rsync-node1.log
```

### node2
```bash
grep -E 'received REQUEST|sent REQUEST|received DATA|sent DATA|forward REQUEST|Forwarding DATA|listening on' /tmp/rsync-node2.log
```

### node3
```bash
grep -E 'received REQUEST|sent DATA|satisfied locally|listening on' /tmp/rsync-node3.log
```

### 停止
node2 / node3 是长循环，实验完直接 `Ctrl+C`。

---

## 8. 手动容器化多节点 TCP

这条链适合你想自己用 Docker network 组网、自己起多个节点容器。

## 8.1 使用的拓扑文件
直接用仓库里的：
- `config/manual/3-node-tcp-docker.yaml`

这个拓扑把 `host` 写成容器名：
- `node1`
- `node2`
- `node3`

前提：你要把容器放在同一个**用户自定义网络**里，依赖 Docker 内置 DNS。

## 8.2 创建网络

```bash
docker network create rsync-manual
```

## 8.3 起三个容器

> 说明：仍然推荐使用默认构建的 `rsync-router:manual`。
> 因为这里直接把 entrypoint 改成 `/app/build/rsync-node-dev`，跑的是 in-memory backend。

### node3

```bash
docker run -d \
  --name node3 \
  --network rsync-manual \
  --entrypoint /app/build/rsync-node-dev \
  rsync-router:manual \
  --transport tcp \
  --topology /app/config/manual/3-node-tcp-docker.yaml \
  --node-id 3 \
  --seed demo_key=VALUE_FROM_NODE3 \
  --iterations 1000000 \
  --sleep-ms 200
```

### node2

```bash
docker run -d \
  --name node2 \
  --network rsync-manual \
  --entrypoint /app/build/rsync-node-dev \
  rsync-router:manual \
  --transport tcp \
  --topology /app/config/manual/3-node-tcp-docker.yaml \
  --node-id 2 \
  --iterations 1000000 \
  --sleep-ms 200
```

### node1

```bash
docker run -d \
  --name node1 \
  --network rsync-manual \
  --entrypoint /app/build/rsync-node-dev \
  rsync-router:manual \
  --transport tcp \
  --topology /app/config/manual/3-node-tcp-docker.yaml \
  --node-id 1 \
  --request-keys demo_key \
  --iterations 400 \
  --sleep-ms 200
```

## 8.4 看日志

```bash
docker logs -f node1
docker logs -f node2
docker logs -f node3
```

常用抓取：

```bash
docker logs node1 2>&1 | grep -E 'startup configuration|sent REQUEST|received DATA|Received data|listening on'
docker logs node2 2>&1 | grep -E 'received REQUEST|sent REQUEST|received DATA|sent DATA|forward REQUEST|Forwarding DATA|listening on'
docker logs node3 2>&1 | grep -E 'received REQUEST|sent DATA|satisfied locally|listening on'
```

## 8.5 清理

```bash
docker rm -f node1 node2 node3
docker network rm rsync-manual
```

---

## 9. 手动配置 / 搭建 5 节点 Kathara 拓扑

如果你想复现仓库当前 phase6 那条已验证实验链，最稳的是直接走仓库现成脚本。

## 9.1 关键文件

- 拓扑源文件：
  - `config/acceptance/acyclic-5node-redis.yaml`
- 渲染脚本：
  - `scripts/kathara/phase6a_acyclic_branch_render_topology.sh`
- 起 lab：
  - `scripts/kathara/phase6a_acyclic_branch_up.sh`
- 清 lab：
  - `scripts/kathara/phase6a_acyclic_branch_down.sh`

## 9.2 渲染拓扑

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
scripts/kathara/phase6a_acyclic_branch_render_topology.sh
```

默认会生成：

```text
kathara/phase6a-acyclic-branch-smoke/shared/phase6a-acyclic-branch-topology.yaml
```

### 可覆盖的环境变量
渲染脚本支持：
- `NODE1_IP ... NODE5_IP`
- `NODE1_PORT ... NODE5_PORT`
- `SOURCE_TOPOLOGY`
- `OUTPUT_TOPOLOGY`

例如：

```bash
NODE1_IP=10.50.1.1 \
NODE2_IP=10.50.1.2 \
NODE3_IP=10.50.1.3 \
NODE4_IP=10.50.1.4 \
NODE5_IP=10.50.1.5 \
scripts/kathara/phase6a_acyclic_branch_render_topology.sh
```

## 9.3 起 lab（含镜像构建）

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
BUILD_NO_CACHE=0 scripts/kathara/phase6a_acyclic_branch_up.sh
```

### 这条链会做什么
- 先渲染 5 节点拓扑
- `kathara lstart --print`
- 按下列 build args 构建镜像：

```text
--build-arg ENABLE_REDIS=ON
--build-arg FETCH_REDIS_DEPS=ON
```

- 再 `kathara lclean`
- 再 `kathara lstart --noterminals`

### 注意
这条链默认会联网拉 Redis 相关依赖。
如果外网不通，可能会在 Docker build 阶段失败。

## 9.4 清理 lab

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
scripts/kathara/phase6a_acyclic_branch_down.sh
```

---

## 10. 配置项与参数速查

## 10.1 `rsync-node-dev` 常用 CLI

```bash
--node-id <id>
--transport <mock|tcp>
--topology <path>
--iterations <count>
--sleep-ms <ms>
--seed <k=v,k=v>
--request-keys <k1,k2>
--tcp-self <host:port>
--tcp-peer <id=host:port>
```

## 10.2 常用环境变量

```bash
RSYNC_NODE_ID
RSYNC_TRANSPORT
RSYNC_TOPOLOGY_CONFIG
RSYNC_LOOP_ITERATIONS
RSYNC_LOOP_SLEEP_MS
RSYNC_SEED_DATA
RSYNC_REQUEST_KEYS
RSYNC_TCP_SELF
RSYNC_TCP_PEERS
REDIS_SOCKET_PATH
RSYNC_APP_TARGET
RSYNC_APP_ARGS
RSYNC_APP_ARGS_FILE
```

## 10.3 配置优先级（TCP endpoint）
从 `src/network/tcp_network.cpp` 当前实际解析逻辑看，顺序是：

1. **先看 env / CLI override**
   - `RSYNC_TCP_SELF`
   - `RSYNC_TCP_PEERS`
   - `--tcp-self`
   - `--tcp-peer`
2. **没有 override 时，再用 topology 文件里的 `host/port`**
3. **若 topology 缺字段，再走 legacy default fallback**
   - 默认 host：`127.0.0.1`
   - 默认 port：`40000 + node_id`

所以真实结论是：**override 优先级高于 topology。**

---

## 11. 日志查看与验收方式

## 11.1 本机直接跑
直接把 stdout/stderr 存下来：

```bash
./build/rsync-node-dev ... | tee /tmp/node1.log
```

## 11.2 容器

```bash
docker logs -f <container-name>
```

## 11.3 Kathara
当前仓库 phase6 脚本默认日志目录：

```text
kathara/phase6a-acyclic-branch-smoke/shared/logs/
```

常见文件：
- `node1.log`
- `node2.log`
- `node3.log`
- `node4.log`
- `node5.log`
- `node4.startup.log`（6C 会用到）

## 11.4 现成验收命令

### phase6a 成功面
```bash
BUILD_NO_CACHE=0 scripts/acceptance/phase6a_kathara_acyclic_branch_smoke.sh
```

### phase6b failure smoke
```bash
BUILD_NO_CACHE=0 scripts/acceptance/phase6b_kathara_acyclic_branch_failure_smoke.sh
```

### phase6c isolation smoke
```bash
BUILD_NO_CACHE=0 scripts/acceptance/phase6c_kathara_acyclic_branch_isolation_smoke.sh
```

### Redis 测试套件
```bash
make redis_test
```

---

## 12. 常见故障排查

## 12.1 `cache backend: in-memory (redis support disabled at build time)`
含义：
- 你当前二进制没有启用 Redis 支持
- 不是报错，是构建方式决定的

如果你想试 Redis-enabled 构建：

```bash
cmake -S . -B build \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=ON
cmake --build build -j$(nproc)
```

---

## 12.2 `FAIL: docker build` / `Failed to clone repository: https://github.com/redis/hiredis.git`
这通常是：
- 外网不通
- GitHub 拉依赖超时
- `FETCH_REDIS_DEPS=ON` 触发了外部下载

处理建议：
1. 先用默认构建（不启 Redis）验证主流程
2. 或提前装好依赖，改用：

```bash
cmake -S . -B build \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=OFF
cmake --build build -j$(nproc)
```

---

## 12.3 `validation error: Transport requires an existing topology file`
说明：
- `--topology` / `RSYNC_TOPOLOGY_CONFIG` 指向的文件不存在

检查：

```bash
ls -l <your-topology-file>
```

---

## 12.4 `validation error: Node ID X is not present in topology file`
说明：
- 你传的 `--node-id` 在 YAML 里没有定义

---

## 12.5 `TopologyParser: missing host / missing port`
说明：
- 你在 `tcp` 模式下给的 topology 缺少 `host` 或 `port`
- `config/3-node-topology.yaml` 这种只带 `neighbors` 的文件不适合直接跑 TCP

请改用：
- `config/manual/single-node.yaml`
- `config/manual/3-node-tcp-local.yaml`
- `config/manual/3-node-tcp-docker.yaml`
- 或自己按同样格式写

---

## 12.6 端口占用 / 容器起不来
如果你用本机 3 进程 TCP：
- 检查 `42201 / 42202 / 42203` 是否被占用
- 必要时自己改 YAML 端口

如果你用容器：
- 确认都在同一个 `docker network` 里
- 确认容器名和 topology `host` 一致

---

## 12.7 Kathara 起不来
优先检查：
- `kathara` 是否已安装
- `docker` 是否可用
- 当前用户是否有权限执行 Kathara / Docker

先单独验证：

```bash
kathara --version
docker version
```

---

## 12.8 phase6 脚本跑完后残留 `shared/state/` 或 `phase6a-acyclic-branch-topology.yaml`
这些通常是：
- 运行过程中生成的 lab / topology 产物
- 不一定是代码脏改

常规清理：

```bash
scripts/kathara/phase6a_acyclic_branch_down.sh
rm -rf kathara/phase6a-acyclic-branch-smoke/shared/state
rm -f kathara/phase6a-acyclic-branch-smoke/shared/phase6a-acyclic-branch-topology.yaml
```

---

## 13. 推荐你实际怎么用

如果你是**第一次自己动手**，我建议顺序是：

1. **先跑 demo**
   ```bash
   make run
   ```
2. **再跑本机 3 进程 TCP**
   - 用 `config/manual/3-node-tcp-local.yaml`
3. **再跑手动容器 3 节点**
   - 用 `config/manual/3-node-tcp-docker.yaml`
4. **最后再跑 Kathara 5 节点 acceptance**
   ```bash
   BUILD_NO_CACHE=0 scripts/acceptance/phase6a_kathara_acyclic_branch_smoke.sh
   BUILD_NO_CACHE=0 scripts/acceptance/phase6c_kathara_acyclic_branch_isolation_smoke.sh
   ```

这样你能先把：
- 二进制
- 配置
- 拓扑
- 容器
- Kathara

这五层分开看清，再回头做更复杂的实验。

---

## 14. 本手册覆盖范围说明

本手册只覆盖仓库当前已经存在并已核实的内容：
- 手动构建
- 手动 TCP 运行
- 手动容器化运行
- 手动拓扑文件
- Kathara phase6 实验脚本
- 日志抓取与验收命令

**不包含**：
- 新的控制面设计
- 新的 Docker Compose / Kubernetes 编排
- ring / mesh / 多路径竞争新实验
- 任何未在仓库里出现过的新脚本

如果后续你要把“纯手动多容器 + 5 节点 + Redis-enabled + 非 Kathara”也固化成仓库标准链，建议下一步再单独补一份脚本，而不是继续把命令堆在 README 里。
