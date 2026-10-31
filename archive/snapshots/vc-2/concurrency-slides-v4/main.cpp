#include "src/node.hpp"
#include "src/network/mock_network.hpp"
#include "src/network/topology_parser.hpp"
#include <thread>
#include <chrono>
#include <fstream>
#include <sstream>
#include <iostream>
#include <vector>
#include <memory>

int main(int argc, char** argv) {
    const std::string topo_path = (argc >= 2) ? argv[1] : "config/3-node-topology.yaml";

    // Parse once: topology + protocol + scenario.
    std::ifstream file(topo_path);
    if (!file.is_open()) {
        std::cerr << "main: cannot open " << topo_path << std::endl;
        return 1;
    }
    std::stringstream buf; buf << file.rdbuf();
    TopologyParser parser;
    auto parsed = parser.parse_config(buf.str());

    if (parsed.topology.empty()) {
        std::cerr << "main: no nodes in topology" << std::endl;
        return 1;
    }

    std::cout << "[main] topology=" << topo_path
              << " nodes=" << parsed.topology.size()
              << " max_hop_count=" << parsed.protocol.max_hop_count
              << " ticks=" << parsed.scenario.ticks << std::endl;

    // Build Node config from parsed protocol params.
    Node::Config node_config;
    node_config.max_hop_count = parsed.protocol.max_hop_count;

    // Create one Node per parsed entry. Each owns its own MockNetwork
    // (which still re-parses the YAML, but that's fine for the demo).
    std::vector<std::unique_ptr<Node>> nodes;
    nodes.reserve(parsed.topology.size());
    for (const auto& [id, _cfg] : parsed.topology) {
        auto net = std::make_unique<MockNetwork>(id);
        net->configure_topology(topo_path, id);
        nodes.push_back(std::make_unique<Node>(id, std::move(net), nullptr, node_config));
    }

    // Helper: find node by id (linear scan; fine for demo sizes).
    auto find_node = [&](uint32_t id) -> Node* {
        for (auto& n : nodes) {
            // We don't have a getter on Node; map by parse order.
            // Simpler: use parallel vector indexed by parsed order.
            (void)n; break;
        }
        return nullptr;
    };
    (void)find_node;

    // Build id → Node* map.
    std::map<uint32_t, Node*> node_by_id;
    {
        size_t i = 0;
        for (const auto& [id, _cfg] : parsed.topology) {
            node_by_id[id] = nodes[i++].get();
        }
    }

    // Apply scenario: data placement.
    for (const auto& d : parsed.scenario.data) {
        auto it = node_by_id.find(d.node_id);
        if (it == node_by_id.end()) {
            std::cerr << "[main] data: unknown node " << d.node_id << std::endl;
            continue;
        }
        it->second->addLocalData(d.key, d.value);
        std::cout << "[main] place data at Node " << d.node_id
                  << ": " << d.key << "=" << d.value << std::endl;
    }

    // Tick loop. Each processOne() drains its inbox and flushes union windows.
    // REQUESTs are fired at their scheduled `at_tick` (default 0).
    for (uint32_t tick = 0; tick < parsed.scenario.ticks; ++tick) {
        for (const auto& r : parsed.scenario.requests) {
            if (r.at_tick != tick) continue;
            auto it = node_by_id.find(r.node_id);
            if (it == node_by_id.end()) {
                std::cerr << "[main] request: unknown node " << r.node_id << std::endl;
                continue;
            }
            std::cout << "[main] tick=" << tick << " Node " << r.node_id << " requests: ";
            for (const auto& k : r.keys) std::cout << k << " ";
            std::cout << std::endl;
            it->second->requestFile(r.keys);
        }
        for (auto& n : nodes) n->processOne();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    std::cout << "[main] done." << std::endl;
    return 0;
}
