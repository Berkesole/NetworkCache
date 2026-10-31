# concurrency-finite-cache-v1

有限副本缓存 + 应用层按字节 LRU 的实现说明、事件契约与确定性测试记录。

- 版本名：`concurrency-finite-cache-v1`
- 远端候选独立根目录：`/home/simpleai/zhengzihao/networkcache-concurrency-finite-cache-v1`（**尚未创建**）
- 状态：**代码与确定性测试完成；尚未经过真实网络运行，未构建镜像，未启动任何 smoke/pilot/formal 队列。**
- 本文档与 `FINITE_CACHE_EVICTION_PLAN.md`（同目录快照）一并阅读：那份是设计稿，本文是实现结果与**明确变更说明**。

## 1. 基线与来源

实现基于 simple-station 上已冻结的并发容量版本，而不是本机 GitHub 主线：

| 项目 | 值 |
|---|---|
| 主机 | `simple-station` |
| 根目录 | `/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1` |
| 源码 | `<root>/source` |
| 分支 | `simple-station/concurrency-capacity-v1` |
| commit | `ac2fc775c43d718222d66cbe3ad80dea8c3b42e5` |
| tracked 文件数 | 535（导入后逐个 sha256 校验一致） |
| 该仓库 origin | `/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5/source` |

重要事实：该远端仓库是一条 **26 commit 的部署本地血缘**（origin 指向上面的 slides-v5 源码目录），与本机仓库的 GitHub 历史**没有共同祖先**，`ac2fc77` 在本机仓库中不存在。因此本分支的**第一个 commit 是逐字节导入的基线**，`git diff <基线 commit>..HEAD` 就是完整实现差异。

同时核对过：`capacity-v1/source` 与 `slides-v5/source` 的 C++ 源文件（`src/node.cpp`、`src/node.hpp`、`src/cache/*`、`src/event_trace.*`、`app/rsync_node_main.cpp`、`main.cpp`、`CMakeLists.txt`、`docker/*`、`kathara/.../start-node.sh`）**逐字节相同**，所以两个版本共用同一份协议实现，镜像标签 `rsync-router:concurrency-slides-v5` 与这份源码对应。

### 1.1 基线修复（必须）

`CMakeLists.txt` 无条件引用 `experiments/content_discovery/delay_find_content/oracle_direct_fetch.cpp`，而该文件在**这条血缘里不存在**，导致 `cmake` 直接失败、整个项目无法配置，也就无法为新版本构建镜像。已加 `if(EXISTS ...)` 守卫：文件存在时行为不变，不存在时跳过并打印原因。这是新版本能被构建的最小前提，与缓存语义无关。

## 2. 相对旧设计文档的变更（prompt 为准）

旧设计稿 `FINITE_CACHE_EVICTION_PLAN.md` 中以下内容被更正或替换，旧文档相应位置保留原文以便对照。

### 2.1 术语更正

1. **预算 0 是副本旁路，不是驱逐。** 只有曾真实驻留过的对象被移除才算 eviction。实现上预算 0 只产生 `replica_bypass`（reason `budget_zero`），**永不产生 `replica_evict`**。
2. **438 个对象是"已观察样本中的单节点最大值"**，不是任何负载/seed 的全局上界。§4.1 的回放数字全部是沿 unlimited 运行路径的**反事实回放**，不是实测有限缓存结果。
3. **`last_touch_mono_ns` 只能算时间间隔**，不能算 reuse distance。契约里该差值叫 **`idle_age_ns`**。真正的 reuse distance 需要有序访问流 + 不同 key 计数；**本版本明确不提供**（不是 0，也不是缺失）。
4. **TCP Direct 没有 C++ Node 缓存 trace**（`RSYNC_SKIP_APP=1`，其 `traces/` 为空是构造使然），它的传输证据在 `tcp-traces/`。空 `traces/` 不得被当作"NC 缓存证据缺失"。
5. **旧报告的"副本服务保留率"是回放指标**，不是真实命中率，不得当作命中率引用。

### 2.2 设计选择变更

1. **Owner 与 Replica 共用一个 Redis 实例、不同命名空间**（旧稿把这一点留作待定）：`owner:<logical-key>` 与 `replica:<logical-key>`。协议、schedule、应用请求只用 logical key，前缀不泄漏到线上。编码是单射且两命名空间不相交（有测试）。
2. **候选容量按字节固定**：0 / 262144 / 524288 / 1048576 / 2097152 / 4194304 / 16777216 bytes + `unlimited`。对象数只是 64 KiB 对象下的展示换算；底层始终按真实 value 长度计费。**32 MiB 不作为第一轮独立性能处理组**，只作足量缓存回归对照。
3. **Redis 保持 `maxmemory=0` / `noeviction`**，容量完全由应用层管理；本轮**没有**给对象加 TTL（`RedisCache::put` 仍是普通 SET）。
4. **默认 `legacy` 策略保留原 Node 的正常行为与后端异常传播方式**（单一扁平 key 空间、无预算、无 LRU、不新增 cache 事件）。**这不是"逐字节兼容"承诺**：本轮已把门面在 legacy 下捕获后端 `get`/`put`/`exists` 异常、转成 miss / `AlreadyResident` / `BackendError` 的行为改回原样传播（见 §3.7）。但新增的 trace `schema_version`、cache 事件契约、`control/fatal/*` 进程诊断、owner seed-once/seal 校验与 Redis 超时不确定性都**不属于** legacy 兼容承诺（见 §3.7 与 §7）。
5. **`unlimited` 是独立策略值，与 0 不混用**：它走与 `lru` 完全相同的 owner/replica 接口与记账，只是不做预算与驱逐，保证有限组有可配对的同代码路径对照。已修正一个真实缺陷：旧实现里 `budget_bytes==0` 会连带禁用 `unlimited`。
6. **移除 `local_files_`**，代之以 store 自有的"可服务目录"（owner ∪ 驻留副本），从结构上保证"驱逐后目录项立即消失、owner 项永不消失"，不再依赖调用方手动同步。

## 3. 实现的最终语义

### 3.1 组件

| 文件 | 职责 |
|---|---|
| `src/cache/key_namespace.hpp` | logical→physical key 编码/解码；单射与不相交性 |
| `src/cache/cache_events.hpp` | 事件名、`FieldList`、sink 接口、单调时钟 |
| `src/cache/owner_store.{hpp,cpp}` | owner 索引；seed-once、只读、参与预算、永不驱逐 |
| `src/cache/replica_cache.{hpp,cpp}` | 按字节预算的 LRU；准入/驱逐/旁路/冲突/修复 |
| `src/cache/node_cache.{hpp,cpp}` | 门面：owner 优先读取、tier、可服务目录、策略、事件发射 |
| `src/cache/trace_event_sink.{hpp,cpp}` | 事件 sink → `rsync_trace` 适配器（缓存核心不依赖 trace 写入器） |
| `src/event_trace.cpp` | 记录新增 `schema_version`；允许追加新字段（不再被白名单丢弃） |

### 3.2 Owner 语义

- 初始化期写入一次；`seal()` 之后 `seed()` 抛 `std::logic_error`。`seal()` 在**首次服务/准入**时自动生效（应用也在 seeding 后显式调用）。
- **不计入副本预算，永不参与驱逐**，owner 命中不刷新副本 LRU。
- 同一 logical key 重复 seed：内容相同 → 幂等，不计重复；**大小或内容不同 → 发 `cache_key_conflict`（reason `owner_content_mismatch`）并抛错终止**，不静默接受冲突数据。
- owner 对象在存储中丢失（索引有、KV 无）→ 发 `cache_backend_error`（reason `owner_kv_missing`, `fatal=1`）**并抛错停止该 run**，绝不当作"空对象"继续服务。

### 3.3 副本 LRU 语义

