# worker-c v8: connection reuse ablation

Same new image, default-off per-neighbor connection reuse. Only TcpNetwork
.cpp/.hpp change; Node, Bloom, QueryTable, UnionWindow, cache, wire codec and
application code remain byte-identical. New image derives from verified v4.
No overwrite of old branches, raw or image. Only worker-c, no local repo edits.

Reuse is enabled by control/tcp-connection-reuse.enabled before startup.
All32 node logs must verify mode. Keep FIFO, one frame/peer/IO tick, IO sleep1ms,
Nagle defaults, no application concurrency cap. Partial/ambiguous send errors
never replay a frame; detected closed idle sockets may reconnect before writing.
Idle sockets do not count as pending messages and are closed on cleanup.

New transport tests cover fragmented/coalesced framing,64KiB, FIFO IDs/values,
two peers, actual connection count, idle quiescence, closed-peer reconnect,
partial-send error no replay and cleanup. Existing runtime/controller tests run.
Two full-network correctness smokes (fresh/reuse) precede four load runs:
Poisson fresh, burst reuse, Poisson reuse, burst fresh.
Fixed25ms node loop,30s query TTL,1.5s fallback,4096 requests,seed001,
arrival seed9173,128rps,32nodes,512objects,Zipf1.1,64KiB,2ms/1G links.
No TCP Direct performance claim; no change to forwarding/cache/fallback policy.

Independent cold labs, raw append-only, Restart=no, one bounded startup check.
Load timeout is retained; correctness/transport/process/cleanup error stops.
Only pre-request infrastructure fault retries once. No ongoing polling.
CLI: python3 experiments/concurrency_pilot_v8/pilot.py preflight|status|analyze;
pilot --execute runs gated queue. Root /data/networkcache-concurrency-pilot-v8;
branch codex/single-cluster-concurrency-pilot-v8.

derived/transport_comparison.md/json and transport_gate.json report completion,
early/late latency, tail, connect/reuse/error counters, queue residence, frames,
CPU/memory and all wall-clock stages. V7 verifies inputs only, not cross-image
speedups. Reuse also retains TCP connection state; no handshake-only causal claim.
Single seed, no formal CI, convergence gate, multiseed expansion, Slides or PDF.
