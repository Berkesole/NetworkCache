#include "node.hpp"
#include "cache/in_memory_cache.hpp"
#include "event_trace.hpp"
#include "experiment_data.hpp"
#ifdef ENABLE_REDIS_CACHE
#include "cache/redis_cache.hpp"
#endif
#include "network/mock_network.hpp"
#include "network/packet_codec.hpp"
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
#include <unordered_set>
#include <unistd.h>

namespace {

enum class TransportMode {
    Mock,
    Tcp,
};

struct ScheduledRequestEvent {
    uint32_t at_tick = 0;
    std::vector<std::string> keys;
};

struct ScheduledOracleUnicastEvent {
    uint32_t at_tick = 0;
    std::string key;
    uint32_t provider_node_id = 0;
};

struct NodeDevConfig {
    uint32_t node_id = 1;
    TransportMode transport = TransportMode::Tcp;
    std::string topology_path = "config/3-node-topology.yaml";
    int loop_iterations = 10;
    int loop_sleep_ms = 100;
    std::vector<std::pair<std::string, std::string>> seed_data;
    std::vector<std::string> request_keys;
    std::vector<ScheduledRequestEvent> scenario_requests;
    std::vector<ScheduledOracleUnicastEvent> oracle_unicast_requests;
    ProtocolConfig protocol;
    std::string oracle_unicast_schedule_path;
    std::optional<TcpEndpoint> tcp_self_endpoint;
    std::map<uint32_t, TcpEndpoint> tcp_peer_endpoints;
    bool loop_iterations_overridden = false;
    bool loop_sleep_ms_overridden = false;
    bool seed_data_overridden = false;
    bool request_keys_overridden = false;
    std::string experiment_data_file;
    std::string experiment_control_dir;
    std::string experiment_schedule_hash;
    std::string federation_interface;
    std::string federation_activation_mode = "direct-interface";
};

struct ExperimentDataSpec {
    std::string key;
    uint64_t size = 0;
    uint64_t seed = 0;
    std::string expected_hash;
};

struct ActiveExperimentRequest {
    std::string request_index;
    std::string key;
    uint64_t expected_size = 0;
    std::string expected_hash;
    uint64_t started_ns = 0;
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

bool env_enabled(const char* name) {
    const auto value = read_env(name);
    return value.has_value() && *value != "0" && *value != "false" && *value != "FALSE";
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

bool parse_uint64_value(const std::string& text, uint64_t& out_value) {
    try {
        size_t consumed = 0;
        const unsigned long long parsed = std::stoull(text, &consumed, 10);
        if (consumed != text.size()) return false;
        out_value = static_cast<uint64_t>(parsed);
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

std::vector<std::string> split_tab_line(const std::string& value) {
    std::vector<std::string> items;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, '\t')) {
        items.push_back(trim_copy(item));
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
        config.loop_iterations_overridden = true;
    }

    if (const auto value = read_env("RSYNC_LOOP_SLEEP_MS")) {
        if (!parse_int_value(*value, config.loop_sleep_ms) ||
            config.loop_sleep_ms < 0) {
            error = "RSYNC_LOOP_SLEEP_MS must be a non-negative integer";
            return false;
        }
        config.loop_sleep_ms_overridden = true;
    }

    if (const auto value = read_env("RSYNC_SEED_DATA")) {
        config.seed_data = parse_seed_data(*value, error);
        if (!error.empty()) {
            return false;
        }
        config.seed_data_overridden = true;
    }

    if (const auto value = read_env("RSYNC_REQUEST_KEYS")) {
        config.request_keys = parse_csv_list(*value);
        config.request_keys_overridden = true;
    }

    if (const auto value = read_env("RSYNC_ORACLE_UNICAST_SCHEDULE")) {
        config.oracle_unicast_schedule_path = *value;
    }
    if (const auto value = read_env("RSYNC_EXPERIMENT_DATA_FILE")) {
        config.experiment_data_file = *value;
        config.seed_data_overridden = true;
    }
    if (const auto value = read_env("RSYNC_EXPERIMENT_CONTROL_DIR")) {
        config.experiment_control_dir = *value;
        config.scenario_requests.clear();
        config.request_keys_overridden = true;
    }
    if (const auto value = read_env("RSYNC_FEDERATION_ACTIVATION_MODE")) {
        if (*value != "direct-interface" && *value != "logical-only") {
            error = "RSYNC_FEDERATION_ACTIVATION_MODE must be direct-interface or logical-only";
            return false;
        }
        config.federation_activation_mode = *value;
    }
    if (const auto value = read_env("RSYNC_EXPERIMENT_SCHEDULE_HASH")) {
        config.experiment_schedule_hash = *value;
    }
    if (const auto value = read_env("RSYNC_FEDERATION_INTERFACE")) {
        if (value->size() < 4 || value->substr(0, 3) != "eth" ||
            !std::all_of(value->begin() + 3, value->end(), [](unsigned char c) { return std::isdigit(c); })) {
            error = "RSYNC_FEDERATION_INTERFACE must have eth<index> form";
            return false;
        }
        config.federation_interface = *value;
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
            config.loop_iterations_overridden = true;
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
            config.loop_sleep_ms_overridden = true;
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
            config.seed_data_overridden = true;
            continue;
        }
        if (arg == "--request-keys") {
            auto value = require_value("--request-keys");
            if (!value.has_value()) {
                return false;
            }
            config.request_keys = parse_csv_list(*value);
            config.request_keys_overridden = true;
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

bool load_parse_result(const std::string& path,
                       ParseResult& result,
                       std::string& error) {
    std::ifstream file(path);
    if (!file.is_open()) {
        error = "Unable to open topology config: " + path;
        return false;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();

    TopologyParser parser;
    result = parser.parse_config(buffer.str());
    if (result.topology.empty()) {
        if (parser.has_errors()) {
            error = "Topology config is malformed and produced no nodes: " + path;
        } else {
            error = "Topology config did not define any nodes: " + path;
        }
        return false;
    }
    return true;
}

bool load_oracle_unicast_schedule(NodeDevConfig& config,
                                  const ParseResult& parsed,
                                  std::string& error) {
    config.oracle_unicast_requests.clear();
    if (config.oracle_unicast_schedule_path.empty()) {
        return true;
    }
    config.scenario_requests.clear();

    std::ifstream file(config.oracle_unicast_schedule_path);
    if (!file.is_open()) {
        error = "Unable to open oracle unicast schedule: " +
                config.oracle_unicast_schedule_path;
        return false;
    }

    std::string line;
    uint32_t line_number = 0;
    while (std::getline(file, line)) {
        ++line_number;
        line = trim_copy(line);
        if (line.empty()) {
            continue;
        }
        const auto fields = split_tab_line(line);
        if (!fields.empty() && fields[0] == "tick") {
            continue;
        }
        if (fields.size() < 3) {
            error = "Oracle unicast schedule line " + std::to_string(line_number) +
                    " must contain at least tick, key, provider";
            return false;
        }

        uint32_t tick = 0;
        uint32_t provider = 0;
        if (!parse_uint32_value(fields[0], tick)) {
            error = "Oracle unicast schedule line " + std::to_string(line_number) +
                    " has invalid tick: " + fields[0];
            return false;
        }
        if (fields[1].empty()) {
            error = "Oracle unicast schedule line " + std::to_string(line_number) +
                    " has empty key";
            return false;
        }
        if (!parse_uint32_value(fields[2], provider) || provider == 0) {
            error = "Oracle unicast schedule line " + std::to_string(line_number) +
                    " has invalid provider: " + fields[2];
            return false;
        }
        if (parsed.topology.count(provider) == 0) {
            error = "Oracle unicast schedule line " + std::to_string(line_number) +
                    " references unknown provider Node " + std::to_string(provider);
            return false;
        }

        config.oracle_unicast_requests.push_back(
            ScheduledOracleUnicastEvent{tick, fields[1], provider});
    }

    std::stable_sort(config.oracle_unicast_requests.begin(),
                     config.oracle_unicast_requests.end(),
                     [](const ScheduledOracleUnicastEvent& lhs,
                        const ScheduledOracleUnicastEvent& rhs) {
                         return lhs.at_tick < rhs.at_tick;
                     });
    return true;
}

void apply_scenario_defaults(NodeDevConfig& config, const ParseResult& parsed) {
    config.protocol = parsed.protocol;

    if (!config.loop_iterations_overridden) {
        config.loop_iterations = static_cast<int>(parsed.scenario.ticks);
    }

    if (!config.seed_data_overridden) {
        config.seed_data.clear();
        for (const auto& item : parsed.scenario.data) {
            if (item.node_id == config.node_id) {
                config.seed_data.emplace_back(item.key, item.value);
            }
        }
    }

    if (!config.request_keys_overridden) {
        config.scenario_requests.clear();
        for (const auto& item : parsed.scenario.requests) {
            if (item.node_id == config.node_id) {
                config.scenario_requests.push_back(
                    ScheduledRequestEvent{item.at_tick, item.keys});
            }
        }
        std::stable_sort(config.scenario_requests.begin(),
                         config.scenario_requests.end(),
                         [](const ScheduledRequestEvent& lhs,
                            const ScheduledRequestEvent& rhs) {
                             return lhs.at_tick < rhs.at_tick;
                         });
    }
}

bool validate_config(NodeDevConfig& config,
                     ParseResult& parsed,
                     std::string& error) {
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

    if (!load_parse_result(config.topology_path, parsed, error)) {
        return false;
    }

    if (parsed.topology.count(config.node_id) == 0) {
        error = "Node ID " + std::to_string(config.node_id) +
                " is not present in topology file: " + config.topology_path;
        return false;
    }

    if (!config.loop_iterations_overridden &&
        parsed.scenario.ticks >
            static_cast<uint32_t>(std::numeric_limits<int>::max())) {
        error = "Scenario ticks exceed supported loop_iterations range";
        return false;
    }

    for (const auto& item : parsed.scenario.data) {
        if (parsed.topology.count(item.node_id) == 0) {
            error = "Scenario data references unknown node ID " +
                    std::to_string(item.node_id) + " in topology file: " +
                    config.topology_path;
            return false;
        }
    }

    for (const auto& item : parsed.scenario.requests) {
        if (parsed.topology.count(item.node_id) == 0) {
            error = "Scenario request references unknown node ID " +
                    std::to_string(item.node_id) + " in topology file: " +
                    config.topology_path;
            return false;
        }
    }

    if (config.transport == TransportMode::Tcp) {
        for (const auto& [peer_node_id, _] : config.tcp_peer_endpoints) {
            if (parsed.topology.count(peer_node_id) == 0) {
                error = "TCP peer override references unknown node ID " +
                        std::to_string(peer_node_id) + " in topology file: " +
                        config.topology_path;
                return false;
            }
        }
    }

    apply_scenario_defaults(config, parsed);
    if (!load_oracle_unicast_schedule(config, parsed, error)) {
        return false;
    }
    return true;
}

void print_config(const NodeDevConfig& config) {
    std::cout << "[rsync-node-dev] startup configuration\n";
    std::cout << "  node_id: " << config.node_id << "\n";
    std::cout << "  transport: " << transport_mode_to_string(config.transport) << "\n";
    std::cout << "  topology: " << config.topology_path << "\n";
    std::cout << "  protocol.max_hop_count: " << config.protocol.max_hop_count << "\n";
    std::cout << "  loop_iterations: " << config.loop_iterations << "\n";
    std::cout << "  loop_sleep_ms: " << config.loop_sleep_ms << "\n";
    std::cout << "  seed_data_entries: " << config.seed_data.size() << "\n";
    std::cout << "  request_keys: " << config.request_keys.size() << "\n";
    std::cout << "  scheduled_scenario_requests: " << config.scenario_requests.size()
              << "\n";
    std::cout << "  oracle_unicast_schedule: "
              << (config.oracle_unicast_schedule_path.empty()
                      ? std::string("<none>")
                      : config.oracle_unicast_schedule_path)
              << "\n";
    std::cout << "  scheduled_oracle_unicast_requests: "
              << config.oracle_unicast_requests.size() << "\n";
    std::cout << "  experiment_control_dir: "
              << (config.experiment_control_dir.empty() ? "<none>" : config.experiment_control_dir)
              << "\n";
    std::cout << "  federation_activation_mode: "
              << config.federation_activation_mode << "\n";
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

std::vector<ExperimentDataSpec> load_experiment_data(const std::string& path) {
    std::vector<ExperimentDataSpec> specs;
    if (path.empty()) return specs;
    std::ifstream input(path);
    if (!input.is_open()) {
        throw std::runtime_error("unable to open experiment data file: " + path);
    }
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') continue;
        const auto fields = split_tab_line(line);
        if (!fields.empty() && fields[0] == "key") continue;
        if (fields.size() < 4) throw std::runtime_error("invalid experiment data row: " + line);
        ExperimentDataSpec spec;
        spec.key = fields[0];
        if (!parse_uint64_value(fields[1], spec.size) ||
            !parse_uint64_value(fields[2], spec.seed)) {
            throw std::runtime_error("invalid experiment data size/seed: " + line);
        }
        spec.expected_hash = fields[3];
        specs.push_back(std::move(spec));
    }
    return specs;
}

uint64_t steady_ns() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

void atomic_write(const std::filesystem::path& path, const std::string& content) {
    std::filesystem::create_directories(path.parent_path());
    const auto temporary = path.string() + ".tmp." + std::to_string(::getpid());
    {
        std::ofstream output(temporary, std::ios::trunc);
        if (!output.good()) throw std::runtime_error("unable to write " + temporary);
        output << content;
        output.flush();
    }
    std::filesystem::rename(temporary, path);
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

    ParseResult parsed;
    if (!validate_config(config, parsed, error)) {
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

    Node::Config node_runtime_config;
    node_runtime_config.max_hop_count = config.protocol.max_hop_count;
    if (const auto value = read_env("RSYNC_QUERY_TIMEOUT_MS")) {
        uint32_t timeout_ms = 0;
        if (!parse_uint32_value(*value, timeout_ms) || timeout_ms == 0 || timeout_ms > 60000) {
            std::cerr << "[rsync-node-dev] invalid RSYNC_QUERY_TIMEOUT_MS=" << *value << "\n";
            return EXIT_FAILURE;
        }
        node_runtime_config.query_timeout = std::chrono::milliseconds(timeout_ms);
    }
    if (const auto value = read_env("RSYNC_BLOOM_BITS")) {
        uint32_t bloom_bits = 0;
        if (!parse_uint32_value(*value, bloom_bits) || bloom_bits < 128 ||
            bloom_bits > 65536 || bloom_bits % 8 != 0) {
            std::cerr << "[rsync-node-dev] invalid RSYNC_BLOOM_BITS=" << *value << "\n";
            return EXIT_FAILURE;
        }
        node_runtime_config.bloom_bits = bloom_bits;
    }
    if (const auto value = read_env("RSYNC_BLOOM_HASH_COUNT")) {
        uint32_t hash_count = 0;
        if (!parse_uint32_value(*value, hash_count) ||
            (hash_count != 1 && hash_count != 3)) {
            std::cerr << "[rsync-node-dev] invalid RSYNC_BLOOM_HASH_COUNT=" << *value << "\n";
            return EXIT_FAILURE;
        }
        node_runtime_config.bloom_hash_count = hash_count;
    }
    node_runtime_config.staged_fallback_enabled = env_enabled("RSYNC_STAGED_FALLBACK");
    if (const auto value = read_env("RSYNC_FALLBACK_DELAY_MS")) {
        uint32_t fallback_delay_ms = 0;
        if (!parse_uint32_value(*value, fallback_delay_ms) ||
            fallback_delay_ms == 0 || fallback_delay_ms >=
                static_cast<uint32_t>(node_runtime_config.query_timeout.count())) {
            std::cerr << "[rsync-node-dev] invalid RSYNC_FALLBACK_DELAY_MS="
                      << *value << "\n";
            return EXIT_FAILURE;
        }
        node_runtime_config.fallback_delay =
            std::chrono::milliseconds(fallback_delay_ms);
    }
    const auto wire_version = PacketCodec::configured_wire_version();
    if (node_runtime_config.staged_fallback_enabled &&
        (wire_version != PacketCodec::kSignedRequestWireVersion ||
         node_runtime_config.bloom_bits != RequestPacket::kSignatureBloomBits ||
         node_runtime_config.bloom_hash_count != RequestPacket::kSignatureHashCount ||
         node_runtime_config.fallback_delay >= node_runtime_config.query_timeout)) {
        std::cerr
            << "[rsync-node-dev] staged fallback requires wire v3, "
            << "1024 Bloom bits, 3 hashes, and fallback delay < query timeout\n";
        return EXIT_FAILURE;
    }
    std::cout << "[rsync-node-dev] bloom configuration bits="
              << node_runtime_config.bloom_bits << " hashes="
              << node_runtime_config.bloom_hash_count
              << " staged_fallback="
              << (node_runtime_config.staged_fallback_enabled ? "enabled" : "disabled")
              << " fallback_delay_ms=" << node_runtime_config.fallback_delay.count()
              << " query_timeout_ms=" << node_runtime_config.query_timeout.count() << "\n";
    std::cout << "[rsync-node-dev] wire configuration version="
              << static_cast<unsigned>(wire_version) << "\n";
    auto* tcp_network = dynamic_cast<TcpNetwork*>(network.get());
    Node node(config.node_id,
              std::move(network),
              std::move(cache),
              node_runtime_config);

    try {
        for (const auto& spec : load_experiment_data(config.experiment_data_file)) {
            auto value = rsync_experiment::deterministic_value(
                static_cast<std::size_t>(spec.size), spec.seed);
            const auto actual_hash = rsync_experiment::hex64(rsync_experiment::fnv1a64(value));
            if (actual_hash != spec.expected_hash) {
                throw std::runtime_error("experiment seed hash mismatch for " + spec.key);
            }
            node.addLocalData(spec.key, value);
            std::cout << "[rsync-node-dev] seeded experiment key=" << spec.key
                      << " value_size=" << value.size()
                      << " value_hash=" << actual_hash << "\n";
        }
    } catch (const std::exception& e) {
        std::cerr << "[rsync-node-dev] experiment data error: " << e.what() << "\n";
        return EXIT_FAILURE;
    }

    for (const auto& [key, value] : config.seed_data) {
        node.addLocalData(key, value);
        std::cout << "[rsync-node-dev] seeded local key=" << key
                  << " value_size=" << value.size() << "\n";
    }

    if (!config.request_keys.empty()) {
        std::cout << "[rsync-node-dev] firing explicit startup request for "
                  << config.request_keys.size() << " key(s)\n";
        node.requestFile(config.request_keys);
    }

    const bool experiment_mode = !config.experiment_control_dir.empty();
    const std::filesystem::path control_root(config.experiment_control_dir);
    std::optional<ActiveExperimentRequest> active_request;
    std::unordered_set<std::string> processed_commands;
    std::unordered_set<std::string> processed_state_markers;
    bool activation_processed = false;
    uint64_t last_protocol_activity_ns = steady_ns();
    std::string active_quiesce_index;
    std::filesystem::path active_quiesce_ack;
    if (experiment_mode) {
        node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
            if (!active_request.has_value() || delivery.key != active_request->key) return;
            const uint64_t finished_ns = steady_ns();
            const std::string actual_hash =
                rsync_experiment::hex64(rsync_experiment::fnv1a64(delivery.value));
            const bool size_ok = delivery.value.size() == active_request->expected_size;
            const bool hash_ok = actual_hash == active_request->expected_hash;
            const std::string status = size_ok && hash_ok ? "ok" : "integrity_mismatch";
            const auto cached_route = node.debugCachedRoute(delivery.key);
            std::ostringstream row;
            row << active_request->request_index << '\t' << config.node_id << '\t'
                << delivery.key << '\t' << status << '\t' << delivery.value.size() << '\t'
                << actual_hash << '\t' << active_request->started_ns << '\t' << finished_ns
                << '\t' << delivery.request_id << '\t' << delivery.via << '\t'
                << config.experiment_schedule_hash << '\t'
                << (cached_route.has_value() ? std::to_string(*cached_route) : "") << '\n';
            atomic_write(control_root / "completions" /
                             (active_request->request_index + ".tsv"), row.str());
            active_request.reset();
        });
        atomic_write(control_root / "ready" /
                         ("node" + std::to_string(config.node_id) + ".ready"),
                     "ready\t" + std::to_string(config.node_id) + "\n");
    }

    const bool trace_loop_ticks = env_enabled("RSYNC_EVENT_TRACE_LOOP_TICKS");
    for (int iteration = 0; iteration < config.loop_iterations; ++iteration) {
        const uint32_t tick = static_cast<uint32_t>(iteration);
        if (experiment_mode) {
            if (std::filesystem::exists(control_root / "stop")) break;
            const auto state_marker_dir = control_root / "protocol-state-markers";
            if (std::filesystem::exists(state_marker_dir)) {
                std::vector<std::filesystem::path> markers;
                for (const auto& entry : std::filesystem::directory_iterator(state_marker_dir)) {
                    if (entry.is_regular_file() && entry.path().extension() == ".tsv") {
                        markers.push_back(entry.path());
                    }
                }
                std::sort(markers.begin(), markers.end());
                for (const auto& marker : markers) {
                    if (!processed_state_markers.insert(marker.string()).second) continue;
                    std::ifstream input(marker);
                    std::string line;
                    std::getline(input, line);
                    const auto fields = split_tab_line(line);
                    if (fields.size() != 3) continue;
                    std::ostringstream rows;
                    rows << "snapshot_id\tphase\tbin\tnode\tneighbor\tbits_set\tbit_count\toccupancy\n";
                    const auto state = node.debugSyncBloomOccupancy();
                    if (state.empty()) {
                        rows << fields[0] << '\t' << fields[1] << '\t' << fields[2]
                             << '\t' << config.node_id << "\t0\t0\t"
                             << node_runtime_config.bloom_bits << "\t0\n";
                    } else {
                        for (const auto& item : state) {
                            rows << fields[0] << '\t' << fields[1] << '\t' << fields[2]
                                 << '\t' << config.node_id << '\t' << item.neighbor_id << '\t'
                                 << item.bits_set << '\t' << item.bit_count << '\t'
                                 << (item.bit_count == 0 ? 0.0 :
                                     static_cast<double>(item.bits_set) / item.bit_count) << '\n';
                        }
                    }
                    atomic_write(control_root / "protocol-state" /
                                     (marker.stem().string() + "-node" +
                                      std::to_string(config.node_id) + ".tsv"),
                                 rows.str());
                }
            }
            const auto activation_path = control_root / "activation" /
                ("node" + std::to_string(config.node_id) + ".tsv");
            if (!activation_processed && std::filesystem::exists(activation_path)) {
                activation_processed = true;
                std::ifstream activation_input(activation_path);
                std::string activation_line;
                std::getline(activation_input, activation_line);
                const auto fields = split_tab_line(activation_line);
                std::string activation_status = "rejected";
                std::string activation_error = "invalid activation command";
                uint32_t peer_id = 0;
                uint32_t peer_port = 0;
                if (fields.size() == 6 && fields[5] == config.experiment_schedule_hash &&
                    parse_uint32_value(fields[0], peer_id) &&
                    parse_uint32_value(fields[2], peer_port) && peer_port <= 65535 &&
                    tcp_network != nullptr) {
                    const bool authorized =
                        (config.node_id == 1 && peer_id == 33) ||
                        (config.node_id == 33 && peer_id == 1);
                    if (!authorized) {
                        activation_error = "runtime federation activation is restricted to nodes 1 and 33";
                    } else if (config.federation_activation_mode == "direct-interface" &&
                               config.federation_interface.empty()) {
                        activation_error = "federation interface is not configured";
                    } else {
                        const bool logical_only =
                            config.federation_activation_mode == "logical-only";
                        const std::string expected_peer_link = config.node_id == 1
                            ? "172.31.16.2" : "172.31.16.1";
                        const std::string expected_peer_host = config.node_id == 1
                            ? "10.90.0.33" : "10.90.0.1";
                        if (fields[1] != expected_peer_host ||
                            (!logical_only && fields[4] != expected_peer_link) ||
                            (logical_only && fields[4] != "external-border-route")) {
                            activation_error =
                                "federation endpoint or link address is not authorized";
                        } else {
                            bool interface_ok = true;
                            if (!logical_only) {
                                const std::string command = "ip link set dev " +
                                    config.federation_interface + " up";
                                interface_ok = std::system(command.c_str()) == 0;
                            }
                            if (!interface_ok) {
                                activation_error = "failed to enable federation interface";
                            } else {
                                bool routes_ok = true;
                                if (!logical_only) {
                                    const uint32_t first = config.node_id == 1 ? 33 : 1;
                                    const uint32_t last = config.node_id == 1 ? 64 : 32;
                                    for (uint32_t target = first; target <= last; ++target) {
                                        const std::string route = "ip route replace 10.90.0." +
                                            std::to_string(target) + "/32 via " + fields[4] +
                                            " dev " + config.federation_interface +
                                            " src 10.90.0." + std::to_string(config.node_id);
                                        if (std::system(route.c_str()) != 0) routes_ok = false;
                                    }
                                }
                                TcpEndpoint endpoint{fields[1], static_cast<uint16_t>(peer_port)};
                                if (!routes_ok) {
                                    activation_error = "failed to install federation routes";
                                } else if (tcp_network->add_runtime_neighbor(peer_id, endpoint, activation_error)) {
                                    activation_status = "ok";
                                }
                            }
                        }
                    }
                }
                const uint64_t acknowledged_ns = steady_ns();
                rsync_trace::record(config.node_id, "federation_activation",
                                    {{"peer", std::to_string(peer_id)},
                                     {"status", activation_status},
                                     {"error", activation_error}});
                atomic_write(control_root / "activated" /
                                 ("node" + std::to_string(config.node_id) + ".tsv"),
                             std::to_string(config.node_id) + "\t" +
                                 std::to_string(peer_id) + "\t" + activation_status + "\t" +
                                 std::to_string(acknowledged_ns) + "\t" + activation_error + "\n");
            }
            if (active_request.has_value()) {
                const auto cancel_path = control_root / "cancel" /
                    (active_request->request_index + "-node" +
                     std::to_string(config.node_id) + ".marker");
                if (std::filesystem::exists(cancel_path)) {
                    const auto cancelled_index = active_request->request_index;
                    rsync_trace::record(config.node_id, "experiment_request_cancel",
                                        {{"experiment_request_index", cancelled_index},
                                         {"key", active_request->key},
                                         {"reason", "harness_isolation_probe_timeout"}});
                    active_request.reset();
                    atomic_write(control_root / "cancelled" /
                                     (cancelled_index + "-node" +
                                      std::to_string(config.node_id) + ".tsv"),
                                 cancelled_index + "\t" +
                                     std::to_string(config.node_id) + "\t" +
                                     std::to_string(steady_ns()) + "\n");
                }
            }
            const auto command_dir = control_root / "commands" /
                ("node" + std::to_string(config.node_id));
            if (!active_request.has_value() && std::filesystem::exists(command_dir)) {
                std::vector<std::filesystem::path> commands;
                for (const auto& entry : std::filesystem::directory_iterator(command_dir)) {
                    if (entry.is_regular_file() && entry.path().extension() == ".tsv") {
                        commands.push_back(entry.path());
                    }
                }
                std::sort(commands.begin(), commands.end());
                for (const auto& command : commands) {
                    if (!processed_commands.insert(command.string()).second) continue;
                    std::ifstream input(command);
                    std::string line;
                    std::getline(input, line);
                    const auto fields = split_tab_line(line);
                    if (fields.size() < 7 || fields[6] != config.experiment_schedule_hash) {
                        std::cerr << "[rsync-node-dev] invalid experiment command "
                                  << command << "\n";
                        continue;
                    }
                    uint32_t owner = 0;
                    uint64_t expected_size = 0;
                    if (!parse_uint32_value(fields[3], owner) ||
                        !parse_uint64_value(fields[4], expected_size)) {
                        std::cerr << "[rsync-node-dev] invalid command numbers " << command << "\n";
                        continue;
                    }
                    active_request = ActiveExperimentRequest{
                        fields[0], fields[2], expected_size, fields[5], steady_ns()};
                    rsync_trace::record(config.node_id, "experiment_request_start",
                                        {{"experiment_request_index", fields[0]},
                                         {"key", fields[2]},
                                         {"src", std::to_string(config.node_id)},
                                         {"dest", std::to_string(owner)},
                                         {"value_bytes", std::to_string(expected_size)},
                                         {"via", fields[1]}});
                    if (fields[1] == "known-owner") {
                        node.requestFileFromProvider(fields[2], owner);
                    } else {
                        node.requestFile({fields[2]});
                    }
                    break;
                }
            }
        }
        for (const auto& event : config.scenario_requests) {
            if (event.at_tick != tick) {
                continue;
            }

            std::cout << "[rsync-node-dev] tick=" << tick
                      << " firing scenario request for " << event.keys.size()
                      << " key(s)\n";
            for (const auto& key : event.keys) {
                rsync_trace::record(config.node_id, "schedule_fire",
                                    {{"key", key},
                                     {"src", std::to_string(config.node_id)},
                                     {"tick", std::to_string(tick)}});
            }
            node.requestFile(event.keys);
        }
        for (const auto& event : config.oracle_unicast_requests) {
            if (event.at_tick != tick) {
                continue;
            }

            std::cout << "[rsync-node-dev] tick=" << tick
                      << " firing oracle-unicast request key=" << event.key
                      << " provider=Node " << event.provider_node_id << "\n";
            rsync_trace::record(config.node_id, "schedule_fire",
                                {{"key", event.key},
                                 {"src", std::to_string(config.node_id)},
                                 {"dest", std::to_string(event.provider_node_id)},
                                 {"tick", std::to_string(tick)},
                                 {"via", "oracle_unicast"}});
            node.requestFileFromProvider(event.key, event.provider_node_id);
        }

        if (trace_loop_ticks) {
            rsync_trace::record(config.node_id, "process_one_start",
                                {{"tick", std::to_string(tick)}});
        }
        const bool protocol_activity = node.processOne();
        if (experiment_mode) {
            if (protocol_activity) {
                last_protocol_activity_ns = steady_ns();
                if (!active_quiesce_ack.empty()) {
                    std::error_code ignored;
                    std::filesystem::remove(active_quiesce_ack, ignored);
                }
            }
            const auto quiesce_dir = control_root / "quiesce";
            if (std::filesystem::exists(quiesce_dir)) {
                std::vector<std::filesystem::path> markers;
                for (const auto& entry : std::filesystem::directory_iterator(quiesce_dir)) {
                    if (entry.is_regular_file() && entry.path().extension() == ".marker") {
                        markers.push_back(entry.path());
                    }
                }
                std::sort(markers.begin(), markers.end());
                if (!markers.empty()) {
                    const auto index = markers.back().stem().string();
                    if (index != active_quiesce_index) {
                        active_quiesce_index = index;
                        active_quiesce_ack = control_root / "quiesced" /
                            (index + "-node" + std::to_string(config.node_id) + ".ack");
                    }
                    if (steady_ns() - last_protocol_activity_ns >= 100000000ULL &&
                        !std::filesystem::exists(active_quiesce_ack)) {
                        atomic_write(active_quiesce_ack,
                                     index + "\t" + std::to_string(config.node_id) + "\t" +
                                         std::to_string(last_protocol_activity_ns) + "\n");
                    }
                }
            }
        }
        if (trace_loop_ticks) {
            rsync_trace::record(config.node_id, "process_one_end",
                                {{"tick", std::to_string(tick)}});
        }
        if (config.loop_sleep_ms > 0) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(config.loop_sleep_ms));
        }
    }

    if (experiment_mode) {
        rsync_trace::flush();
        atomic_write(control_root / "stopped" /
                         ("node" + std::to_string(config.node_id) + ".stopped"),
                     "stopped\t" + std::to_string(config.node_id) + "\n");
    }
    std::cout << "[rsync-node-dev] completed " << config.loop_iterations
              << " loop iteration(s)\n";
    rsync_trace::flush();
    return EXIT_SUCCESS;
}