- 成功准入置为 MRU；**只有真实服务读取**（`use()`）刷新 recency，每次服务最多一次 touch（有 `touch_seq` 计数）。
- `exists` / `resident` / `physicalExists` / 诊断遍历 / gauge 采样 **不刷新** recency。
- **仅收到重复 DATA**（key 已驻留）→ `AdmitStatus::AlreadyResident`，不写入、不重复计费、不刷新 recency。
- 同一 key 已是本节点 owner → **不创建冗余副本**，发 `data_admit_skipped`（reason `owner_resident`）。
- 插入前先按 LRU 选victim并删除，保证结束后 `resident_value_bytes ≤ budget_bytes`；单对象大于预算 → 旁路（reason `object_larger_than_budget`），仍正常转发与交付。
- 同一 key：大小不同 → 冲突（`size_mismatch`，只用 in-memory 索引判定，不读后端）；大小相同但字节不同 → 冲突（`content_mismatch`）；字节相同 → 不是冲突，正常重复。在 `lru` / `bypass` / `unlimited` 下**无条件**做精确内容校验，没有关闭入口（详见 §4.5）；`legacy` 单独保留历史行为。
- **容量拒绝与后端故障严格区分**：配额/过大/预算0 走 `replica_bypass`；真实 Redis 读写/删除失败走 `cache_backend_error`，且**失败操作绝不记为成功 admit/evict**。故障再分两类：**能证明副作用未发生**（确定性测试后端；victim 保持驻留、不计数）与**结果不确定**（真实 Redis 连接/响应失败：既不做成"仍在驻留"，也不计为成功驱逐，`run` 立即失败，见 §4.6）。
- **部分成功的驱逐按事实收尾**（`finalizeEvictions`）：ReplicaCache 先删 victim 再写新对象，所以「删除已成功、随后写入失败」是可能的。**已确认删除成功**的 victim 一律从可服务目录移除，并按删除顺序各发一条 `replica_evict`，**不为了伪原子性把它插回**；能证明未执行的删除失败的 victim 保持驻留、不计数、不发事件，新对象也不写入。该收尾对**所有**准入结果（成功或失败/不确定）都执行，且先于本次的最终结果事件，因此一次尝试的最终结果事件仍然恰好一个。
- 索引声称驻留但 KV 无值（副本）→ 发 `cache_backend_error`（`replica_kv_missing`, `fatal=0`），立即丢弃索引项并在批次末尾 `maintain()` 清理目录项；该 key 视为 miss，请求改为远程获取，**不静默丢弃应用请求**。

### 3.4 在途 DATA 与 pin

不需要 pin 机制，也没有引入。现有 `Value` 所有权模型已经足够：DATA 载荷在服务/转发处都是**独立拷贝**（`DataPacket::create(..., std::move(copy))`，`LocalCache::get` 返回新 `Value`），LRU 不持有任何指向存储的内部引用，因此驱逐不可能造成悬空引用。测试 `eviction_during_inflight_data_does_not_corrupt_delivery` 断言驱逐后已取出的值仍完整。

### 3.5 路由与 Bloom

未改动：Bloom 参数、QueryTable 算法、fallback、摘要更新机制全部保持原样；**没有新增 Bloom 清位操作**。允许邻居摘要短暂指向已驱逐对象，因为实际服务前一定查存储（`handleRequest` 一律走 `lookupForService`，查不到就不发成功 DATA）。缺失对象不会产生成功 DATA，也不会错误完成应用请求（有测试）。请求仍可通过既有发现/fallback 路径到达被保护的 owner。

### 3.6 接入点（全部入口）

三条写路径与所有读路径都接到门面上：

| 入口 | 处理 |
|---|---|
| `Node::addLocalData` | owner seed（`seedOwner`） |
| `handleData`（fallback 分支） | 到达 DATA → `admitReplica`（phase `fallback`） |
| `handleData`（preferred 分支） | 到达 DATA → `admitReplica`（phase `preferred`） |
| `requestFile` 本地命中 | `lookupForService` → tier / lru_touched → 发 `local_cache_hit` / `provider_hit` |
| `requestFileFromProvider` 本地命中 | 同上 |
| `handleRequest` provider 遍历 | 目录遍历 + `lookupForService`（tier / lru_touched 进 `provider_hit`） |

`hasLocalData` = 目录探测（不 touch）。原 `local_files_` 成员已删除。

### 3.7 legacy 兼容性：正常行为与后端异常传播

基线是导入提交 `0feb4b22`（原 Node 直接调用 `LocalCache::get/put/exists`）。本轮逐项核对后，把门面在 legacy 下**吞掉后端异常**的分支改回原样传播。对照如下（"修复前 legacy" 指 `11988816`）：

| 路径 | 旧 Node（`0feb4b22`） | 修复前 legacy | 本轮结果 |
|---|---|---|---|
| owner/初始写入 `addLocalData` | `put`（裸 key）异常向外抛；成功后才 `local_files_.insert` | `seedOwner`→`OwnerStore::seed`：`put` 异常同样向外抛，成功后才进目录 | 已一致，无需修复 |
| 本地应用读取 `requestFile` | `exists`、`get` 异常都向外抛；`exists` 为真就不再远程获取（即使 `get` 返回空） | `exists` 抛；`get` 异常**被捕获转成 miss**，并继续远程获取 | **已修复**：`get` 异常传播；`exists` 为真时不再回退远程获取 |
| provider 服务读取 `handleRequest` | `get` 异常向外抛 | `get` 异常被捕获，按"未命中"跳过 | **已修复**：异常传播 |
| DATA 准入写入 | `exists` 1 次 + 缺失时 `put` 1 次，两者异常都向外抛 | `exists` **2 次**（Node 探测 + 门面），`put` 异常被捕获转 `BackendError`，Node 继续交付/转发 | **已修复**：只 1 次 `exists` + 1 次 `put`，异常传播；写失败不留目录项、不计成功、不继续后续操作 |
| `exists`/目录查询 | `cache_->exists` 异常向外抛 | `backend_->exists` 异常向外抛 | 已一致，无需修复 |
| 重复 DATA 跳过 | `exists` 为真 → 不写、直接继续 | 行为一致，但多一次 `exists` | 行为一致；冗余探测随 DATA 准入修复一并消除 |

规则：legacy 下后端 `get`/`put`/`exists` 失败一律以 `throw;` 原样传播（保留异常类型与信息），不转成 miss、`AlreadyResident`、`BackendError` 或"正常成功"；失败写入不新增目录项、不更新成功记账、不执行原路径不会执行的后续操作；legacy 继续使用裸 logical key，不产生 `owner:`/`replica:` 前缀，也不新增 cache 事件。

**明确不属于 legacy 兼容承诺**：

- **新增校验（未放宽）**：owner 现在是 seed-once + seal。调用链 `Node::addLocalData` → `NodeCache::seedOwner` → `OwnerStore::seed`：旧实现对同一 key 再写一次会直接覆盖；现在 `seal()` 之后再次 seed 抛 `std::logic_error`，同一 key 不同大小的再次 seed 抛 `std::runtime_error`。**owner 重复 seed 的精确内容比较始终开启**（`OwnerStore::verify_content_` 默认 `true`），因此同大小不同内容也抛——它**不受 `RSYNC_CACHE_VERIFY_REPLICA_CONTENT` 控制**，该变量只影响有限缓存模式的副本/legacy 路径（`NodeCache` 从不把它传给 `OwnerStore`）。这是**新增的正确性校验**，把它改回覆盖会重新引入"静默用冲突内容覆盖 owner 对象"，属于安全规则，本轮**不自行放宽**；如确需旧初始化语义请另行决策。
- **trace 版式**：`schema_version` 字段与旧 16 字段的追加规则是新增设施，不能声称输出逐字节一致。
- **进程入口（fatal 标记的范围）**：`control/fatal/node<id>.*` **只由 `app/rsync_node_main.cpp` 主运行循环里被捕获的异常产生**：`processOne()` 抛出的 `ImmutabilityViolation` → `.immutability-violation`，本轮的 `BackendIndeterminate`/`CacheUnavailable` → `.backend-indeterminate`/`.backend-unavailable`，其余 `std::exception` → `.runtime-error`；循环内三个 `requestFile*` 调用点的后端异常也会写 `.backend-indeterminate`/`.backend-unavailable`。**不是所有异常都会生成 fatal 文件**：初始化 seeding（`load_experiment_data` 捕获后只打印并 `return EXIT_FAILURE`；`config.seed_data` 循环未捕获，异常直接逃出 `main`）、启动期显式请求（1060–1064）、`sealOwners`/`emitConfigEvent`、以及命令处理块里那些在 try 之外的 `throw`，都不会写 fatal 标记。这是**入口如何报告异常**，与 Node 内是否传播是两件事；旧入口没有该标记（异常直接逃出 `main`），本轮保留并扩展新机制、不回退。
- **provider 遍历顺序**：`local_files_` 与 `serviceableKeys()` 都是 `std::unordered_set`，迭代顺序未定义，不承诺同一顺序。
- **Redis 路径**：未在本机编译/实测；Redis 超时不等于操作未执行，本轮不提供该情形下的保证（见 §7）。

