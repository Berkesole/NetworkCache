#pragma once

#include "network_interface.hpp"

#include <cstdint>
#include <cstddef>
#include <map>
#include <mutex>
#include <optional>
#include <queue>
#include <string>
#include <vector>
#include <atomic>
#include <thread>

struct TcpEndpoint {
    std::string host = "127.0.0.1";
    uint16_t port = 0;
};

class TcpNetwork : public NetworkInterface {
public:
    explicit TcpNetwork(uint32_t my_node_id);
    ~TcpNetwork() override;

    std::unique_ptr<Packet> receive() override;
    void send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt) override;
    void broadcast(std::unique_ptr<Packet> pkt) override;
    void configure_topology(const std::string& config_file_path, uint32_t my_node_id) override;
    std::vector<uint32_t> get_neighbors() const override;

    bool is_configured() const;
    std::optional<TcpEndpoint> get_endpoint(uint32_t node_id) const;
    std::optional<std::string> get_endpoint_source(uint32_t node_id) const;
    void set_endpoint_overrides(std::map<uint32_t, TcpEndpoint> overrides);
    // Atomically install one locally configured peer at runtime.  This is a
    // bootstrap primitive, not a membership-discovery protocol.
    bool add_runtime_neighbor(uint32_t node_id, const TcpEndpoint& endpoint,
                              std::string& error);
    static bool merge_runtime_neighbor(uint32_t self_id, bool configured,
                                       uint32_t node_id, const TcpEndpoint& endpoint,
                                       std::map<uint32_t, TcpEndpoint>& endpoints,
                                       std::map<uint32_t, std::string>& endpoint_sources,
                                       std::vector<uint32_t>& neighbors,
                                       std::string& error);

private:
    struct EncodedFrame {
        std::vector<uint8_t> payload;
    };

    struct InboundConnection {
        int fd = -1;
        std::vector<uint8_t> buffer;
        std::string peer_label;
    };

    void close_runtime();
    bool open_listener(const TcpEndpoint& endpoint, const std::string& endpoint_source);
    void accept_pending_connections();
    void read_pending_frames();
    void read_from_connection(InboundConnection& connection, bool& keep_connection);
    void extract_frames(InboundConnection& connection);
    void enqueue_frame(EncodedFrame frame);
    void start_io_thread();
    void stop_io_thread();
    void io_loop();
    std::optional<TcpEndpoint> resolve_endpoint(uint32_t node_id) const;
    static bool set_non_blocking(int fd);
    static bool send_all(int fd, const std::vector<uint8_t>& buffer);
    static TcpEndpoint make_default_endpoint(uint32_t node_id);

    uint32_t my_node_id_;
    int listener_fd_ = -1;
    bool configured_ = false;
    std::vector<uint32_t> neighbors_;
    std::map<uint32_t, TcpEndpoint> endpoints_;
    std::map<uint32_t, std::string> endpoint_sources_;
    std::map<uint32_t, TcpEndpoint> endpoint_overrides_;
    std::queue<EncodedFrame> inbound_frames_;
    std::vector<InboundConnection> inbound_connections_;
    std::atomic<bool> io_stop_{false};
    std::thread io_thread_;

    mutable std::mutex mutex_;
};
