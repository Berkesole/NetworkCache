// Node-level integration tests for the finite replica cache.
//
// These run two real Node instances over an in-process network (no Kathara, no
// image) and cover:
//   * the three cache write paths (owner seeding, and both DATA paths);
//   * every read path used to serve a request;
//   * one delivery per application request with a correct value;
//   * replica eviction removing the directory entry while owners survive;
//   * cache bypass not affecting forwarding or delivery;
//   * eviction at the same time as an in-flight DATA send;
//   * a request for an object nobody holds producing no successful DATA;
//   * immutable-object integrity under the real fromEnv() production
//     configuration: conflicting DATA aborts, an unreadable local reference
//     drops the packet without consuming protocol state, a verified duplicate
//     issues exactly one integrity read (no redundant second read), and two
//     still-pending requests both survive a conflict;
//   * legacy backend-error propagation: local/provider reads and DATA writes
//     rethrow the original failure instead of becoming a miss, a successful
//     service or an admission status, with no extra backend round trip.
//
// Delivery assertions use both the application callback and the real structured
// trace (rsync_trace::record); the cache event sink never carries app_receive.
// main() installs an isolated trace file before any test records and restores
// the environment afterwards.

#include "cache_test_support.hpp"

#include "cache/in_memory_cache.hpp"
#include "event_trace.hpp"
#include "network/network_interface.hpp"
#include "node.hpp"
#include "packet.hpp"

#include <chrono>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <unistd.h>
#include <vector>

using cache_test::makeValueOfSize;
using cache_test::RecordingCacheEventSink;

namespace {

struct Mesh {
    std::map<std::uint32_t, std::deque<std::unique_ptr<Packet>>> inbox;
};

class TestNetwork final : public NetworkInterface {
public:
    TestNetwork(Mesh* mesh, std::uint32_t id, std::vector<std::uint32_t> neighbors)
        : mesh_(mesh), id_(id), neighbors_(std::move(neighbors)) {}

    std::unique_ptr<Packet> receive() override {
        auto& inbox = mesh_->inbox[id_];
        if (inbox.empty()) return nullptr;
        auto packet = std::move(inbox.front());
        inbox.pop_front();
        return packet;
    }

    bool has_pending_io() const override { return !mesh_->inbox.at(id_).empty(); }

    void send_direct(std::uint32_t destination,
                     std::unique_ptr<Packet> packet) override {
        if (!packet) return;
        packet->dest_node_id = destination;
        mesh_->inbox[destination].push_back(std::move(packet));
    }

    void broadcast(std::unique_ptr<Packet> packet) override {
        for (const auto neighbor : neighbors_) {
            send_direct(neighbor, packet->clone());
        }
    }

    void configure_topology(const std::string&, std::uint32_t) override {}
    std::vector<std::uint32_t> get_neighbors() const override { return neighbors_; }

private:
    Mesh* mesh_;
    std::uint32_t id_;
    std::vector<std::uint32_t> neighbors_;
};

struct Delivery {
    std::uint32_t node = 0;
    std::string key;
    std::string value;
};

Node::Config nodeConfig(rsync_cache::CachePolicy policy, std::size_t budget,
                        std::uint32_t id) {
    Node::Config config;
    config.max_hop_count = 12;
    config.cache_policy.policy = policy;
    config.cache_policy.budget_bytes = budget;
    config.cache_policy.gauge_interval_ms = 0;
    (void)id;
    return config;
}

// Staged-fallback configuration: needed to arm real origin queries so a
// fallback DATA packet can be identity-valid and part of a live request.
Node::Config stagedNodeConfig(rsync_cache::CachePolicy policy, std::size_t budget,
                              std::uint32_t id) {
    Node::Config config = nodeConfig(policy, budget, id);
    config.staged_fallback_enabled = true;
    config.bloom_bits = RequestPacket::kSignatureBloomBits;
    config.bloom_hash_count = RequestPacket::kSignatureHashCount;
    config.query_timeout = std::chrono::milliseconds(250);
    config.fallback_delay = std::chrono::milliseconds(20);
    return config;
}

// ---------------------------------------------------------------------------
// Production finite-cache configuration
// ---------------------------------------------------------------------------
// The integrity tests must run the configuration the experiment actually uses.
// A hand-built CachePolicyConfig silently leaves verify_replica_content=false,
// because only fromEnv() forces it on for lru/bypass/unlimited. These helpers
// therefore drive the real environment parser (with the environment saved and
// restored) instead of copying its decisions by hand.
rsync_cache::CachePolicyConfig productionCacheConfig(const std::string& policy,
                                                     std::size_t budget_bytes) {
    struct SavedEnv {
        std::string name;
        std::optional<std::string> value;
    };
    const char* names[] = {"RSYNC_CACHE_POLICY", "RSYNC_REPLICA_BUDGET_BYTES",
                           "RSYNC_REPLICA_BUDGET_OBJECTS",
                           "RSYNC_CACHE_VERIFY_REPLICA_CONTENT",
                           "RSYNC_CACHE_GAUGE_INTERVAL_MS"};
    std::vector<SavedEnv> saved;
    for (const char* name : names) {
        const char* value = std::getenv(name);
        saved.push_back(SavedEnv{name, value != nullptr
                                           ? std::optional<std::string>(value)
                                           : std::nullopt});
    }

    setenv("RSYNC_CACHE_POLICY", policy.c_str(), 1);
    setenv("RSYNC_REPLICA_BUDGET_BYTES", std::to_string(budget_bytes).c_str(), 1);
    unsetenv("RSYNC_REPLICA_BUDGET_OBJECTS");
    unsetenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT");
    setenv("RSYNC_CACHE_GAUGE_INTERVAL_MS", "0", 1);
    rsync_cache::CachePolicyConfig config = rsync_cache::CachePolicyConfig::fromEnv();

    for (const auto& item : saved) {
        if (item.value.has_value()) {
            setenv(item.name.c_str(), item.value->c_str(), 1);
        } else {
            unsetenv(item.name.c_str());
        }
    }
    config.gauge_interval_ms = 0;  // deterministic: no timer-driven gauge
    return config;
}

// Staged production node: real fromEnv() cache policy plus the staged fallback
// state needed to arm genuine origin queries. The 10 s origin lifetime keeps the
// test deterministic under file I/O instead of racing a 250 ms deadline.
Node::Config productionStagedConfig(std::uint32_t id, std::size_t budget) {
    Node::Config config = stagedNodeConfig(rsync_cache::CachePolicy::Lru, budget, id);
    config.cache_policy = productionCacheConfig("lru", budget);
    config.query_timeout = std::chrono::seconds(10);
    return config;
}

// Non-staged production node, for the preferred DATA path where no origin query
// state is involved.
Node::Config productionPlainConfig(std::uint32_t id, std::size_t budget) {
    Node::Config config = nodeConfig(rsync_cache::CachePolicy::Lru, budget, id);
    config.cache_policy = productionCacheConfig("lru", budget);
    return config;
}

// ---------------------------------------------------------------------------
// Structured-trace helpers
// ---------------------------------------------------------------------------
// rsync_trace::record writes to the file named by RSYNC_EVENT_TRACE_PATH, which
// main() installs before the first test records anything (the recorder reads its
// environment exactly once). These helpers locate records by the fields they
// actually carry, never by line number.
const std::string& nodeTracePath() {
    static const std::string path =
        (std::filesystem::temp_directory_path() /
         ("rsync-node-finite-cache-" + std::to_string(::getpid()) + ".jsonl"))
            .string();
    return path;
}

std::vector<std::string> readTraceLines() {
    rsync_trace::flush();
    std::vector<std::string> lines;
    std::ifstream input(nodeTracePath());
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty()) lines.push_back(line);
    }
    return lines;
}

