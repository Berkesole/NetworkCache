#pragma once

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

// Forward declaration
struct ParsedNodeConfig;

// Helper to trim whitespace from a string
static inline std::string trim(const std::string& s) {
    size_t start = 0;
    while (start < s.size() &&
           std::isspace(static_cast<unsigned char>(s[start])) != 0) {
        ++start;
    }

    size_t end = s.size();
    while (end > start &&
           std::isspace(static_cast<unsigned char>(s[end - 1])) != 0) {
        --end;
    }

    return s.substr(start, end - start);
}

static inline std::string strip_inline_comment(const std::string& s) {
    bool in_single_quotes = false;
    bool in_double_quotes = false;

    for (size_t index = 0; index < s.size(); ++index) {
        const char current = s[index];
        if (current == '\'' && !in_double_quotes) {
            in_single_quotes = !in_single_quotes;
            continue;
        }
        if (current == '"' && !in_single_quotes) {
            in_double_quotes = !in_double_quotes;
            continue;
        }
        if (current == '#' && !in_single_quotes && !in_double_quotes) {
            return s.substr(0, index);
        }
    }

    return s;
}

static inline std::string strip_optional_quotes(std::string value) {
    if (value.size() >= 2) {
        const char first = value.front();
        const char last = value.back();
        if ((first == '"' && last == '"') ||
            (first == '\'' && last == '\'')) {
            value = value.substr(1, value.size() - 2);
        }
    }
    return value;
}

struct ParsedNodeConfig {
    uint32_t id = 0;
    std::vector<uint32_t> neighbors;
    std::optional<std::string> host;
    std::optional<uint16_t> port;
};

struct ProtocolConfig {
    uint32_t max_hop_count = 6;
};

struct ScenarioDataItem {
    uint32_t node_id;
    std::string key;
    std::string value;
};

struct ScenarioRequestItem {
    uint32_t node_id;
    std::vector<std::string> keys;
    uint32_t at_tick = 0;  // tick at which to fire this request
};

struct ScenarioConfig {
    uint32_t ticks = 10;
    std::vector<ScenarioDataItem> data;
    std::vector<ScenarioRequestItem> requests;
};

struct ParseResult {
    std::map<uint32_t, ParsedNodeConfig> topology;
    ProtocolConfig protocol;
    ScenarioConfig scenario;
};

// Parser for a simplified YAML-like topology file
class TopologyParser {
    enum class ScenarioList { None, Data, Requests };

public:
    std::map<uint32_t, ParsedNodeConfig> parse(const std::string& config_content) {
        return parse_config(config_content).topology;
    }

    ParseResult parse_config(const std::string& config_content) {
        errors_.clear();

        ParseResult result;
        std::stringstream ss(config_content);
        std::string line;

        std::optional<uint32_t> current_node_id;
        enum class Section { None, Protocol, Nodes, Scenario } section = Section::None;
        ScenarioList scenario_list = ScenarioList::None;
        bool saw_nodes_section = false;
        size_t line_number = 0;

        while (std::getline(ss, line)) {
            ++line_number;
            const std::string raw_line = line;
            line = trim(strip_inline_comment(line));

            if (line.empty()) {
                continue;
            }

            if (line == "protocol:") {
                section = Section::Protocol;
                current_node_id.reset();
                scenario_list = ScenarioList::None;
                continue;
            }
            if (line == "nodes:") {
                section = Section::Nodes;
                current_node_id.reset();
                scenario_list = ScenarioList::None;
                saw_nodes_section = true;
                continue;
            }
            if (line == "scenario:") {
                section = Section::Scenario;
                current_node_id.reset();
                scenario_list = ScenarioList::None;
                continue;
            }

            switch (section) {
                case Section::Protocol:
                    parse_protocol_line(line_number, raw_line, line, result.protocol);
                    break;
                case Section::Scenario:
                    parse_scenario_line(line_number,
                                        raw_line,
                                        line,
                                        scenario_list,
                                        result.scenario);
                    break;
                case Section::Nodes:
                    parse_node_line(line_number,
                                    raw_line,
                                    line,
                                    current_node_id,
                                    result.topology);
                    break;
                case Section::None:
                    report_error(line_number,
                                 "content outside a recognized section is ignored",
                                 raw_line);
                    break;
            }
        }

        if (!saw_nodes_section) {
            report_error("missing required 'nodes:' section");
        }

        return result;
    }

    bool has_errors() const {
        return !errors_.empty();
    }

    const std::vector<std::string>& errors() const {
        return errors_;
    }

private:
    static bool parse_uint32(const std::string& value,
                             uint32_t& out_value,
                             std::string& error) {
        try {
            size_t consumed = 0;
            const unsigned long parsed = std::stoul(value, &consumed, 10);
            if (consumed != value.size() ||
                parsed == 0 ||
                parsed > std::numeric_limits<uint32_t>::max()) {
                throw std::out_of_range("expected integer in range 1-4294967295");
            }
            out_value = static_cast<uint32_t>(parsed);
            return true;
        } catch (const std::exception& e) {
            error = e.what();
            return false;
        }
    }

