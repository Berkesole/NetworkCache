#include "tcp_network.hpp"

#include "packet_codec.hpp"
#include "topology_parser.hpp"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <chrono>
#include <thread>

namespace {

std::string read_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        throw std::runtime_error("TcpNetwork: unable to open topology file: " + path);
    }

    std::stringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

void close_fd(int& fd) {
    if (fd >= 0) {
        ::close(fd);
        fd = -1;
    }
}

std::string format_endpoint(const TcpEndpoint& endpoint) {
    std::ostringstream stream;
    stream << endpoint.host << ":" << endpoint.port;
    return stream.str();
}

std::string describe_missing_topology_fields(bool missing_host, bool missing_port) {
    if (missing_host && missing_port) {
        return "host and port";
    }
    if (missing_host) {
        return "host";
    }
    return "port";
}

TcpEndpoint make_legacy_default_endpoint(uint32_t node_id) {
    return TcpEndpoint{
        "127.0.0.1",
        static_cast<uint16_t>(40000U + (node_id % 10000U))
    };
}

struct ResolvedTcpEndpoint {
    TcpEndpoint endpoint;
    std::string source;
    std::optional<std::string> warning;
};

struct ResolvedTopologyEndpoints {
    std::map<uint32_t, TcpEndpoint> endpoints;
    std::map<uint32_t, std::string> sources;
    std::vector<std::string> warnings;
};

ResolvedTcpEndpoint resolve_topology_endpoint(
    uint32_t node_id,
    const ParsedNodeConfig& config,
    const std::optional<TcpEndpoint>& override_endpoint) {
    if (override_endpoint.has_value()) {
        return ResolvedTcpEndpoint{
            *override_endpoint,
            "env/CLI override",
            std::nullopt
        };
    }

    const bool has_host = config.host.has_value() && !config.host->empty();
    const bool has_port = config.port.has_value();
    if (has_host && has_port) {
        return ResolvedTcpEndpoint{
            TcpEndpoint{*config.host, *config.port},
            "topology/config",
            std::nullopt
        };
    }

    TcpEndpoint endpoint = make_legacy_default_endpoint(node_id);
    if (has_host) {
        endpoint.host = *config.host;
    }
    if (has_port) {
        endpoint.port = *config.port;
    }

    const std::string missing_fields =
        describe_missing_topology_fields(!has_host, !has_port);
    const std::string source =
        "legacy default fallback (topology/config missing " + missing_fields + ")";
    const std::string warning =
        "Node " + std::to_string(node_id) + " is missing " + missing_fields +
        " in topology/config; using legacy default fallback " +
        format_endpoint(endpoint);
    return ResolvedTcpEndpoint{endpoint, source, warning};
}

ResolvedTopologyEndpoints resolve_topology_endpoints(
    const std::map<uint32_t, ParsedNodeConfig>& topology,
    const std::map<uint32_t, TcpEndpoint>& runtime_overrides) {
    ResolvedTopologyEndpoints resolved;

    for (const auto& [node_id, config] : topology) {
        const auto override_it = runtime_overrides.find(node_id);
        const std::optional<TcpEndpoint> override_endpoint =
            override_it == runtime_overrides.end()
                ? std::nullopt
                : std::make_optional(override_it->second);

        const ResolvedTcpEndpoint selection =
            resolve_topology_endpoint(node_id, config, override_endpoint);
        resolved.endpoints[node_id] = selection.endpoint;
        resolved.sources[node_id] = selection.source;
        if (selection.warning.has_value()) {
            resolved.warnings.push_back(*selection.warning);
        }
    }

    for (const auto& [node_id, endpoint] : runtime_overrides) {
        if (topology.count(node_id) != 0) {
            continue;
        }

        resolved.warnings.push_back(
            "ignoring env/CLI endpoint override for unknown Node " +
            std::to_string(node_id) + " -> " + format_endpoint(endpoint));
    }

    return resolved;
}

bool is_would_block(int error_code) {
    return error_code == EAGAIN || error_code == EWOULDBLOCK;
}

