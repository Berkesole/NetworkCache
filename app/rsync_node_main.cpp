#include "node.hpp"
#include "cache/in_memory_cache.hpp"
#ifdef ENABLE_REDIS_CACHE
#include "cache/redis_cache.hpp"
#endif
#include "network/mock_network.hpp"
#include "network/tcp_network.hpp"
#include "network/topology_parser.hpp"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

enum class TransportMode {
    Mock,
    Tcp,
};

struct NodeDevConfig {
    uint32_t node_id = 1;
    TransportMode transport = TransportMode::Tcp;
    std::string topology_path = "config/3-node-topology.yaml";
    int loop_iterations = 10;
    int loop_sleep_ms = 100;
    std::vector<std::pair<std::string, std::string>> seed_data;
    std::vector<std::string> request_keys;
    std::optional<TcpEndpoint> tcp_self_endpoint;
    std::map<uint32_t, TcpEndpoint> tcp_peer_endpoints;
};

std::string trim_copy(const std::string& value) {
    size_t start = 0;
    while (start < value.size() &&
           std::isspace(static_cast<unsigned char>(value[start])) != 0) {
        ++start;
    }

    size_t end = value.size();
    while (end > start &&
           std::isspace(static_cast<unsigned char>(value[end - 1])) != 0) {
        --end;
    }

    return value.substr(start, end - start);
}

std::optional<std::string> read_env(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || value[0] == '\0') {
        return std::nullopt;
    }
    return std::string(value);
}

