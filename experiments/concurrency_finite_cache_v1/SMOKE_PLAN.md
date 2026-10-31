# 六槽 smoke：冻结矩阵、端到端接入审计与启动计划

状态（本轮）：**计划与审计完成；smoke 未启动（NOT_RUN）**。原因是两项必须先解决的前置条件（见 §5），
不是资源争用——simple-station 本轮只读检查时空闲（load≈0.5、`docker ps` 为空、只有 katharanp 守护进程）。
本文件不宣称任何 smoke 槽通过。

## 1. 冻结的六槽矩阵（本轮冻结，不再改动）

| # | 速率档 | 方法 | 副本缓存策略 | slot id |
|---|---|---|---|---|
| 1 | r32 | NC | bypass，预算 0 | `finite-smoke-n16-b65536-c512-poisson-r32-nc-bypass0--s001-r1` |
| 2 | r32 | NC | LRU，预算 1 MiB | `finite-smoke-n16-b65536-c512-poisson-r32-nc-lru1m--s001-r1` |
| 3 | r32 | TCP Direct | 不缓存 | `finite-smoke-n16-b65536-c512-poisson-r32-tcp-direct--s001-r1` |
| 4 | r128 | NC | bypass，预算 0 | `finite-smoke-n16-b65536-c512-poisson-r128-nc-bypass0--s001-r1` |
| 5 | r128 | NC | LRU，预算 1 MiB | `finite-smoke-n16-b65536-c512-poisson-r128-nc-lru1m--s001-r1` |
| 6 | r128 | TCP Direct | 不缓存 | `finite-smoke-n16-b65536-c512-poisson-r128-tcp-direct--s001-r1` |

固定执行顺序即上表 1→6；每槽一次运行（`seed=1, repetition=1`），不做统计比较。

### 1.1 `r32`/`r128` 的准确含义（已核对生成器）

`experiments/concurrency_slides_v5/matrix.py` 的 `conditions()` 里 `rate ∈ (32,128)`，它是**到达速率**
（open-arrival / Poisson 到达过程的每秒请求数），**不是并发上限**。同文件另有
`per_node_inflight is None`、`concurrency_cap is None`（`pilot.preflight()` 第 61 行断言），
即并发不受 rate 限制。冻结选择：smoke 用**显式到达间隔**驱动，
`r32 → 31.25 ms`、`r128 → 7.8125 ms`（`matrix.slot` 的 `arrival_seed = 9172 + seed = 9173`，
保证到达抖动可复现）。

### 1.2 共同条件（沿用已实现并发版本；已核对）

- 16 个协议主机（`hosts=16`），每对象 64 KiB（`size=65536`），目录 512 对象（`catalog=512`）。
- owner 原始数据保留、不计入副本预算（既有 `owner:`/`replica:` 命名空间语义，未改）。
- NC 开启完整缓存与协议 trace（`RSYNC_EVENT_TRACE=1`、`FLUSH_EVERY=1`）。
- 传输/链路（来自 `pilot.environment()` 第 95–101 行，冻结）：wire v3、Bloom 1024/3、
  staged fallback=1、fallback 1500 ms、query timeout 30000 ms、link delay 2 ms、rate 1000 Mbit、
  queue 1000 packets、MTU 1500、loop_sleep_ms=25、`RSYNC_REDIS_MAXMEMORY=0`、
  `maxmemory-policy=noeviction`、`RSYNC_REDIS_REQUIRE_EMPTY=1`、Redis timeout 1000 ms。
- 超时策略：`pilot.timeout_policy()` 冻结值（started_request_abort=None、slow_marker=8 s、
  unstarted_dispatch_abort=8 s、protocol_query_lifetime=30 s、origin_fallback=1.5 s、
  watchdog_after_last_arrival=60 s、tcp_direct_exchange_timeout=5 s、quiescence_timeout=20 s、
  quiescence_stable=0.5 s）。

### 1.3 请求计划（沿用既有 smoke 设计）

`pilot.input_rows()` 对 `stage=='smoke'` 用 6 个 `formal` catalog key 生成 4 个测量组，共 **16 个测量请求**
（组1：4 个不同 key；组2：同 key ×4；组3：4 个节点各一次同 key；组4：首 key ×4），
外加 `phase=='setup'` 的 setup 请求。**同速率的三个配置共享同一请求计划**（同一 key/节点/顺序），
仅传输/缓存配置不同；如 TCP Direct 因接口差异需要不同计划，将在该槽 `config.json` 中单独记录差异。