const char* packet_type_to_string(PacketType type) {
    switch (type) {
        case PacketType::REQUEST:
            return "REQUEST";
        case PacketType::DATA:
            return "DATA";
        default:
            return "UNKNOWN";
    }
}

std::string format_packet_destination(const Packet& packet) {
    if (packet.type == PacketType::REQUEST &&
        packet.dest_node_id == kBroadcastNodeId) {
        return "broadcast";
    }

    return "Node " + std::to_string(packet.dest_node_id);
}

std::string peer_label_from_address(const sockaddr_in& address) {
    char host_buffer[INET_ADDRSTRLEN] = {0};
    const char* host = ::inet_ntop(AF_INET, &address.sin_addr, host_buffer,
                                   sizeof(host_buffer));
    if (host == nullptr) {
        host = "unknown";
    }

    std::ostringstream stream;
    stream << host << ":" << ntohs(address.sin_port);
    return stream.str();
}

uint32_t decode_frame_length(const std::vector<uint8_t>& buffer) {
    return (static_cast<uint32_t>(buffer[0]) << 24) |
           (static_cast<uint32_t>(buffer[1]) << 16) |
           (static_cast<uint32_t>(buffer[2]) << 8) |
           static_cast<uint32_t>(buffer[3]);
}

std::vector<sockaddr_in> resolve_ipv4_candidates(const TcpEndpoint& endpoint,
                                                 bool passive,
                                                 std::string& error) {
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = passive ? AI_PASSIVE : 0;

    const std::string service = std::to_string(endpoint.port);
    const char* host = endpoint.host.empty() ? nullptr : endpoint.host.c_str();

    addrinfo* raw_results = nullptr;
    const int rc = ::getaddrinfo(host, service.c_str(), &hints, &raw_results);
    if (rc != 0) {
        error = ::gai_strerror(rc);
        return {};
    }

    std::vector<sockaddr_in> candidates;
    for (addrinfo* current = raw_results; current != nullptr; current = current->ai_next) {
        if (current->ai_addr == nullptr ||
            current->ai_addrlen != sizeof(sockaddr_in)) {
            continue;
        }

        sockaddr_in address{};
        std::memcpy(&address, current->ai_addr, sizeof(address));
        candidates.push_back(address);
    }

    ::freeaddrinfo(raw_results);

    if (candidates.empty()) {
        error = "no IPv4 address candidates";
    }

    return candidates;
}

}  // namespace

TcpNetwork::TcpNetwork(uint32_t my_node_id)
    : my_node_id_(my_node_id) {}

TcpNetwork::~TcpNetwork() {
    close_runtime();
}

std::unique_ptr<Packet> TcpNetwork::receive() {
    EncodedFrame frame;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (inbound_frames_.empty()) {
            return nullptr;
        }

        frame = std::move(inbound_frames_.front());
        inbound_frames_.pop();
    }

    try {
        auto packet = PacketCodec::decode(frame.payload);
        std::cout << "TcpNetwork (ID " << my_node_id_
                  << "): received " << packet_type_to_string(packet->type)
                  << " packet from Node " << packet->src_node_id
                  << " to " << format_packet_destination(*packet)
                  << " over TCP (" << frame.payload.size()
                  << " payload bytes)\n";
        return packet;
    } catch (const std::exception& ex) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to decode TCP frame: "
                  << ex.what() << "\n";
        return nullptr;
    }
}

