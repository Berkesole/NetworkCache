# 容量筛选 pilot v1 — 状态

**阶段**：长度校准完成（2 runs，均 PASS）；容量筛选进行中。

## 筛选进度（截至本次更新）

| 槽（按冻结顺序） | wall(s) | 完成/失败 | 驱逐 | 窗口吞吐 | 结论 |
|---|---:|---:|---:|---:|---|
| 1. lru-16MiB-s001 | 101.3 | 4096/0 | 1763 | 126.56/s | PASS |
| 2. lru-0.25MiB-s002 | 101.6 | 4096/0 | 18346 | 129.61/s | PASS |
| 3. lru-4MiB-s003 | 101.8 | 4096/0 | 6083 | 127.41/s | PASS |
| 4. lru-0.5MiB-s003 | 103.2 | 4096/0 | 14058 | 126.83/s | PASS |

**4/24 完成，全部 PASS**（含离线验收规则中的 correctness gates）。

- 队列以 `setsid nohup` **脱离会话**在远端串行执行，可续跑（已完成的槽按 `result.json` 跳过）；
  状态查询：`python3 /tmp/inspect_results.py <PR> screening`（只读一次）。
- 实际节奏约 **5–8 分钟/槽**（测量路径 ~101 s + 44 容器拆卸与 `kathara lclean` 数分钟），
  剩余 20 槽 ≈ **1.7–2.5 小时**。
- 未修改任何历史 raw/selection/报告/清单；未重建镜像；未操作 worker-a/b。

## 冻结物与哈希

## 冻结物与哈希

| 文件 | SHA-256 |
|---|---|
| `CAPACITY_SCREENING_PLAN.md` | `eab3c54a32acf5d16628feb6e42277f28b53df253134234be4f6252195f63381` |
| `pilot_matrix.json`（`matrix_sha256` 自校验） | `2814ab6217f496be0219581a17aa3a67277f6cf0b8a3cbd28d117524af2ce0c6` |

runner 启动前校验：矩阵 schema/version、自哈希、八档 policy/budget 映射、`hosts=32`、
`object_size=65536`、`catalog_objects=512`、`rate=128`、`model=poisson`、order 长度 24 且无重复、
每 block 恰好覆盖八档、以及 **plan SHA == 实际 `CAPACITY_SCREENING_PLAN.md` 的 SHA**；
任一不符即拒绝启动。Markdown 与 JSON 不一致同样拒绝。

## 确定性与测试

- `pilot_matrix_gen.py --self-test`：11/11（含篡改档位/删槽/错 hosts/错哈希拒绝）
- `pilot_plan.py --self-test`：9/9（到达数组来自冻结生成器、篡改数组/哈希拒绝、矩阵-计划哈希联动）
- `pilot_accept.py --self-test`：9/9（**unlimited / 16 MiB 无驱逐必须 PASS**；同一证据在小 LRU 档要求驱逐时必须 REJECTED；bypass 有驱逐拒绝；快照不一致拒绝；无监控记录 → EVIDENCE_INCOMPLETE）
- `pilot_metrics.py --self-test`：11/11（吞吐分母=测量窗口、排空单列、失败请求保留、p99 样本不足标注、provider 不可归因时不给请求级命中率）
- `pilot_runner.py --self-test`：7/7（容器精确限定）

## 冻结设计摘要

- 32 节点；512 formal 对象 × 64 KiB；每节点 16 owner 对象；owner 只读不占预算。
- 32 setup + **4096** measurement；Poisson 全局速率 **128 req/s**；`arrival_seed = 9172 + seed`；开环释放、无 inflight cap。
- 八档：bypass(0) / 262144 / 524288 / 1048576 / 2097152 / 4194304 / 16777216 / unlimited（独立策略）。
- 校准：seed001 × {0.25 MiB LRU, unlimited}，≤4 runs。
- 筛选：8 档 × seeds 001–003 × 1 rep = **24 slots**，确定性顺序（每 block 覆盖八档，seed 在 block 内混合）。
- 预热/测量按**计划到达时间**切分（初始 25% / 75%），校准后冻结。

## 工时估计

单 run 到达窗口 ≈32 s + 排空 ≤60 s + 启停 60–90 s ≈ 2.5–3.5 min。
校准 ≤4 runs ≈ 12–15 min；筛选 24 runs ≈ 60–85 min；封存/回传/分析 ≈ 15–25 min。
合计约 **1.5–2.5 小时**（依实际启停与验证耗时）。

## 禁止事项确认

未修改 C++/协议/Bloom/LRU/对象大小；未重建或覆盖镜像；未触碰历史 raw/selection/报告/清单；
未操作 worker-a/b；未启动正式矩阵；未更新 Slides；`vldb/VLDB-networkcache` 子模块的用户改动
**保留且不提交**。