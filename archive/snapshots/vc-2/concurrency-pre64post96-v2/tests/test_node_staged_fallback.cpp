#include <algorithm>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <chrono>
#include <cstdint>
#include <deque>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "cache/in_memory_cache.hpp"
#include "network/network_interface.hpp"
#include "node.hpp"
#include "packet.hpp"
#include "query_table.hpp"

namespace {

using namespace std::chrono_literals;

struct SentPacket {
    PacketType type = PacketType::REQUEST;
    uint64_t request_id = 0;
    uint32_t source = 0;
    uint32_t destination = 0;
    uint32_t hop_count = 0;
    bool fallback = false;
    bool aggregated = false;
    std::vector<RequestPacket::KeySignature> signatures;
    std::string key;
};

class RecordingNetwork final : public NetworkInterface {
public:
    explicit RecordingNetwork(std::vector<uint32_t> neighbors)
        : neighbors_(std::move(neighbors)) {}

    std::unique_ptr<Packet> receive() override {
        if (inbox_.empty()) return nullptr;
        auto packet = std::move(inbox_.front());
        inbox_.pop_front();
        return packet;
    }

    void send_direct(uint32_t destination,
                     std::unique_ptr<Packet> packet) override {
        assert(packet);
        SentPacket sent;
        sent.type = packet->type;
        sent.request_id = packet->request_id;
        sent.source = packet->src_node_id;
        sent.destination = destination;
        sent.hop_count = packet->hop_count;
        if (packet->type == PacketType::REQUEST) {
            const auto* request = static_cast<const RequestPacket*>(packet.get());
            sent.fallback = request->is_fallback;
            sent.aggregated = request->is_aggregated;
            sent.signatures = request->key_signatures;
        } else {
            sent.key = static_cast<const DataPacket*>(packet.get())->key;
        }
        sent_.push_back(std::move(sent));
    }

    void broadcast(std::unique_ptr<Packet> packet) override {
        assert(packet);
        for (const auto neighbor : neighbors_) {
            send_direct(neighbor, packet->clone());
        }
    }

    void configure_topology(const std::string&, uint32_t) override {}

    std::vector<uint32_t> get_neighbors() const override {
        return neighbors_;
    }

    void inject(std::unique_ptr<Packet> packet) {
        inbox_.push_back(std::move(packet));
    }

    void clearSent() { sent_.clear(); }

    const std::vector<SentPacket>& sent() const { return sent_; }

private:
    std::vector<uint32_t> neighbors_;
    std::deque<std::unique_ptr<Packet>> inbox_;
    std::vector<SentPacket> sent_;
};

Node::Config fallbackConfig() {
    Node::Config config;
    config.max_hop_count = 12;
    config.query_timeout = 250ms;
    config.fallback_delay = 20ms;
    config.bloom_bits = RequestPacket::kSignatureBloomBits;
    config.bloom_hash_count = RequestPacket::kSignatureHashCount;
    config.staged_fallback_enabled = true;
    return config;
}

std::vector<SentPacket> requestPackets(const RecordingNetwork& network) {
    std::vector<SentPacket> requests;
    for (const auto& sent : network.sent()) {
        if (sent.type == PacketType::REQUEST) requests.push_back(sent);
    }
    return requests;
}

std::unique_ptr<RequestPacket> fallbackPacket(
    uint64_t uid, uint32_t source, uint32_t destination, uint32_t hop,
    const std::string& key) {
    auto request = RequestPacket::create(
        source, {key}, RequestPacket::kSignatureBloomBits,
        RequestPacket::kSignatureHashCount);
    request->request_id = uid;
    request->src_node_id = source;
    request->dest_node_id = destination;
    request->hop_count = hop;
    request->is_fallback = true;
    request->is_aggregated = false;
    return request;
}

void testImmediateFallbackWhenOnlyMatchIsUpstream() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{1, 3, 4});
    auto* recording = network.get();
    auto cache = std::make_unique<InMemoryCache>();
    auto* local_cache = cache.get();
    Node node(2, std::move(network), std::move(cache), fallbackConfig());

    const std::string key = "only-upstream-matches";

    // Learning a returned key records node 1 in SyncTable. Remove the local
    // cache copy afterwards so this test isolates routing rather than service.
    recording->inject(DataPacket::create(
        7, 1, 2, key, Value(std::vector<uint8_t>{1, 2, 3})));
    assert(node.processOne());
    local_cache->del(key);
    recording->clearSent();

    auto preferred = RequestPacket::create(
        1, {key}, RequestPacket::kSignatureBloomBits,
        RequestPacket::kSignatureHashCount);
    preferred->dest_node_id = 2;
    preferred->hop_count = 3;
    preferred->is_aggregated = true;
    recording->inject(std::move(preferred));
    assert(node.processOne());

    auto requests = requestPackets(*recording);
    std::sort(requests.begin(), requests.end(),
              [](const SentPacket& lhs, const SentPacket& rhs) {
                  return lhs.destination < rhs.destination;
              });
    assert(requests.size() == 2);
    assert(requests[0].destination == 3);
    assert(requests[1].destination == 4);
    for (const auto& request : requests) {
        assert(!request.fallback);
        assert(request.hop_count == 4);
        assert(request.signatures.size() == 1);
        assert(request.signatures[0] ==
               RequestPacket::signatureForKey(key));
    }
}