void TcpNetwork::send_direct(uint32_t dest_node_id, std::unique_ptr<Packet> pkt) {
    if (!pkt) {
        return;
    }

    if (!configured_) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): send_direct called before configure_topology()\n";
        return;
    }

    const auto endpoint = resolve_endpoint(dest_node_id);
    if (!endpoint.has_value()) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): destination Node " << dest_node_id
                  << " is missing from topology\n";
        return;
    }

    // Stamp the egress node onto the packet header before serialization so
    // the on-wire destination matches the actual next hop for unicast sends.
    pkt->dest_node_id = dest_node_id;

    const PacketType packet_type = pkt->type;

    std::vector<uint8_t> payload;
    try {
        payload = PacketCodec::encode(*pkt);
    } catch (const std::exception& ex) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to encode packet for Node "
                  << dest_node_id << ": " << ex.what() << "\n";
        return;
    }

    if (payload.size() > std::numeric_limits<uint32_t>::max()) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): encoded packet for Node " << dest_node_id
                  << " exceeds the TCP frame size limit\n";
        return;
    }

    std::string resolve_error;
    const auto candidates = resolve_ipv4_candidates(*endpoint, false, resolve_error);
    if (candidates.empty()) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to resolve destination host for Node "
                  << dest_node_id << " (" << endpoint->host << ":"
                  << endpoint->port << "): " << resolve_error << "\n";
        return;
    }

    int socket_fd = -1;
    int last_errno = 0;
    for (const auto& address : candidates) {
        socket_fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (socket_fd < 0) {
            last_errno = errno;
            continue;
        }

        if (::connect(socket_fd, reinterpret_cast<const sockaddr*>(&address),
                      sizeof(address)) == 0) {
            break;
        }

        last_errno = errno;
        close_fd(socket_fd);
    }

    if (socket_fd < 0) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to connect to Node " << dest_node_id
                  << " at " << endpoint->host << ":" << endpoint->port
                  << ": " << std::strerror(last_errno) << "\n";
        return;
    }

    std::vector<uint8_t> wire_frame(sizeof(uint32_t) + payload.size());
    const auto frame_size = static_cast<uint32_t>(payload.size());
    wire_frame[0] = static_cast<uint8_t>((frame_size >> 24) & 0xFF);
    wire_frame[1] = static_cast<uint8_t>((frame_size >> 16) & 0xFF);
    wire_frame[2] = static_cast<uint8_t>((frame_size >> 8) & 0xFF);
    wire_frame[3] = static_cast<uint8_t>(frame_size & 0xFF);
    std::copy(payload.begin(), payload.end(), wire_frame.begin() + sizeof(uint32_t));

    if (!send_all(socket_fd, wire_frame)) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to send frame to Node " << dest_node_id
                  << " at " << endpoint->host << ":" << endpoint->port
                  << ": " << std::strerror(errno) << "\n";
        close_fd(socket_fd);
        return;
    }

    std::cout << "TcpNetwork (ID " << my_node_id_
              << "): sent " << packet_type_to_string(packet_type)
              << " packet to Node " << dest_node_id
              << " over TCP at " << endpoint->host << ":" << endpoint->port
              << " (" << payload.size() << " payload bytes)\n";
    close_fd(socket_fd);
}

void TcpNetwork::broadcast(std::unique_ptr<Packet> pkt) {
    if (!pkt) {
        return;
    }

    const auto neighbors = get_neighbors();
    for (size_t i = 0; i < neighbors.size(); ++i) {
        if (i + 1 == neighbors.size()) {
            send_direct(neighbors[i], std::move(pkt));
        } else {
            send_direct(neighbors[i], pkt->clone());
        }
    }
}

