# 容量筛选 pilot v1 — 计划与冻结说明

状态：**计划冻结中（长度校准先行）**。本文件与 `pilot_matrix.json` 必须一致，runner 启动前校验两者的 SHA
与内容；不一致即拒绝启动。历史计划/报告不删除、不覆盖。

## 1. 实验问题与可主张范围

**问题**：在固定 workload（32 节点、Zipf α=1.1、Poisson 全局到达率 128 req/s、512×64 KiB 对象）下，
逐节点副本预算从 `bypass/0` 到 `unlimited` 的八档中，哪些档位存在**明显的副本缓存竞争**、
哪些档位相对 **unlimited 配对基线**出现**有代表性**的性能变化，从而为正式矩阵选档。

**可主张范围（本轮）**：容量筛选（screening）。3 个 seeds × 1 repetition，**不是**正式统计结论；
不做吞吐饱和扫描；不发布性能优劣结论；不更新 Slides。

**不可主张**：10-seed 正式统计、稳态最优容量、命中率（除非能唯一归因最终交付 provider）、
把本轮 TCP 无关数据当作性能基线（本轮不新增 TCP run）。

## 2. workload 来源、拓扑、对象与 owner

| 项目 | 冻结值 |
|---|---|
| 生成器 | `experiments/concurrency_slides_v5/{matrix.py,pilot.py,controller.py}`（冻结血缘） |
| scenario | `inputs/single/scenarios/kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b65536-seed00{1,2,3}.yaml` |
| mapped 数据 | `inputs/single/mapped/<scenario>/{schedule.tsv,catalog.tsv,data/node1..32.tsv}` |
| 拓扑 | **32 个协议节点**（`- id: 1..32`），点对点 LAN，无 switch 参与服务 |
| 对象 | catalog 中 **512 个 formal 对象**，每个 65,536 B（64 KiB），目录总量 **32 MiB** |
| owner 分配 | catalog `owner` 列，**每节点恰好 16 个 formal 对象**（512/32） |
| owner 语义 | owner 原始数据独立、只读、**不计入副本预算、永不驱逐**（已验收实现） |
| setup | 32 个 setup 请求（`phase=setup`），先于测量段释放 |
| 测量请求 | **4096** 个（`phase=measurement`），与 `matrix.request_count(zipf)=4096` 一致 |

核实结论（逐项来自实际文件，非文件名推断）：

- `schedule.tsv` 的 phase 计数 = 32 setup + 4096 measurement；
- `catalog.tsv` 的 `catalog_class` 计数 = 512 formal + 32 setup；
- `campaign.json` 的 `per_node_inflight = null`、`concurrency_cap = null` → **无 inflight 上限**；
- `arrival_seed_rule = "9172 + workload_seed"` → seed 1/2/3 对应 9173/9174/9175；
- `offsets(count,'poisson',rate,seed)` 累加 `Exp(1)/rate` → **r128 是全局请求到达速率**
  （平均间隔 1/128 s ≈ 7.8125 ms，4096 请求 ≈ 32 s 到达窗口）；
- `Engine` 按计划偏移 `admit(now)` 释放，`serial=False` → **释放与前一个请求是否完成无关**（开环到达）；
- 无隐藏截断：未到达即 `not_arrived`，看门狗 `base+arrival[-1]+60 s`，超时按 §6 判失败。

若上述任一项与实际不符 → 停止冻结并报告（本轮已全部核实通过）。

## 3. 八档容量（准确字节数）

| 档位 | policy | budget_bytes | 说明 |
|---|---|---:|---|
| bypass | `bypass` | 0 | **旁路**，不是“插入后立刻驱逐”，永不产生 `replica_evict` |
| 0.25 MiB | `lru` | 262144 | 4 × 64 KiB |
| 0.5 MiB | `lru` | 524288 | 8 |
| 1 MiB | `lru` | 1048576 | 16 |
| 2 MiB | `lru` | 2097152 | 32 |
| 4 MiB | `lru` | 4194304 | 64 |
| 16 MiB | `lru` | 16777216 | 256 |
| unlimited | `unlimited` | （无预算） | **独立策略值**，不得用 0 表示 |

32 MiB 是**目录总量**，不是副本预算档；旧描述的“此前 32 MiB 缓存”不沿用。

## 4. seeds、到达速率与运行顺序

- seeds：**001、002、003**（生成器支持 1..10，已核实文件存在）。
- 速率：**固定 128 req/s**（全局、Poisson），本轮不做速率扫描。
- 策略×seed：**8 × 3 × 1 = 24 runs**（筛选）；另有 §6 的长度校准 ≤4 runs，不进筛选分母。
- 运行顺序：用固定随机种子（`screening-order-v1`）对 24 个 (tier, seed) 组合做确定性 shuffle，
  并保证每个容量档在前 8 / 中 8 / 后 8 各出现一次（尽量分散，**不宣称三个 seed 能实现完美平衡**）。
  完整顺序写入 `pilot_matrix.json.order`，运行时按序执行。
- 同一 seed 的八档共享：对象内容与 owner 分配、requester/key/请求方向序列、planned arrival offsets、
  setup/预热/测量请求与超时规则。每档 run 均从**干净独立**的实验状态开始，不继承上一档缓存。

## 5. 预热与测量长度（先用校准确定，再冻结）

初始候选（依据：既有并发实验的 4096 请求、约 32 s 到达窗口、`loop_sleep_ms=25`、
以及 smoke 实测每 run 启停 60–90 s，不宜把单 run 拉到十分钟以上）：