    static bool parse_non_negative_uint32(const std::string& value,
                                          uint32_t& out_value,
                                          std::string& error) {
        try {
            size_t consumed = 0;
            const unsigned long parsed = std::stoul(value, &consumed, 10);
            if (consumed != value.size() ||
                parsed > std::numeric_limits<uint32_t>::max()) {
                throw std::out_of_range("expected integer in range 0-4294967295");
            }
            out_value = static_cast<uint32_t>(parsed);
            return true;
        } catch (const std::exception& e) {
            error = e.what();
            return false;
        }
    }

    void parse_protocol_line(size_t line_number,
                             const std::string& raw_line,
                             const std::string& line,
                             ProtocolConfig& protocol) {
        if (line.rfind("max_hop_count:", 0) != 0) {
            return;
        }

        const std::string value = trim(line.substr(line.find(':') + 1));
        uint32_t max_hop_count = 0;
        std::string error;
        if (!parse_uint32(value, max_hop_count, error)) {
            report_error(line_number,
                         "invalid max_hop_count '" + value + "': " + error,
                         raw_line);
            return;
        }

        protocol.max_hop_count = max_hop_count;
    }

    void parse_scenario_line(size_t line_number,
                             const std::string& raw_line,
                             const std::string& line,
                             ScenarioList& scenario_list,
                             ScenarioConfig& scenario) {
        if (line.rfind("ticks:", 0) == 0) {
            const std::string value = trim(line.substr(line.find(':') + 1));
            uint32_t ticks = 0;
            std::string error;
            if (!parse_non_negative_uint32(value, ticks, error)) {
                report_error(line_number,
                             "invalid scenario ticks '" + value + "': " + error,
                             raw_line);
            } else {
                scenario.ticks = ticks;
            }
            scenario_list = ScenarioList::None;
            return;
        }

        if (line == "data:") {
            scenario_list = ScenarioList::Data;
            return;
        }

        if (line == "requests:") {
            scenario_list = ScenarioList::Requests;
            return;
        }

        if (line.rfind("- ", 0) != 0) {
            return;
        }

        if (scenario_list == ScenarioList::None) {
            report_error(line_number,
                         "scenario item found before data: or requests:",
                         raw_line);
            return;
        }

        const std::string item = trim(line.substr(2));
        const size_t colon = item.find(':');
        if (colon == std::string::npos) {
            report_error(line_number, "malformed scenario item", raw_line);
            return;
        }

        uint32_t node_id = 0;
        std::string error;
        const std::string node_id_value = trim(item.substr(0, colon));
        if (!parse_uint32(node_id_value, node_id, error)) {
            report_error(line_number,
                         "invalid scenario node id '" + node_id_value + "': " + error,
                         raw_line);
            return;
        }

        const std::string rest = trim(item.substr(colon + 1));
        if (scenario_list == ScenarioList::Data) {
            parse_scenario_data_item(line_number, raw_line, node_id, rest, scenario);
        } else {
            parse_scenario_request_item(line_number, raw_line, node_id, rest, scenario);
        }
    }

    void parse_scenario_data_item(size_t line_number,
                                  const std::string& raw_line,
                                  uint32_t node_id,
                                  const std::string& rest,
                                  ScenarioConfig& scenario) {
        const size_t separator = rest.find('=');
        if (separator == std::string::npos) {
            report_error(line_number, "scenario data item missing '='", raw_line);
            return;
        }

        ScenarioDataItem item;
        item.node_id = node_id;
        item.key = trim(rest.substr(0, separator));
        item.value = trim(rest.substr(separator + 1));
        if (item.key.empty()) {
            report_error(line_number, "scenario data item has an empty key", raw_line);
            return;
        }

        scenario.data.push_back(std::move(item));
    }

    void parse_scenario_request_item(size_t line_number,
                                     const std::string& raw_line,
                                     uint32_t node_id,
                                     const std::string& rest,
                                     ScenarioConfig& scenario) {
        ScenarioRequestItem item;
        item.node_id = node_id;

        std::string keys_part = rest;
        const size_t at = rest.rfind('@');
        if (at != std::string::npos) {
            keys_part = trim(rest.substr(0, at));
            const std::string tick_value = trim(rest.substr(at + 1));
            std::string error;
            if (!parse_non_negative_uint32(tick_value, item.at_tick, error)) {
                report_error(line_number,
                             "invalid scenario request @tick '" + tick_value +
                                 "': " + error,
                             raw_line);
            }
        }

        std::stringstream keys(keys_part);
        std::string key;
        while (std::getline(keys, key, ',')) {
            key = trim(key);
            if (!key.empty()) {
                item.keys.push_back(key);
            }
        }

        if (item.keys.empty()) {
            report_error(line_number,
                         "scenario request item does not include any keys",
                         raw_line);
            return;
        }

        scenario.requests.push_back(std::move(item));
    }