void TcpNetwork::configure_topology(const std::string& config_file_path, uint32_t my_node_id) {
    std::string config_content;
    try {
        config_content = read_file(config_file_path);
    } catch (const std::exception& ex) {
        close_runtime();
        std::cerr << ex.what() << "\n";
        return;
    }

    TopologyParser parser;
    const auto topology = parser.parse(config_content);
    if (topology.empty()) {
        close_runtime();
        std::cerr << "TcpNetwork (ID " << my_node_id
                  << "): topology/config path produced no nodes: "
                  << config_file_path << "\n";
        return;
    }

    std::map<uint32_t, TcpEndpoint> runtime_overrides;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        runtime_overrides = endpoint_overrides_;
    }

    ResolvedTopologyEndpoints resolved =
        resolve_topology_endpoints(topology, runtime_overrides);
    for (const auto& warning : resolved.warnings) {
        std::cerr << "TcpNetwork (ID " << my_node_id
                  << "): " << warning << "\n";
    }

    const auto it = topology.find(my_node_id);
    if (it == topology.end()) {
        close_runtime();
        std::cerr << "TcpNetwork (ID " << my_node_id
                  << "): node ID " << my_node_id
                  << " is not defined in topology/config path: "
                  << config_file_path << "\n";
        return;
    }

    const auto endpoint_it = resolved.endpoints.find(my_node_id);
    if (endpoint_it == resolved.endpoints.end()) {
        close_runtime();
        std::cerr << "TcpNetwork (ID " << my_node_id
                  << "): missing endpoint for Node ID "
                  << my_node_id << "\n";
        return;
    }
    const TcpEndpoint self_endpoint = endpoint_it->second;
    const std::string self_source = resolved.sources[my_node_id];

    close_runtime();

    my_node_id_ = my_node_id;
    if (!open_listener(self_endpoint, self_source)) {
        std::lock_guard<std::mutex> lock(mutex_);
        endpoints_ = std::move(resolved.endpoints);
        endpoint_sources_ = std::move(resolved.sources);
        neighbors_ = it->second.neighbors;
        configured_ = false;
        return;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        endpoints_ = std::move(resolved.endpoints);
        endpoint_sources_ = std::move(resolved.sources);
        neighbors_ = it->second.neighbors;
        configured_ = true;
    }
    start_io_thread();

    std::cout << "TcpNetwork (ID " << my_node_id_
              << "): listening on " << self_endpoint.host
              << ":" << self_endpoint.port
              << " (source: " << self_source << ")"
              << " with " << it->second.neighbors.size()
              << " neighbor(s)\n";
}

std::vector<uint32_t> TcpNetwork::get_neighbors() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return neighbors_;
}

bool TcpNetwork::is_configured() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return configured_;
}

std::optional<TcpEndpoint> TcpNetwork::get_endpoint(uint32_t node_id) const {
    return resolve_endpoint(node_id);
}

std::optional<std::string> TcpNetwork::get_endpoint_source(uint32_t node_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = endpoint_sources_.find(node_id);
    if (it == endpoint_sources_.end()) {
        return std::nullopt;
    }

    return it->second;
}

void TcpNetwork::set_endpoint_overrides(std::map<uint32_t, TcpEndpoint> overrides) {
    std::lock_guard<std::mutex> lock(mutex_);
    endpoint_overrides_ = std::move(overrides);
}

bool TcpNetwork::add_runtime_neighbor(uint32_t node_id, const TcpEndpoint& endpoint,
                                      std::string& error) {
    std::lock_guard<std::mutex> lock(mutex_);
    return merge_runtime_neighbor(my_node_id_, configured_, node_id, endpoint,
                                  endpoints_, endpoint_sources_, neighbors_, error);
}

bool TcpNetwork::merge_runtime_neighbor(
    uint32_t self_id, bool configured, uint32_t node_id, const TcpEndpoint& endpoint,
    std::map<uint32_t, TcpEndpoint>& endpoints,
    std::map<uint32_t, std::string>& endpoint_sources,
    std::vector<uint32_t>& neighbors, std::string& error) {
    if (!configured) {
        error = "network is not configured";
        return false;
    }
    if (node_id == 0 || node_id == self_id) {
        error = "runtime neighbor id must be nonzero and different from self";
        return false;
    }
    if (endpoint.host.empty() || endpoint.port == 0) {
        error = "runtime neighbor endpoint is invalid";
        return false;
    }
    const auto existing = endpoints.find(node_id);
    if (existing != endpoints.end() &&
        (existing->second.host != endpoint.host || existing->second.port != endpoint.port)) {
        error = "runtime neighbor endpoint conflicts with existing endpoint";
        return false;
    }
    if (existing == endpoints.end()) {
        endpoints[node_id] = endpoint;
        endpoint_sources[node_id] = "runtime-local-bootstrap";
    }
    if (std::find(neighbors.begin(), neighbors.end(), node_id) == neighbors.end()) {
        neighbors.push_back(node_id);
        std::sort(neighbors.begin(), neighbors.end());
    }
    error.clear();
    return true;
}