void testDataBeforeDeadlineCancelsFallback() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{11});
    auto* recording = network.get();
    Node node(10, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    std::size_t deliveries = 0;
    node.setApplicationCallback(
        [&](const Node::ApplicationDelivery& delivery) {
            assert(delivery.key == "arrives-before-deadline");
            ++deliveries;
        });

    node.requestFile({"arrives-before-deadline"});
    const auto preferred = requestPackets(*recording);
    assert(preferred.size() == 1);
    assert(!preferred[0].fallback);
    recording->clearSent();

    recording->inject(DataPacket::create(
        preferred[0].request_id, 11, 10, "arrives-before-deadline",
        Value(std::vector<uint8_t>{4, 5, 6})));
    assert(node.processOne());
    assert(deliveries == 1);

    std::this_thread::sleep_for(30ms);
    node.processOne();
    assert(requestPackets(*recording).empty());
    assert(deliveries == 1);
}

void testDeadlineTriggersOnceAndDuplicateDataIsSuppressed() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{21});
    auto* recording = network.get();
    Node node(20, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    std::size_t deliveries = 0;
    node.setApplicationCallback(
        [&](const Node::ApplicationDelivery& delivery) {
            assert(delivery.key == "needs-fallback");
            ++deliveries;
        });

    node.requestFile({"needs-fallback"});
    recording->clearSent();
    std::this_thread::sleep_for(30ms);
    assert(node.processOne());

    const auto first_flush = requestPackets(*recording);
    assert(first_flush.size() == 1);
    assert(first_flush[0].fallback);
    assert(QueryTable::isStableUid(first_flush[0].request_id));
    assert(QueryTable::stableUidOrigin(first_flush[0].request_id) == 20);
    const auto uid = first_flush[0].request_id;

    recording->clearSent();
    assert(!node.processOne());
    assert(requestPackets(*recording).empty());

    recording->inject(DataPacket::create(
        uid, 21, 20, "needs-fallback",
        Value(std::vector<uint8_t>{7, 8, 9})));
    recording->inject(DataPacket::create(
        uid, 21, 20, "needs-fallback",
        Value(std::vector<uint8_t>{7, 8, 9})));
    assert(node.processOne());
    assert(deliveries == 1);
}

void testFallbackLoopAndDiamondDuplicatesAreSuppressed() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{31, 32, 33});
    auto* recording = network.get();
    Node node(30, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    const auto uid = QueryTable::makeStableUid(40, 1);
    const std::string key = "diamond-key";

    // First arrival establishes parent 31 and fans out into both branches.
    recording->inject(fallbackPacket(uid, 31, 30, 2, key));
    assert(node.processOne());
    auto first = requestPackets(*recording);
    std::sort(first.begin(), first.end(),
              [](const SentPacket& lhs, const SentPacket& rhs) {
                  return lhs.destination < rhs.destination;
              });
    assert(first.size() == 2);
    assert(first[0].destination == 32);
    assert(first[1].destination == 33);
    assert(first[0].fallback && first[1].fallback);
    assert(first[0].hop_count == 3 && first[1].hop_count == 3);

    // Equal-hop arrival from the other side of a diamond and a worse-hop
    // return around a ring must not emit another wave.
    recording->clearSent();
    recording->inject(fallbackPacket(uid, 32, 30, 2, key));
    recording->inject(fallbackPacket(uid, 33, 30, 4, key));
    assert(node.processOne());
    assert(requestPackets(*recording).empty());
}

