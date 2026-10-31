# Build the experiment-line NetworkCache implementation at the repository root.
CMAKE ?= cmake
BUILD_DIR ?= build
REDIS_BUILD_DIR ?= build-redis
BUILD_TYPE ?= Debug
JOBS ?= 2
FETCH_REDIS_DEPS ?= OFF

.PHONY: all configure run test test-core run-mesh test-redis clean

all: configure
	$(CMAKE) --build "$(BUILD_DIR)" --parallel $(JOBS)

configure:
	$(CMAKE) -S . -B "$(BUILD_DIR)" \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DENABLE_REDIS=OFF \
		-DFETCH_REDIS_DEPS=OFF

run: all
	"$(BUILD_DIR)/rsync-router" config/3-node-topology.yaml

test: all
	"$(BUILD_DIR)/test_core"
	"$(BUILD_DIR)/test_packet_codec"
	"$(BUILD_DIR)/test_query_table_fallback"
	"$(BUILD_DIR)/test_node_staged_fallback"
	"$(BUILD_DIR)/test_runtime_neighbor"
	"$(BUILD_DIR)/test_cache_namespace"
	"$(BUILD_DIR)/test_replica_cache"
	"$(BUILD_DIR)/test_node_cache_events"
	"$(BUILD_DIR)/test_node_finite_cache"
	"$(BUILD_DIR)/test_cache_trace_schema"
	"$(BUILD_DIR)/test_redis_acceptance"

test-core: all
	"$(BUILD_DIR)/test_core"

run-mesh: all
	"$(BUILD_DIR)/test_mesh_demo"

test-redis:
	$(CMAKE) -S . -B "$(REDIS_BUILD_DIR)" \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DENABLE_REDIS=ON \
		-DFETCH_REDIS_DEPS=$(FETCH_REDIS_DEPS)
	$(CMAKE) --build "$(REDIS_BUILD_DIR)" \
		--target test_redis_cache_contract test_redis_acceptance_live \
		--parallel $(JOBS)
	bash scripts/tests/run_redis_suite.sh \
		"$(REDIS_BUILD_DIR)/test_redis_cache_contract" \
		"$(REDIS_BUILD_DIR)/rsync-node-dev"

clean:
	$(CMAKE) --build "$(BUILD_DIR)" --target clean

