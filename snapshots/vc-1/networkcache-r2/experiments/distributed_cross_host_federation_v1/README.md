# Cross-host Physical Federation v1

Execution recovery on 2026-09-12: Kathara helper commands use `--` before the
container command so `--trace` reaches Python. Coordinator startup failures
publish a failed pair result and stop to the matching peer attempt; mismatched
peer readiness has a bounded timeout. TCP seed001 rep1 resumes at attempt003
on both domains (four-attempt cap unchanged). Completed NC data retains its
original campaign snapshot. The original frozen manifests are archived under
`execution-revisions/`; the new campaign records its compatible NC parent and
execution-only changes. Raw attempts are not rewritten.

This suite is a new, isolated experiment. It does not rename or rewrite any
Federation-v4 raw artifact. In reports, Federation-v4 is the **Single-host
Logical Federation** and this suite is the **Cross-host Physical Federation**.

## Frozen boundary

- Domain A runs on `volcengine` with protocol nodes 01--32, eight leaves, four
  spines, and border 250.
- Domain B runs on `volcengine-1` with protocol nodes 33--64, eight leaves,
  four spines, and border 450.
- The protocol adjacency 01--33 is added at `t0`; there is no physical Kathara
  link between those nodes.
- Physical inter-domain traffic follows protocol node -> leaf -> spine ->
  border -> WireGuard -> border -> spine -> leaf -> protocol node.
- Each WireGuard egress is shaped to 2 ms, 1 Gbit/s, queue 1000, with no
  injected loss/reordering/duplication. WireGuard MTU is 1420 and all
  experiment-facing interfaces use MTU 1400.
- The workload is 512 objects/domain, Zipf alpha 1.1, 64 KiB values, BF1024 h3,
  wire v3, global closed loop. Each POST batch has 128 bidirectional remote and
  32 local-control requests.
- Formal selection is NC/TCP Direct x 10 seeds x 2 repetitions = 40 joint runs.
  Both domains participate in every run.

`campaign.json` is deliberately pre-horizon and cannot be used to start a
formal matrix. `freeze_campaign.py` writes immutable campaign and assignment
artifacts from the three calibration reports. On 2026-09-11 the user accepted
64 POST batches as the fixed observation horizon; strict convergence checks
remain diagnostics, not a formal-start gate. This is not a claim of statistical
equivalence or universally stable latency.

Preflight checks actual `tc` configuration and connectivity. Observed public
network RTT and ping loss are diagnostics, not performance admission thresholds.

## Why the decomposition does not need synchronized clocks

The requester end-to-end interval is measured on its host. Each internal
component subtracts timestamps from only one host. The two DCI directions plus
serialization are then the nonnegative residual:

```text
T_end_to_end
= requester-domain egress
+ DCI roundtrip residual
+ serving-domain discovery
+ provider processing
+ serving-domain return
+ requester-domain delivery
```

The passive border monitor tags packets with the currently active global
closed-loop request. Linux exposes decrypted ingress on the WireGuard device
but not the equivalent clear-text egress to an AF_PACKET socket on that device,
so observer v2 records ingress on `wg-xfed-v1` and local egress on the domain's
border bridge. The request direction classifies each captured stream as
REQUEST or DATA without inspecting or changing the protocol payload.

The analyzer starts from the actual `app_receive` and walks timestamped DATA
receive/forward edges backwards.  For a remote delivery, it resumes from the
first complete DATA stream at the serving border and walks back to the unique
provider. This excludes slower DATA branches that remain in flight after the
application has accepted one copy; the first same-key provider hit is never
used as a shortcut. Header-only reverse-direction control streams are excluded
using the frozen value-size lower bound before the DATA stream is selected. A
missing/ambiguous chain, missing border event, negative
component, or closure error over 1 ms marks that request's decomposition missing.
End-to-end observations remain included. Per-batch and per-period coverage is
reported, and component means are conditional on available decompositions.
Application completion, integrity, isolation and provenance gates still apply.
Cross-host one-way delay is never published.

Observer v3 records TCP sequence/acknowledgment numbers. DATA boundary selection
uses the first contiguous byte coverage through the FIN-defined stream length,
excluding later retransmissions. Missing/gapped sequence evidence is not guessed.
Legacy v2 calibration remains readable; seed003 request1141 has an explicitly
missing decomposition rather than a clipped negative component. Validator v5
keeps the request's original end-to-end latency and reports the missing reason.

## Control flow

The public CLI is:

