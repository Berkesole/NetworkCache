// Live Redis acceptance driver for the finite-cache experiment.
//
// This is NOT a simulation: it connects to a real Redis over a Unix socket and
// drives the real RedisCache + NodeCache. It is compiled only when Redis
// dependencies are available (ENABLE_REDIS=ON) and is run by
// run_redis_acceptance.sh against a temporary, dedicated instance.
//
// Modes:
//   run <socket> <identity> [evidence.jsonl]
//       Fresh-instance startup verification (verifyRedisStartup), owner/replica
//       binary round-trip, budget-0 bypass, limited LRU eviction, unlimited,
//       duplicate DATA skip, multi-victim eviction and accounting closure, and
//       a non-empty-DB rejection check. Emits one JSON line per observation.
//   restart-check <socket> <identity> <baseline_run_id> <baseline_evicted> [evidence]
//       Confirms that RedisMonitor detects an instance restart / counter
//       rollback after the orchestrator restarted the instance.
//
// Exit code 0 = all checks passed, 1 = a check failed, 2 = usage.

#include "cache/node_cache.hpp"
#include "cache/redis_acceptance.hpp"
#include "cache/redis_cache.hpp"

#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

using rsync_cache::AdmitStatus;
using rsync_cache::CacheNamespace;
using rsync_cache::CachePolicy;
using rsync_cache::CachePolicyConfig;
using rsync_cache::NodeCache;
using rsync_cache::RedisInstanceInfo;
using rsync_cache::RedisMonitor;
// RedisCache is a global class (it implements the global LocalCache interface).