    void parse_node_line(size_t line_number,
                         const std::string& raw_line,
                         const std::string& line,
                         std::optional<uint32_t>& current_node_id,
                         std::map<uint32_t, ParsedNodeConfig>& topology) {
        if (line.rfind("- id:", 0) == 0) {
            const std::string id_str = trim(line.substr(line.find(':') + 1));
            uint32_t parsed_id = 0;
            std::string error;
            if (!parse_uint32(id_str, parsed_id, error)) {
                report_error(line_number,
                             "invalid node id '" + id_str + "': " + error,
                             raw_line);
                current_node_id.reset();
                return;
            }

            current_node_id = parsed_id;
            auto& node_config = topology[*current_node_id];
            if (node_config.id == *current_node_id) {
                report_error(line_number,
                             "duplicate node id; merging fields into the existing node entry",
                             raw_line);
            }
            node_config.id = *current_node_id;
            return;
        }

        if (!current_node_id.has_value()) {
            report_error(line_number,
                         "node property found before a valid '- id:' entry",
                         raw_line);
            return;
        }

        if (line.rfind("neighbors:", 0) == 0) {
            parse_neighbors_line(line_number,
                                 raw_line,
                                 line,
                                 *current_node_id,
                                 topology);
            return;
        }

        if (line.rfind("host:", 0) == 0) {
            std::string host = trim(line.substr(line.find(':') + 1));
            host = trim(strip_optional_quotes(host));
            if (host.empty()) {
                report_error(line_number,
                             "empty host value for node " +
                                 std::to_string(*current_node_id),
                             raw_line);
            } else {
                topology[*current_node_id].host = std::move(host);
            }
            return;
        }

        if (line.rfind("port:", 0) == 0) {
            parse_port_line(line_number,
                            raw_line,
                            line,
                            *current_node_id,
                            topology);
            return;
        }

        // Ignore unrecognized properties for forward compatibility.
    }

    void parse_neighbors_line(size_t line_number,
                              const std::string& raw_line,
                              const std::string& line,
                              uint32_t current_node_id,
                              std::map<uint32_t, ParsedNodeConfig>& topology) {
        std::string neighbors_str = trim(line.substr(line.find(':') + 1));
        if (neighbors_str.empty() ||
            neighbors_str.front() != '[' ||
            neighbors_str.back() != ']') {
            report_error(line_number,
                         "malformed neighbors list for node " +
                             std::to_string(current_node_id) + ": " +
                             neighbors_str,
                         raw_line);
            return;
        }

        neighbors_str = neighbors_str.substr(1, neighbors_str.length() - 2);
        std::stringstream ns_ss(neighbors_str);
        std::string neighbor_id_str;
        while (std::getline(ns_ss, neighbor_id_str, ',')) {
            neighbor_id_str = trim(neighbor_id_str);
            if (neighbor_id_str.empty()) {
                continue;
            }

            uint32_t parsed_neighbor = 0;
            std::string error;
            if (!parse_uint32(neighbor_id_str, parsed_neighbor, error)) {
                report_error(line_number,
                             "invalid neighbor id '" + neighbor_id_str +
                                 "' for node " + std::to_string(current_node_id) +
                                 ": " + error,
                             raw_line);
                continue;
            }

            topology[current_node_id].neighbors.push_back(parsed_neighbor);
        }
    }

    void parse_port_line(size_t line_number,
                         const std::string& raw_line,
                         const std::string& line,
                         uint32_t current_node_id,
                         std::map<uint32_t, ParsedNodeConfig>& topology) {
        const std::string port_str = trim(line.substr(line.find(':') + 1));
        try {
            size_t consumed = 0;
            const unsigned long port = std::stoul(port_str, &consumed, 10);
            if (consumed != port_str.size() ||
                port == 0 ||
                port > std::numeric_limits<uint16_t>::max()) {
                throw std::out_of_range("expected integer in range 1-65535");
            }
            topology[current_node_id].port = static_cast<uint16_t>(port);
        } catch (const std::exception& e) {
            report_error(line_number,
                         "invalid port '" + port_str + "' for node " +
                             std::to_string(current_node_id) + ": " + e.what(),
                         raw_line);
        }
    }

    void report_error(size_t line_number,
                      const std::string& message,
                      const std::string& raw_line) {
        std::ostringstream stream;
        stream << "TopologyParser: line " << line_number << ": " << message;
        const std::string trimmed_line = trim(raw_line);
        if (!trimmed_line.empty()) {
            stream << " [" << trimmed_line << "]";
        }

        const std::string formatted = stream.str();
        errors_.push_back(formatted);
        std::cerr << formatted << std::endl;
    }

    void report_error(const std::string& message) {
        const std::string formatted = "TopologyParser: " + message;
        errors_.push_back(formatted);
        std::cerr << formatted << std::endl;
    }

    std::vector<std::string> errors_;
};
