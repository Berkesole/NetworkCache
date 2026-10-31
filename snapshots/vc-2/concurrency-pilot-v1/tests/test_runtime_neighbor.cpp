#include "network/tcp_network.hpp"

#include <cassert>
#include <iostream>
#include <map>
#include <string>
#include <vector>

int main() {
    std::map<uint32_t, TcpEndpoint> endpoints{{1, {"10.90.0.1", 44001}}};
    std::map<uint32_t, std::string> sources{{1, "topology"}};
    std::vector<uint32_t> neighbors{2};
    std::string error;
    const TcpEndpoint peer{"10.90.0.33", 44033};
    assert(TcpNetwork::merge_runtime_neighbor(1, true, 33, peer, endpoints, sources, neighbors, error));
    assert(neighbors == std::vector<uint32_t>({2, 33}));
    assert(sources[33] == "runtime-local-bootstrap");
    assert(TcpNetwork::merge_runtime_neighbor(1, true, 33, peer, endpoints, sources, neighbors, error));
    assert(neighbors == std::vector<uint32_t>({2, 33}));
    assert(!TcpNetwork::merge_runtime_neighbor(1, true, 33, {"10.90.0.99", 44033}, endpoints, sources, neighbors, error));
    assert(!TcpNetwork::merge_runtime_neighbor(1, true, 1, peer, endpoints, sources, neighbors, error));
    assert(!TcpNetwork::merge_runtime_neighbor(1, false, 34, peer, endpoints, sources, neighbors, error));
    std::cout << "runtime neighbor tests passed\n";
}