void TcpNetwork::close_runtime() {
    stop_io_thread();
    close_fd(listener_fd_);
    for (auto& connection : inbound_connections_) {
        close_fd(connection.fd);
    }
    inbound_connections_.clear();

    std::lock_guard<std::mutex> lock(mutex_);
    while (!inbound_frames_.empty()) {
        inbound_frames_.pop();
    }
    neighbors_.clear();
    endpoints_.clear();
    endpoint_sources_.clear();
    configured_ = false;
}

void TcpNetwork::start_io_thread() {
    stop_io_thread();
    io_stop_.store(false);
    io_thread_ = std::thread(&TcpNetwork::io_loop, this);
}

void TcpNetwork::stop_io_thread() {
    io_stop_.store(true);
    if (io_thread_.joinable()) {
        io_thread_.join();
    }
}

void TcpNetwork::io_loop() {
    while (!io_stop_.load()) {
        accept_pending_connections();
        read_pending_frames();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

void TcpNetwork::enqueue_frame(EncodedFrame frame) {
    std::lock_guard<std::mutex> lock(mutex_);
    inbound_frames_.push(std::move(frame));
}

std::optional<TcpEndpoint> TcpNetwork::resolve_endpoint(uint32_t node_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = endpoints_.find(node_id);
    if (it == endpoints_.end()) {
        return std::nullopt;
    }

    return it->second;
}

TcpEndpoint TcpNetwork::make_default_endpoint(uint32_t node_id) {
    return TcpEndpoint{
        "127.0.0.1",
        static_cast<uint16_t>(40000U + (node_id % 10000U))
    };
}

bool TcpNetwork::open_listener(const TcpEndpoint& endpoint,
                               const std::string& endpoint_source) {
    std::string resolve_error;
    const auto candidates = resolve_ipv4_candidates(endpoint, true, resolve_error);
    if (candidates.empty()) {
        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): failed to resolve listener endpoint "
                  << format_endpoint(endpoint)
                  << " (source: " << endpoint_source << "): "
                  << resolve_error << "\n";
        return false;
    }

    int last_errno = 0;
    for (const auto& address : candidates) {
        int listener_fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (listener_fd < 0) {
            last_errno = errno;
            continue;
        }

        int reuse = 1;
        if (::setsockopt(listener_fd, SOL_SOCKET, SO_REUSEADDR, &reuse,
                         sizeof(reuse)) < 0) {
            last_errno = errno;
            close_fd(listener_fd);
            continue;
        }

        if (::bind(listener_fd, reinterpret_cast<const sockaddr*>(&address),
                   sizeof(address)) < 0) {
            last_errno = errno;
            close_fd(listener_fd);
            continue;
        }

        if (::listen(listener_fd, 16) < 0) {
            last_errno = errno;
            close_fd(listener_fd);
            continue;
        }

        if (!set_non_blocking(listener_fd)) {
            last_errno = errno;
            close_fd(listener_fd);
            continue;
        }

        listener_fd_ = listener_fd;
        return true;
    }

    std::cerr << "TcpNetwork (ID " << my_node_id_
              << "): failed to bind listener on "
              << format_endpoint(endpoint)
              << " (source: " << endpoint_source << ")"
              << ": " << std::strerror(last_errno) << "\n";
    return false;
}

void TcpNetwork::accept_pending_connections() {
    if (listener_fd_ < 0) {
        return;
    }

    for (;;) {
        sockaddr_in address{};
        socklen_t address_length = sizeof(address);
        int client_fd = ::accept(listener_fd_,
                                 reinterpret_cast<sockaddr*>(&address),
                                 &address_length);
        if (client_fd < 0) {
            if (is_would_block(errno)) {
                return;
            }

            std::cerr << "TcpNetwork (ID " << my_node_id_
                      << "): accept failed: "
                      << std::strerror(errno) << "\n";
            return;
        }

        if (!set_non_blocking(client_fd)) {
            std::cerr << "TcpNetwork (ID " << my_node_id_
                      << "): failed to set accepted socket non-blocking: "
                      << std::strerror(errno) << "\n";
            close_fd(client_fd);
            continue;
        }

        InboundConnection connection;
        connection.fd = client_fd;
        connection.peer_label = peer_label_from_address(address);
        inbound_connections_.push_back(std::move(connection));

        std::cout << "TcpNetwork (ID " << my_node_id_
                  << "): accepted TCP connection from "
                  << inbound_connections_.back().peer_label << "\n";
    }
}

void TcpNetwork::read_pending_frames() {
    for (size_t index = 0; index < inbound_connections_.size();) {
        bool keep_connection = true;
        read_from_connection(inbound_connections_[index], keep_connection);
        if (keep_connection) {
            ++index;
            continue;
        }

        close_fd(inbound_connections_[index].fd);
        inbound_connections_.erase(inbound_connections_.begin() +
                                   static_cast<std::ptrdiff_t>(index));
    }
}

void TcpNetwork::read_from_connection(InboundConnection& connection,
                                      bool& keep_connection) {
    std::vector<uint8_t> chunk(4096);

    for (;;) {
        const ssize_t bytes_read =
            ::recv(connection.fd, chunk.data(), chunk.size(), 0);
        if (bytes_read > 0) {
            connection.buffer.insert(connection.buffer.end(),
                                     chunk.begin(),
                                     chunk.begin() + bytes_read);
            try {
                extract_frames(connection);
            } catch (const std::exception& ex) {
                std::cerr << "TcpNetwork (ID " << my_node_id_
                          << "): dropping malformed TCP stream from "
                          << connection.peer_label << ": "
                          << ex.what() << "\n";
                keep_connection = false;
                return;
            }
            continue;
        }

        if (bytes_read == 0) {
            keep_connection = false;
            return;
        }

        if (errno == EINTR) {
            continue;
        }

        if (is_would_block(errno)) {
            return;
        }

        std::cerr << "TcpNetwork (ID " << my_node_id_
                  << "): recv failed from " << connection.peer_label
                  << ": " << std::strerror(errno) << "\n";
        keep_connection = false;
        return;
    }
}

void TcpNetwork::extract_frames(InboundConnection& connection) {
    while (connection.buffer.size() >= sizeof(uint32_t)) {
        const uint32_t frame_size = decode_frame_length(connection.buffer);
        if (frame_size == 0) {
            throw std::runtime_error("zero-length frame");
        }

        const size_t total_size = sizeof(uint32_t) + frame_size;
        if (connection.buffer.size() < total_size) {
            return;
        }

        std::vector<uint8_t> payload(
            connection.buffer.begin() + static_cast<std::ptrdiff_t>(sizeof(uint32_t)),
            connection.buffer.begin() + static_cast<std::ptrdiff_t>(total_size));
        connection.buffer.erase(connection.buffer.begin(),
                                connection.buffer.begin() +
                                    static_cast<std::ptrdiff_t>(total_size));

        std::cout << "TcpNetwork (ID " << my_node_id_
                  << "): received TCP frame from "
                  << connection.peer_label << " ("
                  << payload.size() << " payload bytes)\n";
        enqueue_frame({std::move(payload)});
    }
}

bool TcpNetwork::set_non_blocking(int fd) {
    const int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return false;
    }

    return ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

bool TcpNetwork::send_all(int fd, const std::vector<uint8_t>& buffer) {
    size_t total_sent = 0;
    while (total_sent < buffer.size()) {
        const ssize_t bytes_sent = ::send(
            fd,
            buffer.data() + static_cast<std::ptrdiff_t>(total_sent),
            buffer.size() - total_sent,
            0);
        if (bytes_sent > 0) {
            total_sent += static_cast<size_t>(bytes_sent);
            continue;
        }

        if (bytes_sent < 0 && errno == EINTR) {
            continue;
        }

        return false;
    }

    return true;
}