void testCachedDataStillFulfilsPendingPreferredReversePathOnce() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{1, 3});
    auto* recording = network.get();
    Node node(2, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    const std::string key = "cached-pending-reverse-path";
    const Value value(std::vector<uint8_t>{10, 11, 12});

    // A preferred REQUEST arriving from node 1 creates a reverse-path
    // obligation.  It is forwarded toward node 3 because the value is not yet
    // local.
    auto preferred = RequestPacket::create(
        1, {key}, RequestPacket::kSignatureBloomBits,
        RequestPacket::kSignatureHashCount);
    const auto request_id = preferred->request_id;
    preferred->dest_node_id = 2;
    preferred->hop_count = 1;
    preferred->is_aggregated = true;
    recording->inject(std::move(preferred));
    assert(node.processOne());
    const auto requests = requestPackets(*recording);
    assert(requests.size() == 1);
    assert(requests[0].destination == 3);

    // Simulate an independently materialized local copy before the DATA for
    // this request returns.  Cache duplicate suppression must not hide the
    // still-pending reverse-path send to node 1.
    node.addLocalData(key, value);
    recording->clearSent();
    recording->inject(DataPacket::create(request_id, 3, 2, key, value));
    assert(node.processOne());

    std::vector<SentPacket> data;
    for (const auto& sent : recording->sent()) {
        if (sent.type == PacketType::DATA) data.push_back(sent);
    }
    assert(data.size() == 1);
    assert(data[0].destination == 1);
    assert(data[0].key == key);

    // The exact pending obligation is one-shot.  A second DATA copy must not
    // replay the stale Bloom-based QueryTable entry.
    recording->clearSent();
    recording->inject(DataPacket::create(request_id, 3, 2, key, value));
    assert(node.processOne());
    for (const auto& sent : recording->sent()) {
        assert(sent.type != PacketType::DATA);
    }
}

void testLocalProviderResponseConsumesPreferredReversePath() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{1, 3});
    auto* recording = network.get();
    Node node(2, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    const std::string key = "local-provider-consumes-reverse-path";
    const Value value(std::vector<uint8_t>{13, 14, 15});
    node.addLocalData(key, value);

    auto preferred = RequestPacket::create(
        1, {key}, RequestPacket::kSignatureBloomBits,
        RequestPacket::kSignatureHashCount);
    const auto request_id = preferred->request_id;
    preferred->dest_node_id = 2;
    preferred->hop_count = 1;
    preferred->is_aggregated = true;
    recording->inject(std::move(preferred));
    assert(node.processOne());

    std::size_t local_responses = 0;
    for (const auto& sent : recording->sent()) {
        if (sent.type == PacketType::DATA && sent.destination == 1) {
            ++local_responses;
        }
    }
    assert(local_responses == 1);

    // Preferred routing continues after a cache hit, so a downstream copy can
    // still return. The already-sent local response must prevent a duplicate
    // DATA from being relayed to the upstream requester.
    recording->clearSent();
    recording->inject(DataPacket::create(request_id, 3, 2, key, value));
    assert(node.processOne());
    for (const auto& sent : recording->sent()) {
        assert(sent.type != PacketType::DATA);
    }
}

void testFallbackParentImprovementDoesNotPoisonDataReturn() {
    auto network = std::make_unique<RecordingNetwork>(
        std::vector<uint32_t>{31, 32, 33});
    auto* recording = network.get();
    Node node(30, std::move(network), std::make_unique<InMemoryCache>(),
              fallbackConfig());
    const auto uid = QueryTable::makeStableUid(40, 77);
    const std::string key = "parent-improvement-race";
    const Value value(std::vector<uint8_t>{20, 21, 22});

    // First establish node 31 as the parent, then replace it with the shorter
    // path through node 32 while responses from the old tree may be in flight.
    recording->inject(fallbackPacket(uid, 31, 30, 4, key));
    assert(node.processOne());
    recording->clearSent();
    recording->inject(fallbackPacket(uid, 32, 30, 2, key));
    assert(node.processOne());

    // DATA arriving from the current parent travels in the wrong direction.
    // It must be suppressed without consuming the per-key return tombstone.
    recording->clearSent();
    recording->inject(DataPacket::create(uid, 32, 30, key, value));
    assert(node.processOne());
    for (const auto& sent : recording->sent()) {
        assert(sent.type != PacketType::DATA);
    }

    // A subsequent response from a child remains eligible and must return via
    // the current best parent, node 32.
    recording->clearSent();
    recording->inject(DataPacket::create(uid, 33, 30, key, value));
    assert(node.processOne());
    std::vector<SentPacket> data;
    for (const auto& sent : recording->sent()) {
        if (sent.type == PacketType::DATA) data.push_back(sent);
    }
    assert(data.size() == 1);
    assert(data[0].destination == 32);
    assert(data[0].key == key);
}

