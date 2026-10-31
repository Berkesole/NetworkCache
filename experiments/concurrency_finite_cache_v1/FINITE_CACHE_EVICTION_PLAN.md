# 并发 NetworkCache：有限缓存、驱逐策略与容量竞争实施说明

编写日期：2026-09-17（Asia/Shanghai）  
状态：待实施设计与验收清单；不是已实现功能或已完成实验的声明。

## 0. 本文要解决什么

在 simple-station 已有开放到达并发版本上，建立一个能够明确解释、复现和测量的有限缓存实验版本，回答：

1. 缓存能容纳多少对象，什么时候开始发生容量竞争？
2. 在有限容量和并发访问下，LRU驱逐怎样影响命中率、排队、尾延迟和网络开销？
3. 缓存副本被驱逐、路由摘要尚未更新时，协议是否仍能正确完成请求？

实施顺序必须是：核对工作集与传输大小 → owner与副本分离 → 可观测的按字节LRU → 正确性测试 → 小规模试跑 → 冻结正式实验。

**本文件不是启动实验、修改历史campaign或删除数据的授权。** 本轮只编写文档。正式配置、重复次数、观察时长和运行预算确定后，再启动对应实验。

## 1. 资源、路径及不可触碰范围

### 1.1 已有版本

- SSH目标：`simple-station`。
- 并发容量版本远端根目录：`/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1`。
- 其源码：上述目录下的`source/`；实验入口在`source/experiments/concurrency_capacity_v1/`。
- 固定负载并发版本：`/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5`。
- 本机容量框架：`/root/zzh/rsync/experiments/concurrency_capacity_v1`。
- 本机仓库：`/root/zzh/rsync`。

### 1.2 新版本建议

建议命名`concurrency-finite-cache-v1`，使用独立源码快照、镜像、campaign和raw根目录。远端候选根目录为`/home/simpleai/zhengzihao/networkcache-concurrency-finite-cache-v1`。这是建议路径，尚未创建或冻结。

不得覆盖已有capacity-v1、slides-v5、长时窗逻辑联邦或跨主机联邦的源码快照、镜像标签对应关系、selection与raw。不得操作worker-a/b，不自动更新Slides，不生成PDF。原始attempt只追加；失败记录保留，禁止删掉失败后重跑来选择成功样本。

## 2. 已核实事实与待核实事项

### 2.1 已核实

| 项目 | 已知配置或代码行为 |
|---|---|
| 当前单集群容量实验对象目录 | 512个唯一对象 |
| 对象value大小 | 64 KiB = 65,536 bytes |
| 节点数 | 16或32个协议节点 |
| 网络接口MTU | 1500 bytes |
| 默认Redis模板 | `maxmemory 256mb`、`maxmemory-policy allkeys-lru` |
| 正式历史/并发runner覆盖 | `RSYNC_REDIS_MAXMEMORY=0`、`RSYNC_REDIS_MAXMEMORY_POLICY=noeviction` |
| 覆盖方式 | 启动脚本先展开模板，再追加实验配置，之后启动Redis |
| RedisCache写入 | 普通SET，不设置对象TTL |
| 本机InMemoryCache | 没有自动容量限制或驱逐策略 |
| owner与副本 | 都写入Node持有的cache后端，未见owner免驱逐保护 |
| 驱逐同步 | 未见Redis驱逐事件到local_files_及路由摘要的完整同步流程 |
| 并发执行 | 开放到达；允许多个请求同时在途；具有独立应用请求ID |

源码依据（相对本机仓库）：

- `docker/redis.conf.template:30`、`docker/redis.conf:27`：默认容量与LRU。
- `kathara/ruikekkk/shared/start-node.sh:55`：启动配置覆盖链。
- `experiments/content_discovery/delay_find_content/scripts/run_latency_v3_once.sh:120`：历史实验覆盖；v4入口继续调用此脚本。
- `experiments/concurrency_capacity_v1/pilot.py:170`：当前容量实验覆盖。
- `src/cache/redis_cache.cpp:84`：SET；该文件也提供显式DEL，但没有自动驱逐管理。
- `src/node.cpp:240`、`app/rsync_node_main.cpp:1025`附近：owner对象初始化进入相同缓存后端。

