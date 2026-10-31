#include "network/tcp_network.hpp"
#include "network/packet_codec.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif

#include <cassert>
#include <iostream>
#include <map>
#include <string>
#include <vector>

// Deterministic injection at the IO-thread enqueue boundary, no socket races.
struct TcpNetworkTestAccess {
    static void push(TcpNetwork& network, uint64_t id) {
        RequestPacket p; p.request_id=id; p.src_node_id=2;
        network.enqueue_frame({PacketCodec::encode(p)});
    }
    static void malformed(TcpNetwork& network) { network.enqueue_frame({{0xff}}); }
};

static void test_receive_snapshot() {
    TcpNetwork n(1);
    n.begin_receive_batch();
    TcpNetworkTestAccess::push(n,1);
    assert(!n.receive()); // Empty entry snapshot stays empty.
    n.begin_receive_batch();
    TcpNetworkTestAccess::push(n,2);
    assert(n.receive()->request_id==1);
    TcpNetworkTestAccess::push(n,3);
    assert(!n.receive()); // Live producer cannot extend the current tick.
    n.begin_receive_batch();
    assert(n.receive()->request_id==2);
    TcpNetworkTestAccess::push(n,4);
    assert(n.receive()->request_id==3);
    assert(!n.receive());
    n.begin_receive_batch();
    assert(n.receive()->request_id==4);
    assert(!n.receive());
    TcpNetworkTestAccess::malformed(n);
    TcpNetworkTestAccess::push(n,5);
    n.begin_receive_batch();
    assert(!n.receive());
    n.begin_receive_batch(); // Malformed frame does not discard following FIFO data.
    assert(n.receive()->request_id==5);
    assert(!n.receive());
}

int main() {
    test_receive_snapshot();
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
