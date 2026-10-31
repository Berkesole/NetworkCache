# Delay-Find-Content Topology Plan

This directory records the paper-facing topology decisions for the
`delay_find_content` experiments. The current target is Kua-DC v3: host-only
rsync nodes over a pure spine/leaf forwarding fabric.

## Current Decision

Kua-DC v3 replaces the earlier protocol-fabric model:

- `kua-dc-v3-16`: 16 rsync host/storage nodes, 4 leaf switches, and 4 spine
  switches.
- `kua-dc-v3-32`: 32 rsync host/storage nodes, 8 leaf switches, and 4 spine
  switches.

The 16/32 count is host count only. Leaf and spine devices are Kathara
forwarding devices and are not counted as rsync protocol nodes.

Leaf/spine devices:

- configure interfaces, static routes, `ip_forward=1`, and optional `tc netem`
  delay;
- do not run Redis;
- do not run `rsync-node-dev`;
- never act as requester, provider, cache, aggregate point, or SyncTable node.

Rsync protocol neighbors exist only among host nodes. A host-to-host TCP
connection may physically traverse leaf/spine devices, but those devices only
forward IP packets. In v3, `provider_hit_hop` is therefore an rsync overlay hop,
while physical fabric delay is represented by Kathara link delay.

## Kua Reference

The Kua paper evaluates a datacenter-like topology:

- storage nodes connect to rack switches;
- rack switches connect through core switches;
- links use 2 ms delay, no bandwidth limits, and no loss.

For this project, the useful mapping is the datacenter fabric shape rather than
an exact NDN runtime reproduction. Kua's switches run an NDN forwarding plane;
our v3 switches are deliberately pure forwarding devices. The rsync protocol
continues to run only on hosts.

## Kathara Mapping

Kathara provides devices and collision domains. For v3, the renderer separates
two graphs:

- protocol graph: generated scenario YAML with rsync hosts and host-to-host
  overlay neighbors;
- physical graph: generated Kathara leaf/spine devices and host-switch links.

The renderer writes host startup files that run `start-node.sh`, and switch
startup files that run `start-switch.sh`. Switch logs are startup/network logs
only and are not parsed as protocol events.

For v3 hosts, the stable rsync service IP (`10.90.0.<host>`) is assigned to the
host-facing Ethernet interface as a /32 address. This lets host-to-host TCP
sessions arrive through the forwarding fabric while keeping the protocol
endpoint stable across Kathara runs. Legacy scenarios keep the older loopback
assignment.

## Docker Mirror

Docker bridge cannot model the physical leaf/spine fabric in the same way.
The v3 Docker mirror therefore contains only the rsync hosts and the same
host-level protocol overlay. Its metadata marks:

```text
docker_mirror_model=host_overlay_no_physical_fabric
```

Docker remains useful for checking the protocol pipeline and analyzer on the
same host overlay. It should not be used as a numerical substitute for Kathara
physical fabric latency.

## Final Shapes

`kua-dc-v3-16`:

- Rsync hosts: `1-16`.
- Leaves: `101-104`.
- Spines: `201-204`.
- Host attachment: 4 hosts per leaf.
- Physical links: 16 host-leaf links + 16 leaf-spine links = 32.

`kua-dc-v3-32`:

- Rsync hosts: `1-32`.
- Leaves: `101-108`.
- Spines: `201-204`.
- Host attachment: 4 hosts per leaf.
- Physical links: 32 host-leaf links + 32 leaf-spine links = 64.

Host overlay:

- each rack selects 1 primary gateway host;
- ordinary hosts connect to the local gateway host in the rsync overlay;
- gateway hosts connect to all local hosts and to the previous/next rack
  gateways, forming a bounded-degree ring;
- provider/requester nodes are selected only from host nodes.

Both topologies use:

- `protocol.max_hop_count: 8`;
- Kathara link delay defaulting to 2 ms through Linux `tc netem`;
- no bandwidth limit, no loss, no eviction, and no replica factor sweep.

## Historical Models

`kua-dc-v1` and `kua-dc-v2` used an older protocol-fabric model where rack/core
fabric nodes also ran `rsync-node-dev` and could cache DATA. Those results are
kept as mechanism validation and diagnostic history, but they are not the
current topology target.

Binary-tree, grid, ring, and smaller spine-leaf variants can remain engineering
smoke tests. They are not the next paper-facing delay-find-content topology
set.

## Acceptance Targets

- `kua-dc-v3-16` render reports `host_count=16`, `leaf_count=4`,
  `spine_count=4`, and `physical_edge_count=32`.
- `kua-dc-v3-32` render reports `host_count=32`, `leaf_count=8`,
  `spine_count=4`, and `physical_edge_count=64`.
- `kathara lstart -d kathara/ruikekkk --print` passes after rendering each v3
  scenario.
- Switch startup files call `start-switch.sh`, not `start-node.sh`.
- Switches show `ip_forward=1` and 2 ms netem delay when
  `KATHARA_LINK_DELAY_MS=2`.
- Kathara single-key smoke for 16/32 produces cold and path-materialization
  rows with complete latency.
- Kathara Zipf/Uniform smoke must reach measurement completion ratio 1.0 before
  formal n30 is started.