## 4. 事件契约

新增 `schema_version`（默认 `"2"`，`RSYNC_EVENT_TRACE_SCHEMA_VERSION` 可覆盖）写入**每条**记录；旧 16 个字段顺序不变，调用方提供的非旧字段按传入顺序追加，因此新字段不会再被静默丢弃。

| 事件 | 发出时机 | 必填字段 | reason 枚举 | 频次 |
|---|---|---|---|---|
| `cache_config` | run 启动、seeding 之后一次 | `budget_mode`、`budget_bytes`、`namespace_owner`、`namespace_replica`、`owner_store`、`value_granularity`、`verify_replica_content`、`gauge_interval_ms`、`redis_maxmemory`、`redis_maxmemory_policy`、`redis_require_empty`、`redis_socket_path` | `startup` | 每 run 1 次 |
| `replica_admit` | **put 成功之后** | `key`、`value_bytes`、`resident_bytes`、`resident_objects`、`evicted_count`、`evicted_bytes`、`budget_mode`、`budget_bytes` | `admitted` | 每次成功准入 1 次 |
| `replica_evict` | **del 成功之后**（事件在 `admit()` 返回后统一写出，见 §4.4 时间戳） | `key`、`value_bytes`（本 victim 大小）、`resident_bytes`、`resident_objects`（**该 victim 删除后的快照**）、`inserted_mono_ns`、`last_touch_mono_ns`、`evicted_mono_ns`、`idle_age_ns`、`insert_seq`、`touch_seq`、`cause_key`、`cache_contract_revision` | `budget` | 每个 victim 1 次 |
| `replica_bypass` | 拒绝缓存且仍正常转发/交付 | `key`、`value_bytes`、`resident_bytes`、`resident_objects` | `budget_zero` / `policy_bypass` / `object_larger_than_budget` | 每次拒绝 1 次 |
| `data_admit_skipped` | 到达 DATA 命中已有对象 | `key`、`value_bytes`、`resident_bytes`、`resident_objects` | `already_cached` / `owner_resident` | 每次跳过 1 次 |
| `cache_key_conflict` | DATA 路径检测到同 key 冲突（另有 owner 重复 seed 冲突） | DATA 路径：`key`、`value_bytes`、`incoming_bytes`、`existing_bytes`、`tier`、`namespace`、`cache_contract_revision`；owner seed：`key`、`namespace`、`value_bytes`、`resident_bytes` | DATA 路径：`size_mismatch` / `content_mismatch`；owner seed：`owner_content_mismatch`（修订 2 的旧 DATA 事件 reason 为 `replica_size_mismatch` / `replica_content_mismatch`，见 §4.4） | DATA 路径每次冲突 1 次并抛 `ImmutabilityViolation`；owner seed 冲突 1 次并抛错 |
| `cache_backend_error` | 后端操作失败、结果不确定或 KV/索引分叉 | `key`、`reason`、`detail`、`fatal`、`category`、`cache_contract_revision` | `replica_put_failed` / `replica_evict_failed` / `replica_get_failed` / `owner_get_failed` / `replica_kv_missing` / `owner_kv_missing` / `backend_get_failed`（`category=pre_side_effect|divergence`）；`replica_put_indeterminate` / `replica_evict_indeterminate` / `replica_get_indeterminate` / `owner_put_indeterminate` / `owner_get_indeterminate`（`category=indeterminate`, `fatal=1`） | 每次失败 1 次；`indeterminate` 之后 store 不可用（见 §4.6） |
| `cache_gauge` | 周期（`gauge_interval_ms`，0 = 关闭） | `owner_objects`、`owner_bytes`、`replica_objects`、`replica_bytes`、`resident_bytes`、`resident_objects`、`admits`、`evictions`、`evicted_bytes`、`bypasses_budget_zero`、`bypasses_too_large`、`skipped_already_resident`、`conflicts`、`backend_errors`、`serves`、`touches` | `periodic` | 每周期 1 次 |

命中事件（`provider_hit` / `local_cache_hit`）**新增两个字段**，不新增事件名：

- `tier` ∈ `owner` / `replica` / `miss` / `unknown`。`unknown` 只在 `legacy` 策略下出现——**不能归因时写 unknown，不猜**。
- `lru_touched` ∈ `0` / `1`。用来验证"重复网络 DATA 不得人为保活"确实生效。

### 4.1 请求身份

每个事件都带 `request_id`、`origin_query_uid`、`app_request_id`、`phase`（`preferred` / `fallback`）、`source_node`。**无法唯一关联时写空字符串**，不编造单一 request id。

### 4.2 分母与互斥（统计边界）

- 每次准入尝试**恰好**产生 `replica_admit` / `replica_bypass` / `data_admit_skipped` / `cache_key_conflict` / `cache_backend_error` **之一**（有专项测试）。
- **0 号预算下 `replica_evict` 计数必须为 0**（有专项测试）。
- 计数闭合：`admits − evictions == replica_objects`，`evicted_bytes == evictions × value_bytes`（单一大小时），事件条数 == 对应计数器（有专项测试）。
- **`replica_evict` 的逐事件驻留量（契约修订 2，已冻结）**：`resident_bytes` / `resident_objects` 是该 victim **成功删除之后、新对象尚未插入之前**的副本驻留状态快照，只统计 ReplicaCache，不含 owner。快照在 `ReplicaCache::evictTail()` 里删除成功且 `resident_bytes_` / `index_` 更新之后当场采集（`ReplicaVictim::resident_bytes_after` / `resident_objects_after`），**不再**从最终状态加减反推，因此与「新对象是否最终写入成功」「victim 个数」「各 victim 大小是否相同」都无关。`value_bytes` 是本 victim 的大小，不是新对象大小。多 victim 时事件顺序 = 实际成功删除顺序。
- **多 victim 数值例子**（预算 100，A=20、B=30、C=40 驻留共 90，无 touch 故删除顺序 A→B→C，插入 D=80）：`evict A` → 70 bytes / 2 对象；`evict B` → 40 bytes / 1 对象；`evict C` → 0 bytes / 0 对象；`admit D` → 80 bytes / 1 对象。若 D 的写入失败，前三条驱逐事件**数值完全相同**，且不产生 `admit`，最终副本占用为 0 bytes / 0 对象。
- `provider_hit`（服务次数）、`app_receive`（交付次数）、`data_handle_start`（DATA 到达次数）是**三种不同事件**，分母不同，不得相加。
- 三种服务来源用 `tier` + 当前节点身份 + 请求方身份共同区分：**请求源本地副本服务 / 中间节点副本服务 / owner 服务**。
- 容量实验关闭详细 trace 时：`cache_gauge` 仍提供计数与驻留量，事件缺失**不得被解释为 0**；本轮不修改任何旧分析器去重分类旧 raw。

### 4.3 Redis 内存与自动驱逐

- `cache_gauge` **不轮询 Redis**（成本约束）：只发进程内计数，无后端往返。
- `redis_used_memory` / `redis_evicted_keys` 由**启动脚本低频采集**（out-of-band），频率与成本在 smoke 计划里冻结；`cache_config` 记录 `redis_maxmemory` / `redis_maxmemory_policy` / `redis_require_empty`，但那是环境变量值，**不等于实测 CONFIG GET**。
- 启动空库已由既有 `kathara/ruikekkk/shared/start-node.sh` 的 `RSYNC_REDIS_REQUIRE_EMPTY=1` + `redis-cli DBSIZE` 强制；LRU 状态是进程内的，新 run 天然为空。
- `noeviction` 下若观测到 `evicted_keys` 增量 > 0，必须当作**环境/资源事件**处理，不得算作应用 LRU 的一次正常驱逐。