行号随代码修改会变化，执行时应按符号再次定位。此前只读抽查的远端capacity run环境快照和本地长时窗run元数据均记录0/noeviction；没有对全部历史容器做实时CONFIG GET。因此不能说所有历史运行都曾实测零驱逐。

### 2.2 必须补查，不能把假设当事实

1. 从实际生成的对象分配表统计每节点owner数量，不只使用平均值。
2. 对比远端冻结源码、本机当前源码和目标镜像，避免误用不同版本。
3. 核对NC传输的完整对象编码、协议头、TCP写入方式，以及是否有应用层分片；不能由MTU反推应用协议分片方式。
4. 在新实验启动时保存Redis实际`CONFIG GET maxmemory`、`CONFIG GET maxmemory-policy`和INFO快照，而不只记录环境变量。
5. 核对新版本中所有缓存读取/写入入口，包括本地请求、转发服务、DATA返回、重复DATA及同key并发完成。

**2026-09-17 只读补齐情况。** 第5项已完成：写入入口恰好三个，全部进同一个 `cache_` 与同一个 `local_files_`，没有任何 owner/副本区分——`node.cpp:241`（`addLocalData`，owner/seed，调用方 `app/rsync_node_main.cpp:1031` 与 `:1042`）、`node.cpp:911`（fallback阶段副本插入）、`node.cpp:1022`（preferred阶段副本插入）；读取入口是 `node.cpp:637`（遍历 `local_files_` 后 `cache_->get`，仅在返回值非空时记 `provider_hit`）。另两项已确证的硬事实：`RedisCache::del()` 虽已实现但 `Node` 从不调用（写入3处、删除0处），`local_files_` 只有插入没有擦除（`242`/`912`/`1023` 插入，0处擦除）。§6.3 第2问（并发模型）亦已回答：`Node` 由 `node.cpp:1202` 的 `while (auto pkt = network_->receive())` 单线程串行驱动，`src/node.hpp` 无 mutex 或线程，应用层LRU记账不需要锁，真正的风险只剩在途DATA的value生命周期与pin语义。第3项的帧尺寸部分已可回答（见 §12.2），链路包数仍缺。

## 3. 容量与包大小：统一计算口径

### 3.1 三种大小不能混用

- **对象value**：64 KiB，当前缓存预算拟按它计量。
- **应用协议消息**：value加key、请求ID、长度字段等编码开销，必须从实现计算。
- **网络包/帧**：还包含传输与网络头；MTU限制不是缓存对象大小。TCP分段、重传和链路封装需要实测。

不把64 KiB对象称为“一个64 KiB网络包”。不简单用`65536/1500`作为实际包数；只有确认传输协议、头部和有效载荷后，才能做分段数量估算。ACK、重传、REQUEST与DATA分别计量。

需要输出一份传输尺寸表：消息类型、value bytes、应用编码bytes、实际网络包数量、实际传输bytes、重传bytes、采集接口和计数层级。抓包时避免在同一条路径多个接口重复计数。TCP Direct和NC必须使用一致的测量层级。

### 3.2 当前单集群工作集估算

定义：唯一对象数N=512，value大小S=65,536 bytes，协议节点数H。每节点owner对象数为O_i；以下平均值假设均匀分配。

全局唯一value总量：`N × S = 32 MiB`。

| 指标 | H=16 | H=32 |
|---|---:|---:|
| 每节点平均owner对象数 | 32 | 16 |
| 每节点平均owner value大小 | 2 MiB | 1 MiB |
| 每节点非owner对象数上界 | 480 | 496 |
| 每节点非owner value总量上界 | 30 MiB | 31 MiB |
| 所有对象在所有节点各有一份：对象实例上界 | 8,192 | 16,384 |
| 上述全系统value总量，含owner | 512 MiB | 1 GiB |
| 上述全系统副本value总量，不含owner | 480 MiB | 992 MiB |

公式：每节点副本工作集上界`W_i=(N−O_i)×S`；全系统含owner的完全复制上界`H×N×S`；全系统副本上界`(H−1)×N×S`。

这些是理论上界，不是实际占用。它们不包括Redis结构、分配器碎片、LRU元数据、local_files_、Bloom、QueryTable、连接和在途DATA。