bool traceLineHasAll(const std::string& line,
                     const std::vector<std::string>& needles) {
    for (const auto& needle : needles) {
        if (line.find(needle) == std::string::npos) return false;
    }
    return true;
}

std::size_t countTraceRecords(const std::vector<std::string>& lines,
                              const std::vector<std::string>& needles) {
    std::size_t total = 0;
    for (const auto& line : lines) {
        if (traceLineHasAll(line, needles)) ++total;
    }
    return total;
}

std::string nodeField(std::uint32_t id) {
    return "\"node\":" + std::to_string(id) + ",";
}

// A packet forwarded downstream must be DATA; the staged requests themselves
// also put REQUEST packets in the same inbox while arming.
std::size_t dataPacketsIn(const Mesh& mesh, std::uint32_t node_id) {
    const auto it = mesh.inbox.find(node_id);
    if (it == mesh.inbox.end()) return 0;
    std::size_t total = 0;
    for (const auto& packet : it->second) {
        if (packet && packet->type == PacketType::DATA) ++total;
    }
    return total;
}

// A two-node line: node 1 owns the object, node 2 asks for it.
struct TwoNode {
    Mesh mesh;
    RecordingCacheEventSink sink1;
    RecordingCacheEventSink sink2;
    std::vector<Delivery> deliveries;
    std::unique_ptr<Node> provider;
    std::unique_ptr<Node> client;

    TwoNode(rsync_cache::CachePolicy policy, std::size_t budget,
            const std::string& key, std::size_t value_bytes) {
        provider = std::make_unique<Node>(
            1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
            std::make_unique<InMemoryCache>(), nodeConfig(policy, budget, 1));
        client = std::make_unique<Node>(
            2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
            std::make_unique<InMemoryCache>(), nodeConfig(policy, budget, 2));
        provider->cacheStore()->setEventSink(&sink1);
        client->cacheStore()->setEventSink(&sink2);
        provider->addLocalData(key, makeValueOfSize(value_bytes));
        client->setApplicationCallback([this](const Node::ApplicationDelivery& delivery) {
            deliveries.push_back(Delivery{2, delivery.key,
                                          cache_test::valueString(delivery.value)});
        });
    }

    void run(std::size_t max_rounds = 200) {
        for (std::size_t round = 0; round < max_rounds; ++round) {
            const bool a = provider->processOne();
            const bool b = client->processOne();
            if (!a && !b) break;
        }
    }
};

}  // namespace

CACHE_TEST(owner_seeded_key_is_served_with_owner_tier) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 65536, 1));
    node.cacheStore()->setEventSink(&sink);
    node.addLocalData("owned", makeValueOfSize(1024));

    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(cache_test::valueString(delivery.value));
    });
    node.requestFile({"owned"});

    CACHE_REQUIRE(delivered.size() == 1, "the owner request must be delivered exactly once");
    CACHE_REQUIRE(delivered.at(0) == std::string(1024, 'x'), "delivered value wrong");
    CACHE_REQUIRE(sink.count("local_cache_hit") == 0,
                  "cache-layer events must not duplicate Node's hit events");
    CACHE_REQUIRE(node.cacheStore()->gauge().touches == 0,
                  "an owner read must not touch the replica LRU");
    CACHE_REQUIRE(node.cacheStore()->tierOf("owned") == rsync_cache::CacheTier::Owner,
                  "owner tier lost");
}

CACHE_TEST(data_arrival_admits_a_replica_and_repeat_is_a_no_touch_skip) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 65536, 1));
    node.cacheStore()->setEventSink(&sink);

    // Preferred DATA path: a fresh, non-stable request id.
    node.processOne();  // drain anything pending first
    mesh.inbox[1].push_back(DataPacket::create(9001, 2, 1, "rep",
                                               makeValueOfSize(2048)));
    CACHE_REQUIRE(node.processOne(), "injecting DATA must count as activity");
    CACHE_REQUIRE(sink.count("replica_admit") == 1, "DATA arrival must admit a replica");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 1, "replica not resident");
    CACHE_REQUIRE(node.cacheStore()->tierOf("rep") == rsync_cache::CacheTier::Replica,
                  "replica tier wrong");

    const auto admits_before = node.cacheStore()->gauge().admits;
    const auto touches_before = node.cacheStore()->gauge().touches;
    mesh.inbox[1].push_back(DataPacket::create(9002, 2, 1, "rep",
                                               makeValueOfSize(2048)));
    node.processOne();
    CACHE_REQUIRE(sink.count("data_admit_skipped") == 1,
                  "a duplicate DATA arrival must be recorded as a skip");
    CACHE_REQUIRE(node.cacheStore()->gauge().admits == admits_before,
                  "a duplicate DATA arrival must not re-admit");
    CACHE_REQUIRE(node.cacheStore()->gauge().touches == touches_before,
                  "a duplicate DATA arrival must not refresh recency");
    CACHE_REQUIRE(sink.count("replica_evict") == 0, "no eviction expected");
}

CACHE_TEST(owner_key_never_gets_a_redundant_replica) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 65536, 1));
    node.cacheStore()->setEventSink(&sink);
    node.addLocalData("owned", makeValueOfSize(1024));

    mesh.inbox[1].push_back(DataPacket::create(9101, 2, 1, "owned",
                                               makeValueOfSize(1024)));
    node.processOne();
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "owner key must not be admitted as a replica");
    CACHE_REQUIRE(sink.count("data_admit_skipped") == 1, "expected an explicit skip");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      sink.named("data_admit_skipped").at(0), "reason") == "owner_resident",
                  "skip reason must say the node is the owner");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 0, "no replica was created");
    CACHE_REQUIRE(node.cacheStore()->backend()->exists("owner:owned"),
                  "owner object must be stored under the owner namespace");
    CACHE_REQUIRE(!node.cacheStore()->backend()->exists("replica:owned"),
                  "owner object leaked into the replica namespace");
}

CACHE_TEST(eviction_drops_the_directory_entry_but_keeps_owners) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 1024, 1));
    node.cacheStore()->setEventSink(&sink);
    node.addLocalData("owned", makeValueOfSize(1024));

    mesh.inbox[1].push_back(DataPacket::create(9201, 2, 1, "r1", makeValueOfSize(1024)));
    node.processOne();
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("r1") == 1, "r1 resident");

    mesh.inbox[1].push_back(DataPacket::create(9202, 2, 1, "r2", makeValueOfSize(1024)));
    node.processOne();
    CACHE_REQUIRE(sink.count("replica_evict") == 1, "second admission must evict r1");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("r1") == 0,
                  "evicted replica must leave the serviceable directory");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("r2") == 1, "r2 must be resident");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("owned") == 1,
                  "owner entry must survive replica eviction");
    CACHE_REQUIRE(node.cacheStore()->backend()->exists("owner:owned"),
                  "owner object must still be stored");
    CACHE_REQUIRE(!node.cacheStore()->backend()->exists("replica:r1"),
                  "evicted replica must be removed from storage");
}