### 4.4 事件契约版本、时间戳与旧 trace 兼容边界

- **生产者修订标识**：`cache_contract_revision`（当前修订 **4**）。修订 4 起 `cache_backend_error` 带 `category`（`pre_side_effect` / `indeterminate` / `divergence`）：修订 3 及以前，任何 `cache_backend_error` 都隐含"操作未发生"；修订 4 引入"结果不确定"类，此类操作**可能已经执行**且 store 随即不可用，因此两类不能按同一语义读取。修订 3 起冲突事件带 `tier` 与 `size_mismatch`/`content_mismatch`，且 DATA 路径强制完整性校验。修订 2 起，`replica_evict` 的 `resident_bytes`/`resident_objects` 是删除后快照，并新增 `evicted_mono_ns`。该字段出现在每个 `replica_evict`/`cache_key_conflict`/`cache_backend_error` 上，并在 `cache_config`（每 run 一次）中声明，因此单条事件即可自描述其语义。
- **旧 trace 不可按新语义解释**：本修订之前（修订 1，隐式）的生产者**不发** `cache_contract_revision`，其 `replica_evict` 的逐事件 `resident_bytes`/`resident_objects` 是用「最终状态 + 加回 victim」推导的，**不是**删除后的运行状态，**不可信**。分析器必须按「事件上是否存在 `cache_contract_revision`（及其值）」分支处理，或直接丢弃旧事件的逐事件驻留量；不要把旧事件按新语义重算。修订 2 的生产者其 `cache_key_conflict` 没有 `tier`、reason 为 `replica_size_mismatch`/`replica_content_mismatch`，同样不可按修订 3 的语义读取。**本仓库不重写任何历史 trace/raw。**
- **`schema_version` 与 `cache_contract_revision` 是两件事**：前者是 trace 记录**版式**版本（字段布局，默认 `2`），后者是 cache 事件的**语义**修订号。本次没有改版式，因此 `schema_version` 未变。
- **时间戳语义**：记录上的 `ts_mono_ns` 是**记录写出时间**；一次准入的多个驱逐事件都在 `admit()` 返回之后写出，所以 `ts_mono_ns` **不是**底层 DEL 完成时刻，也不代表各 victim 之间的真实时间间隔。真正的操作收尾时间保存在 `evicted_mono_ns`（在 `evictTail()` 删除成功、状态更新之后采集，仍不等于网络层 DEL 的精确完成时刻），`idle_age_ns = evicted_mono_ns − last_touch_mono_ns`。**驱逐顺序以 victim 顺序为准**，不依赖时间戳。
- `idle_age_ns` 是「距上次被使用的时间间隔」，**不是** reuse distance；本版本不提供 reuse distance。
- `replica_bypass` / `data_admit_skipped` 上的 `resident_bytes` / `resident_objects` 表示**该次操作未改变**的当前驻留状态（它们不产生状态变化）；只有 `replica_evict` 使用删除后快照，`replica_admit` 使用写入并登记后的状态。

### 4.5 不可变对象完整性（冲突 DATA 的拒绝位置与边界）

可验证范围（先声明，避免过度承诺）：

| 情形 | 本版本能否准确判定 | 依据 |
|---|---|---|
| A 本节点持有 owner 原始对象 | **能** | 与 owner 字节做精确比较（先比 in-memory 记录的大小，再读后端比内容） |
| B 本节点驻留该 key 的副本 | **能** | 与该副本字节做精确比较，读取**不刷新 LRU**（`ReplicaCache::peek`） |
| C 既无 owner 也无副本 | **不能** | 这是本节点首次见到该对象；仅凭 logical key 与收到的 value 无法判断其正确性。按"首份拷贝"接受，不作为已验证数据 |
| D 该副本已被驱逐 | **不能** | 本版本**不保留** key→指纹历史（那需要无界表、生命周期策略并会影响有限缓存实验），因此与已驱逐旧内容的历史冲突无法检测 |

比较是**精确字节相等**（不是短 hash），因此不存在碰撞概率问题；应用层的 expected size/hash 校验保持不变，仍是交付的最终判据。

冲突判定：大小不同 → `size_mismatch`；大小相同但字节不同 → `content_mismatch`；字节相同 → 不是冲突（正常重复）。`lru` / `bypass` / `unlimited` **强制**在其适用范围内做精确内容校验，没有关闭入口；`legacy` 保持其正常行为与后端异常传播方式（本轮已修，见 §3.7），但**不宣称**逐字节兼容。

**Node 中的调用顺序**（关键，避免先消费协议状态）：

- fallback / stable-UID 路径：① `QueryTable::probeFallbackData()`（**非消费**探针）判定身份与相关性 → ② `Node::verifyIncomingData()` 做完整性校验 → ③ 才调用 `routeFallbackData()` 消费反向路径义务/标记满足 → ④ 准入、交付或转发。身份不合法（未知 UID、key 不在查询集合、签名不匹配）的包在①就被丢弃，**不会**触发全局冲突失败。
- preferred 路径：① `verifyIncomingData()` → ② 才 `markDataSatisfiedByKey()` / `getQueryTable()` 消费状态 → ③ 准入与交付/转发。preferred 路径本身没有身份拒绝规则，因此对每个本地可验证的 DATA 都做校验；无可信本地参照的包照旧通过。
- 两条路径上，完整性判定所必需的读取与比较都在②**完整结束**；只有②通过后才会消费任何 QueryTable 状态。

**同一次 DATA 处理不重复读取**：`checkImmutability()` 在副本命中且大小相等时用 `ReplicaCache::peek()` 读取并精确比较一次（不刷新 LRU）。为避免 `ReplicaCache::admit()` 在协议状态已被消费后再读同一对象一遍（该第二读若失败，旧实现会把它误当作"准入失败"而继续交付/转发），门面把这次校验结果作为**一次性凭证**传给紧随其后的 `admitReplica()`：凭证绑定**确切的 logical key 与确切字节**，由 `checkImmutability()` 私有地产生、在 `admitReplica()` 入口即被消费并清除，且不是任何调用方可传入的 `verified=true`。`ReplicaCache::admit()` 自身对直接调用方仍保留完整内容校验；凭证只消除 Node 快路径上的这次重复读取，不改变大小冲突、预算、驱逐、put 失败、计数器与 recency 的语义。

**修复后的后端读取次数**（仅陈述来源，未测量时延/成本）：

| DATA 情形 | 完整性读取次数 |
|---|---:|
| 本节点有 owner，大小相同 | 1 次 owner `get` |
| 本节点有 owner，大小不同 | 0（in-memory 大小即可判定） |
| 本节点驻留副本，大小相同（重复 DATA） | 恰好 1 次 `peek`；修复后**无第二次读取** |
| 本节点驻留副本，大小不同 | 0 |
| 副本索引在但 KV 无值 / 读取失败 | 1 次尝试读取，结果判为不可验证 |
| 无 owner 也无副本（边界 C 首份拷贝） | 0 次完整性读取（随后准入的 `put` 是写，不是校验读） |

凭证长期不保留：它只活到同一次 DATA 处理的下一次准入，不产生 key→指纹历史表，也不影响边界 C/D。

冲突 DATA 不会：覆盖 owner 或已有副本、触发准入或驱逐、被转发到下游、触发成功应用回调、产生成功 `app_receive`、把对应查询标记成功。**完整性读取失败**（`cache_backend_error`，reason `replica_get_failed` / `owner_get_failed`）与"完成检查后的准入失败"是两件不同的事：前者不匹配、该包被丢弃且**不消费协议状态、不缓存、不转发、不交付**；后者（如 put/del 失败、预算旁路）保持既有协议语义不变，例如旁路仍正常转发与交付。

**错误传播链**：`NodeCache::checkImmutability()` 先发**恰好一条** `cache_key_conflict`，再抛 `rsync_cache::ImmutabilityViolation`（fail-closed，调用方无法忽略）→ `Node::verifyIncomingData()` 不吞异常 → `Node::processOne()` 抛出 → `app/rsync_node_main.cpp` 的主循环捕获，写入 `control/fatal/node<id>.immutability-violation`（含 key/reason/tier/大小，**不含 value 字节**）、flush trace、`return EXIT_FAILURE` → 现有 runner（`controller.py` / `capacity_reports.py` / `federation.py`）对 `control/fatal/` 下**任意**文件即判 run 失败（`CorrectnessError: runtime fatal diagnostic`）。该入口链为**静态核对**，本次未做真实进程故障注入。