**结论：对这组工作集，每节点约32 MiB的value预算已经足以放下全部对象。默认256 MB Redis上限大概率不会被对象value撑满，不能据此构造有效的缓存竞争实验。** 是否触发真实Redis上限仍需测内存开销，不能仅凭value量断言。

此表只适用于512对象的单集群实验。若每个联邦域各有512个不同对象，必须按跨域唯一对象并集重新计算，不能沿用32 MiB总工作集。

### 3.3 候选副本预算

按value字节预算B，等大小对象可容纳数为`floor(B/S)`：

| 每节点预算 | 64 KiB对象数 | 相对于512对象目录 | 用途 |
|---|---:|---:|---|
| 0 | 0 | 0% | 不缓存副本对照；仍保留owner和正常转发 |
| 1 MiB | 16 | 3.125% | 小容量候选 |
| 2 MiB | 32 | 6.25% | 备用细分档 |
| 4 MiB | 64 | 12.5% | 中小容量候选 |
| 8 MiB | 128 | 25% | 备用细分档 |
| 16 MiB | 256 | 50% | 较宽裕候选 |
| 32 MiB | 512 | 100% | 足量对照 |
| unlimited | 不设预算 | — | 与新版本有限缓存组同环境的基线 |

第一轮建议按 §4.1 的实测回放取 **0 / 4 / 8 / 16 / 32 / 64 / 256 对象** 及 unlimited 基线（等价于 0 / 0.25 / 0.5 / 1 / 2 / 4 / 16 MiB）。已被回放否决的是 32 MiB（驱逐为0，等同unlimited）与作为处理组的 16 MiB（保留率97.9%，接近无竞争）。不能预先把某档标记为“激烈竞争”：Zipf热点集中、节点经过的对象集合和访问顺序都会改变压力。

请求次数不是缓存唯一对象数。32,768次访问同一512对象目录，不能推算为32,768份不同缓存对象。并发还会增加在途内存，这需要独立测量。

## 4. 第一步：计算实际竞争程度，筛选容量档

先编写只读分析器，读取冻结schedule及可用的节点访问记录，输出：

1. 每节点owner对象数、owner bytes、唯一请求对象数。
2. 每节点实际服务/经过/插入的唯一副本数及bytes；没有相应trace时写UNKNOWN，禁止用全局请求序列冒充节点流量。
3. 各候选容量的LRU回放：命中率、驱逐次数、驱逐bytes、首次填满位置、对象再访问间隔及reuse distance。
4. 每千次访问驱逐数与每千次插入驱逐数分别输出，分母不能混用。
5. 按节点给分布和最大值，不能仅报告全系统平均。

说明：无容量限制运行的路径可能与有限缓存路径不同，因此回放只是筛选配置，不是正式性能结果。尤其现有capacity多数条件没有完整事件trace，无法凭空重建每节点副本访问流；证据不足时增加独立诊断试跑，而不是修改旧raw。

### 4.1 已有 raw 上的回放结果（2026-09-17，只读）

脚本：`experiments/concurrency_capacity_v1/replay_working_set.py`。只读冻结raw，不写回，不改变capacity campaign指纹。它按真实准入规则（`Node::handleData` 的 `if (!hasLocalData(key)) { put; insert }`，而 `hasLocalData` = `cache_->exists`）在时间序上重放每个节点的DATA到达，再对候选预算做LRU回放；owner归属取自`requests.csv`的`owner`列（也是控制器写进节点控制TSV的值）。owner常驻对象不计入副本预算，owner命中不刷新副本recency。

复现方式（脚本经stdin送入，远端不落文件）：

```
R=/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1/raw/formal
ARGS=""
for c in capacity-n32-poisson-r128-nc capacity-n32-poisson-r128-tcp-direct; do
  for s in 001 002 003 004 005; do ARGS="$ARGS $R/$c--s$s"; done
done
ssh simple-station "python3 - --brief $ARGS" \
  < experiments/concurrency_capacity_v1/replay_working_set.py > replay.json
```

