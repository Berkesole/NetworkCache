#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <exception>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

struct Config {
    uint32_t node_id = 0;
    uint16_t listen_port = 0;
    uint32_t ticks = 0;
    uint32_t loop_sleep_ms = 25;
    uint32_t connect_timeout_ms = 5000;
    std::string data_file;
    std::string schedule_file;
    std::string log_file;
};

struct Request {
    uint32_t tick = 0;
    std::string request_index;
    std::string phase;
    std::string request_pair_id;
    std::string distance_class;
    std::string expected_cold_provider_hop;
    std::string expected_warm_cache_hop;
    std::string key;
    uint32_t provider = 0;
    std::string provider_ip;
    uint16_t provider_port = 0;
};

std::mutex log_mutex;

std::vector<std::string> split_tsv(const std::string& line) {
    std::vector<std::string> parts;
    std::string current;
    std::istringstream stream(line);
    while (std::getline(stream, current, '\t')) {
        parts.push_back(current);
    }
    return parts;
}

uint32_t parse_u32(const std::string& value, const std::string& name) {
    try {
        size_t consumed = 0;
        const auto parsed = std::stoul(value, &consumed, 10);
        if (consumed != value.size() || parsed > UINT32_MAX) {
            throw std::out_of_range(name);
        }
        return static_cast<uint32_t>(parsed);
    } catch (const std::exception&) {
        throw std::runtime_error("invalid " + name + ": " + value);
    }
}

uint16_t parse_u16(const std::string& value, const std::string& name) {
    const auto parsed = parse_u32(value, name);
    if (parsed > UINT16_MAX) {
        throw std::runtime_error("invalid " + name + ": " + value);
    }
    return static_cast<uint16_t>(parsed);
}

std::string utc_timestamp_ns() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto seconds = time_point_cast<std::chrono::seconds>(now);
    const auto ns = duration_cast<nanoseconds>(now - seconds).count();
    const std::time_t tt = system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&tt, &tm);
    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S")
        << "." << std::setw(9) << std::setfill('0') << ns << "Z";
    return out.str();
}

void log_event(std::ofstream& log, const std::map<std::string, std::string>& fields) {
    std::lock_guard<std::mutex> lock(log_mutex);
    log << utc_timestamp_ns() << " oracle_event";
    for (const auto& [key, value] : fields) {
        log << " " << key << "=" << value;
    }
    log << "\n";
    log.flush();
}

std::map<std::string, std::string> load_data(const std::string& path) {
    std::map<std::string, std::string> data;
    std::ifstream input(path);
    if (!input.good()) {
        return data;
    }
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto parts = split_tsv(line);
        if (parts.size() < 2 || parts[0] == "key") {
            continue;
        }
        data[parts[0]] = parts[1];
    }
    return data;
}

std::vector<Request> load_schedule(const std::string& path) {
    std::vector<Request> requests;
    std::ifstream input(path);
    if (!input.good()) {
        return requests;
    }
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        const auto parts = split_tsv(line);
        if (parts.empty() || parts[0] == "tick") {
            continue;
        }
        if (parts.size() < 11) {
            throw std::runtime_error("invalid schedule row: " + line);
        }
        Request request;
        request.tick = parse_u32(parts[0], "tick");
        request.request_index = parts[1];
        request.phase = parts[2];
        request.request_pair_id = parts[3];
        request.distance_class = parts[4];
        request.expected_cold_provider_hop = parts[5];
        request.expected_warm_cache_hop = parts[6];
        request.key = parts[7];
        request.provider = parse_u32(parts[8], "provider");
        request.provider_ip = parts[9];
        request.provider_port = parse_u16(parts[10], "provider_port");
        requests.push_back(std::move(request));
    }
    return requests;
}

bool send_all(int fd, const std::string& value) {
    const char* data = value.data();
    size_t remaining = value.size();
    while (remaining > 0) {
        const ssize_t sent = ::send(fd, data, remaining, 0);
        if (sent <= 0) {
            return false;
        }
        data += sent;
        remaining -= static_cast<size_t>(sent);
    }
    return true;
}