int failures = 0;
std::ofstream evidence_stream;
bool evidence_open = false;

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (unsigned char ch : value) {
        switch (ch) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    constexpr char hex[] = "0123456789abcdef";
                    out << "\\u00" << hex[(ch >> 4) & 0x0f] << hex[ch & 0x0f];
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

void emit(const std::string& event, const std::vector<std::pair<std::string, std::string>>& fields) {
    std::ostringstream line;
    line << "{\"event\":\"" << json_escape(event) << "\"";
    for (const auto& [key, value] : fields) {
        line << ",\"" << json_escape(key) << "\":\"" << json_escape(value) << "\"";
    }
    line << "}";
    std::cout << line.str() << "\n";
    if (evidence_open) {
        evidence_stream << line.str() << "\n";
        evidence_stream.flush();
    }
}

void check(bool condition, const std::string& name, const std::string& detail = {}) {
    emit("check", {{"name", name}, {"status", condition ? "pass" : "fail"},
                   {"detail", detail}});
    if (!condition) ++failures;
}

bool openEvidence(const std::string& path) {
    if (path.empty()) return false;
    evidence_stream.open(path, std::ios::out | std::ios::trunc);
    evidence_open = evidence_stream.is_open();
    return evidence_open;
}

Value valueOfSize(std::size_t size, std::uint8_t fill) {
    return Value(std::vector<std::uint8_t>(size, fill));
}

CachePolicyConfig configFor(CachePolicy policy, std::size_t budget) {
    CachePolicyConfig config;
    config.policy = policy;
    config.budget_bytes = budget;
    config.gauge_interval_ms = 0;
    config.verify_replica_content = true;  // production fromEnv() behaviour
    return config;
}

// Modes ---------------------------------------------------------------------

int runStartupAndComponent(const std::string& socket, const std::string& identity) {
    // 1. Fresh instance must pass the startup rules.
    RedisCache cache(socket);
    const RedisInstanceInfo fresh = cache.readInstanceInfo(identity);
    const auto verdict = rsync_cache::verifyRedisStartup(fresh, socket, identity);
    emit("redis_startup", {{"socket", socket}, {"identity", identity},
                           {"version", fresh.version}, {"run_id", fresh.run_id},
                           {"maxmemory", std::to_string(fresh.maxmemory)},
                           {"policy", fresh.maxmemory_policy},
                           {"db_keys", std::to_string(fresh.db_keys)},
                           {"evicted_keys", std::to_string(fresh.evicted_keys)},
                           {"used_memory", std::to_string(fresh.used_memory)},
                           {"accepted", verdict.accepted ? "1" : "0"},
                           {"reason", verdict.reason}});
    check(verdict.accepted, "fresh_instance_accepted", verdict.reason);
    check(fresh.evicted_keys >= 0, "baseline_evicted_keys_present", fresh.run_id);
    check(fresh.used_memory >= 0, "baseline_used_memory_present", {});
    check(!fresh.run_id.empty(), "run_id_recorded", fresh.run_id);

    // 2. Owner/replica namespaces over real Redis, binary values included.
    auto backend = std::make_unique<RedisCache>(socket);
    backend->put(rsync_cache::physicalKey(CacheNamespace::Owner, "live-ns"),
                 Value(std::vector<std::uint8_t>{0x00, 0x01, 0xff, 0x7f, 0x00}));
    backend->put(rsync_cache::physicalKey(CacheNamespace::Replica, "live-ns"),
                 Value(std::vector<std::uint8_t>{0xff, 0xfe}));
    const auto owner_value = backend->get(rsync_cache::physicalKey(CacheNamespace::Owner, "live-ns"));
    const auto replica_value = backend->get(rsync_cache::physicalKey(CacheNamespace::Replica, "live-ns"));
    check(owner_value.has_value() && owner_value->data() ==
              (std::vector<std::uint8_t>{0x00, 0x01, 0xff, 0x7f, 0x00}),
          "owner_binary_round_trip");
    check(replica_value.has_value() && replica_value->data() ==
              (std::vector<std::uint8_t>{0xff, 0xfe}),
          "replica_binary_round_trip");
    check(rsync_cache::physicalKey(CacheNamespace::Owner, "live-ns") !=
              rsync_cache::physicalKey(CacheNamespace::Replica, "live-ns"),
          "namespaces_distinct");
    backend->del(rsync_cache::physicalKey(CacheNamespace::Owner, "live-ns"));
    backend->del(rsync_cache::physicalKey(CacheNamespace::Replica, "live-ns"));

    // A NodeCache with a live Redis backend: owner seeding + seal + conflict.
    {
        auto store = std::make_unique<NodeCache>(
            std::make_unique<RedisCache>(socket), configFor(CachePolicy::Lru, 1024), nullptr);
        store->seedOwner("live-owner", valueOfSize(64, 'o'));
        store->sealOwners();
        check(store->isOwner("live-owner"), "owner_seeded");
        bool reseed_protected = false;
        try {
            store->seedOwner("live-owner", valueOfSize(128, 'o'));
        } catch (const std::exception&) {
            reseed_protected = true;
        }
        check(reseed_protected, "owner_reseed_conflict_protected");
        const auto served = store->lookupForService("live-owner");
        check(served.value.has_value() && served.value->size() == 64,
              "owner_served_from_redis");
        backend->del("owner:live-owner");
    }

    // 3. Budget 0 bypass: nothing is written.
    {
        auto store = std::make_unique<NodeCache>(
            std::make_unique<RedisCache>(socket), configFor(CachePolicy::Lru, 0), nullptr);
        const auto result = store->admitReplica("live-bypass", valueOfSize(32, 'b'));
        check(result.status == AdmitStatus::BypassedBudgetZero, "budget_zero_bypass");
        check(!backend->exists("replica:live-bypass"), "budget_zero_writes_nothing");
    }

    // 4. Limited LRU eviction and accounting closure, with real DEL/SET.
    {
        auto store = std::make_unique<NodeCache>(
            std::make_unique<RedisCache>(socket), configFor(CachePolicy::Lru, 100), nullptr);
        const auto a = store->admitReplica("live-a", valueOfSize(40, 'a'));
        const auto b = store->admitReplica("live-b", valueOfSize(40, 'b'));
        const auto e = store->admitReplica("live-e", valueOfSize(90, 'e'));
        check(a.inserted() && b.inserted() && e.inserted(), "lru_admissions");
        check(e.victims.size() == 2, "lru_multi_victim_count",
              std::to_string(e.victims.size()));
        check(e.victims.at(0).key == "live-a" && e.victims.at(1).key == "live-b",
              "lru_victim_order");
        check(!backend->exists("replica:live-a") && !backend->exists("replica:live-b"),
              "lru_victims_deleted_in_redis");
        const auto gauge = store->gauge();
        check(gauge.replica_bytes <= gauge.budget_bytes, "resident_within_budget");
        check(gauge.admits - gauge.evictions == gauge.replica_objects,
              "accounting_closes", std::to_string(gauge.admits) + "/" +
                  std::to_string(gauge.evictions) + "/" +
                  std::to_string(gauge.replica_objects));
        check(e.victims.at(0).resident_bytes_after == 40, "victim_a_snapshot_after_delete");
        check(e.victims.at(1).resident_bytes_after == 0, "victim_b_snapshot_after_delete");

        // Duplicate DATA must not refresh the LRU or re-admit.
        const auto admits_before = store->gauge().admits;
        const auto touches_before = store->gauge().touches;
        const auto dup = store->admitReplica("live-e", valueOfSize(90, 'e'));
        check(dup.status == AdmitStatus::AlreadyResident, "duplicate_is_already_resident");
        check(store->gauge().admits == admits_before, "duplicate_no_re_admit");
        check(store->gauge().touches == touches_before, "duplicate_no_lru_touch");
    }

    // 5. Unlimited: no eviction, all three resident.
    {
        auto store = std::make_unique<NodeCache>(
            std::make_unique<RedisCache>(socket), configFor(CachePolicy::Unlimited, 0), nullptr);
        store->admitReplica("live-u1", valueOfSize(200, 'x'));
        store->admitReplica("live-u2", valueOfSize(200, 'y'));
        const auto gauge = store->gauge();
        check(gauge.replica_objects == 2 && gauge.evictions == 0, "unlimited_no_eviction");
    }

    // 6. Non-empty DB must now be refused (never cleared).
    const RedisInstanceInfo dirty = cache.readInstanceInfo(identity);
    const auto dirty_verdict = rsync_cache::verifyRedisStartup(dirty, socket, identity);
    check(!dirty_verdict.accepted && dirty_verdict.reason == "db_not_empty",
          "non_empty_db_refused", dirty_verdict.reason);
    check(dirty.db_keys > 0, "db_keys_reported", std::to_string(dirty.db_keys));

    // 7. used_memory is the Redis total, not the application replica bytes.
    {
        auto store = std::make_unique<NodeCache>(
            std::make_unique<RedisCache>(socket), configFor(CachePolicy::Lru, 1024), nullptr);
        store->admitReplica("live-mem", valueOfSize(64, 'm'));
        const auto app_bytes = store->gauge().replica_bytes;
        const RedisInstanceInfo sample = cache.readInstanceInfo(identity);
        check(sample.used_memory != static_cast<long long>(app_bytes),
              "used_memory_is_not_app_replica_bytes",
              std::to_string(sample.used_memory) + " vs " + std::to_string(app_bytes));
        RedisMonitor monitor(sample.evicted_keys, sample.run_id, identity, 1000);
        const auto observed = monitor.observe(sample);
        check(monitor.configValid() && observed.ok, "monitor_accepts_live_sample",
              observed.reason);
        emit("redis_monitor_sample",
             {{"run_id", sample.run_id},
              {"evicted_keys", std::to_string(sample.evicted_keys)},
              {"used_memory", std::to_string(sample.used_memory)},
              {"status", observed.ok ? "ok" : observed.reason}});
    }
    return failures == 0 ? 0 : 1;
}

int runRestartCheck(const std::string& socket, const std::string& identity,
                    const std::string& baseline_run_id, long long baseline_evicted) {
    RedisCache cache(socket);
    const RedisInstanceInfo info = cache.readInstanceInfo(identity);
    RedisMonitor monitor(baseline_evicted, baseline_run_id, identity, 1000);
    const auto sample = monitor.observe(info);
    emit("redis_restart_check",
         {{"baseline_run_id", baseline_run_id}, {"now_run_id", info.run_id},
          {"baseline_evicted", std::to_string(baseline_evicted)},
          {"now_evicted", std::to_string(info.evicted_keys)},
          {"status", sample.ok ? "ok" : sample.reason}});
    check(!sample.ok, "restart_detected", sample.reason);
    check(sample.restarted || sample.counter_regressed, "restart_flag");
    return failures == 0 ? 0 : 1;
}

int runFaultChain(const std::string& socket, const std::string& identity,
                  const std::string& run_dir) {
    // Real process, real RedisCache/NodeCache, real connection loss injected by
    // the orchestrator: backend fault -> cache exception -> the store refuses to
    // continue -> the process writes a fatal marker and exits non-zero. This
    // exercises the component process chain that the Node relies on; it is NOT
    // a Kathara/network run.
    const std::filesystem::path control(run_dir + "/control");
    std::filesystem::create_directories(control / "fatal");
    std::filesystem::create_directories(run_dir + "/traces");

    auto store = std::make_unique<NodeCache>(
        std::make_unique<RedisCache>(socket), configFor(CachePolicy::Lru, 1048576), nullptr);
    store->seedOwner("fault-key", valueOfSize(64, 'f'));
    store->sealOwners();
    check(store->lookupForService("fault-key").value.has_value(), "precondition_owner_servable");

    // Announce readiness, then wait for the orchestrator to kill Redis.
    { std::ofstream(control / "ready") << "ready\n"; }
    for (int i = 0; i < 200; ++i) {
        if (std::filesystem::exists(control / "kill-redis")) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    check(std::filesystem::exists(control / "kill-redis"), "orchestrator_killed_redis");
    // Let the killed socket settle before the read.
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    bool indeterminate = false;
    std::string detail;
    try {
        (void)store->lookupForService("fault-key");
    } catch (const rsync_cache::BackendIndeterminate& e) {
        indeterminate = true;
        detail = e.what();
    } catch (const std::exception& e) {
        detail = std::string("unexpected exception type: ") + e.what();
    }
    check(indeterminate, "backend_fault_is_indeterminate", detail);
    check(!store->available(), "store_poisoned");

    bool refused = false;
    try {
        (void)store->lookupForService("fault-key");
    } catch (const rsync_cache::CacheUnavailable&) {
        refused = true;
    } catch (const std::exception&) {
    }
    check(refused, "second_call_refused");

    // Mirror the application's fatal marker so the validator can reject the run.
    {
        std::ofstream marker(control / "fatal" / "node1.backend-indeterminate");
        marker << "backend_indeterminate\t" << detail << "\n";
    }
    emit("process_fault_chain",
         {{"socket", socket}, {"identity", identity},
          {"indeterminate", indeterminate ? "1" : "0"},
          {"poisoned", store->available() ? "0" : "1"},
          {"second_call_refused", refused ? "1" : "0"},
          {"fatal_marker", "control/fatal/node1.backend-indeterminate"}});
    if (failures != 0) return 1;
    // Mirror the service's exit: an unconfirmable backend result is fatal, so
    // the process terminates non-zero AND leaves the fatal marker. The
    // orchestrator treats that non-zero exit plus the marker as PASS.
    return 3;
}

}  // namespace

int main(int argc, char** argv) {
    // A backend going away must surface as a classified BackendIndeterminate,
    // not as an unclassified SIGPIPE death, so ignore SIGPIPE.
    std::signal(SIGPIPE, SIG_IGN);
    if (argc < 4) {
        std::cerr << "usage: test_redis_acceptance_live "
                     "<run|restart-check|fault-chain> <socket> <identity> [...]\n";
        return 2;
    }
    const std::string mode = argv[1];
    const std::string socket = argv[2];
    const std::string identity = argv[3];

    try {
        if (mode == "run") {
            if (argc >= 5) openEvidence(argv[4]);
            return runStartupAndComponent(socket, identity);
        }
        if (mode == "restart-check") {
            if (argc < 6) {
                std::cerr << "restart-check needs <baseline_run_id> <baseline_evicted>\n";
                return 2;
            }
            if (argc >= 7) openEvidence(argv[6]);
            return runRestartCheck(socket, identity, argv[4], std::stoll(argv[5]));
        }
        if (mode == "fault-chain") {
            if (argc < 5) {
                std::cerr << "fault-chain needs <run_dir>\n";
                return 2;
            }
            if (argc >= 6) openEvidence(argv[5]);
            return runFaultChain(socket, identity, argv[4]);
        }
        std::cerr << "unknown mode: " << mode << "\n";
        return 2;
    } catch (const std::exception& e) {
        emit("fatal", {{"mode", mode}, {"error", e.what()}});
        std::cerr << "fatal: " << e.what() << "\n";
        return 1;
    }
}