配置：新模式**无条件**精确比较，没有可关闭的入口——显式设置 `RSYNC_CACHE_VERIFY_REPLICA_CONTENT=0` 会让启动失败；该变量的历史含义仅在 `legacy` 下保留。`cache_config` 新增 `integrity_verification=exact_bytes`。

### 4.6 后端结果不确定：保守中止、错误分类与记账

真实 Redis 的失败不都能证明"操作未发生"：客户端可能已把 `DEL`/`SET` 发出、Redis 已执行，而响应丢失或连接断开。旧实现把 `del` 异常当作"victim 仍在驻留"、把 `put` 异常当作"新对象一定不存在"，这只对"副作用前失败"成立。本轮采用保守边界：**无法确认后端状态时终止当前 run，不尝试在线恢复后继续产生正式数据。**

分类（`cache_backend_error.category`，契约修订 4）：

| 类别 | 何时产生 | 存储状态 | 处理 |
|---|---|---|---|
| `pre_side_effect` | 确定性测试后端证明副作用未发生；参数/校验失败 | 已知（未改变） | 记录普通 backend error（`fatal=0`），有限模式按既有语义继续 |
| `indeterminate` | 连接断开/响应丢失等无法确认结果 | **未知** | 记录 `fatal=1` 事件；store 置不可用；run 立即失败 |
| `divergence` | 索引与 KV 不一致（非操作失败） | 已知 | 按既有修复路径记录/处理（`replica_kv_missing` 等） |

**不可用状态**：`ReplicaCache`/`OwnerStore`/`NodeCache` 均有 `available()`；出现不确定结果即置 false，之后 `admit`/`use`/`peek`/`seed`/`lookupForService`/`exists`/`checkImmutability` 一律抛 `CacheUnavailable`——不是靠调用方记得检查布尔字段。诊断读取（`gauge`、`serviceableKeys`、`tierOf`）仍可读，供致命记录使用。**所有** `BackendIndeterminate` 路径都必须毒化整个门面：不只是带已确认 victims 的 `IndeterminateAdmission`，还包括**无凭证时重复对象内容读取**抛出的普通 `BackendIndeterminate`，以及 legacy 的 `lookupForService`/`exists` 与 owner 重复 seed 的内容读取；每个失败只发一条 `cache_backend_error`，且普通捕获**不**触碰 `IndeterminateAdmission` 的 victim 收尾逻辑。

**完整调用链（结果不确定 → 终止 run）**：

1. `RedisCache::get/put/del/exists` 捕获 `sw::redis::Error` → 抛 `rsync_cache::BackendIndeterminate`（类型即分类；不再统一包成 `runtime_error`）。
2. `ReplicaCache`：`put` 不确定 → 置不可用、不更新索引、抛 `IndeterminateAdmission`（携带此前**已确认**的 `victims`）；`del` 不确定 → 置不可用，该 victim 既不计成功驱逐、也不留在索引/目录（不再宣称驻留），已确认的前序 victim 仍在 `result.victims`；`get`/`peek` 不确定 → 置不可用并原样抛出，绝不转成 miss 或"读到空"。
3. `OwnerStore::seed/get` 不确定 → 置不可用并原样抛出，不新增/不读取索引。
4. `NodeCache`：`markUnavailable()`；对已确认 victim 执行 `finalizeEvictions`（目录 + 每 victim 一条 `replica_evict`）；对不确定操作发一条 `category=indeterminate, fatal=1` 的 `cache_backend_error`，并且**不**把它计入成功 admit/evict；随后 `throw;` 保留原始类型与信息。
5. `Node::processOne()` 不吞异常。6. `app/rsync_node_main.cpp` 主循环捕获 `BackendIndeterminate`/`CacheUnavailable` → 写 `control/fatal/node<id>.backend-indeterminate`（或 `.backend-unavailable`）+ flush trace + `EXIT_FAILURE`；循环内三个 `requestFile*` 调用点同样处理。validator 对 `control/fatal/` 任意文件或任何 `cache_backend_error` 记录判 run 失败。

**记账原则**：已确认的操作如实记账（A 的 `replica_evict` 保留）；不确定的操作既不算成功、也不宣称其相反状态（不声称 victim 仍驻留、不声称新对象不存在）。因此一次不确定操作之后 `admits − evictions == replica_objects` 可能**故意不闭合**——这是"结果未知且 run 作废"的表示，不是可继续使用的状态。**不重试**（重试无法证明第一次是否已执行）、**不伪造回滚**、不确定后不再缓存/提供/转发/交付。

**读取异常的 run 级处理**：读取不修改存储；`pre_side_effect` 读取失败仍按普通 backend error 记录（有限模式返回 miss），但 validator 拒绝任何带 `cache_backend_error` 的 run——后端故障造成的额外 fallback/时延不得被当作正常缓存性能。`indeterminate` 读取则直接置不可用并终止。

### 4.7 Redis 启动核验与运行期监控接口

为避免"只在文档里描述"，本轮实现了**无 Redis 依赖、可独立单元测试**的接口（`src/cache/redis_acceptance.{hpp,cpp}`，测试目标 `test_redis_acceptance`）：

- `parseRedisInfo()`：解析 INFO 文本（纯函数），**严格校验**：数值先解析到临时变量、只有被完整消费的合法整数才赋值；非法后缀（如 `maxmemory:0garbage`）、溢出、不允许的负值、**重复的关键字段**都会写入 `parse_error` 而不是被当成零；缺失（保持哨兵 `-1`）与合法零是两回事。
- `verifyRedisStartup(info, expected_socket_path, expected_identity)`：要求 `INFO` 可读且 `parse_error` 为空、连接的是**显式指定**的绝对 socket 路径、实例身份与版本/`run_id` 已记录、**合法的监控基线**（`evicted_keys >= 0`、`used_memory >= 0`）、`maxmemory == 0`、`maxmemory_policy == noeviction`、实验 DB `DBSIZE == 0`。**DB 非空即拒绝，绝不 FLUSHDB。** 另加 `namespacesDisjoint()`：对一组对抗性 logical key（空、`owner:`/`replica:` 前缀、嵌套、互为前缀）重跑编码单射/不相交自检，编码回归时 fail-closed（`namespaces_not_disjoint`）。
- `RedisMonitor`：构造参数（基线 `evicted_keys >= 0`、非空 `run_id`/身份、`interval_ms > 0`）必须合法，否则 `configValid()==false` 且 `observe()` 一律失败；低频 `observe()` 检测 `evicted_keys` 相对启动基线增加（自动驱逐）、计数回退或 `run_id` 变化（重启/计数器重置）、INFO 不可读、以及 `parse_error`/`used_memory` 或 `evicted_keys` 缺失；这些一律判失败，**不会**被静默当成"没有自动驱逐"。`used_memory` 只作为 **Redis 总内存**暴露，绝不冒充应用副本 value 字节数。`costSource()` 明示开销来源：每 `interval_ms` **一次 INFO、独立监控连接**，不给每次 DATA 增加查询；采样频率即开销来源，不宣称"开销可忽略"。

真实取值由 `RedisCache::readInstanceInfo(identity)` 提供（读 INFO + DBSIZE），它只在 `ENABLE_REDIS=ON` 时编译；启动脚本应使用**独立的** `RedisCache` 实例做监控。身份、socket 路径与采样间隔由启动脚本传入（建议 `RSYNC_REDIS_INSTANCE_ID`、`REDIS_SOCKET_PATH`、`RSYNC_CACHE_GAUGE_INTERVAL_MS`）。owner/replica 命名空间不相交由 `key_namespace` 的结构性编码保证并有既有测试。**本轮没有在真实 Redis 上编译或运行这条接口**（本机无 hiredis/redis++ 与 redis-server），见 §7。

### 4.8 run 验收 gate（本轮新增组件 validator）

