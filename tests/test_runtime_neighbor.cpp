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
#include <chrono>
#include <thread>
#include <future>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>

// Deterministic injection at the IO-thread enqueue boundary, no socket races.
struct TcpNetworkTestAccess {
    static void push(TcpNetwork& network, uint64_t id) {
        RequestPacket p; p.request_id=id; p.src_node_id=2;
        network.enqueue_frame({PacketCodec::encode(p)});
    }
    static void malformed(TcpNetwork& network) { network.enqueue_frame({{0xff}}); }
    static uint16_t start(TcpNetwork& n, std::map<uint32_t,TcpEndpoint> endpoints={}) {
        assert(n.open_listener({"127.0.0.1",0},"test"));
        sockaddr_in address{};socklen_t size=sizeof(address);
        assert(::getsockname(n.listener_fd_,reinterpret_cast<sockaddr*>(&address),&size)==0);
        n.endpoints_=std::move(endpoints);n.configured_=true;n.start_io_thread();
        return ntohs(address.sin_port);
    }
};

static void test_nonblocking_send_and_peer_isolation() {
    using namespace std::chrono_literals;
    // A peer that never accepts fills its tiny SYN/accept backlog.
    int slow=::socket(AF_INET,SOCK_STREAM,0);assert(slow>=0);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    assert(::bind(slow,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0);
    assert(::listen(slow,1)==0);socklen_t size=sizeof(address);
    assert(::getsockname(slow,reinterpret_cast<sockaddr*>(&address),&size)==0);
    {
        TcpNetwork receiver(2);const auto fast_port=TcpNetworkTestAccess::start(receiver);
        TcpNetwork sender(1);TcpNetworkTestAccess::start(sender,{{2,{"127.0.0.1",fast_port}},{3,{"127.0.0.1",ntohs(address.sin_port)}}});
        auto submitted=std::async(std::launch::async,[&] {
            for (uint64_t id=1;id<=64;++id) {
                auto p=std::make_unique<RequestPacket>();p->request_id=id;p->src_node_id=1;
                sender.send_direct(3,std::move(p));
            }
            for (uint64_t id=101;id<=116;++id) {
                auto p=std::make_unique<RequestPacket>();p->request_id=id;p->src_node_id=1;
                sender.send_direct(2,std::move(p));
            }
        });
        assert(submitted.wait_for(1s)==std::future_status::ready);submitted.get();
        std::vector<uint64_t> received;const auto deadline=std::chrono::steady_clock::now()+2s;
        while (received.size()<16 && std::chrono::steady_clock::now()<deadline) {
            if (auto p=receiver.receive()) received.push_back(p->request_id);
            std::this_thread::sleep_for(1ms);
        }
        assert(received.size()==16);
        for (std::size_t i=0;i<received.size();++i) assert(received[i]==101+i);
        assert(sender.has_pending_io()); // Stalled peer remains honest pending work.
    } // Runtime teardown must not wait for a blocked connect.
    ::close(slow);
}

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
    test_nonblocking_send_and_peer_isolation();
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