回放可用的原始证据只有trace开启的条件。`capacity_matrix.py` 的 `TRACE_ON_CONDITIONS` 只包含 `capacity-n32-poisson-r128-nc` 与 `capacity-n32-poisson-r128-tcp-direct`，各5个seed。其中 **TCP Direct 的 `traces/` 目录为空**：该方法的节点不运行C++ Node应用（`RSYNC_SKIP_APP=1`），因此**没有缓存侧trace**，只有 `tcp-traces/` 的transport事件。结论：缓存指标只能由NC条件提供，TCP Direct只能做时延/吞吐对照。

NC，5个seed均值（32节点）：

| 每节点副本预算 | 驱逐次数 | 重复插入（相对unlimited多出的admit） | 副本服务保留率 |
|---|---:|---:|---:|
| 0（旁路） | 13230 | 8439 | 0.0% |
| 1 对象 / 64 KiB | 12156 | 7396 | 6.0% |
| 2 对象 | 11336 | 6609 | 11.4% |
| 4 对象 | 9902 | 5239 | 22.2% |
| 8 对象 | 8409 | 3874 | 39.2% |
| 16 对象 / 1 MiB | 7447 | 3168 | 54.6% |
| 32 对象 / 2 MiB | 6469 | 2668 | 66.3% |
| 64 对象 / 4 MiB | 5156 | 1984 | 76.8% |
| 128 对象 / 8 MiB | 3500 | 1076 | 88.9% |
| 256 对象 / 16 MiB | 1612 | 231 | 97.9% |
| 512 对象 / 32 MiB | 0 | 0 | 100.0% |
| unlimited | 0 | 0 | 100.0% |

"副本服务保留率" = 该预算下仍为命中的副本服务次数 ÷ 实测副本服务总次数；它才是判断某档有没有竞争的依据。可直接读出的结论：

1. **512 对象 / 32 MiB 档驱逐为0，与unlimited逐项相同。** 该档是退化的对照组，不是处理组；§3.3 与 §9.1 把它当候选处理组使用是错的。原因是本工作集下unlimited也从未超过每节点438个副本对象。
2. **真正的竞争区间在 256 对象（16 MiB）以下**，保留率从0%单调升到97.9%，弯曲最快的一段在 **4–64 对象**。第一轮预算建议改为按对象数取 **0 / 4 / 8 / 16 / 32 / 64 / 256**，外加unlimited基线；不要使用 1/4/16/32 MiB 这组，其中 32 MiB 已证明等同unlimited、16 MiB 已接近无竞争。
3. **副本服务占绝对多数**：5个seed平均 owner 服务 2781 次 vs 副本服务 14591 次（84%）。预算0时这14591次副本命中全部消失，请求路径随之改变。这是本实验的主要机制，也说明必须与unlimited在同一部署内配对比较，不能与旧运行绝对值合并。
4. **驱逐直接制造重复插入**：预算0时admit为13230次，而unlimited为4863次（2.7倍），差额全部是驱逐后的再插入。§10.1 要求的"重复插入"指标在实现前就已可量化，且是区分容量档的敏感量。
5. **节点间极不均匀**：每节点唯一副本数 minimum 17、maximum 438，均值约150。必须逐节点报告；只报全局平均会掩盖承担工作集的少数hub节点。预算小于438对象时，这些hub节点必然驱逐而多数节点可能一次都不驱逐——这本身是有效观察，但结论必须限定为"部分节点发生竞争"。
6. **请求只覆盖部分目录**：每seed实际被请求的唯一key为439–459个（目录512），每节点被请求到的owner对象为10–17个。从未被请求的对象不参与本实验的命中统计，§3.2 的"512对象"是目录规模而非实测工作集。
7. **回放是反事实近似**：容量改变会改变DATA转发树，因此上表是筛选区间，不是性能预测（与本节开头的说明一致）。

选择容量档的依据是能覆盖：频繁替换、部分热点保留、基本无替换三个区域。是否“激烈”用命中率、驱逐频率和重复插入量描述，不临时发明一个结果导向的通过阈值。

## 5. 第二步：实现owner与副本分离

### 5.1 存储职责

- OwnerStore：保存本节点负责的原始对象，不参与缓存驱逐；对象身份来自冻结对象分配表。
- ReplicaCache：保存途中获得的副本，执行字节预算与LRU。
- 本地读取统一封装：先查OwnerStore，再查ReplicaCache；上层必须知道命中类型以便统计。
- TCP Direct只从OwnerStore取数据，不引入副本缓存。