`experiments/concurrency_finite_cache_v1/validate_run.py` 是对本实验的**新增**组件 validator（不改动冻结的 capacity/slides validator），并且**把"后端错误门槛"与"run 级 READY"分开**：

- `--backend-gate <run_dir>` 只做后端错误门槛，发布**局部**状态 `BACKEND_GATE_PASS` / `BACKEND_GATE_FAIL`，**不发布 run 级 READY**。它拒绝：`control/fatal/*` 任意文件、任意 `cache_backend_error`、损坏/截断/非对象 JSON 记录；**没有任何 trace 文件也判 FAIL**（不能从"没有证据"推出"没有错误"）。
- `--run <run_dir> --manifest <manifest.json>` 才是完整 validator。READY 要求**全部**满足：后端门槛通过；provenance（manifest 列出的每个文件存在且 sha256 匹配，且**没有未登记的 `*.jsonl`**）；trace 完整性（期望节点都出现、必需事件都在）；监控证据（manifest 要求时每节点有 `cache_gauge`）；请求完成与内容正确性（`experiment_request_start` + 匹配 key/`value_bytes` 的 `app_receive`）。**没有可信 manifest 就拒绝**（`manifest_required`），绝不靠"扫描当前剩下的文件"推断期望集合。
- 工具只读原始证据、从不改写或删除；**删除或修改已登记证据不可能让失败变成通过**（文件缺失或哈希不匹配都判失败）。预置的 `READY` 标记不参与判定。

`--self-test` 用派生 fixture 覆盖 18 个用例（干净 → READY；空目录 → 非 READY 且 gate FAIL；缺 manifest；fatal；100% 完成 + backend error；已登记文件被修改/删除；未登记 trace；缺节点/事件/监控；请求未完成/内容不符；损坏与非对象 JSON；不改写证据）。**这是组件/派生 fixture 验收，不是真实进程链验收**（见 §7）。

### 4.9 真实 Redis 验收入口（可执行，非纯函数）

- `run_redis_acceptance.sh [build] [acceptance]`：在私有 `mktemp -d` 里启动**专属** `redis-server`（`port 0` 仅 Unix socket、`maxmemory 0`、`noeviction`、无持久化），先跑既有 `test_redis_cache_contract`，再跑 `test_redis_acceptance_live run`，然后**只重启自己启动的 PID** 让监控验证 `instance_restarted`，最后只清理该 PID 与临时目录；不连系统 Redis、不 FLUSH 别的实例、不 `pkill`。证据写入 `<acceptance>/redis_acceptance.jsonl` 与 `redis_acceptance_summary.txt`。
- `test_redis_acceptance_live`（仅 `ENABLE_REDIS=ON` 编译）：真实 `RedisCache`+`NodeCache` 覆盖启动核验、owner/replica 二进制读写、owner seed/seal/重复 seed 冲突、预算 0、有限 LRU 多 victim 驱逐与计数闭合、受害快照、重复 DATA 不刷新 LRU、unlimited、非空 DB 拒绝、`used_memory != 应用字节`、监控采样与重启检测；`fault-chain` 模式在专属 Redis 被 kill 后确认 `BackendIndeterminate`→store 毒化→再次调用被 `CacheUnavailable` 拒绝，并写出 `control/fatal/node1.backend-indeterminate`。
- `run_process_fault_chain.sh [build] [acceptance]`：真实进程链——(A) 不可达 Redis 时 `rsync-node-dev` 初始化非零退出；(B) 专属 Redis 被杀后真实缓存进程抛不确定错误、写 fatal 标记并以非零码退出；(C) validator 对带 fatal 标记的 run 判 `BACKEND_GATE_FAIL`，**即使存在 100% 完成记录**。证据写入 `<acceptance>/process_fault_chain.txt` 与 `process_fault_chain.jsonl`。
- `Dockerfile.redis-acceptance`：以 `-DENABLE_REDIS=ON -DFETCH_REDIS_DEPS=ON` 构建，并**硬性禁止 CMake 静默降级**（grep `Redis support enabled` + 检查 `redis_cache.cpp.o` 与 Redis-only 二进制存在）。
- 后端断开必须表现为可分类的 `BackendIndeterminate` 而非未分类的 SIGPIPE 死亡，因此 `rsync-node-dev` 与 live 驱动在 `main` 起始处 `SIGPIPE` 置为忽略；故障随后进入 run-loop 的 fatal 标记路径。
- `run_component_acceptance.sh <build> <acceptance_root> [sha] [image_id]`：隔离运行入口——启动核验/空 DB、记录身份/run ID/监控基线、目标进程与**独立连接**周期监控并发、连续采样、最终样本与监控退出码、证据清单，最后调用正式 validator（`--run --manifest`）判定 READY。每次用唯一版本化目录，已存在即拒绝写入。
- `redis_fault_proxy.py <listen> <upstream> <control> <log>`：受控 RESP 代理，可“先转发后丢弃响应”或“直接关闭”，并记录每条命令的 `forwarded`/`reply_dropped`；因为先转发，独立连接可核验服务端已执行——即真实“已执行但响应丢失”窗口。
- `run_node_fault_chain.sh <build> <acceptance> [sha] [image_id]`：驱动**实际** `rsync-node-dev`——(A) 运行期 owner GET 响应丢失；(B) 初始化 seeding SET 响应丢失（连接成功、不得进入服务）；(C) 独立 GET 证明被丢弃响应的 SET 已执行。fatal 标记由应用自身产生。
- `run_process_fault_chain.sh [build] [acceptance] [positive|reverse]`：严格区分“harness 失败”与“正确观察到目标失败”（精确 exit 3、五个必要断言、预期 fatal 原因、readiness 超时即失败、validator 拒绝须含预期 marker 原因）；`reverse` 用 `RSYNC_FAULT_CHAIN_FORCE_FAIL` 强制断言失败并要求整链失败。
- **validator 输入契约（正式）**：manifest 必含 `schema/version`、`run{...}`、非空 `expected_nodes`/`required_events`/`files`/`requests`、`monitor{...}`；每个登记文件必须合法 SHA-256 且核验通过；每个计划请求必须完成一次且实际内容 hash 匹配；`cache_gauge` 不能替代 `redis_monitor_*` 证据。`--self-test` 覆盖 40 个用例。

## 5. 配置示例

```sh
# legacy（默认，旧行为；不得与预算变量同时设置）
# 不设置 RSYNC_CACHE_POLICY

# 副本旁路：仍转发与交付，绝不缓存
RSYNC_CACHE_POLICY=bypass

# 有限按字节 LRU（对象数只是换算糖；底层按真实 value 长度计费）
RSYNC_CACHE_POLICY=lru
RSYNC_REPLICA_BUDGET_BYTES=1048576        # 1 MiB = 16 个 64 KiB 对象
# 或者：RSYNC_REPLICA_BUDGET_OBJECTS=16
#       RSYNC_REPLICA_BUDGET_OBJECT_BYTES=65536   # 可选，默认 65536

# 足量对照：与 lru 同一套接口与记账，只是不设预算
RSYNC_CACHE_POLICY=unlimited

# lru / bypass / unlimited 下完整性校验强制开启，无法关闭；
# 显式设置 RSYNC_CACHE_VERIFY_REPLICA_CONTENT=0 会让启动失败。
# 该变量的历史含义仅在 legacy 下保留（legacy 兼容性未验收）。
RSYNC_CACHE_GAUGE_INTERVAL_MS=100         # 0 = 关闭周期 cache_gauge
RSYNC_EVENT_TRACE_SCHEMA_VERSION=2
```

第一轮候选容量（与 prompt 一致，按字节）：

| 档 | bytes | 64 KiB 对象数 |
|---|---:|---:|
| 0（旁路） | 0 | 0 |
| 4 对象 | 262144 | 4 |
| 8 对象 | 524288 | 8 |
| 16 对象 | 1048576 | 16 |
| 32 对象 | 2097152 | 32 |
| 64 对象 | 4194304 | 64 |
| 256 对象 | 16777216 | 256 |
| unlimited | — | 无预算 |

32 MiB（33554432）仅作足量回归对照，不作为第一轮独立性能处理组。

## 6. 确定性测试