bool parse_uint32_value(const std::string& text, uint32_t& out_value) {
    try {
        size_t consumed = 0;
        const unsigned long parsed = std::stoul(text, &consumed, 10);
        if (consumed != text.size() ||
            parsed > std::numeric_limits<uint32_t>::max()) {
            return false;
        }
        out_value = static_cast<uint32_t>(parsed);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool parse_int_value(const std::string& text, int& out_value) {
    try {
        size_t consumed = 0;
        const int parsed = std::stoi(text, &consumed, 10);
        if (consumed != text.size()) {
            return false;
        }
        out_value = parsed;
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool parse_uint16_value(const std::string& text, uint16_t& out_value) {
    try {
        size_t consumed = 0;
        const unsigned long parsed = std::stoul(text, &consumed, 10);
        if (consumed != text.size() ||
            parsed == 0 ||
            parsed > std::numeric_limits<uint16_t>::max()) {
            return false;
        }
        out_value = static_cast<uint16_t>(parsed);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

std::optional<TransportMode> parse_transport_mode(const std::string& value) {
    if (value == "mock") {
        return TransportMode::Mock;
    }
    if (value == "tcp") {
        return TransportMode::Tcp;
    }
    return std::nullopt;
}

const char* transport_mode_to_string(TransportMode mode) {
    switch (mode) {
        case TransportMode::Mock:
            return "mock";
        case TransportMode::Tcp:
            return "tcp";
    }
    return "unknown";
}

std::string format_tcp_endpoint(const TcpEndpoint& endpoint) {
    std::ostringstream stream;
    stream << endpoint.host << ":" << endpoint.port;
    return stream.str();
}

std::vector<std::string> parse_csv_list(const std::string& value) {
    std::vector<std::string> items;
    std::stringstream stream(value);
    std::string item;

    while (std::getline(stream, item, ',')) {
        item = trim_copy(item);
        if (!item.empty()) {
            items.push_back(item);
        }
    }

    return items;
}

std::vector<std::pair<std::string, std::string>> parse_seed_data(
    const std::string& value,
    std::string& error) {
    std::vector<std::pair<std::string, std::string>> items;
    for (const auto& token : parse_csv_list(value)) {
        const size_t separator = token.find('=');
        if (separator == std::string::npos || separator == 0) {
            error = "Expected seed data entries in key=value form: " + token;
            return {};
        }

        std::string key = trim_copy(token.substr(0, separator));
        std::string raw_value = token.substr(separator + 1);
        if (key.empty()) {
            error = "Seed data key cannot be empty: " + token;
            return {};
        }

        items.emplace_back(std::move(key), std::move(raw_value));
    }

    return items;
}

bool parse_tcp_endpoint(const std::string& value,
                        TcpEndpoint& endpoint,
                        std::string& error) {
    const size_t separator = value.rfind(':');
    if (separator == std::string::npos ||
        separator == 0 ||
        separator + 1 >= value.size()) {
        error = "Expected endpoint in host:port form: " + value;
        return false;
    }

    const std::string host = trim_copy(value.substr(0, separator));
    const std::string port_text = trim_copy(value.substr(separator + 1));
    uint16_t port = 0;
    if (host.empty() || !parse_uint16_value(port_text, port)) {
        error = "Expected endpoint in host:port form: " + value;
        return false;
    }

    endpoint = TcpEndpoint{host, port};
    return true;
}

bool parse_tcp_peer_overrides(const std::string& value,
                              std::map<uint32_t, TcpEndpoint>& overrides,
                              std::string& error) {
    for (const auto& token : parse_csv_list(value)) {
        const size_t separator = token.find('=');
        if (separator == std::string::npos || separator == 0) {
            error = "Expected TCP peer override in node_id=host:port form: " + token;
            return false;
        }

        const std::string node_id_text = trim_copy(token.substr(0, separator));
        const std::string endpoint_text = trim_copy(token.substr(separator + 1));

        uint32_t node_id = 0;
        if (!parse_uint32_value(node_id_text, node_id) || node_id == 0) {
            error = "TCP peer override requires a positive node ID: " + token;
            return false;
        }

        TcpEndpoint endpoint;
        if (!parse_tcp_endpoint(endpoint_text, endpoint, error)) {
            return false;
        }

        overrides[node_id] = std::move(endpoint);
    }

    return true;
}

bool apply_env_overrides(NodeDevConfig& config, std::string& error) {
    if (const auto value = read_env("RSYNC_NODE_ID")) {
        if (!parse_uint32_value(*value, config.node_id) || config.node_id == 0) {
            error = "RSYNC_NODE_ID must be a positive integer";
            return false;
        }
    }

    if (const auto value = read_env("RSYNC_TRANSPORT")) {
        const auto mode = parse_transport_mode(*value);
        if (!mode.has_value()) {
            error = "RSYNC_TRANSPORT must be one of: mock, tcp";
            return false;
        }
        config.transport = *mode;
    }

    if (const auto value = read_env("RSYNC_TOPOLOGY_CONFIG")) {
        config.topology_path = *value;
    }

    if (const auto value = read_env("RSYNC_LOOP_ITERATIONS")) {
        if (!parse_int_value(*value, config.loop_iterations) ||
            config.loop_iterations < 0) {
            error = "RSYNC_LOOP_ITERATIONS must be a non-negative integer";
            return false;
        }
    }

    if (const auto value = read_env("RSYNC_LOOP_SLEEP_MS")) {
        if (!parse_int_value(*value, config.loop_sleep_ms) ||
            config.loop_sleep_ms < 0) {
            error = "RSYNC_LOOP_SLEEP_MS must be a non-negative integer";
            return false;
        }
    }

    if (const auto value = read_env("RSYNC_SEED_DATA")) {
        config.seed_data = parse_seed_data(*value, error);
        if (!error.empty()) {
            return false;
        }
    }

    if (const auto value = read_env("RSYNC_REQUEST_KEYS")) {
        config.request_keys = parse_csv_list(*value);
    }

    if (const auto value = read_env("RSYNC_TCP_SELF")) {
        TcpEndpoint endpoint;
        if (!parse_tcp_endpoint(*value, endpoint, error)) {
            return false;
        }
        config.tcp_self_endpoint = std::move(endpoint);
    }

    if (const auto value = read_env("RSYNC_TCP_PEERS")) {
        if (!parse_tcp_peer_overrides(*value, config.tcp_peer_endpoints, error)) {
            return false;
        }
    }

    return true;
}

void print_usage(const char* program_name) {
    std::cout
        << "Usage: " << program_name << " [options]\n"
        << "  --node-id <id>             Node ID for this process\n"
        << "  --transport <mock|tcp>     Transport mode\n"
        << "  --topology <path>          Topology config path\n"
        << "  --iterations <count>       Number of processOne() iterations\n"
        << "  --sleep-ms <milliseconds>  Delay between iterations\n"
        << "  --seed <k=v,k=v>           Seed local cache entries before requests\n"
        << "  --request-keys <k1,k2>     Request keys after startup\n"
        << "  --tcp-self <host:port>     TCP listener endpoint override for this node\n"
        << "  --tcp-peer <id=host:port>  TCP peer endpoint override (repeatable or CSV)\n"
        << "  --help                     Show this help message\n";
}

bool parse_args(int argc, char** argv, NodeDevConfig& config, bool& show_help,
                std::string& error) {
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto require_value = [&](const char* option_name) -> std::optional<std::string> {
            if (i + 1 >= argc) {
                error = std::string(option_name) + " requires a value";
                return std::nullopt;
            }
            ++i;
            return std::string(argv[i]);
        };

        if (arg == "--help") {
            show_help = true;
            return true;
        }
        if (arg == "--node-id") {
            auto value = require_value("--node-id");
            if (!value.has_value()) {
                return false;
            }
            if (!parse_uint32_value(*value, config.node_id) || config.node_id == 0) {
                error = "--node-id must be a positive integer";
                return false;
            }
            continue;
        }
        if (arg == "--transport") {
            auto value = require_value("--transport");
            if (!value.has_value()) {
                return false;
            }
            const auto mode = parse_transport_mode(*value);
            if (!mode.has_value()) {
                error = "--transport must be one of: mock, tcp";
                return false;
            }
            config.transport = *mode;
            continue;
        }
        if (arg == "--topology") {
            auto value = require_value("--topology");
            if (!value.has_value()) {
                return false;
            }
            config.topology_path = *value;
            continue;
        }
        if (arg == "--iterations") {
            auto value = require_value("--iterations");
            if (!value.has_value()) {
                return false;
            }
            if (!parse_int_value(*value, config.loop_iterations) ||
                config.loop_iterations < 0) {
                error = "--iterations must be a non-negative integer";
                return false;
            }
            continue;
        }
        if (arg == "--sleep-ms") {
            auto value = require_value("--sleep-ms");
            if (!value.has_value()) {
                return false;
            }
            if (!parse_int_value(*value, config.loop_sleep_ms) ||
                config.loop_sleep_ms < 0) {
                error = "--sleep-ms must be a non-negative integer";
                return false;
            }
            continue;
        }
        if (arg == "--seed") {
            auto value = require_value("--seed");
            if (!value.has_value()) {
                return false;
            }
            config.seed_data = parse_seed_data(*value, error);
            if (!error.empty()) {
                return false;
            }
            continue;
        }
        if (arg == "--request-keys") {
            auto value = require_value("--request-keys");
            if (!value.has_value()) {
                return false;
            }
            config.request_keys = parse_csv_list(*value);
            continue;
        }
        if (arg == "--tcp-self") {
            auto value = require_value("--tcp-self");
            if (!value.has_value()) {
                return false;
            }

            TcpEndpoint endpoint;
            if (!parse_tcp_endpoint(*value, endpoint, error)) {
                error = "--tcp-self " + error;
                return false;
            }

            config.tcp_self_endpoint = std::move(endpoint);
            continue;
        }
        if (arg == "--tcp-peer") {
            auto value = require_value("--tcp-peer");
            if (!value.has_value()) {
                return false;
            }

            if (!parse_tcp_peer_overrides(*value, config.tcp_peer_endpoints, error)) {
                error = "--tcp-peer " + error;
                return false;
            }
            continue;
        }

        error = "Unknown argument: " + arg;
        return false;
    }

    return true;
}

bool load_topology(const std::string& path,
                   std::map<uint32_t, ParsedNodeConfig>& topology,
                   std::string& error) {
    std::ifstream file(path);
    if (!file.is_open()) {
        error = "Unable to open topology config: " + path;
        return false;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();

    TopologyParser parser;
    topology = parser.parse(buffer.str());
    if (topology.empty()) {
        if (parser.has_errors()) {
            error = "Topology config is malformed and produced no nodes: " + path;
        } else {
            error = "Topology config did not define any nodes: " + path;
        }
        return false;
    }
    return true;
}

bool validate_config(const NodeDevConfig& config, std::string& error) {
    if (config.node_id == 0) {
        error = "Node ID must be greater than zero";
        return false;
    }

    if (config.loop_iterations < 0 || config.loop_sleep_ms < 0) {
        error = "Loop configuration cannot be negative";
        return false;
    }

    if (!std::filesystem::exists(config.topology_path)) {
        error = "Transport requires an existing topology file: " +
                config.topology_path;
        return false;
    }

    std::map<uint32_t, ParsedNodeConfig> topology;
    if (!load_topology(config.topology_path, topology, error)) {
        return false;
    }

    if (topology.count(config.node_id) == 0) {
        error = "Node ID " + std::to_string(config.node_id) +
                " is not present in topology file: " + config.topology_path;
        return false;
    }

    if (config.transport == TransportMode::Tcp) {
        for (const auto& [peer_node_id, _] : config.tcp_peer_endpoints) {
            if (topology.count(peer_node_id) == 0) {
                error = "TCP peer override references unknown node ID " +
                        std::to_string(peer_node_id) + " in topology file: " +
                        config.topology_path;
                return false;
            }
        }
    }

    return true;
}

void print_config(const NodeDevConfig& config) {
    std::cout << "[rsync-node-dev] startup configuration\n";
    std::cout << "  node_id: " << config.node_id << "\n";
    std::cout << "  transport: " << transport_mode_to_string(config.transport) << "\n";
    std::cout << "  topology: " << config.topology_path << "\n";
    std::cout << "  loop_iterations: " << config.loop_iterations << "\n";
    std::cout << "  loop_sleep_ms: " << config.loop_sleep_ms << "\n";
    std::cout << "  seed_data_entries: " << config.seed_data.size() << "\n";
    std::cout << "  request_keys: " << config.request_keys.size() << "\n";
    if (config.transport == TransportMode::Tcp) {
        std::cout << "  tcp_endpoint_precedence: topology/config -> env/CLI overrides -> legacy default fallback\n";
        if (config.tcp_self_endpoint.has_value()) {
            std::cout << "  tcp_self_override: "
                      << format_tcp_endpoint(*config.tcp_self_endpoint) << "\n";
        } else {
            std::cout << "  tcp_self_override: <none>\n";
        }

        std::cout << "  tcp_peer_overrides: "
                  << config.tcp_peer_endpoints.size() << "\n";
        for (const auto& [peer_node_id, endpoint] : config.tcp_peer_endpoints) {
            std::cout << "    node " << peer_node_id << " -> "
                      << format_tcp_endpoint(endpoint) << "\n";
        }
    }
}

void print_tcp_endpoint_report(const TcpNetwork& network, uint32_t node_id) {
    std::vector<uint32_t> nodes_to_report{node_id};
    const auto neighbors = network.get_neighbors();
    nodes_to_report.insert(nodes_to_report.end(), neighbors.begin(), neighbors.end());
    std::sort(nodes_to_report.begin(), nodes_to_report.end());
    nodes_to_report.erase(
        std::unique(nodes_to_report.begin(), nodes_to_report.end()),
        nodes_to_report.end());

    std::cout << "[rsync-node-dev] resolved tcp endpoints\n";
    for (const uint32_t report_node_id : nodes_to_report) {
        const auto endpoint = network.get_endpoint(report_node_id);
        const auto source = network.get_endpoint_source(report_node_id);
        if (!endpoint.has_value()) {
            std::cout << "  node " << report_node_id << ": <missing>\n";
            continue;
        }

        std::cout << "  node " << report_node_id;
        if (report_node_id == node_id) {
            std::cout << " (self)";
        }
        std::cout << ": " << format_tcp_endpoint(*endpoint);
        if (source.has_value()) {
            std::cout << " [" << *source << "]";
        }
        std::cout << "\n";
    }
}

std::unique_ptr<NetworkInterface> build_network(const NodeDevConfig& config) {
    if (config.transport == TransportMode::Tcp) {
        auto network = std::make_unique<TcpNetwork>(config.node_id);
        std::map<uint32_t, TcpEndpoint> endpoint_overrides =
            config.tcp_peer_endpoints;
        if (config.tcp_self_endpoint.has_value()) {
            endpoint_overrides[config.node_id] = *config.tcp_self_endpoint;
        }
        network->set_endpoint_overrides(std::move(endpoint_overrides));
        network->configure_topology(config.topology_path, config.node_id);
        if (!network->is_configured()) {
            std::cerr << "[rsync-node-dev] tcp transport failed to initialize\n";
            return nullptr;
        }

        print_tcp_endpoint_report(*network, config.node_id);
        std::cout << "[rsync-node-dev] tcp transport initialized with "
                  << network->get_neighbors().size() << " neighbor(s)\n";
        return network;
    }

    auto network = std::make_unique<MockNetwork>(config.node_id);
    network->configure_topology(config.topology_path, config.node_id);
    std::cout << "[rsync-node-dev] mock transport initialized with "
              << network->get_neighbors().size() << " neighbor(s)\n";
    return network;
}

std::unique_ptr<LocalCache> build_cache() {
#ifdef ENABLE_REDIS_CACHE
    const std::string socket_path =
        read_env("REDIS_SOCKET_PATH").value_or(RedisCache::DEFAULT_SOCKET_PATH);
    auto cache = std::make_unique<RedisCache>(socket_path);
    std::cout << "[rsync-node-dev] cache backend: redis (default), socket="
              << socket_path << std::endl;
    return cache;
#else
    std::cout << "[rsync-node-dev] cache backend: in-memory (redis support disabled at build time)"
              << std::endl;
    return std::make_unique<InMemoryCache>();
#endif
}

}  // namespace

int main(int argc, char** argv) {
    std::cout << std::unitbuf;
    std::cerr << std::unitbuf;

    NodeDevConfig config;
    std::string error;

    if (!apply_env_overrides(config, error)) {
        std::cerr << "[rsync-node-dev] config error: " << error << "\n";
        return EXIT_FAILURE;
    }

    bool show_help = false;
    if (!parse_args(argc, argv, config, show_help, error)) {
        std::cerr << "[rsync-node-dev] argument error: " << error << "\n";
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    if (show_help) {
        print_usage(argv[0]);
        return EXIT_SUCCESS;
    }

    if (!validate_config(config, error)) {
        std::cerr << "[rsync-node-dev] validation error: " << error << "\n";
        return EXIT_FAILURE;
    }

    print_config(config);
    std::cout.flush();

    auto network = build_network(config);
    if (!network) {
        return EXIT_FAILURE;
    }

    std::unique_ptr<LocalCache> cache;
    try {
        cache = build_cache();
    } catch (const std::exception& e) {
        std::cerr << "[rsync-node-dev] cache init failed: " << e.what() << "\n";
        return EXIT_FAILURE;
    }

    Node node(config.node_id, std::move(network), std::move(cache));

    for (const auto& [key, value] : config.seed_data) {
        node.addLocalData(key, value);
        std::cout << "[rsync-node-dev] seeded local key=" << key
                  << " value_size=" << value.size() << "\n";
    }

    if (!config.request_keys.empty()) {
        node.requestFile(config.request_keys);
    }

    for (int iteration = 0; iteration < config.loop_iterations; ++iteration) {
        node.processOne();
        if (config.loop_sleep_ms > 0) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(config.loop_sleep_ms));
        }
    }

    std::cout << "[rsync-node-dev] completed " << config.loop_iterations
              << " loop iteration(s)\n";
    return EXIT_SUCCESS;
}