实现选择应优先保持新版本内NC/TCP的owner数据来源可解释；不能无记录地把NC换成更快的存储而把差异归因于缓存策略。Redis可保留为后端，但owner与replica的命名空间或实例必须隔离，并验证不发生key冲突。

### 5.2 必须保证

- 任何副本驱逐都不能删除owner对象。
- 不缓存副本时仍能从owner获取、转发和交付对象。
- local_files_要能精确反映当前可提供的owner及副本，不能只增不减。
- 所有旧cache_读写入口都经过新接口，不允许残留绕过预算的写入。

## 6. 第三步：应用层按字节LRU

### 6.1 首版固定语义

- 预算只计算副本value bytes；Redis实际内存另行观测。
- 成功命中副本更新LRU；owner命中不更新副本LRU。
- 首次插入置为最近使用；同key重复插入/收到重复DATA如何更新recency必须固定，建议仅真正使用该副本的访问刷新，不能让重复网络DATA人为保活。
- 插入前选择足够的最久未使用副本驱逐，最终`resident_value_bytes ≤ budget_bytes`。
- 对象大于整个预算时不缓存，但正常转发和交付。
- 预算为0明确表示副本旁路，不是Redis的`maxmemory 0`；两者含义完全不同。
- unlimited作为显式策略值，不与0复用。
- 容量拒绝、重复对象、真实Redis写入错误必须区分。不能把真实后端故障静默记成“没有缓存”。

### 6.2 Redis设置

新版本建议仍由应用层控制预算，Redis不再另行自动LRU驱逐，避免应用不知道删除了哪些对象。启动时校验实际Redis配置。若设置额外Redis安全上限，必须给owner、元数据和碎片预留空间，并将碰到该上限作为环境/资源事件处理，不能当作应用LRU的一次正常驱逐。

### 6.3 并发一致性

- 原子维护实际KV、LRU顺序、大小计数与本地目录；异常路径不留下幽灵对象或错误占用。
- 明确当前Node是事件循环串行访问还是多线程访问，再选择同步方式，不能仅因名字为async就假定线程安全。 **已确证（2026-09-17）：单线程串行事件循环**（`node.cpp:1202`，`src/node.hpp` 无线程与锁），因此不需要为LRU元数据加锁；需要额外保护的是已开始发送的DATA value引用与pin计数。
- 已开始发送或交付的DATA必须持有独立有效值/引用；驱逐不能使其悬空。
- 如果使用pin机制，必须记录pinned bytes、可驱逐bytes以及全部被pin时的插入旁路行为；不得偷偷突破预算。
- 同key多个应用请求按各自ID恰好完成一次，缓存替换不能改变这个规则。

## 7. 第四步：驱逐与Bloom/路由信息的关系

首版保持当前协议路由算法、Bloom参数与fallback策略不变；不同时引入Counting Bloom或周期性全量广播。

需要做的事情：

1. 驱逐后立即清理本地副本目录。
2. 邻居旧摘要可能仍指向此节点，明确允许这种暂时过期。
3. 节点收到请求时查实际对象；没有对象不得发送成功DATA，也不得错误关闭待完成查询。
4. 验证请求能继续通过现有发现/fallback路径找到受保护的owner。
5. 用可确证的历史驱逐记录区分“驱逐相关失效”与“普通Bloom假阳性”；无法确定原因的记UNKNOWN，不能全算成驱逐导致。

禁止直接清除普通Bloom中某个key的位，因为这些位可能被其他key共享。现有路由TTL即使存在，也不等于有逐对象驱逐通知。摘要更新机制作为后续独立变量评估。

## 8. 第五步：正确性测试清单

先完成确定性单元测试，再运行小规模拓扑smoke：

- 预算0、不足一个对象、恰好一个对象、多对象边界。
- 可手算的LRU序列，逐步验证被驱逐的key与占用。
- 同key重复put、不同大小替换、删除不存在key。
- owner在持续副本替换中永不丢失。
- 删除副本后local_files_不再包含该副本，owner条目不受影响。
- Redis异常时元数据不失配；错误不能被吞掉。
- 正在发送DATA时发生驱逐，不影响接收完整性。
- 同节点同key/不同key多请求并发，恰好一次交付与hash正确。
- 路由摘要故意指向已驱逐节点，最终仍完成；无错误成功、无永久卡死。
- 全部副本旁路时NC和TCP都能正确读取owner。
- 重启/恢复时，不继承旧run缓存或上一run的LRU统计。
- 缺失记录、重复请求ID、占用超预算、错误owner分配必须拒绝验收。

