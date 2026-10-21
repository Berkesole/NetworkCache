# Implementation guide

## Data access and the two state tables

A node stores named values through the `LocalCache` interface. `Node::requestFile` accepts exact string keys. A local cache hit returns the value through the prototype notification hook. For a miss, the node records pending demand and consults SyncTable for candidate neighbors. Keys with candidate routes are grouped by next hop; keys without a route are encoded into a request sent to the node's neighbors.

REQUEST packets carry Bloom-filter summaries rather than the original key list. A receiving node checks its locally known keys against that summary and returns matching data. It records the incoming neighbor's demand in QueryTable, then selects forwarding candidates using overlap between the request summary and neighbor summaries in SyncTable. With no matching candidates, it explores adjacent nodes. It excludes the previous hop and bounds forwarding with a hop limit. A local match does not stop the remaining request from being forwarded.

The two tables answer different questions:

| Component | Question answered | Runtime update |
| --- | --- | --- |
| SyncTable | Which neighbor is a candidate next hop for this name or request summary? | On accepting previously uncached DATA, the node adds the name to the incoming neighbor's summary. |
| QueryTable | Which neighbors or local requester may be waiting for this returned name? | Incoming and locally initiated requests record Bloom-filter demand entries with expiration times. |

A DATA packet carries an actual key and value. If the key is not already cached, the node stores the value, learns a candidate route through the incoming neighbor, and looks up matching pending demand. It forwards data to those waiting neighbors or invokes the local notification hook. DATA already present in the local cache is discarded on this path.

SyncTable information is learned from data arrivals in the active runtime; there is no periodic SYNC packet exchange. Some helper declarations and comments describe additional strategies that are not exercised by `Node`.

## Request aggregation and reuse

`UnionWindow` combines request summaries by outgoing neighbor using bitwise OR. `Node::processOne` drains incoming packets and then flushes these accumulated summaries. Thus aggregation depends on which requests are processed together; it is not an independently configured wall-clock batching interval.

Reuse occurs through both stored values and learned next-hop summaries. QueryTable provides the reverse delivery information for matching pending requests. These mechanisms cooperate within `Node`; the package does not expose them as independent, interchangeable optimization modules.

## Source map

| File | Responsibility |
| --- | --- |
| [node.cpp](../src/node.cpp) | Access, forwarding, learning, return delivery, and aggregation |
| [bloomfilter.hpp](../src/bloomfilter.hpp) | Add, membership query, and OR merge |
| [sync_table.hpp](../src/sync_table.hpp) | Neighbor summaries and routing helpers |
| [query_table.hpp](../src/query_table.hpp) | Demand entries and expiration |
| [union_window.hpp](../src/union_window.hpp) | Per-egress accumulation |
| [packet.hpp](../src/packet.hpp) | REQUEST and DATA representations |
| [packet_codec.cpp](../src/network/packet_codec.cpp) | TCP wire encoding and decoding |
| [tcp_network.cpp](../src/network/tcp_network.cpp) | Socket transport and topology endpoints |
| [mock_network.hpp](../src/network/mock_network.hpp) | In-process transport |
| [cache.hpp](../src/cache/cache.hpp) | Cache interface |

## Snapshot semantics

The filter defaults to 1,024 bits and one `std::hash<std::string>` hash. Matches are approximate; a request can retrieve a key it did not explicitly name. All participating nodes should use a compatible hash implementation. The TCP codec serializes individual filter bits as bytes rather than as a compact 128-byte bitmap.

QueryTable entries expire after 500 ms by default. The node does not provide general end-to-end retries or guaranteed discovery when state is stale or requests outlive that window. SyncTable includes maintenance helpers, but the processing loop does not schedule periodic synchronization or maintenance. The notification hook currently prints received values instead of providing a complete application API.

The in-memory cache has no capacity bound or eviction policy. The aggregation flag used in memory is not included in the current TCP encoding, so behavior that depends on preserving that flag across hops cannot be assumed to be identical between transports. These are properties of this snapshot and matter when interpreting the local checks or extending it to other deployments.