全部在进程内跑，**不需要 Redis、不需要 Kathara lab、不需要镜像**。

```sh
cmake -S . -B build-finite-cache -DCMAKE_BUILD_TYPE=Debug \
      -DENABLE_REDIS=OFF -DFETCH_REDIS_DEPS=OFF
cmake --build build-finite-cache -j"$(nproc)"
cd build-finite-cache
for t in test_cache_namespace test_replica_cache test_node_cache_events \
         test_cache_trace_schema test_node_finite_cache test_redis_acceptance; do ./$t; done
```

| 目标 | 用例数 | 覆盖 |
|---|---|---|
| `test_cache_namespace` | 9 | 编码单射性/命名空间不相交/往返；legacy 裸 key；owner 只读与 seal；owner 冲突拒绝；owner 永不被驱逐；驱逐后目录项消失 |
| `test_replica_cache` | 15 | 预算 0 / 不足一对象 / 恰好一对象；按字节驱逐；手算 LRU 序列；诊断不 touch；重复准入不双计不刷新；冲突拒绝；unlimited 与足量预算一致；put/del 失败不记账；victim 时间戳与序号；**victim 逐次删除快照（含不同大小、put 失败、后续删除失败三种情形）**；分叉修复 |
| `test_node_cache_events` | 50 | 见 §4 的字段/互斥/闭合/reason/`tier`/`lru_touched`/owner 不 touch/分叉上报/owner 丢失致命/legacy 零事件；`fromEnv` 全部校验分支；部分成功驱逐的 5 个用例；**逐事件驻留量**的 7 个用例；**legacy 后端异常传播 5 个用例**；**结果不确定 9 个用例**（写入不确定、多 victim 中 A 确认 + B 不确定、读取不确定不再变 miss、owner 写入不确定、`pre_side_effect` 与 `indeterminate` 分类区分、**无凭证重复内容读取不确定必须毒化整个门面**、**legacy `lookupForService`/`exists` 不确定毒化门面**、**owner 重复 seed 读取不确定毒化一次**） |
| `test_cache_trace_schema` | 2 | 真实写出到文件：`schema_version`；旧 16 字段保留；新字段不被丢弃；调用方覆盖旧字段生效；不出现 `reuse_distance` |
| `test_redis_acceptance` | 12 | INFO 解析；启动核验接受正确隔离实例并逐条拒绝（INFO 不可读、路径非显式/不匹配、身份未记录/不匹配、版本/run_id 缺失、`maxmemory≠0`、policy 非 noeviction、DB 非空）；owner/replica 编码不相交与单射自检；监控检测自动驱逐、重启/计数回退、INFO 不可读与 evicted_keys 缺失；**非法数值后缀/重复关键字段/负数/溢出必须拒绝**，缺失与合法零区分，监控构造参数与基线合法性校验；开销来源声明。**不需要 Redis server** |
| `test_node_finite_cache` | 27 | Node 三条写路径与全部读路径；owner tier 且不 touch；DATA 准入与重复跳过；owner 不建冗余副本；驱逐移目录且 owner 存活；端到端取回；预算 0 旁路仍交付；驱逐与在途 DATA 并存；无人持有对象不产生成功 DATA；**`fromEnv()` 等价的生产配置**；**完整性读取失败 / 单次完整性读取 / 两请求 pending**；**legacy 调用链异常传播 8 例 + 有限模式回归 1 例**；**DATA 写入结果不确定时 Node 传播并毒化 store、不再交付/转发** |

结果（本机 `Debug`，无 Redis；`build-redis-boundary` 全新构建目录）：

```
test_core                    exit=0  所有测试通过
test_packet_codec            exit=0  passed
test_query_table_fallback    exit=0  passed
test_node_staged_fallback    exit=0  passed      <- 既有回归
test_runtime_neighbor        exit=0  passed
test_mesh_demo               exit=0  finished
test_cache_namespace         exit=0  9/9 passed
test_replica_cache           exit=0  15/15 passed
test_node_cache_events       exit=0  50/50 passed
test_cache_trace_schema      exit=0  2/2 passed
test_redis_acceptance        exit=0  12/12 passed
test_node_finite_cache       exit=0  27/27 passed
```

12 个目标全部通过；6 个既有回归目标无回归。`validate_run.py --self-test` 18/18 PASS。`rsync-node-dev` 在该目录构建成功（`ENABLE_REDIS=OFF`，因此这只覆盖 InMemoryCache 路径，**不代表 Redis 路径已通过**；`ENABLE_REDIS=ON` 能配置完成，但因依赖不可用而报告 Redis disabled，见 §7）。实现过程中被测试捕获并修复的真实缺陷（记录在案，不再复现）：

1. `local_files_` 被删除后，驱逐的 victim 未从可服务目录移除（目录与存储不一致）。
2. LEGACY 策略下 `namespaced` 仍为 true，导致 owner 写入 `owner:` 前缀而读取裸 key —— 被既有 `test_node_staged_fallback` 直接抓出。
3. `budget_bytes == 0` 连带禁用了 `unlimited`，违反"unlimited 与 0 不混用"。
4. 重复 DATA 命中时不调用 store，导致 `data_admit_skipped` 永远采集不到。
5. 部分成功驱逐未被收尾：驱逐成功但随后准入失败时，已删除的 victim 仍留在可服务目录里，且没有 `replica_evict`（只有 `Admitted` 分支做了收尾）。已修复，见 §3.3 与 §4.2。
6. `replica_evict` 的逐事件 `resident_bytes`/`resident_objects` 由最终状态加减反推，不是删除后的运行状态（等大小示例也会错）。已改为在 `evictTail()` 删除成功处直接采集每 victim 快照，并新增 `evicted_mono_ns` 与 `cache_contract_revision`。已修复，见 §4.2 与 §4.4。
7. 冲突 DATA 只被记录为 `cache_key_conflict` 后仍可能被转发/交付：`bypass`/`unlimited`/`lru` 下 owner 同 key 不比较内容，同大小不同内容会被当作 `AlreadyResident`，且检查发生在 QueryTable 状态被消费之后。已修复：新增 `checkImmutability()`（字节级比较、fail-closed 抛异常）与 `probeFallbackData()`（非消费探针），Node 两条 DATA 路径在消费状态前拒绝冲突包，见 §4.5。
8. 生产配置（`verify_replica_content` 打开）下完整性校验存在**第二次读取**缺口：`checkImmutability()` 已读过驻留副本，`ReplicaCache::admit()` 会在 QueryTable 状态被消费之后**再读同一对象**做内容比较；该第二读失败时旧实现仍可能继续交付/转发，破坏"完整性读取失败不消费状态、不转发、不交付"的保证。已修复：`checkImmutability()` 产生绑定确切 key 与字节的**一次性私有凭证**，`admitReplica()` 消费该凭证并经私有的 `ReplicaCache::admitWithVerifiedResident()` 跳过重复读取；`ReplicaCache::admit()` 对直接调用方仍保留完整内容校验，见 §4.5。回归测试用后端调用计数证明合法重复 DATA 只产生 1 次完整性读取（旧实现为 2 次），并同时注入第二读故障证明它不会触发。
9. legacy 错误传播不兼容：门面在 legacy 下把后端 `get` 异常吞成 miss（`requestFile`/`handleRequest` 随后继续远程获取或跳过 provider），把 DATA 写入/探测异常吞成 `BackendError` 后继续处理，并让 DATA 路径多出一次 `exists`。原 Node（`0feb4b22`）对 `get`/`put`/`exists` 一律向外传播且只探测一次。已修复：legacy 的 `lookupForService`/`admitReplica` 不再捕获，异常原样传播；Node 的 DATA 路径在 legacy 下复用门面内那一次 `exists`，不再额外探测；`exists` 为真但读空时不再回退远程获取。见 §3.7，反向验证在修复前源码上 3 个门面用例 + 6 个 Node 用例失败。
10. 后端异常被当作"操作未发生"：`del` 抛错就当作 victim 仍驻留、`put` 抛错就当作新对象不存在、`get` 抛错就当作 miss。真实 Redis 可能在命令已执行后才丢失响应。已修复：新增 `BackendIndeterminate`/`CacheUnavailable` 类型与 `category` 分类，`ReplicaCache`/`OwnerStore`/`NodeCache` 在不确定结果后置不可用并拒绝后续调用；多 victim 驱逐中已确认的 A 仍记账、不确定的 B 既不计成功也不宣称驻留；`Node` 与主入口把不确定升级为 fatal 并终止 run。见 §4.6，契约修订 4。
11. 本轮验收发现的三个漏洞。① `NodeCache::admitReplica()` 只捕获 `IndeterminateAdmission`：无完整性凭证时重复对象内容读取抛出的普通 `BackendIndeterminate` 不毒化门面、不发事件，之后仍能读取 owner（`owner_served_after_indeterminate_admit=1, backend_error_events=0`）。已修复：补普通 `BackendIndeterminate` 捕获，并审计 legacy `lookupForService`/`exists` 与 owner 重复 seed 读取，全部毒化门面且每失败只记一条事件，普通捕获不触碰 victim 收尾。② `validate_run.py` 空目录返回 READY、跳过损坏 JSON。已改为 `--backend-gate` 只发布 `BACKEND_GATE_PASS/FAIL`，完整 READY 需 `--run --manifest`（provenance 哈希、无未登记 trace、节点/事件/监控/请求完成与内容正确性）；删除或修改已登记证据必判失败。③ `stoll` 先写输出后判完整性、调用方忽略返回值，`maxmemory:0garbage` 被当零。已改为先解析到临时变量、仅完整合法才赋值，并拒绝缺失/溢出/负值/非法后缀/重复字段，启动要求合法监控基线、monitor 校验构造参数与样本字段。反向验证：旧 `node_cache.cpp` 下 4 个新用例失败，旧 `redis_acceptance.cpp` 下 3 个失败，旧 validator 对空目录返回 READY。