- **到达窗口** ≈ 32 s（4096 / 128）。
- **预热段** = 到达窗口前 25%（≈1024 个请求）。
- **测量段** = 到达窗口后 75%（≈3072 个请求），以**计划到达时间**划分，等长窗口定义在计划中预先固定。
- 冷启动轨迹（warmup 段）单独保留，**不与稳态测量段混合**且始终标注。

校准判据（预先定义，见 §6）：若测量段出现持续积压增长、驱逐趋零且竞争无法确认、
完成样本不足以描述 p95/p99（测量段完成数 < 1000）、或运行时间/磁盘超出预算，
则在**同一 seed/策略**上增加一次更长观察（新 attempt）；最多 4 个校准 runs。

## 6. 请求超时、排空与失败判据

复用冻结超时策略（`pilot.timeout_policy()`，与并发实验一致）：
`protocol_query_lifetime_seconds=30`、`origin_fallback_seconds=1.5`、`slow_request_marker_seconds=8`、
`unstarted_dispatch_abort_seconds=8`、`watchdog_seconds_after_last_arrival=60`、
`tcp_direct_exchange_timeout_seconds=5`、`quiescence_timeout_seconds=20`、`quiescence_stable_seconds=0.5`。

- 到达后未在窗口内完成的请求计入 `not_completed`/`timeout`，**不被剔除**（避免幸存者偏差）。
- 排空：最后到达后等待至全部请求终态或看门狗（≤60 s），排空时长单独记录。
- 失败判据（任一即当前 run 失败并**停止后续队列**）：fatal 诊断、`cache_backend_error`、
  内容 hash 不符、计划请求缺失或重复完成、trace/completion/audit 损坏、监控缺失或实例身份不符、
  计划/工具哈希不符。**脚本适配问题**可修复后用新 attempt（每 slot ≤2 次）。

## 7. 指标定义与分母

**应用层**：planned / released / completed / success / timeout / not_completed；
offered rate（计划）与 actual release rate（实际释放）；**测量窗口内完成吞吐 = 窗口内完成数 ÷ 测量窗口时长**；
排空时间（最后到达→最后完成）；端到端延迟 p50/p95/p99（以 `total_latency_ns` 为准，
样本不足时标注“样本不足，不作精确结论”）；调度滞后与执行时延（`scheduler_delay_ns` / `execution_ns`）。

**缓存与路由**：per-node owner/replica 服务；请求级命中按**可唯一归因的事件**分类
（`local_cache_hit` = 本节点副本；`provider_hit` = 中间节点或 owner 服务，需按 `via`/tier 细分）；
`replica_admit`/`replica_evict`/`replica_bypass` 计数；**每秒驱逐**与**每千完成请求驱逐**；
驻留对象/字节时间序列（`cache_gauge`）；per-node 分布（尤其 hub）；fallback 事件与内容正确性。

**口径约束**：不把 provider 事件数、DATA 事件数与应用交付数相加；“缓存命中率”必须标明是
**请求级**还是**服务事件级**；若无法唯一归因最终交付 provider，只报服务事件级比例。

**驱逐后 fallback 关联**：审计 trace 是否足以串联 `驱逐 key → 后续请求涉及该 key → 旧路由失败 → fallback → 正确交付`；
只对证据完整的案例作机制结论；普通 Zipf pilot 未出现该情形时**不算协议失败**，报告覆盖数量即可。

**聚合**：先算每个 run/seed 指标；展示逐 seed 结果与相对**同 seed unlimited**的差值/ratio；
不把所有请求当作独立重复。

## 8. 校准与筛选的最大运行数量

- 长度校准：**≤4 runs**（初始 seed001 × {0.25 MiB, unlimited}，必要时各加一次更长观察）。
- 容量筛选：**24 slots**（8 档 × 3 seeds × 1 rep）。
- 不自动增加速率、容量档、seeds 或 repetitions；不自动启动正式矩阵。

## 9. provenance

- 镜像：`rsync-router:concurrency-finite-cache-smoke-81c22fe9`
  `sha256:f418668fd2ef7e695a45db2673bdeff66a3d49f2d94ca3bbcc2409e910355d3d`（不重建、不覆盖）。
- 源码提交、工具 SHA-256（runner/validator/analysis/plan）、workload 文件 SHA-256
  （scenario、schedule、catalog、32 个 data 文件，逐一与冻结 `campaign.json.inputs` 的每文件哈希核对）
  在每 run 的 `provenance.json` 中分别记录；**镜像、源码、执行工具、复验工具分别列示**。
- 复验：复用 `monitor_evidence.py`、`strict_audit.py`、`offline_accept.py`、`evidence_archive.py`
  的证据绑定与封存规则；pilot 显式适配矩阵，不复用 smoke 的“48 请求/16 节点”常量。

## 10. 预计时间与停止条件

- 单 run：到达窗口 ≈32 s + 排空 ≤60 s + 启停 60–90 s ≈ **2.5–3.5 min**。
- 校准 ≤4 runs ≈ 12–15 min；筛选 24 runs ≈ 60–85 min；加封存/回传/分析 ≈ 15–25 min。
- **合计约 1.5–2.5 小时**（依实际启停与验证耗时而定；不为了 ETA 放宽门槛）。
- 停止条件：24 筛选 slots 全部完成并整理结果，或触发 §6 失败判据，或达到本轮运行上限。
  完成后停止并等待用户审阅正式实验计划；不自动启动正式矩阵。