CACHE_TEST(end_to_end_fetch_admits_a_replica_and_serves_the_second_request_locally) {
    TwoNode flow(rsync_cache::CachePolicy::Lru, 4 * 65536, "k", 4096);
    flow.client->requestFile({"k"});
    flow.run();

    CACHE_REQUIRE(flow.deliveries.size() == 1, "the first request must be delivered once");
    CACHE_REQUIRE(flow.deliveries.at(0).value == std::string(4096, 'x'),
                  "the delivered value must be correct");
    // provider_hit is emitted by Node into the structured trace, not into the
    // cache event sink, so assert the owner path through the store state.
    CACHE_REQUIRE(flow.provider->cacheStore()->tierOf("k") == rsync_cache::CacheTier::Owner,
                  "the provider must hold the object as an owner");
    CACHE_REQUIRE(flow.provider->cacheStore()->gauge().touches == 0,
                  "serving from the owner tier must not touch the replica LRU");
    CACHE_REQUIRE(flow.provider->cacheStore()->gauge().replica_objects == 0,
                  "the provider must not create a replica of its own object");
    CACHE_REQUIRE(flow.sink2.count("replica_admit") == 1, "the client must have cached the replica");
    CACHE_REQUIRE(flow.client->cacheStore()->gauge().touches == 0,
                  "admitting a replica must not count as a service touch");

    flow.sink2.clear();
    flow.client->requestFile({"k"});  // now served locally from the replica
    flow.run();
    CACHE_REQUIRE(flow.deliveries.size() == 2, "the second request must be delivered exactly once");
    CACHE_REQUIRE(flow.deliveries.at(1).value == std::string(4096, 'x'),
                  "the second delivered value must be correct");
    CACHE_REQUIRE(flow.client->cacheStore()->gauge().touches == 1,
                  "a replica service must refresh recency exactly once");
    CACHE_REQUIRE(flow.client->cacheStore()->tierOf("k") == rsync_cache::CacheTier::Replica,
                  "the second request must be served from the replica tier");
}

CACHE_TEST(budget_zero_bypass_still_forwards_and_delivers) {
    TwoNode flow(rsync_cache::CachePolicy::Lru, 0, "k", 4096);
    flow.client->requestFile({"k"});
    flow.run();

    CACHE_REQUIRE(flow.deliveries.size() == 1, "bypass must not break delivery");
    CACHE_REQUIRE(flow.deliveries.at(0).value == std::string(4096, 'x'),
                  "delivered value must be correct with caching disabled");
    CACHE_REQUIRE(flow.sink2.count("replica_bypass") == 1, "the bypass must be recorded");
    CACHE_REQUIRE(flow.sink2.count("replica_evict") == 0, "budget 0 must never evict");
    CACHE_REQUIRE(flow.sink2.count("replica_admit") == 0, "budget 0 must never admit");
    CACHE_REQUIRE(flow.client->cacheStore()->gauge().replica_objects == 0,
                  "budget 0 must leave the replica store empty");
    CACHE_REQUIRE(flow.client->cacheStore()->serviceableKeys().count("k") == 0,
                  "a bypassed object must not be advertised as serviceable");

    // A repeat request still has to fetch remotely and deliver.
    flow.client->requestFile({"k"});
    flow.run();
    CACHE_REQUIRE(flow.deliveries.size() == 2, "the repeat request must be delivered once");
    CACHE_REQUIRE(flow.deliveries.at(1).value == std::string(4096, 'x'), "value must be correct");
}

CACHE_TEST(eviction_during_inflight_data_does_not_corrupt_delivery) {
    TwoNode flow(rsync_cache::CachePolicy::Lru, 4096, "k", 4096);
    flow.client->requestFile({"k"});
    flow.run();
    CACHE_REQUIRE(flow.deliveries.size() == 1, "first delivery");

    // Take a served copy, then evict the object out from under it.
    auto snapshot = flow.client->cacheStore()->lookupForService("k");
    CACHE_REQUIRE(snapshot.value.has_value(), "the replica must be servable");
    const std::string before = cache_test::valueString(*snapshot.value);

    flow.mesh.inbox[2].push_back(DataPacket::create(9301, 1, 2, "other",
                                                    makeValueOfSize(4096)));
    flow.run();
    CACHE_REQUIRE(flow.client->cacheStore()->serviceableKeys().count("k") == 0,
                  "k must have been evicted");
    // The Value handed out before the eviction is an independent copy: eviction
    // must not leave a dangling reference.
    CACHE_REQUIRE(cache_test::valueString(*snapshot.value) == before,
                  "an already-served value must not be mutated by eviction");
    CACHE_REQUIRE(before == std::string(4096, 'x'), "the served copy must still be correct");

    // The owner in the same exchange is never evicted and can still serve.
    flow.sink2.clear();
    flow.client->requestFile({"k"});
    flow.run();
    CACHE_REQUIRE(flow.deliveries.size() == 2, "k must still be obtainable after eviction");
    CACHE_REQUIRE(flow.deliveries.at(1).value == std::string(4096, 'x'),
                  "re-fetched value must be correct");
}

CACHE_TEST(absent_object_produces_no_successful_data) {
    // Both nodes are empty: the request must not produce a delivery and no DATA
    // may be reported as a successful service.
    Mesh mesh;
    RecordingCacheEventSink sink1;
    RecordingCacheEventSink sink2;
    std::vector<std::string> delivered;
    Node node1(1, std::make_unique<TestNetwork>(&mesh, 1, std::vector<std::uint32_t>{2}),
               std::make_unique<InMemoryCache>(),
               nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 65536, 1));
    Node node2(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
               std::make_unique<InMemoryCache>(),
               nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 65536, 2));
    node1.cacheStore()->setEventSink(&sink1);
    node2.cacheStore()->setEventSink(&sink2);
    node2.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(cache_test::valueString(delivery.value));
    });

    node2.requestFile({"missing"});
    for (int round = 0; round < 50; ++round) {
        const bool a = node1.processOne();
        const bool b = node2.processOne();
        if (!a && !b) break;
    }
    CACHE_REQUIRE(delivered.empty(), "nobody holds the object, so nothing may be delivered");
    CACHE_REQUIRE(sink1.count("provider_hit") == 0, "no provider hit may be reported");
    CACHE_REQUIRE(sink2.count("replica_admit") == 0, "nothing may be admitted");
    CACHE_REQUIRE(node2.cacheStore()->gauge().replica_objects == 0, "client store must stay empty");
}

// ---------------------------------------------------------------------------
// Immutable-object integrity on the DATA paths: a conflicting packet must not
// be cached, forwarded or delivered, and must abort the run.
// ---------------------------------------------------------------------------

CACHE_TEST(preferred_conflicting_data_aborts_without_forwarding_or_delivery) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1, 3}),
              std::make_unique<InMemoryCache>(),
              productionPlainConfig(2, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(delivery.key);
    });

    // 1. learn a trusted copy from a valid preferred DATA packet.
    node.processOne();
    mesh.inbox[2].push_back(DataPacket::create(9101, 3, 2, "k", makeValueOfSize(100, 'a')));
    node.processOne();
    CACHE_REQUIRE(node.cacheStore()->tierOf("k") == rsync_cache::CacheTier::Replica,
                  "k must be resident after the first copy");

    // 2. establish a downstream obligation, so an accepted DATA for k would have
    //    been forwarded to node 1.
    mesh.inbox[2].push_back(RequestPacket::create(1, {"k"}, 1024, 1));
    node.processOne();
    mesh.inbox[1].clear();  // node 2 already served that request from its replica

    const auto before = node.cacheStore()->gauge();
    sink.clear();
    // 3. same key, different bytes, fresh request id: identity fine, content not.
    mesh.inbox[2].push_back(DataPacket::create(9102, 3, 2, "k", makeValueOfSize(100, 'b')));
    bool threw = false;
    try {
        node.processOne();
    } catch (const rsync_cache::ImmutabilityViolation&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "a conflicting preferred DATA must abort the run");
    CACHE_REQUIRE(delivered.empty(), "no successful application callback may happen");
    CACHE_REQUIRE(mesh.inbox[1].empty(), "nothing may be forwarded to the downstream requester");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1, "exactly one conflict event");
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "a conflict must not admit");
    CACHE_REQUIRE(sink.count("data_admit_skipped") == 0, "a conflict must not look like a skip");
    const auto event = sink.named("cache_key_conflict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "key") == "k", "conflict key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "tier") == "replica", "conflict tier");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "phase") == "preferred", "conflict phase");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "reason") == "content_mismatch",
                  "conflict reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "request_id") == "9102",
                  "conflict request id");

    const auto after = node.cacheStore()->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes &&
                      after.replica_objects == before.replica_objects,
                  "residency must be unchanged by the conflict");
    CACHE_REQUIRE(after.touches == before.touches, "the conflict must not touch the LRU");
    const auto stored = node.cacheStore()->lookupForService("k");
    CACHE_REQUIRE(stored.value.has_value(), "the original replica must still be servable");
    CACHE_REQUIRE(cache_test::valueString(*stored.value) == std::string(100, 'a'),
                  "the original bytes must be preserved");
}

