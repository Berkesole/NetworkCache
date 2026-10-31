#pragma once
#include <queue>
#include <memory>
#include <map>
#include <string>
#include <vector>
#include "../packet.hpp"

// 抽象网络接口
// Abstract network interface
class NetworkInterface {
public:
    virtual ~NetworkInterface() = default;

    // 从网络接收一个数据包
    // Receives a packet from the network
    virtual std::unique_ptr<Packet> receive() = 0;

    // 单播发送数据包到指定目标节点
    // Unicast sends a packet to a specified destination node
    virtual void send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt) = 0;

    // 广播数据包给所有邻居节点
    // Broadcasts a packet to all neighbor nodes
    virtual void broadcast(std::unique_ptr<Packet> pkt) = 0;

    // 配置网络拓扑，通常通过配置文件加载
    // Configures network topology, typically loaded from a configuration file
    virtual void configure_topology(const std::string& config_file_path, uint32_t my_node_id) = 0;

    // 获取当前邻居列表（用于调试和观测）
    // Gets the current neighbor list (for debugging and observation)
    virtual std::vector<uint32_t> get_neighbors() const = 0;
};