低容量smoke必须实际产生驱逐事件；“请求全部成功但从未填满缓存”不能证明驱逐路径正确。

## 9. 第六步：试跑与正式矩阵

### 9.1 诊断试跑建议，不是已冻结配置

先32节点、64 KiB、512对象、Zipf α=1.1，选择已有证据支持的低/较高开放到达速率；先少量seed。

第一轮比较 §4.1 给出的对象数档（0 / 4 / 8 / 16 / 32 / 64 / 256 对象）与unlimited基线，只使用LRU，不立即增加LFU/随机驱逐。不要再把 32 MiB 或 16 MiB 当作处理组：前者与unlimited逐项相同，后者只有2.1%的副本服务受影响。选定容量后再加入Uniform，避免第一轮同时改变过多维度。

并发到达率不能照抄“最大完成速率”；应根据已有排队、调度延迟、CPU与完成率选择，避免压测器或宿主机先成为瓶颈。保持同seed的key、endpoint、到达时间序列一致，确定性平衡条件顺序。

### 9.2 观察时长

有限缓存可能持续替换，不以“缓存不再变化”作为稳定标准。试跑要观察占用、命中、驱逐、积压和时延随实际时间的变化，区分缓存预热与持续排队增长。

当前capacity高负载条件有8/16秒的短窗口，不能原样当作长期稳态证据。根据试跑冻结新的观察长度、预热段和统计段；不要看正式结果后移动窗口。选择延长窗口时重新记录请求数、运行预算与campaign。

### 9.3 正式启动前必须填写的决策表

| 决策项 | 当前状态 |
|---|---|
| 新版本名、镜像ID、源码SHA | 待冻结 |
| hosts：16、32或两者 | 待试跑确定 |
| workload：Zipf、Uniform | 第一轮Zipf，正式范围待定 |
| value大小 | 首版固定64 KiB |
| 每节点副本预算集合 | 候选0/1/4/16/32 MiB/unlimited |
| 到达模型与速率 | 待根据已有诊断与试跑选择 |
| owner存储实现与保护方式 | 必须先实现并验收 |
| LRU访问/重复DATA语义 | 必须冻结 |
| seeds与repetitions | 待确定，不能自动沿用旧5seed或旧10×2 |
| 观察长度、预热与分析窗口 | 待试跑后冻结 |
| 最大attempt与失败处理 | 待冻结；raw不可删除，禁止成功选择 |
| 总runs、请求数、磁盘和ETA | 据最终矩阵及试跑时长计算 |

正式run数按全部独立条件数×seed数×repetition数计算。NC不同缓存容量是不同条件；TCP无副本预算，不为每个NC预算重复制造一组相同TCP条件，除非明确为配对时序设计并事先冻结。

## 10. 必须记录的指标与统计边界

### 10.1 缓存

逐节点：owner bytes、resident replica bytes、预算、对象数、插入/旁路/驱逐次数与bytes、命中/未命中、重复插入、实际Redis used_memory与evicted_keys。

区分“请求源本地缓存命中”“中间节点服务命中”“owner服务”。若重复查询、合并或多路径使一次应用请求触发多个查找，查找命中率与应用请求命中比例必须分开。

### 10.2 性能与资源

- 完成率（所有计划请求作分母）、有效吞吐、未完成/未发出数。
- 总延迟、调度延迟、节点开始前等待、执行时间；p50/p95/p99。
- 实际时间上的积压曲线、drain时间、全局/逐节点在途数。
- CPU、RSS、Redis内存、连接/FD、在途DATA与可观测缓冲。
- REQUEST/DATA数量与字节、重传、fallback，以及具备因果证据的失效路由。

未完成请求不能悄悄从性能结论中消失。成功请求的分位数必须标明是条件分布，并与完成率一起报告。容量过载可以是有效数据点，但身份错误、重复交付、hash错误和provenance错误不是可接受过载。