CACHE_TEST(fallback_conflicting_data_is_rejected_without_consuming_query_state) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{}),
              std::make_unique<InMemoryCache>(),
              productionStagedConfig(2, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        // The protocol also emits an uncorrelated legacy notification with an
        // empty app id; the experiment runner ignores it, so count only the
        // deliveries correlated with a registered application request.
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    // Two unfinished application requests for the same key -> two live origin
    // queries with distinct stable UIDs.
    node.requestFileAsync("k", "app-1");
    node.requestFileAsync("k", "app-2");
    const auto uid1 = QueryTable::makeStableUid(2, 1);
    const auto uid2 = QueryTable::makeStableUid(2, 2);

    // First valid DATA: a first copy (declared boundary C), delivered to app-1.
    mesh.inbox[2].push_back(DataPacket::create(uid1, 3, 2, "k", makeValueOfSize(100, 'a')));
    node.processOne();
    CACHE_REQUIRE(delivered.size() == 1,
                  "the first valid DATA must be delivered once, got " +
                      std::to_string(delivered.size()));
    CACHE_REQUIRE(delivered.at(0) == "app-1", "it must complete exactly app-1");
    CACHE_REQUIRE(node.cacheStore()->tierOf("k") == rsync_cache::CacheTier::Replica,
                  "the first copy must be cached");

    // Positive control for the trace assertion below: the same structured-trace
    // mechanism really does observe a successful application delivery. Without
    // this, "no app_receive" could just mean the test cannot see app_receive at
    // all (the cache event sink never carries it: it is written by
    // rsync_trace::record into the trace file).
    const auto lines_after_first = readTraceLines();
    CACHE_REQUIRE(
        countTraceRecords(lines_after_first,
                          {nodeField(2), "\"event\":\"app_receive\"",
                           "\"request_id\":\"" + std::to_string(uid1) + "\"",
                           "\"key\":\"k\""}) == 1,
        "positive control: the delivered first copy must be observable as app_receive");
    const auto trace_mark = lines_after_first.size();

    // Identity-valid, relevant, but the bytes contradict the trusted copy.
    sink.clear();
    mesh.inbox[2].push_back(DataPacket::create(uid2, 3, 2, "k", makeValueOfSize(100, 'b')));
    bool threw = false;
    try {
        node.processOne();
    } catch (const rsync_cache::ImmutabilityViolation&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "a conflicting fallback DATA must abort the run");
    CACHE_REQUIRE(delivered.size() == 1, "the conflict must not complete any application request");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1, "exactly one conflict event");
    const auto event = sink.named("cache_key_conflict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "tier") == "replica", "conflict tier");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "phase") == "fallback", "conflict phase");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "origin_query_uid") ==
                      std::to_string(uid2),
                  "conflict origin UID");
    // Real evidence, replacing the old sink.count("app_receive") assertion: the
    // cache event sink never carries app_receive. Only records written after the
    // conflict are inspected, and there must be none for any request.
    const auto lines_after_conflict = readTraceLines();
    const std::vector<std::string> tail(lines_after_conflict.begin() + trace_mark,
                                        lines_after_conflict.end());
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(2), "\"event\":\"app_receive\""}) == 0,
                  "a conflict must emit no app-level success trace at all");
    CACHE_REQUIRE(
        countTraceRecords(lines_after_conflict,
                          {nodeField(2), "\"event\":\"app_receive\"",
                           "\"request_id\":\"" + std::to_string(uid2) + "\""}) == 0,
        "no delivery may be recorded for the conflicting request");
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "a conflict must not admit");

    // Query state was NOT consumed by the rejected packet: a valid DATA for the
    // same UID is still accepted and completes the pending request.
    sink.clear();
    mesh.inbox[2].push_back(DataPacket::create(uid2, 3, 2, "k", makeValueOfSize(100, 'a')));
    node.processOne();
    CACHE_REQUIRE(delivered.size() == 2 && delivered.at(1) == "app-2",
                  "the pending request must still be completable after the rejected packet");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 1,
                  "the valid duplicate must not add a replica");
}

CACHE_TEST(unrelated_stable_uid_data_is_protocol_dropped_without_a_conflict) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 4096, 2));
    node.cacheStore()->setEventSink(&sink);

    node.processOne();
    mesh.inbox[2].push_back(DataPacket::create(9201, 3, 2, "k", makeValueOfSize(100, 'a')));
    node.processOne();
    const auto before = node.cacheStore()->gauge();
    sink.clear();

    // A stable UID that belongs to no live query: the protocol must drop it and
    // it must NOT be treated as an immutability failure, even though its bytes
    // contradict the resident copy.
    const auto orphan_uid = QueryTable::makeStableUid(9, 7);
    mesh.inbox[2].push_back(DataPacket::create(orphan_uid, 3, 2, "k", makeValueOfSize(100, 'b')));
    CACHE_REQUIRE(node.processOne(), "the unrelated packet must still be processed and dropped");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 0,
                  "an unrelated packet must never trigger a conflict failure");
    CACHE_REQUIRE(sink.count("replica_admit") == 0 && sink.count("cache_backend_error") == 0,
                  "an unrelated packet must not reach the cache");
    const auto after = node.cacheStore()->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes &&
                      after.replica_objects == before.replica_objects,
                  "an unrelated packet must not change residency");
    CACHE_REQUIRE(after.touches == before.touches, "an unrelated packet must not touch the LRU");
}

CACHE_TEST(owner_duplicate_data_is_not_a_conflict_and_creates_no_replica) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::make_unique<InMemoryCache>(),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 4096, 2));
    node.cacheStore()->setEventSink(&sink);
    node.addLocalData("owned", makeValueOfSize(100, 'a'));

    // Byte-identical DATA for an owned key: no conflict, no redundant replica.
    node.processOne();
    mesh.inbox[2].push_back(DataPacket::create(9301, 3, 2, "owned", makeValueOfSize(100, 'a')));
    node.processOne();
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 0, "identical owner content is not a conflict");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 0, "no replica may be created");
    CACHE_REQUIRE(node.cacheStore()->tierOf("owned") == rsync_cache::CacheTier::Owner,
                  "the key is still an owner object");
    const auto owner = node.cacheStore()->lookupForService("owned");
    CACHE_REQUIRE(owner.value.has_value() &&
                      cache_test::valueString(*owner.value) == std::string(100, 'a'),
                  "the owner value must be unchanged");

    // A differing DATA for the same owned key is rejected.
    sink.clear();
    mesh.inbox[2].push_back(DataPacket::create(9302, 3, 2, "owned", makeValueOfSize(100, 'z')));
    bool threw = false;
    try {
        node.processOne();
    } catch (const rsync_cache::ImmutabilityViolation&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "a conflicting DATA for an owned key must abort the run");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1, "exactly one conflict event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      sink.named("cache_key_conflict").at(0), "tier") == "owner",
                  "the conflict must be attributed to the owner tier");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 0, "still no replica");
}

