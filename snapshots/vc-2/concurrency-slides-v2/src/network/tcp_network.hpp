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
#include <deque>
#include <fstream>

struct TcpEndpoint {
    std::string host = "127.0.0.1";
    uint16_t port = 0;
};

class TcpNetwork : public NetworkInterface {
public:
    explicit TcpNetwork(uint32_t my_node_id, bool reuse_connections = false);
    ~TcpNetwork() override;

    std::unique_ptr<Packet> receive() override;
    void begin_receive_batch() override;
    bool has_pending_io() const override;
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
    friend struct TcpNetworkTestAccess;
    struct EncodedFrame {
        std::vector<uint8_t> payload;
    };

    struct InboundConnection {
        int fd = -1;
        std::vector<uint8_t> buffer;
        std::string peer_label;
    };

    struct OutboundFrame {
        uint32_t destination = 0;
        TcpEndpoint endpoint;
        PacketType type = PacketType::REQUEST;
        std::vector<uint8_t> bytes;
        std::size_t offset = 0;
        int fd = -1;
        bool connecting = false;
        uint64_t frame_id = 0, request_id = 0, enqueued_ns = 0;
        uint64_t send_calls = 0, would_block_calls = 0, admitted_depth = 0;
    };
    static uint64_t lane_key(uint32_t peer, PacketType type) {
        return (static_cast<uint64_t>(peer) << 1) | (type == PacketType::DATA ? 1ULL : 0ULL);
    }
    void pump_outbound();
    std::vector<OutboundFrame> outbound_admissions_; // mutex protected
    std::map<uint64_t, std::deque<OutboundFrame>> outbound_peers_; // peer + control/DATA lane, IO thread only
    std::atomic<std::size_t> outbound_count_{0};
    bool reuse_connections_ = false;
    std::map<uint64_t,int> idle_outbound_; // peer + lane; idle sockets are not pending messages
    std::atomic<std::size_t> connection_attempts_{0}, connection_reuses_{0}, connection_errors_{0};
    std::atomic<uint64_t> frame_sequence_{0};
    std::ofstream transport_trace_;
    void record_transport_frame(const OutboundFrame& frame, int error);

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
    std::optional<std::size_t> receive_batch_remaining_;
    std::vector<InboundConnection> inbound_connections_;
    std::atomic<bool> io_stop_{false};
    std::thread io_thread_;

    mutable std::mutex mutex_;
};