## 2. 运行前 manifest（冻结字段）

每个 run 的 `smoke-manifest.json` 至少记录：`schema/version`；六个唯一 slot 及其固定顺序；
每槽完整配置 + workload 哈希（`schedule_sha256`）；源码/镜像/挂载工具各自的 SHA-256（**分别记录**，
不合并成“同一提交”）；预期节点集合与请求身份（`request_index`/node/key/value_bytes/value_hash）；
输出目录；监控策略；验收版本。本轮已冻结 schema，生成器见 §5 待办。

## 3. 端到端接入审计（针对 16 节点与 TCP Direct）

现有组件 validator 只覆盖单节点，**不能直接沿用**。逐项核对结果：

1. **节点配置传递**：`render_ruikekkk_scenario.sh` 从宿主环境把 `export RSYNC_*` 写进每节点启动脚本
   （第 19–44 行收集、约 688–694 行导出），`start-node.sh` 在容器内读取。
   **缺口**：VALUE 列表里**没有** `RSYNC_CACHE_POLICY` / `RSYNC_REPLICA_BUDGET_BYTES`
   （`pilot.environment()` 也未设置），所以 NC 槽不会进入有限缓存路径。必须在 renderer + `environment()`
   增加这两个变量，并让每节点 `cache_config` 记录 policy/budget 以便核验。
2. **多 Redis 实例监控**：`start-node.sh` 每节点起一个独立 Redis（`/run/redis/redis.sock`）。
   现有 `run_component_acceptance.sh` 只监控**一个**实例（node1）。16 节点需要每实例独立身份/run ID/基线/
   时间序列/退出状态，**不能把不同实例的计数混成一条序列**。需扩展为逐实例监控与逐实例校验。
3. **TCP Direct 边界**：`pilot.environment()` 对 tcp-direct 设 `RSYNC_SKIP_APP=1`（不跑 Node 应用），
   其证据来自 `tcp_node.py` 的 `tcp-traces/`；历史记录亦确认该条件 `traces/` 为空。
   因此 TCP Direct 槽**不得**要求 `cache_config`/Redis 监控/缓存事件，改用真实 TCP/应用交付证据；
   也不得为其伪造缓存 schema。
4. **请求与交付**：计划请求 ↔ 唯一 canonical completion ↔ 唯一 delivery-audit，实际大小/hash 贯通；
   不同观测层不重复计数——该规则已在 `validate_run.py`（round-4）实现并测试，需扩展到多节点 manifest。
5. **错误传播**：节点退出（`Health` 的 PID/进程监控）、容器 OOM、fatal、`cache_backend_error`、
   监控失败、validator 失败都必须让当前槽与队列立即停止。冻结 runner 已有 `ctl.ProcessFailure`/
   `CorrectnessError`/`queue stopped: <gate>` 机制，需把新 validator 的门槛接入同一 gate。

### 3.1 manifest/method 配置扩展

需要**显式的、版本化的方法配置**：`method ∈ {nc-bypass0, nc-lru1m, tcp-direct}`，
每方法声明必需门槛与允许缺失的证据种类；NC 两档的缓存门槛**不能**通过可选字段绕过。
需为多节点与 TCP Direct 各补正反向 fixture（缺 audit、重复 completion、错 hash、缺监控、TCP Direct 误报缓存）。

## 4. 逐槽验收门槛（冻结）

- 全部计划节点正常启动并使用冻结配置；计划请求 100% 完成，每请求恰好一次正确应用交付；
  实际 value 大小与 hash 正确；无未知请求、无异常重复完成；无 fatal、无 `cache_backend_error`。
- trace/completion/audit/监控/provenance 完整；所有实际 Redis 实例监控覆盖运行区间，
  无自动驱逐、重启、计数回退或超限缺口；归档清单独立核验通过。