CACHE_TEST(production_config_forces_exact_replica_verification) {
    const auto config = productionCacheConfig("lru", 4 * 4096);
    CACHE_REQUIRE(config.policy == rsync_cache::CachePolicy::Lru, "fromEnv must yield lru");
    CACHE_REQUIRE(config.budget_bytes == 4 * 4096, "fromEnv must read the byte budget");
    CACHE_REQUIRE(config.verify_replica_content,
                  "fromEnv must force exact replica verification on for lru/bypass/unlimited");
}

// The defect fixed here: with the production configuration
// (verify_replica_content on) ReplicaCache::admit() re-read and re-compared a
// resident replica that checkImmutability() had already read, and that second
// read happened AFTER the QueryTable obligation was consumed. This test drives a
// backend that fails the second read and counts reads, so it fails on the old
// implementation for a reason other than "the injected fault did not fire".
CACHE_TEST(verified_duplicate_data_never_issues_a_second_integrity_read) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(41, std::make_unique<TestNetwork>(&mesh, 41, std::vector<std::uint32_t>{}),
              std::move(backend_owner), productionStagedConfig(41, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);

    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    const std::string key = "verified-duplicate-read-count";
    const Value trusted = makeValueOfSize(100, 'a');

    // Arm the query BEFORE the key is resident: a locally resident key is served
    // from the cache and never arms a remote query.
    node.requestFileAsync(key, "app-1");
    const auto uid = QueryTable::makeStableUid(41, 1);

    // Install the trusted resident replica through the store facade, which does
    // not consume any query state.
    node.cacheStore()->admitReplica(key, trusted);
    CACHE_REQUIRE(node.cacheStore()->tierOf(key) == rsync_cache::CacheTier::Replica,
                  "the trusted replica must be resident before the duplicate arrives");
    sink.clear();

    // The integrity read is read index 0; index 1 would be the redundant second
    // read. Failing it means the old implementation cannot report this packet as
    // a clean duplicate.
    backend->failGetAtRelative(1);
    const auto reads_before = backend->getCalls();
    const auto touches_before = node.cacheStore()->gauge().touches;
    const auto objects_before = node.cacheStore()->gauge().replica_objects;

    mesh.inbox[41].push_back(DataPacket::create(uid, 7, 41, key, trusted));
    node.processOne();

    // Direct evidence: exactly one integrity read was issued for this DATA.
    const auto reads_after = backend->getCalls();
    CACHE_REQUIRE(reads_after == reads_before + 1,
                  "a verified duplicate must issue exactly one integrity read, got " +
                      std::to_string(reads_after - reads_before));
    CACHE_REQUIRE(sink.count("cache_backend_error") == 0,
                  "the redundant second read must not be attempted");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 0, "identical bytes are not a conflict");
    CACHE_REQUIRE(delivered.size() == 1 && delivered.at(0) == "app-1",
                  "the verified duplicate must still be delivered normally");
    CACHE_REQUIRE(sink.count("data_admit_skipped") == 1,
                  "the resident replica must be reported as an already-resident skip");
    CACHE_REQUIRE(node.cacheStore()->gauge().touches == touches_before,
                  "a duplicate DATA arrival must not refresh the LRU");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == objects_before,
                  "a duplicate DATA arrival must not change residency");
    backend->clearFaults();
}

CACHE_TEST(first_integrity_read_failure_drops_preferred_data_without_consuming_state) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(42, std::make_unique<TestNetwork>(&mesh, 42, std::vector<std::uint32_t>{43}),
              std::move(backend_owner), productionStagedConfig(42, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);

    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    const std::string key = "preferred-first-read-failure";
    const Value trusted = makeValueOfSize(120, 'a');
    node.requestFileAsync(key, "app-p1");
    const auto uid = QueryTable::makeStableUid(42, 1);
    node.cacheStore()->admitReplica(key, trusted);
    CACHE_REQUIRE(node.cacheStore()->tierOf(key) == rsync_cache::CacheTier::Replica,
                  "the trusted replica must be resident");

    backend->failGets();
    const auto before = node.cacheStore()->gauge();
    const auto forwards_before = dataPacketsIn(mesh, 43);
    sink.clear();
    const auto trace_mark = readTraceLines().size();

    // Preferred path: a fresh, non-stable request id, identity fine.
    mesh.inbox[42].push_back(DataPacket::create(4242, 43, 42, key, trusted));
    CACHE_REQUIRE(node.processOne(), "the dropped packet must still count as processed activity");

    CACHE_REQUIRE(sink.count("cache_backend_error") == 1,
                  "the failed integrity read must be recorded as a backend error");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      sink.named("cache_backend_error").at(0), "reason") ==
                      "replica_get_failed",
                  "backend error reason must be the integrity read");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 0,
                  "a read failure is not a content conflict");
    CACHE_REQUIRE(delivered.empty(), "an unverifiable packet must not be delivered");
    CACHE_REQUIRE(dataPacketsIn(mesh, 43) == forwards_before,
                  "an unverifiable packet must not be forwarded");
    CACHE_REQUIRE(sink.count("replica_admit") == 0 && sink.count("data_admit_skipped") == 0,
                  "an unverifiable packet must not reach admission at all");
    const auto after = node.cacheStore()->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes &&
                      after.replica_objects == before.replica_objects,
                  "an unverifiable packet must not change residency");
    CACHE_REQUIRE(after.touches == before.touches, "an unverifiable packet must not touch the LRU");
    const auto lines_after_drop = readTraceLines();
    const std::vector<std::string> tail(lines_after_drop.begin() + trace_mark,
                                        lines_after_drop.end());
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(42), "\"event\":\"app_receive\""}) == 0,
                  "an unverifiable packet must emit no app_receive trace");

    // Query state survived: a later legitimate preferred DATA for the same key
    // completes the pending request (component invariant; no claim about
    // continuing a production process after a fatal failure).
    backend->clearFaults();
    sink.clear();
    mesh.inbox[42].push_back(DataPacket::create(4243, 43, 42, key, trusted));
    node.processOne();
    CACHE_REQUIRE(delivered.size() == 1 && delivered.at(0) == "app-p1",
                  "the pending preferred request must still complete after a failed integrity read");
    const auto lines_final = readTraceLines();
    // The preferred path attributes app_receive to the wire request id, not to
    // the origin UID, so the positive control locates it by node/key/event.
    CACHE_REQUIRE(
        countTraceRecords(lines_final,
                          {nodeField(42), "\"event\":\"app_receive\"",
                           "\"key\":\"" + key + "\""}) >= 1,
        "positive control: the legitimate preferred delivery must be observable as app_receive");
    // The dropped packet really did belong to a live origin query.
    CACHE_REQUIRE(
        countTraceRecords(lines_final,
                          {nodeField(42), "\"event\":\"origin_fallback_armed\"",
                           "\"request_id\":\"" + std::to_string(uid) + "\""}) == 1,
        "the request whose DATA was dropped must be a real armed origin query");
}

