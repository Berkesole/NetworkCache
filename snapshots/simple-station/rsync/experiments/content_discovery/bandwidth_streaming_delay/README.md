# Bandwidth / Streaming Delay

Measure how object size and bandwidth constraints affect data return after content is discovered.

## Naming Note

The current prototype returns one whole object in a `DATA` packet rather than chunked streaming. For the first paper draft, this experiment may be more accurately described as **object transfer delay under bandwidth constraints**. If chunked DATA is added later, this directory can evolve into a true streaming-delay experiment.

## Question

After a provider is found, how much of end-to-end latency is dominated by data transfer, and how does that change with object size and link bandwidth?

## First Scope

Use TCP/Docker bridge or Kathara once the MockNetwork discovery experiment is stable. Apply bandwidth constraints with `tc` or Kathara link settings.

Suggested first scenario:

- line-3 or line-5 TCP topology
- one requester, one provider
- cold and warm runs

## Independent Variables

- object/value size: 1KB, 16KB, 256KB, 1MB, 16MB
- bandwidth limit: 10Mbps, 100Mbps, 1Gbps
- topology distance: 1 hop, 2 hops, 4 hops
- cache state: cold discovery vs warm hint

## Dependent Metrics

- `provider_hit -> app_receive`
- `request_start -> app_receive`
- bytes returned
- effective goodput
- send/connect errors or timeout rate

## Current Open Decisions

- Whether to add synthetic value generation to `rsync-node-dev` or generate large values in scenario configs.
- Whether to implement chunked DATA before this experiment or keep the first version as whole-object transfer.
- Which environment should be the canonical paper environment: Docker bridge, Kathara, or both.
