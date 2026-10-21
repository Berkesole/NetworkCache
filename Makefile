# Convenience commands. Use separate directories for core and Redis builds.
CMAKE ?= cmake
PYTHON ?= python3
BUILD_DIR ?= build
REDIS_BUILD_DIR ?= build-redis
BUILD_TYPE ?= Debug
JOBS ?= 2
FETCH_REDIS_DEPS ?= OFF

.PHONY: all configure test test-core run run-mesh test-redis clean
all: configure
	$(CMAKE) --build "$(BUILD_DIR)" --parallel $(JOBS)

configure:
	$(CMAKE) -S . -B "$(BUILD_DIR)" -DCMAKE_BUILD_TYPE=$(BUILD_TYPE) -DENABLE_REDIS=OFF -DFETCH_REDIS_DEPS=OFF

test: all
	$(PYTHON) scripts/tests/verify_local.py --build-dir "$(BUILD_DIR)"

test-core: all
	"$(BUILD_DIR)/test_core"

run: all
	"$(BUILD_DIR)/rsync-router" config/3-node-topology.yaml

run-mesh: all
	"$(BUILD_DIR)/test_mesh_demo"

test-redis:
	$(CMAKE) -S . -B "$(REDIS_BUILD_DIR)" -DCMAKE_BUILD_TYPE=$(BUILD_TYPE) -DENABLE_REDIS=ON -DFETCH_REDIS_DEPS=$(FETCH_REDIS_DEPS)
	$(CMAKE) --build "$(REDIS_BUILD_DIR)" --target test_redis --parallel $(JOBS)
	bash scripts/tests/run_redis_suite.sh "$(REDIS_BUILD_DIR)/test_redis_cache_contract" "$(REDIS_BUILD_DIR)/rsync-node-dev"

clean:
	$(CMAKE) --build "$(BUILD_DIR)" --target clean