```bash
python3 experiments/distributed_cross_host_federation_v1/cross_host.py preflight
python3 experiments/distributed_cross_host_federation_v1/cross_host.py deploy --execute
python3 experiments/distributed_cross_host_federation_v1/cross_host.py smoke --execute
python3 experiments/distributed_cross_host_federation_v1/cross_host.py calibrate --execute
python3 experiments/distributed_cross_host_federation_v1/cross_host.py status
python3 experiments/distributed_cross_host_federation_v1/cross_host.py collect
```

`status` is strictly read-only. Execution commands require `--execute` and use
paired `Restart=no` transient units. Raw attempts are append-only. Calibration
and formal slots retain a hard four-attempt budget; the non-evidence smoke has
a ten-attempt diagnostic budget so versioned orchestration fixes never require
deleting failed smoke raw. A recovery attempt floor intentionally ignores older
captured diagnostics after an observer-policy change.

The two generated bootstrap paths are lab-wide dependencies:
`/shared/start-node.sh` and `/shared/start-switch.sh`. Domain runners install
both before Kathara renders or launches a shard; suite-specific helpers remain
under `/shared/cross-host-federation-v1/`.

The four-POST `smoke` may run as soon as both workers pass infrastructure
preflight because it validates topology and control, not the convergence
horizon. `calibrate` still refuses to start until the independent
simple-station 64-POST report exists. Formal `run` also refuses to start until
`frozen_campaign.json` exists.

After collecting calibration attempts, validate and analyze them:

```bash
python3 experiments/distributed_cross_host_federation_v1/collect_and_analyze.py calibration
python3 experiments/distributed_cross_host_federation_v1/freeze_campaign.py \
  --reports derived/calibration/seed001/convergence_report.json \
            derived/calibration/seed002/convergence_report.json \
            derived/calibration/seed003/convergence_report.json \
  --image-id '<matching image id from both workers>' --execute
```

The earlier automatic convergence policy was: if a seed has no candidate at batch 64, run only that seed with
`calibrate --post-batches 96 --seeds N --execute`. If it still has no candidate
at 96, `freeze_campaign.py` blocks automatic horizon selection. The accepted
64-batch decision instead uses `--accepted-post-batches 64 --decision-reason ...`;
its reason and the original diagnostic report hashes enter the frozen campaign.
No 96-batch extension is scheduled under this decision. Calibration attempts are
never eligible for formal selection.

Once 40 formal pairs have been collected:

```bash
python3 experiments/distributed_cross_host_federation_v1/collect_and_analyze.py formal
```

Only a complete 40-slot selection produces `derived/formal_results.json`.
Before that, the collection manifest says `COLLECTING_NO_PARTIAL_STATS` and
contains no partial aggregate.

## Verification

Run the deterministic suite from the repository root:

```bash
python3 experiments/distributed_cross_host_federation_v1/test_cross_host_federation_v1.py
python3 -m py_compile experiments/distributed_cross_host_federation_v1/*.py
bash -n scripts/kathara/render_ruikekkk_scenario.sh
cmake --build build --target rsync-node-dev test_runtime_neighbor -- -j2
./build/test_runtime_neighbor
```

The unit tests cover the 40-slot assignment, horizon decisions, both 45-node
physical shards, absence of a direct 01--33 link, marker parsing, path
confinement, causal-provider selection, missing border evidence, and strict
clock-independent closure.
# PRE 物理隔离修复（2026-09-12）

TCP seed001 attempts003/004 的隔离探针收到真实 `ok, 65536 bytes`，并非 failed-completion
误判。Docker border uplink 默认路由绕过了“尚未安装远端 /32 路由”的旧隔离方式。
现在 attach-border 在启动 helper 之前为全部32个远端地址安装并读回验证 `unreachable /32`；
仅在 t₀ 激活时逐条 replace 为经 DCI gateway 的正常路由。deactivate 同样恢复显式阻断。
严格 isolation 检查未放宽；增加归档 pre-isolation-routes.json 和探针 completion 原文。

新版正式结果根为 `/data/networkcache-cross-host-federation-v1/results-pre-isolation-v2/`，
本地收集为 `collected-pre-isolation-v2/`。旧 `results/` 与 `collected/` 原样保留，旧NC也不
进入新版正式selection：旧物理隔离缺乏保证。新版40个slot全部重新采集，每slot最多4次attempt。
calibration的历史读取保持原目录；新formal验证缓存独立，不能命中旧attempt001缓存。

campaign: `801a58c3620133850a84b3f88237dbb9375642800b72b52966bc0ca395fb2f80`

assignment: `823af620a4cacfe941db0ac4d12e8e6b4a09888ac9864601e3a7daa56bd266a7`

原镜像、协议、workload、64 POST和统计口径不变。27项测试通过；独立Linux网络namespace验证
default可达、unreachable拒绝、replace恢复。simple-station未操作。