std::string read_line(int fd) {
    std::string line;
    char c = '\0';
    while (true) {
        const ssize_t read_count = ::recv(fd, &c, 1, 0);
        if (read_count <= 0) {
            break;
        }
        if (c == '\n') {
            break;
        }
        line.push_back(c);
    }
    return line;
}

void set_socket_timeout(int fd, uint32_t timeout_ms) {
    timeval tv{};
    tv.tv_sec = static_cast<time_t>(timeout_ms / 1000);
    tv.tv_usec = static_cast<suseconds_t>((timeout_ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

void run_server(const Config& config,
                const std::map<std::string, std::string>& data,
                std::ofstream& log,
                std::atomic<bool>& stop) {
    const int listen_fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        throw std::runtime_error("socket() failed");
    }

    int reuse = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(config.listen_port);
    if (::bind(listen_fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        const std::string error = std::strerror(errno);
        ::close(listen_fd);
        throw std::runtime_error("bind() failed: " + error);
    }
    if (::listen(listen_fd, 128) < 0) {
        const std::string error = std::strerror(errno);
        ::close(listen_fd);
        throw std::runtime_error("listen() failed: " + error);
    }

    log_event(log, {{"event", "oracle_server_listen"},
                    {"node", std::to_string(config.node_id)},
                    {"port", std::to_string(config.listen_port)}});

    while (!stop.load()) {
        fd_set read_set;
        FD_ZERO(&read_set);
        FD_SET(listen_fd, &read_set);
        timeval timeout{};
        timeout.tv_sec = 0;
        timeout.tv_usec = 200000;
        const int ready = ::select(listen_fd + 1, &read_set, nullptr, nullptr, &timeout);
        if (ready <= 0 || !FD_ISSET(listen_fd, &read_set)) {
            continue;
        }
        sockaddr_in peer{};
        socklen_t peer_len = sizeof(peer);
        const int client_fd = ::accept(listen_fd, reinterpret_cast<sockaddr*>(&peer), &peer_len);
        if (client_fd < 0) {
            continue;
        }
        set_socket_timeout(client_fd, config.connect_timeout_ms);
        const std::string request_line = read_line(client_fd);
        const auto parts = split_tsv(request_line);
        const std::string request_index = parts.empty() ? "" : parts[0];
        const std::string key = parts.size() > 1 ? parts[1] : "";
        const auto value_it = data.find(key);
        const std::string value = value_it == data.end() ? "" : value_it->second;
        log_event(log, {{"event", "oracle_provider_receive"},
                        {"node", std::to_string(config.node_id)},
                        {"request_index", request_index},
                        {"key", key},
                        {"value_bytes", std::to_string(value.size())}});
        const std::string response = std::to_string(value.size()) + "\t" + value + "\n";
        send_all(client_fd, response);
        ::close(client_fd);
    }

    ::close(listen_fd);
}

int connect_with_retry(const Request& request, uint32_t timeout_ms) {
    using namespace std::chrono;
    const auto deadline = steady_clock::now() + milliseconds(timeout_ms);
    while (steady_clock::now() < deadline) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) {
            std::this_thread::sleep_for(milliseconds(20));
            continue;
        }
        set_socket_timeout(fd, timeout_ms);
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(request.provider_port);
        if (::inet_pton(AF_INET, request.provider_ip.c_str(), &address.sin_addr) == 1 &&
            ::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0) {
            return fd;
        }
        ::close(fd);
        std::this_thread::sleep_for(milliseconds(20));
    }
    return -1;
}

void run_client_schedule(const Config& config,
                         const std::vector<Request>& requests,
                         std::ofstream& log,
                         const std::chrono::steady_clock::time_point& start) {
    using namespace std::chrono;
    for (const auto& request : requests) {
        std::this_thread::sleep_until(start + milliseconds(config.loop_sleep_ms * request.tick));
        log_event(log, {{"event", "oracle_request_start"},
                        {"node", std::to_string(config.node_id)},
                        {"request_index", request.request_index},
                        {"phase", request.phase},
                        {"request_pair_id", request.request_pair_id},
                        {"distance_class", request.distance_class},
                        {"expected_cold_provider_hop", request.expected_cold_provider_hop},
                        {"expected_warm_cache_hop", request.expected_warm_cache_hop},
                        {"key", request.key},
                        {"provider", std::to_string(request.provider)}});

        const int fd = connect_with_retry(request, config.connect_timeout_ms);
        if (fd < 0) {
            log_event(log, {{"event", "oracle_app_receive"},
                            {"node", std::to_string(config.node_id)},
                            {"request_index", request.request_index},
                            {"key", request.key},
                            {"provider", std::to_string(request.provider)},
                            {"status", "connect_failed"},
                            {"value_bytes", ""}});
            continue;
        }

        const std::string request_line = request.request_index + "\t" + request.key + "\n";
        const bool sent = send_all(fd, request_line);
        const std::string response = sent ? read_line(fd) : "";
        ::close(fd);

        std::string value_bytes;
        std::string status = "ok";
        const auto tab = response.find('\t');
        if (!sent || tab == std::string::npos) {
            status = "read_failed";
        } else {
            value_bytes = response.substr(0, tab);
        }

        log_event(log, {{"event", "oracle_app_receive"},
                        {"node", std::to_string(config.node_id)},
                        {"request_index", request.request_index},
                        {"key", request.key},
                        {"provider", std::to_string(request.provider)},
                        {"status", status},
                        {"value_bytes", value_bytes}});
    }
}

Config parse_args(int argc, char** argv) {
    Config config;
    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        auto require_value = [&](const std::string& name) -> std::string {
            if (index + 1 >= argc) {
                throw std::runtime_error("missing value for " + name);
            }
            return argv[++index];
        };

        if (arg == "--node-id") {
            config.node_id = parse_u32(require_value(arg), "node-id");
        } else if (arg == "--listen-port") {
            config.listen_port = parse_u16(require_value(arg), "listen-port");
        } else if (arg == "--ticks") {
            config.ticks = parse_u32(require_value(arg), "ticks");
        } else if (arg == "--loop-sleep-ms") {
            config.loop_sleep_ms = parse_u32(require_value(arg), "loop-sleep-ms");
        } else if (arg == "--connect-timeout-ms") {
            config.connect_timeout_ms = parse_u32(require_value(arg), "connect-timeout-ms");
        } else if (arg == "--data-file") {
            config.data_file = require_value(arg);
        } else if (arg == "--schedule-file") {
            config.schedule_file = require_value(arg);
        } else if (arg == "--log-file") {
            config.log_file = require_value(arg);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (config.node_id == 0 || config.listen_port == 0 || config.ticks == 0 ||
        config.data_file.empty() || config.schedule_file.empty() || config.log_file.empty()) {
        throw std::runtime_error("missing required oracle-direct-fetch arguments");
    }
    return config;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Config config = parse_args(argc, argv);
        auto data = load_data(config.data_file);
        auto schedule = load_schedule(config.schedule_file);
        std::ofstream log(config.log_file, std::ios::app);
        if (!log.good()) {
            throw std::runtime_error("unable to open log file: " + config.log_file);
        }

        std::atomic<bool> stop{false};
        const auto start = std::chrono::steady_clock::now();
        std::exception_ptr server_error;
        std::exception_ptr client_error;
        std::thread server([&] {
            try {
                run_server(config, data, log, stop);
            } catch (...) {
                server_error = std::current_exception();
                stop.store(true);
            }
        });
        std::thread client([&] {
            try {
                run_client_schedule(config, schedule, log, start);
            } catch (...) {
                client_error = std::current_exception();
                stop.store(true);
            }
        });

        const auto runtime = std::chrono::milliseconds(config.loop_sleep_ms * config.ticks + 3000);
        std::this_thread::sleep_until(start + runtime);
        if (client.joinable()) {
            client.join();
        }
        stop.store(true);
        if (server.joinable()) {
            server.join();
        }
        if (server_error) {
            std::rethrow_exception(server_error);
        }
        if (client_error) {
            std::rethrow_exception(client_error);
        }

        log_event(log, {{"event", "oracle_node_done"},
                        {"node", std::to_string(config.node_id)}});
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "oracle-direct-fetch error: " << e.what() << "\n";
        return 2;
    }
}