CACHE_TEST(first_integrity_read_failure_drops_fallback_data_without_consuming_state) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(44, std::make_unique<TestNetwork>(&mesh, 44, std::vector<std::uint32_t>{43}),
              std::move(backend_owner), productionStagedConfig(44, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);

    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    const std::string key = "fallback-first-read-failure";
    const Value trusted = makeValueOfSize(120, 'a');
    node.requestFileAsync(key, "app-f1");
    const auto uid = QueryTable::makeStableUid(44, 1);
    node.cacheStore()->admitReplica(key, trusted);
    CACHE_REQUIRE(node.cacheStore()->tierOf(key) == rsync_cache::CacheTier::Replica,
                  "the trusted replica must be resident");

    backend->failGets();
    const auto before = node.cacheStore()->gauge();
    const auto forwards_before = dataPacketsIn(mesh, 43);
    sink.clear();
    const auto trace_mark = readTraceLines().size();

    // Identity-valid, relevant fallback DATA whose local reference cannot be read.
    mesh.inbox[44].push_back(DataPacket::create(uid, 43, 44, key, trusted));
    CACHE_REQUIRE(node.processOne(), "the dropped packet must still count as processed activity");

    CACHE_REQUIRE(sink.count("cache_backend_error") == 1,
                  "the failed integrity read must be recorded as a backend error");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 0,
                  "a read failure is not a content conflict");
    CACHE_REQUIRE(delivered.empty(), "an unverifiable packet must not be delivered");
    CACHE_REQUIRE(dataPacketsIn(mesh, 43) == forwards_before,
                  "an unverifiable packet must not be forwarded");
    CACHE_REQUIRE(sink.count("replica_admit") == 0 && sink.count("data_admit_skipped") == 0,
                  "an unverifiable packet must not reach admission at all");
    const auto after = node.cacheStore()->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes &&
                      after.replica_objects == before.replica_objects,
                  "an unverifiable packet must not change residency");
    CACHE_REQUIRE(after.touches == before.touches, "an unverifiable packet must not touch the LRU");
    const auto lines_after_drop = readTraceLines();
    const std::vector<std::string> tail(lines_after_drop.begin() + trace_mark,
                                        lines_after_drop.end());
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(44), "\"event\":\"app_receive\""}) == 0,
                  "an unverifiable packet must emit no app_receive trace");
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(44), "\"event\":\"query_key_satisfied\""}) == 0,
                  "an unverifiable packet must not consume the fallback obligation");

    // The obligation survived: a later valid DATA for the same UID completes it.
    backend->clearFaults();
    sink.clear();
    mesh.inbox[44].push_back(DataPacket::create(uid, 43, 44, key, trusted));
    node.processOne();
    CACHE_REQUIRE(delivered.size() == 1 && delivered.at(0) == "app-f1",
                  "the pending fallback request must still complete after a failed integrity read");
    const auto lines_final = readTraceLines();
    CACHE_REQUIRE(
        countTraceRecords(lines_final,
                          {nodeField(44), "\"event\":\"app_receive\"",
                           "\"request_id\":\"" + std::to_string(uid) + "\"",
                           "\"key\":\"" + key + "\""}) >= 1,
        "positive control: the legitimate fallback delivery must be observable as app_receive");
}

// Two genuinely unfinished requests must both survive a conflicting DATA packet.
//
// The existing fallback test completes app-1 (and therefore consumes uid1)
// before the conflict, so it only proves that ONE pending request is untouched.
// Here a trusted replica is installed through the store facade and BOTH origin
// queries are still unresolved when the conflict arrives.
CACHE_TEST(two_pending_requests_both_survive_a_conflicting_data_packet) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    Node node(45, std::make_unique<TestNetwork>(&mesh, 45, std::vector<std::uint32_t>{46}),
              std::make_unique<InMemoryCache>(), productionStagedConfig(45, 4 * 4096));
    node.cacheStore()->setEventSink(&sink);

    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    const std::string key = "conflict-two-pending";
    const Value trusted = makeValueOfSize(100, 'a');

    // Arm both requests while the key is still absent, so each one really is a
    // live remote query: a locally resident key would be served from the cache
    // and would never create an unfinished request.
    node.requestFileAsync(key, "app-A");
    node.requestFileAsync(key, "app-B");
    const auto uid_a = QueryTable::makeStableUid(45, 1);
    const auto uid_b = QueryTable::makeStableUid(45, 2);

    // Then install the trusted resident replica (store facade; consumes no
    // query state).
    node.cacheStore()->admitReplica(key, trusted);
    CACHE_REQUIRE(node.cacheStore()->tierOf(key) == rsync_cache::CacheTier::Replica,
                  "the trusted replica must be resident before the conflict");

    // Both requests are still unfinished BEFORE the conflict: both origin
    // queries were armed, and neither has been satisfied, completed or
    // delivered.
    const auto lines_armed = readTraceLines();
    CACHE_REQUIRE(
        countTraceRecords(lines_armed,
                          {nodeField(45), "\"event\":\"origin_fallback_armed\"",
                           "\"request_id\":\"" + std::to_string(uid_a) + "\""}) == 1,
        "uid A must be an armed origin query");
    CACHE_REQUIRE(
        countTraceRecords(lines_armed,
                          {nodeField(45), "\"event\":\"origin_fallback_armed\"",
                           "\"request_id\":\"" + std::to_string(uid_b) + "\""}) == 1,
        "uid B must be an armed origin query");
    CACHE_REQUIRE(countTraceRecords(lines_armed, {nodeField(45), "\"event\":\"app_receive\""}) == 0,
                  "no application request may have completed yet");
    CACHE_REQUIRE(countTraceRecords(lines_armed, {nodeField(45), "\"event\":\"query_key_satisfied\""}) == 0,
                  "no key may have been marked satisfied yet");
    CACHE_REQUIRE(countTraceRecords(lines_armed, {nodeField(45), "\"event\":\"query_complete\""}) == 0,
                  "no query may have completed yet");
    const auto trace_mark = lines_armed.size();
    sink.clear();

    // Identity-valid, relevant, byte-conflicting DATA for uid A.
    mesh.inbox[45].push_back(DataPacket::create(uid_a, 46, 45, key, makeValueOfSize(100, 'b')));
    bool threw = false;
    try {
        node.processOne();
    } catch (const rsync_cache::ImmutabilityViolation&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "a conflicting DATA must abort with ImmutabilityViolation");
    CACHE_REQUIRE(delivered.empty(), "a conflict must deliver nothing");
    CACHE_REQUIRE(dataPacketsIn(mesh, 46) == 0, "a conflict must forward nothing downstream");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1, "exactly one conflict event");
    CACHE_REQUIRE(sink.count("replica_admit") == 0 && sink.count("data_admit_skipped") == 0,
                  "a conflict must not reach admission");
    const auto conflict_event = sink.named("cache_key_conflict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(conflict_event, "origin_query_uid") ==
                      std::to_string(uid_a),
                  "the conflict must be attributed to uid A");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 1,
                  "a conflict must not change residency");

    // Neither request was consumed by the conflict.
    const auto lines_after_conflict = readTraceLines();
    const std::vector<std::string> tail(lines_after_conflict.begin() + trace_mark,
                                        lines_after_conflict.end());
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(45), "\"event\":\"query_key_satisfied\""}) == 0,
                  "the conflict must not satisfy any query key");
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(45), "\"event\":\"query_complete\""}) == 0,
                  "the conflict must not complete any query");
    CACHE_REQUIRE(countTraceRecords(tail, {nodeField(45), "\"event\":\"app_receive\""}) == 0,
                  "the conflict must not deliver to any application");

    // COMPONENT INVARIANT ONLY. This does not mean a production process may
    // continue after a fatal immutability conflict: the runner treats
    // control/fatal/* as a failed run and stops the node. These two valid
    // packets exist solely to show that the conflict consumed no pending state
    // for either request.
    mesh.inbox[45].push_back(DataPacket::create(uid_a, 46, 45, key, trusted));
    node.processOne();
    mesh.inbox[45].push_back(DataPacket::create(uid_b, 46, 45, key, trusted));
    node.processOne();
    CACHE_REQUIRE(delivered.size() == 2,
                  "both pending requests must still be completable, got " +
                      std::to_string(delivered.size()));
    CACHE_REQUIRE(delivered.at(0) == "app-A" && delivered.at(1) == "app-B",
                  "both pending requests must complete in UID order");
    const auto lines_final = readTraceLines();
    CACHE_REQUIRE(
        countTraceRecords(lines_final,
                          {nodeField(45), "\"event\":\"app_receive\"",
                           "\"request_id\":\"" + std::to_string(uid_a) + "\"",
                           "\"key\":\"" + key + "\""}) >= 1,
        "positive control: uid A's legitimate delivery must be observable");
    CACHE_REQUIRE(
        countTraceRecords(lines_final,
                          {nodeField(45), "\"event\":\"app_receive\"",
                           "\"request_id\":\"" + std::to_string(uid_b) + "\"",
                           "\"key\":\"" + key + "\""}) >= 1,
        "positive control: uid B's legitimate delivery must be observable");
}

