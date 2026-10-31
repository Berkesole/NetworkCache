#pragma once
#include "network_interface.hpp"
#include "topology_parser.hpp"
#include <map>
#include <queue>
#include <vector>
#include <string>
#include <iostream>
#include <optional>
#include <fstream>
#include <sstream>

// 模拟节点上下文
// Mock node context
struct MockNodeContext {
    uint32_t id;
    std::vector<uint32_t> neighbors;
    std::queue<std::unique_ptr<Packet>> inbox;
};

// 模拟网络实现
// Mock network implementation
class MockNetwork : public NetworkInterface {
public:
    // 静态注册表：所有 MockNetwork 实例
    // Static registry: all MockNetwork instances
    inline static std::map<uint32_t, MockNetwork*> all_mock_networks_;

    MockNetwork(uint32_t my_node_id) : my_id_(my_node_id) {
        all_mock_networks_[my_node_id] = this;
    }

    ~MockNetwork() override {
        all_mock_networks_.erase(my_id_);
    }

    std::unique_ptr<Packet> receive() override {
        if (my_context_ && !my_context_->inbox.empty()) {
            auto pkt = std::move(my_context_->inbox.front());
            my_context_->inbox.pop();
            return pkt;
        }
        return nullptr;
    }

    void send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt) override {
        if (my_context_) {
            if (all_mock_networks_.count(dest_node_id)) {
                all_mock_networks_[dest_node_id]->my_context_->inbox.push(std::move(pkt));
            } else {
                std::cerr << "MockNetwork (ID " << my_id_ << "): Error: Destination Node "
                          << dest_node_id << " not found." << std::endl;
            }
        } else {
            std::cerr << "MockNetwork (ID " << my_id_ << "): Error: Not configured." << std::endl;
        }
    }

    void broadcast(std::unique_ptr<Packet> pkt) override {
        if (!my_context_) {
            std::cerr << "MockNetwork (ID " << my_id_ << "): Error: Not configured for broadcast." << std::endl;
            return;
        }

        // 使用多态 clone()，避免类型判断和对象切片
        for (uint32_t neighbor_id : my_context_->neighbors) {
            send_direct(neighbor_id, pkt->clone());
        }
    }

    void configure_topology(const std::string& config_file_path, uint32_t my_node_id) override {
        std::cout << "MockNetwork (ID " << my_id_ << "): Configuring from " << config_file_path << std::endl;

        std::ifstream file(config_file_path);
        if (!file.is_open()) {
            std::cerr << "MockNetwork (ID " << my_id_ << "): Error opening: " << config_file_path << std::endl;
            return;
        }
        std::stringstream buffer;
        buffer << file.rdbuf();
        std::string config_content = buffer.str();

        TopologyParser parser;
        auto result = parser.parse_config(config_content);
        protocol_config_ = result.protocol;

        if (result.topology.count(my_node_id)) {
            const auto& my_config = result.topology.at(my_node_id);
            my_context_ = MockNodeContext{my_config.id, my_config.neighbors, {}};
            std::cout << "MockNetwork (ID " << my_node_id << "): Configured with "
                      << my_context_->neighbors.size() << " neighbors." << std::endl;
        } else {
            std::cerr << "MockNetwork (ID " << my_node_id << "): Warning: Node ID not found in config." << std::endl;
            my_context_.reset();
        }
    }

    const ProtocolConfig& get_protocol_config() const {
        return protocol_config_;
    }

    std::vector<uint32_t> get_neighbors() const override {
        if (my_context_) {
            return my_context_->neighbors;
        }
        return {};
    }

    // 测试辅助方法
    // Test helper methods
    size_t inbox_size() const {
        if (my_context_) {
            return my_context_->inbox.size();
        }
        return 0;
    }

    uint32_t get_id() const {
        return my_id_;
    }

private:
    uint32_t my_id_;
    std::optional<MockNodeContext> my_context_;
    ProtocolConfig protocol_config_;
};