### 10.3 聚合

保持seed是独立重复单位。若有repetition，先在同seed内聚合，再跨seed计算置信区间；具体窗口、均值和分位数定义事先冻结。不要把数万请求当作数万个独立seed。

新版本的NC、TCP、unlimited和有限缓存组必须在同后端/同机器边界内比较。新bridge结果不与旧VDE绝对时延直接合并；不能把后端、TTL、日志策略等变化造成的差异归于驱逐。

## 11. 交付物与验收门槛

交付物：

1. 对象/消息/网络包尺寸审计与每节点工作集计算表。
2. LRU回放脚本、假设说明及容量档位选择报告。脚本已存在：`experiments/concurrency_capacity_v1/replay_working_set.py`，已有 raw 上的结果见 §4.1；仍需在新版本上以真实 `replica_admit`/`replica_evict` 交叉校验（§12.5）。
3. owner/cache分离和LRU实现、配置说明、确定性测试。
4. 独立smoke与试跑结果，含真实驱逐和失效路由案例。
5. 冻结campaign、矩阵、请求计划、镜像/源码/后端SHA、预算与统计定义。
6. 完整正式selection、run status、collection manifest、逐run/逐seed结果与分析报告。

进入正式队列的必要条件：正确性测试通过；owner保护有效；占用不超预算；同key并发交付正确；真实驱逐后请求可完成；Redis实际配置可核实；负载生成器无隐藏并发限制；试跑揭示了至少有竞争和无明显竞争的容量档；观察长度与统计口径已冻结。

完成后的主张范围：只声称指定部署、工作负载、预算和负载下观察到的性能；扫到最高负载不等于找到协议容量上限；短窗口顺利排空不等于长期稳定；路由摘要过期不自动等同于Bloom概率假阳性。

## 12. 事件 trace 契约（finite-cache 版本必须新增）

### 12.1 现状：现有 trace 无法支撑LRU判定

按 `src/node.cpp` 的全部 `rsync_trace::record` 调用，现有事件为：`query_table_set/expired/miss/match`、`request_start`、`local_cache_hit`、`provider_hit`、`app_receive`、`origin_fallback_armed/triggered`、`origin_query_tracked`、`request_send_start/done`、`request_handle_start`、`request_forward_start/done`、`data_handle_start`、`data_forward_start/done`、`data_send_start/done`、`duplicate_request_suppressed`、`fallback_forward`、`immediate_dead_end_fallback`、`query_route_selected`、`query_key_satisfied`、`query_complete`、`query_expired_unsatisfied`、`aggregate_flush`，以及transport层的`pilot_*`。

其中**没有任何缓存插入、旁路或驱逐事件**；容量拒绝、"已存在"、后端写入失败三种情况在trace里都表现为"什么都没发生"（`data_drop` 走 `obsLog`，落在 `node-logs` 而非 `traces`）。因此 §4 与 §10.1 要求的插入/旁路/驱逐/重复插入指标目前无法采集。有限缓存版本必须在实现驱逐的同时把这几类事件显式写出来。

### 12.2 已可用的量，不要重复造

- `data_handle_start`（`packet_type=DATA`）= DATA 到达，即真实准入触发点。
- `provider_hit` / `local_cache_hit` = 真实服务命中（已确认 `provider_hit` 位于 `cache_->get()` 成功分支内）。
- `pilot_wire_send` / `pilot_transport_enqueue` 带 `frame_bytes` 与 `packet_type`：NC下 REQUEST 帧 166–174 B，DATA 帧 65590–65592 B（= 65536 payload + 54–56 B 应用/传输开销）。这满足 §3.1 的"应用协议消息"一列；**它不等于链路包数**，MTU 1500 下的分段/抓包口径仍需单独给出。
- TCP Direct 没有C++ node trace，只有 `tcp-traces/` 的transport事件；TCP Direct 不提供缓存指标。

### 12.3 必须新增的事件