- NC 预算 0：`replica_admit==0`、`replica_evict==0`、驻留对象/字节为 0，旁路与 owner 交付仍完成。
- NC 1 MiB：所有节点 `resident_bytes ≤ 1,048,576`；**至少实际触发副本驱逐**；owner 不被驱逐；
  各节点准入/驱逐/最终驻留/事件快照独立闭合；重复 DATA 不重复计费。
  若 1 MiB 槽无驱逐 → 标记“竞争覆盖不足”，不擅自加请求或改预算。
- TCP Direct：从冻结 owner 获取数据、不使用 Node 副本缓存；用真实 TCP/应用证据验收。

## 5. 本轮识别的前置阻断（必须先解决才能启动）

1. **镜像不含 lab 运行时**：已验证 `rsync-router:concurrency-finite-cache-v1-03caf15c`
   **缺少 `/etc/redis/redis.conf.template`**，而 `start-node.sh` 在其缺失时会
   `missing redis config template` 并 `exit 1`。该镜像由 `Dockerfile.redis-acceptance` 构建，
   只 COPY `source/`，不含 lab 运行时。对照 `rsync-router:concurrency-slides-v5` 有该模板。
   → 需用仓库根 `Dockerfile`（`COPY . /app` + `COPY docker/redis.conf.template /etc/redis/redis.conf.template`
   + supervisor + entrypoint）构建**新唯一标签**的 lab 镜像（不改协议/缓存参数）。
2. **finite-cache 根没有 smoke campaign**：`pilot.preflight()` 要求 `campaign.json`（含 fingerprint、
   matrix、timeout_policy、image_id、sources/inputs 哈希）、`backend.json`（plugin id/name、
   `kathara.conf` 与全局配置哈希、`bin/kathara` 哈希）、`inputs/`、`recovery/complete.json`、
   `bin/kathara`；finite-cache 根只有 `source/ build/ acceptance/ versions/`。
   且冻结 runner 的 `slots('smoke')` 是 hosts{16,32,64}×{nc,tcp-direct}（**另一套六槽**），
   与 §1 的矩阵不同，且不支持缓存 policy/budget。
   → 需在独立 smoke 根建立 campaign：复用 slides-v5 的 `inputs/`（只读）、`bin/kathara`、`kathara.conf`，
   新增 finite-cache 的 `campaign.json`/`backend.json`/`recovery/complete.json`，并加入 §3.1 的方法配置与
   §1 的六槽矩阵。
3. **需要 root**：`network_backend` 用 `nsenter` 进入容器 netns，需 CAP_SYS_ADMIN。
   已验证 `sudo -n true` 成功（免密），满足。

## 6. 启动计划（条件满足后执行，不再等待确认）

1. 在独立目录 `smoke/<唯一运行标识>/` 下建立 campaign（不覆盖旧源码/镜像/campaign/raw/证据）。
2. 构建**新唯一标签** lab 镜像（根 `Dockerfile`，`ENABLE_REDIS=ON`，记录 image ID），不覆盖
   `concurrency-finite-cache-v1-03caf15c` 与 `concurrency-slides-v5`。
3. `preflight` → 冻结并生成 run 前 manifest（§2）。
4. 以 `Restart=no` 的受控 unit / 可恢复队列串行执行六槽；每槽结束立即验收并决定是否继续；
   启动后只做一次状态确认，不持续轮询。
5. 任一完整性/后端/交付/监控/验收失败立即停止后续槽；脚本/路径适配问题先保存失败 attempt 再最小修复并新建 attempt。
6. 先写完最终结论与退出码，结束写入进程，再用 `evidence_archive.py` 生成排除自身的清单并独立核验。
7. 回传本地独立目录并汇报 §八 各项。

预计用时：单槽在既有 smoke 设计下约 2–5 分钟（16 主机 lab 启停 + 16 个测量请求 + 归档），
六槽串行约 **15–30 分钟**；不含首次新镜像构建（约 8–15 分钟）。该范围依据既有 smoke 的槽结构与
本轮组件验收实测（拓扑外），未实测本矩阵，故给范围而非精确 ETA。

## 7. 采集与汇报口径

本轮以功能与测量链验收为目的，**不做性能结论**。若后续执行，可整理各槽诊断值
（完成请求数、实际运行时间、吞吐与延迟分位、NC owner/replica 服务/旁路/准入/驱逐/峰值驻留、各节点资源与监控状态），
并必须注明：这是 smoke、单次运行、trace 开启，**不能作为正式性能结果或更新 Slides**。