## 7. 尚未验证 / 明确边界

- **没有任何真实网络运行。** 全部证据来自进程内 `InMemoryCache`；没有多节点拓扑、没有调度、没有并发与时延测量。
- **本次未做真实进程故障注入。** `control/fatal/*` → runner 判失败这条链仍是静态核对与新增组件 validator 的派生 fixture 验收，没有真实进程链证据；进程内的"冲突后仍可用/异常后仍可用"只作为**组件不变量**，不代表生产进程在致命/不确定错误后可以继续运行。
- **确定性故障模型有两类，但都不是真实 Redis。** `FaultyCache` 的 `fail*` 在副作用**之前**抛 `BackendFailure`（`pre_side_effect`）；`applyThenThrow*` 施加副作用后抛 `BackendIndeterminate`，用于复现旧假设并验证保守中止，但它仍是**进程内注入**，不能证明真实 Redis 上的响应丢失窗口。
- **Redis 后端路径未在本地编译或运行**：本机没有 `hiredis`/`redis++` 头文件与库，也没有 `redis-server` 可执行文件，因此 `ENABLE_REDIS=ON` 配置虽能完成，但报告 `Redis dependencies not available` 并关闭 Redis 支持，`src/cache/redis_cache.cpp` 与 `RedisCache::readInstanceInfo()` 的新异常分类/INFO 读取**未经编译或运行验证**；`test_redis_cache_contract` 未构建。真实 Redis 启动核验、`evicted_keys` 增长/重启检测、owner/replica 命名空间在真实后端上的读写、二进制 value、字节预算与 LRU victim 选择**全部未验证**。`test_redis_acceptance` 只覆盖纯规则与捕获的 INFO 文本，**不能代替真实 Redis 证据**。
- **没有用代理/连接中断验证真实响应丢失**：未证明故障发生在"命令已到达服务端、响应丢失"这一窗口，因此该窗口如实标为未验证，未用模拟测试冒充真实证据。
- **启动核验/监控尚未接入真实启动脚本**：接口与单元测试已实现，但把 `verifyRedisStartup`/`RedisMonitor` 接到实验启动与验收流程需要可用的 Redis 实例，本轮未做。
- **legacy 兼容边界**：本轮之前修好的是**正常行为与后端 `get`/`put`/`exists` 异常传播**（§3.7）。不在承诺内的有：owner seed-once/seal 校验（新增，未放宽；owner 内容比较始终开启，不受 `RSYNC_CACHE_VERIFY_REPLICA_CONTENT` 控制）、trace `schema_version` 与 cache 事件契约、`control/fatal/*` 的有限作用范围（仅主运行循环与循环内请求调用点）、provider 遍历顺序、Redis 超时不确定性。这些都不构成"逐字节兼容"。
- **`control/fatal/*` 不是万能保证**：只有主运行循环及三个 `requestFile*` 调用点捕获的异常会写标记；初始化 seeding、启动期显式请求、命令块中 try 之外的 `throw` 不会写 fatal（见 §3.7）。
- **未构建新镜像、未创建远端新根目录、未启动 smoke/pilot/formal。** 未触碰 capacity-v1、slides-v5、逻辑联邦、跨主机联邦的任何冻结源码/镜像/campaign/raw，未操作 worker-a/b，未更新 Slides，未生成 PDF，未删除任何失败 attempt。
- **协议 trace 的低频采集**：`RedisMonitor` 的采样频率/开销来源已由接口声明（独立连接、每 interval 一次 INFO），但真实启动脚本的采集点尚未实现。
- **reuse distance 明确不提供**，只有 `idle_age_ns`。
- legacy 正常行为与后端异常传播已按 §3.7 修复并回归；owner seed-once/seal 等新增校验仍是有意保留的差异；后端不确定性的保守边界已在 §4.6 实现并做确定性回归，但不代表真实 Redis/真实进程已验收；边界 C（首份拷贝）与 D（已驱逐副本的历史冲突）按 §4.5 明确**不做**内容认证。本版本**不构成"可正式部署"的结论**。
- 不合并绝对时延：本版本使用 bridge/veth，与旧 VDE 实验不是同一环境。
- 本版本**不主张任何性能结论**。

## 8. 后续小规模 smoke 计划（尚未执行）

> 六槽 smoke 的**冻结矩阵、端到端接入审计与启动计划**见 `SMOKE_PLAN.md`；机读冻结见 `smoke_matrix.py`（`--self-test` 17/17）。本轮 smoke **未启动**，原因见 `SMOKE_PLAN.md` §5。

资源需求（需要用户确认后再做，且不得打断任何在跑任务）：

1. 在 simple-station 新建独立根 `/home/simpleai/zhengzihao/networkcache-concurrency-finite-cache-v1`，`source/` 为本分支 checkout（`simple-station/concurrency-finite-cache-v1`），独立 campaign/raw/镜像标签。
2. **构建新镜像**（这一步是长任务）：`docker build -f Dockerfile --build-arg ENABLE_REDIS=ON --build-arg FETCH_REDIS_DEPS=ON -t rsync-router:concurrency-finite-cache-v1 .` — 预计数分钟到十几分钟、1–2 GB 磁盘；需要在 `docker ps` 为空、simple-station 空闲时执行。基线里那个缺失文件的 CMake 守卫是本轮已修的前提。
3. 容器内先跑 Redis 路径的编译与契约测试（`test_redis_cache_contract`），再跑 §6 的 6 个目标（含 `test_redis_acceptance`），并接入 §4.7 的启动核验与监控，确认 `owner:`/`replica:` 前缀、字节预算与自动驱逐检测在后端上行为一致。
4. **smoke 6 槽**（16 主机，小阶梯 r32/r128，traces 打开）：`{nc} × {budget 0, budget 1 MiB} × 1 seed`，外加 2 槽 TCP Direct 对照。验收门槛：
   - `replica_evict > 0`（1 MiB 档）与 `= 0`（0 档）；
   - `resident_bytes ≤ budget_bytes` 全程成立；
   - `admits − evictions == replica_objects`；
   - 无 `owner_kv_missing`、无 `cache_backend_error`；
   - 应用完成率 100 % 且 hash 正确；
   - 容器内 Redis `evicted_keys` 增量 = 0。
5. smoke 通过后再冻结诊断 pilot，补齐旧设计稿 §9.3 决策表（观察长度、预热段、统计段、seeds/repetitions、上界选择），然后才谈正式矩阵。