// ---------------------------------------------------------------------------
// Legacy error-propagation compatibility
//
// The original Node (baseline 0feb4b22) called cache_->get()/put()/exists()
// directly and let backend failures propagate out of requestFile / handleRequest
// / handleData. The facade must not swallow them into a miss, an
// AlreadyResident skip or a non-fatal BackendError in legacy mode, and it must
// not add backend round trips the original path did not make. FaultyCache
// throws a typed BackendFailure before any side effect; it is explicitly NOT a
// model of a Redis timeout where the command may already have been applied.
// ---------------------------------------------------------------------------

CACHE_TEST(legacy_local_read_failure_propagates_instead_of_arming_a_remote_fetch) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    node.cacheStore()->setEventSink(&sink);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(delivery.key);
    });

    node.addLocalData("legacy-local-read", makeValueOfSize(64, 'a'));
    CACHE_REQUIRE(backend->hasPhysical("legacy-local-read"), "owner stored under the bare key");

    backend->failGets();
    bool propagated = false;
    std::string message;
    try {
        node.requestFile({"legacy-local-read"});
    } catch (const cache_test::BackendFailure& e) {
        propagated = true;
        message = e.what();
    }
    CACHE_REQUIRE(propagated,
                  "a legacy local read failure must propagate, not become a cache miss");
    CACHE_REQUIRE(message.find("legacy-local-read") != std::string::npos,
                  "the original backend failure must be rethrown with its key intact");
    CACHE_REQUIRE(delivered.empty(), "nothing may be delivered from a failed local read");
    CACHE_REQUIRE(mesh.inbox[1].empty(), "no REQUEST may be sent after the local read failed");
    CACHE_REQUIRE(sink.events().empty(), "legacy must emit no cache events");
}

CACHE_TEST(legacy_present_but_unreadable_entry_does_not_fetch_remotely) {
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(delivery.key);
    });

    node.addLocalData("legacy-null-read", makeValueOfSize(64, 'a'));
    backend->nullGets();  // exists() still says present, get() returns nothing

    node.requestFile({"legacy-null-read"});
    CACHE_REQUIRE(delivered.empty(), "an unreadable value must not be delivered");
    CACHE_REQUIRE(mesh.inbox[1].empty(),
                  "the original Node did not fetch remotely when exists() said present");
}

CACHE_TEST(legacy_provider_read_failure_is_not_a_successful_service) {
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    node.addLocalData("legacy-provider", makeValueOfSize(64, 'a'));
    backend->failGets();

    mesh.inbox[2].push_back(RequestPacket::create(1, {"legacy-provider"}, 1024, 1));
    bool propagated = false;
    try {
        node.processOne();
    } catch (const cache_test::BackendFailure&) {
        propagated = true;
    }
    CACHE_REQUIRE(propagated,
                  "a provider read failure must propagate, not be served as a success");
    CACHE_REQUIRE(dataPacketsIn(mesh, 1) == 0,
                  "no DATA may be sent by a provider whose read failed");
}

CACHE_TEST(legacy_data_write_failure_propagates_and_aborts_the_packet) {
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    backend->failPuts();

    mesh.inbox[2].push_back(
        DataPacket::create(9101, 3, 2, "legacy-data-write", makeValueOfSize(100, 'a')));
    bool propagated = false;
    std::string message;
    try {
        node.processOne();
    } catch (const cache_test::BackendFailure& e) {
        propagated = true;
        message = e.what();
    }
    CACHE_REQUIRE(propagated, "a legacy DATA write failure must propagate out of the Node");
    CACHE_REQUIRE(message.find("legacy-data-write") != std::string::npos,
                  "the original backend failure must be rethrown");
    // The original Node issued exactly one exists() and, when absent, one put().
    // The facade must not add a second existence probe.
    CACHE_REQUIRE(backend->countCalls("exists:legacy-data-write") == 1,
                  "legacy DATA must issue exactly one exists(), as the original Node did");
    CACHE_REQUIRE(backend->countCalls("put:legacy-data-write") == 1,
                  "the write is attempted exactly once");
    CACHE_REQUIRE(!backend->sawCall("put:replica:legacy-data-write"),
                  "legacy must write the bare logical key");
    CACHE_REQUIRE(!backend->hasPhysical("legacy-data-write"),
                  "a failed write must leave no stored value");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("legacy-data-write") == 0,
                  "a failed write must not enter the directory");
    CACHE_REQUIRE(dataPacketsIn(mesh, 3) == 0,
                  "no DATA may be forwarded after a failed write");
}

CACHE_TEST(legacy_data_exists_failure_propagates_without_a_write) {
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    backend->failExists();

    mesh.inbox[2].push_back(
        DataPacket::create(9102, 3, 2, "legacy-data-exists", makeValueOfSize(100, 'a')));
    bool propagated = false;
    try {
        node.processOne();
    } catch (const cache_test::BackendFailure&) {
        propagated = true;
    }
    CACHE_REQUIRE(propagated, "a legacy DATA existence-probe failure must propagate");
    CACHE_REQUIRE(backend->countCalls("exists:legacy-data-exists") == 1,
                  "exactly one existence probe reaches the backend");
    CACHE_REQUIRE(backend->countCalls("put:legacy-data-exists") == 0,
                  "no write may follow a failed existence probe");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("legacy-data-exists") == 0,
                  "a failed probe must not enter the directory");
    CACHE_REQUIRE(dataPacketsIn(mesh, 3) == 0,
                  "no DATA may be forwarded after a failed existence probe");
}

CACHE_TEST(legacy_duplicate_data_is_skipped_with_one_probe_and_no_write) {
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));

    mesh.inbox[2].push_back(
        DataPacket::create(9201, 3, 2, "legacy-dup", makeValueOfSize(100, 'a')));
    node.processOne();
    CACHE_REQUIRE(backend->hasPhysical("legacy-dup"), "the first copy must be stored");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("legacy-dup") == 1,
                  "the first copy must enter the directory");

    const auto puts_before = backend->putCalls();
    const auto exists_before = backend->countCalls("exists:legacy-dup");
    // Different bytes: legacy still skips the write entirely, exactly as the
    // original Node did (it only suppressed duplicate DATA, it did not compare).
    mesh.inbox[2].push_back(
        DataPacket::create(9202, 3, 2, "legacy-dup", makeValueOfSize(100, 'b')));
    node.processOne();

    CACHE_REQUIRE(backend->putCalls() == puts_before, "a duplicate must not write again");
    CACHE_REQUIRE(backend->countCalls("exists:legacy-dup") == exists_before + 1,
                  "a duplicate issues exactly one existence probe");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("legacy-dup") == 1,
                  "a duplicate must keep the directory entry");
    const auto stored = backend->get("legacy-dup");
    CACHE_REQUIRE(stored.has_value() &&
                      cache_test::valueString(*stored) == std::string(100, 'a'),
                  "a duplicate must not overwrite the stored bytes");
}

CACHE_TEST(legacy_normal_local_hit_still_delivers_without_a_remote_fetch) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{1}),
              std::move(backend_owner), nodeConfig(rsync_cache::CachePolicy::Legacy, 0, 2));
    node.cacheStore()->setEventSink(&sink);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(delivery.key);
    });

    node.addLocalData("legacy-hit", makeValueOfSize(64, 'a'));
    node.requestFile({"legacy-hit"});
    CACHE_REQUIRE(delivered.size() == 1 && delivered.at(0) == "legacy-hit",
                  "a normal legacy local hit must still be delivered exactly once");
    CACHE_REQUIRE(mesh.inbox[1].empty(), "a local hit must not fetch remotely");
    CACHE_REQUIRE(sink.events().empty(), "legacy must emit no cache events");
}