void testAsyncApplicationAssociation() {
    auto network = std::make_unique<RecordingNetwork>(std::vector<uint32_t>{2});
    auto* wire = network.get();
    Node node(1, std::move(network), std::make_unique<InMemoryCache>(), fallbackConfig());
    std::vector<Node::ApplicationDelivery> completed;
    node.setApplicationCallback([&](const auto& d) { if (!d.app_request_id.empty()) completed.push_back(d); });
    const Value value(std::vector<uint8_t>{1, 2, 3});
    node.addLocalData("warm", value);
    node.requestFileAsync("warm", "local1");
    node.requestFileAsync("warm", "local2");
    assert(completed.size() == 2 && completed[0].app_request_id == "local1");
    assert(completed[0].origin_query_uid == 0);
    node.requestFileAsync("cold", "cold1");
    node.requestFileAsync("cold", "cold2");
    node.requestFileAsync("other", "other1");
    wire->inject(DataPacket::create(9999, 2, 1, "other", value));
    node.processOne();
    assert(completed.size() == 3 && completed.back().app_request_id == "other1");
    wire->inject(DataPacket::create(8888, 2, 1, "cold", value));
    node.processOne();
    assert(completed.size() == 5);
    std::unordered_set<std::string> ids;
    for (const auto& d : completed) ids.insert(d.app_request_id);
    assert(ids.size() == 5 && ids.contains("cold1") && ids.contains("cold2"));
    assert(completed[3].origin_query_uid != completed[4].origin_query_uid);
    wire->inject(DataPacket::create(8888, 2, 1, "cold", value));
    wire->inject(DataPacket::create(7777, 2, 1, "unsolicited", value));
    node.processOne();node.processOne();
    assert(completed.size() == 5);
    bool rejected = false;
    try { node.requestFileAsync("warm", "local1"); } catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
}

void testAsyncFallbackAssociation() {
    auto network = std::make_unique<RecordingNetwork>(std::vector<uint32_t>{2});
    auto* wire = network.get();
    Node node(1, std::move(network), std::make_unique<InMemoryCache>(), fallbackConfig());
    std::vector<Node::ApplicationDelivery> done;
    node.setApplicationCallback([&](const auto& d) { if (!d.app_request_id.empty()) done.push_back(d); });
    node.requestFileAsync("same", "a");node.requestFileAsync("same", "b");
    std::this_thread::sleep_for(40ms);node.processOne();
    std::vector<uint64_t> uids;
    for (const auto& p : wire->sent()) if (p.fallback) uids.push_back(p.request_id);
    assert(uids.size() == 2);
    const Value value(std::vector<uint8_t>{1, 2, 3});
    wire->inject(DataPacket::create(uids[1], 2, 1, "same", value));node.processOne();
    assert(done.size() == 1 && done[0].origin_query_uid == uids[1]);
    wire->inject(DataPacket::create(uids[1], 2, 1, "same", value));node.processOne();
    assert(done.size() == 1);
    wire->inject(DataPacket::create(uids[0], 2, 1, "same", value));node.processOne();
    assert(done.size() == 2 && done[0].app_request_id != done[1].app_request_id);
}

}  // namespace

int main() {
    testImmediateFallbackWhenOnlyMatchIsUpstream();
    testDataBeforeDeadlineCancelsFallback();
    testDeadlineTriggersOnceAndDuplicateDataIsSuppressed();
    testFallbackLoopAndDiamondDuplicatesAreSuppressed();
    testCachedDataStillFulfilsPendingPreferredReversePathOnce();
    testLocalProviderResponseConsumesPreferredReversePath();
    testFallbackParentImprovementDoesNotPoisonDataReturn();
    testAsyncApplicationAssociation();
    testAsyncFallbackAssociation();
    std::cout << "node_staged_fallback: all tests passed\n";
    return 0;
}