| 事件 | 关键字段 | 触发点 | 用途 |
|---|---|---|---|
| `cache_config` | `policy`(bypass/lru/unlimited)、`budget_bytes`、`owner_store`、`redis_maxmemory`、`redis_maxmemory_policy` | 每个run启动时写一次 | 让每个run自描述，替代只看环境变量（§2.2第4项） |
| `replica_admit` | `key`、`value_bytes`、`object_owner`、`reason`(preferred/fallback)、`source_node`、`request_id`、`resident_bytes_after`、`resident_count_after`、`evicted_count`、`evicted_bytes` | 副本真正写入成功后 | 插入次数与bytes、逐次驱逐配对、重复插入 |
| `replica_evict` | `key`、`value_bytes`、`last_touch_mono_ns`、`resident_bytes_after`、`resident_count_after`、`reason`(budget) | 每驱逐一个副本 | 驱逐曲线、reuse distance（由 last_touch 直接算，无需再回放） |
| `replica_bypass` | `key`、`value_bytes`、`budget_bytes`、`reason`(budget_zero/object_larger_than_budget/pinned_only) | 拒绝缓存但仍正常转发与交付 | 区分"容量拒绝"与"已存在" |
| `data_admit_skipped` | `key`、`reason`(already_cached) | `hasLocalData` 为真而跳过插入 | 区分"已缓存"与"被拒绝"；缺它无法解释 admit 计数 |
| `cache_backend_error` | `op`(put/get/del)、`key`、`error` | 后端抛错 | §6.1 要求真实故障不被静默记成"没有缓存" |
| `cache_gauge` | `resident_bytes`、`budget_bytes`、`pinned_bytes`、`evictable_bytes`、`owner_objects`、`replica_objects`、`admits`、`evicts`、`bypasses`、`redis_used_memory`、`redis_evicted_keys` | 与现有100 ms timeline同频写一次 | 时间序列、pin占用、Redis实际内存，避免逐事件体积膨胀 |

另外给现有命中事件补字段，不新增事件名：

- `provider_hit` 与 `local_cache_hit` 增加 `tier`（`owner`/`replica`）与 `lru_touched`（`0`/`1`）。`tier` 是 §10.1 三分类（请求源本地命中、中间节点服务命中、owner服务）的必要输入；`lru_touched` 用来验证 §6.1 的"重复网络DATA不得人为保活"确实生效。没有这两个字段，回放只能靠 owner 表反推 tier。

### 12.4 字段约定

现有序列化把几乎所有字段值写成字符串（数字也常是字符串，如 `"hop_count":"0"`）。新增字段沿用同一序列化器，不要在同一文件里混入新的类型约定。事件名使用小写蛇形，与现有一致。`cache_config` 与 `cache_gauge` 必须带 `run_id`，与其他事件共用同一去重键。

### 12.5 与回放、验收的关系

`replay_working_set.py` 目前用 `data_handle_start` 加 owner 表重建准入与 tier；新版本有 `replica_admit`/`replica_evict`/`tier` 之后，应改为直接读取真实事件，并**保留旧回放作为交叉校验**——同一预算下重建序列与实测序列应当一致。低预算 smoke 的验收因此可以写成：`replica_evict` 计数 > 0、`resident_bytes` 从未超过预算、`data_admit_skipped` 与 `replica_bypass` 的分母各自可解释。

## 13. 给接手者的执行清单

- [ ] 只读确认远端版本、当前任务及资源，不打断其他实验。
- [ ] 完成第2–4节审计，提交真实对象分配和尺寸/工作集估算。（§4.1 已给出只读回放与预算区间；§3.1 的帧尺寸已有 166–174 B / 65590–65592 B 证据，链路包数仍缺。）
- [ ] 在实现驱逐的同一改动里落地 §12 的事件契约；没有 `replica_admit`/`replica_evict`/`replica_bypass`/`tier` 就不启动正式矩阵。
- [ ] 创建隔离的新版本，不修改旧campaign与raw。
- [ ] 完成第5–7节实现，记录所有新增语义。
- [ ] 通过第8节确定性测试与真实驱逐smoke。
- [ ] 运行已获授权的诊断试跑，补齐第9.3节决策表和ETA。
- [ ] 冻结配置后才启动正式矩阵；状态检查只做一次快照，不持续轮询。
- [ ] 完成后全量归档、SHA校验、回传、复验，按第10节统计。
- [ ] 单独提交结果报告；是否更新Slides由用户另行决定。