CACHE_TEST(finite_cache_put_failure_still_reports_a_backend_error_without_aborting) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::move(backend_owner),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 4096, 2));
    node.cacheStore()->setEventSink(&sink);
    backend->failPuts();

    mesh.inbox[2].push_back(
        DataPacket::create(9301, 3, 2, "lru-put-fail", makeValueOfSize(100, 'a')));
    // Limited-cache modes convert a backend write failure into a recorded
    // non-fatal backend error and keep processing; that contract must not change.
    node.processOne();
    CACHE_REQUIRE(sink.count("cache_backend_error") == 1,
                  "the limited-cache contract records a backend error");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      sink.named("cache_backend_error").at(0), "reason") ==
                      "replica_put_failed",
                  "backend error reason");
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "a failed write must not emit success");
    CACHE_REQUIRE(node.cacheStore()->gauge().replica_objects == 0,
                  "a failed write must not change residency");
    CACHE_REQUIRE(node.cacheStore()->serviceableKeys().count("lru-put-fail") == 0,
                  "a failed write must not enter the directory");
    CACHE_REQUIRE(backend->countCalls("put:replica:lru-put-fail") == 1,
                  "limited-cache modes use the replica namespace");
    CACHE_REQUIRE(!backend->hasPhysical("lru-put-fail"),
                  "limited-cache modes must not write the bare key");
}

CACHE_TEST(legacy_fallback_write_failure_does_not_roll_back_the_consumed_obligation) {
    // The original fallback order is routeFallbackData() (consumes the reverse
    // path obligation) and only then the cache write. A write failure therefore
    // leaves the query already satisfied; the new code must not invent a
    // rollback the old path never had. This test records that honestly instead
    // of demanding atomicity.
    Mesh mesh;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node::Config config =
        stagedNodeConfig(rsync_cache::CachePolicy::Legacy, 0, 52);
    config.query_timeout = std::chrono::seconds(10);
    Node node(52, std::make_unique<TestNetwork>(&mesh, 52, std::vector<std::uint32_t>{}),
              std::move(backend_owner), config);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        if (!delivery.app_request_id.empty()) {
            delivered.push_back(delivery.app_request_id);
        }
    });

    const std::string key = "legacy-fallback-write";
    node.requestFileAsync(key, "app-lf");
    const auto uid = QueryTable::makeStableUid(52, 1);

    backend->failPuts();
    mesh.inbox[52].push_back(DataPacket::create(uid, 3, 52, key, makeValueOfSize(80, 'a')));
    bool propagated = false;
    try {
        node.processOne();
    } catch (const cache_test::BackendFailure&) {
        propagated = true;
    }
    CACHE_REQUIRE(propagated, "the fallback write failure must propagate");
    CACHE_REQUIRE(delivered.empty(), "a failed write must not deliver");
    CACHE_REQUIRE(!backend->hasPhysical(key), "a failed write stores nothing");

    // Component invariant only (the production entry point stops the node on
    // this exception): the obligation was consumed before the write and is not
    // rolled back, so a later valid DATA for the same UID is duplicate-suppressed
    // instead of completing the application request.
    backend->clearFaults();
    mesh.inbox[52].push_back(DataPacket::create(uid, 3, 52, key, makeValueOfSize(80, 'a')));
    node.processOne();
    CACHE_REQUIRE(delivered.empty(),
                  "the consumed obligation must not be replayed to the application");
    const auto lines = readTraceLines();
    CACHE_REQUIRE(
        countTraceRecords(lines,
                          {nodeField(52), "\"event\":\"duplicate_request_suppressed\"",
                           "\"request_id\":\"" + std::to_string(uid) + "\"",
                           "\"reason\":\"duplicate-data\""}) == 1,
        "positive control: the second DATA must be duplicate-suppressed, proving the "
        "obligation was consumed before the failed write");
}

CACHE_TEST(indeterminate_data_write_stops_the_node_and_poisons_its_store) {
    Mesh mesh;
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    Node node(2, std::make_unique<TestNetwork>(&mesh, 2, std::vector<std::uint32_t>{3}),
              std::move(backend_owner),
              nodeConfig(rsync_cache::CachePolicy::Lru, 4 * 4096, 2));
    node.cacheStore()->setEventSink(&sink);
    std::vector<std::string> delivered;
    node.setApplicationCallback([&](const Node::ApplicationDelivery& delivery) {
        delivered.push_back(delivery.key);
    });
    // The SET is applied, then the response is lost.
    backend->applyThenThrowPuts();

    mesh.inbox[2].push_back(
        DataPacket::create(9401, 3, 2, "ind-data", makeValueOfSize(100, 'a')));
    bool threw = false;
    try {
        node.processOne();
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate DATA write must propagate out of processOne");
    CACHE_REQUIRE(delivered.empty(), "nothing may be delivered from unknown state");
    CACHE_REQUIRE(dataPacketsIn(mesh, 3) == 0, "nothing may be forwarded from unknown state");
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "no success event for an unknown result");
    CACHE_REQUIRE(sink.count("cache_backend_error") == 1, "the fatal backend error is recorded");
    const auto error = sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate",
                  "category");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "1", "fatal");
    CACHE_REQUIRE(backend->hasPhysical("replica:ind-data"),
                  "the value may already be stored");
    CACHE_REQUIRE(!node.cacheStore()->available(), "the node store must be poisoned");

    // A later packet must not be processed as if the cache were healthy.
    mesh.inbox[2].push_back(
        DataPacket::create(9402, 3, 2, "ind-data-2", makeValueOfSize(10)));
    bool unavailable = false;
    try {
        node.processOne();
    } catch (const rsync_cache::CacheUnavailable&) {
        unavailable = true;
    }
    CACHE_REQUIRE(unavailable, "a poisoned node store must refuse later serving/admission");
}

int main() {
    // The trace recorder is a singleton that reads its environment exactly once,
    // on first use. Install an isolated trace file before any test records
    // anything, and restore the previous environment and remove the file
    // afterwards. Every trace assertion locates records by the fields they carry
    // (node, request_id, key, event), never by position.
    struct SavedEnv {
        std::string name;
        std::optional<std::string> value;
    };
    const char* names[] = {"RSYNC_EVENT_TRACE", "RSYNC_EVENT_TRACE_PATH",
                           "RSYNC_EVENT_TRACE_RUN_ID",
                           "RSYNC_EVENT_TRACE_SCHEMA_VERSION"};
    std::vector<SavedEnv> saved;
    for (const char* name : names) {
        const char* value = std::getenv(name);
        saved.push_back(SavedEnv{name, value != nullptr
                                           ? std::optional<std::string>(value)
                                           : std::nullopt});
    }

    std::filesystem::remove(nodeTracePath());
    setenv("RSYNC_EVENT_TRACE", "1", 1);
    setenv("RSYNC_EVENT_TRACE_PATH", nodeTracePath().c_str(), 1);
    setenv("RSYNC_EVENT_TRACE_RUN_ID", "node-finite-cache-test", 1);
    setenv("RSYNC_EVENT_TRACE_SCHEMA_VERSION", "2", 1);

    const int result = cache_test::runAll("node_finite_cache");

    rsync_trace::flush();
    for (const auto& item : saved) {
        if (item.value.has_value()) {
            setenv(item.name.c_str(), item.value->c_str(), 1);
        } else {
            unsetenv(item.name.c_str());
        }
    }
    std::filesystem::remove(nodeTracePath());
    return result;
}