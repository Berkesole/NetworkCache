// tests/mesh.cpp
// 8x8 mesh 拓扑下的协议 demo（MockNetwork + InMemoryCache）
//
// 构建：
//   cmake --build build --target test_mesh_demo -- -j
// 运行：
//   ./build/test_mesh_demo

#include "node.hpp"
#include "network/mock_network.hpp"

#include <chrono>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

int main() {
    constexpr uint32_t kRows        = 8;
    constexpr uint32_t kCols        = 8;
    constexpr uint32_t kTotalNodes  = kRows * kCols; // 64
    const std::string topo_path     = "config/8x8-mesh-topology.yaml";

    std::cout << "========================================\n";
    std::cout << "  8x8 Mesh 拓扑 demo（MockNetwork）\n";
    std::cout << "  拓扑文件: " << topo_path << "\n";
    std::cout << "========================================\n\n";

    // 创建 64 个节点，每个节点注入一个已配置拓扑的 MockNetwork
    std::vector<std::unique_ptr<Node>> nodes;
    nodes.reserve(kTotalNodes);

    for (uint32_t id = 1; id <= kTotalNodes; ++id) {
        auto net = std::make_unique<MockNetwork>(id);
        net->configure_topology(topo_path, id);

        nodes.push_back(std::make_unique<Node>(id, std::move(net)));
    }

    // 选择左上角节点 1 作为发起者，右下角节点 64 持有数据
    const std::string key   = "mesh_key_a";
    const std::string value = "MESH_VALUE_A";

    std::cout << "[MeshDemo] Node 64 addLocalData(\"" << key << "\", \"" << value << "\")\n";
    nodes[63]->addLocalData(key, value); // index 63 => id 64

    std::cout << "[MeshDemo] Node 1 requestFile({\"" << key << "\"})\n";
    nodes[0]->requestFile({key});        // index 0 => id 1

    // 简单轮询驱动：让所有节点轮流处理收包队列
    const int rounds = 200;
    for (int round = 0; round < rounds; ++round) {
        for (auto& n : nodes) {
            n->processOne();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    std::cout << "[MeshDemo] Finished 8x8 mesh demo run\n";
    return 0;
}

