# Makefile - 便捷包装器
# Convenience wrapper for CMake
#
# 用法 / Usage:
#   make                    # 编译所有组件（router + tests）/ Build all components
#   make router             # 只编译 rsync-router / Build router only
#   make test               # 编译并运行所有测试 / Build and run all tests
#   make test-core          # 运行核心测试（无依赖）/ Run core tests (no dependencies)
#   make test-redis         # 运行 Redis 测试 / Run Redis tests
#   make clean              # 清理构建输出 / Clean build output
#   make distclean          # 删除 build 目录 / Remove build directory

CMAKE       ?= cmake
BUILD_DIR   ?= build
BUILD_TYPE  ?= Release
GENERATOR   ?= Unix Makefiles
REDIS_TEST_ENABLE_REDIS ?= ON
REDIS_TEST_FETCH_DEPS   ?= ON

.PHONY: all configure configure-redis-tests router test test-core test-redis clean distclean demo run mesh run-mesh

# 默认目标：编译所有组件
# Default target: build all components
all: configure
	$(CMAKE) --build $(BUILD_DIR) -- -j

# 配置阶段
# Configuration phase
configure:
	$(CMAKE) -S . -B $(BUILD_DIR) -G "$(GENERATOR)" -DCMAKE_BUILD_TYPE=$(BUILD_TYPE)

# 编译主程序
# Build main program
router: configure
	$(CMAKE) --build $(BUILD_DIR) --target rsync-router -- -j

# 为 Redis 测试套件单独配置（显式启用 Redis 支持）
# Dedicated configure step for Redis test suite (explicitly enables Redis support)
configure-redis-tests:
	$(CMAKE) -S . -B $(BUILD_DIR) -G "$(GENERATOR)" -DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DENABLE_REDIS=$(REDIS_TEST_ENABLE_REDIS) -DFETCH_REDIS_DEPS=$(REDIS_TEST_FETCH_DEPS)

# ============================================================================
# 测试相关命令
# Test commands
# ============================================================================

# 运行所有测试
# Run all tests
test: test-core test-redis

# 运行核心测试（无依赖）
# Run core tests (no dependencies)
test-core: configure
	@echo "========================================="
	@echo "  编译核心测试..."
	@echo "========================================="
	$(CMAKE) --build $(BUILD_DIR) --target test_core -- -j
	@echo ""
	@echo "========================================="
	@echo "  运行核心测试..."
	@echo "========================================="
	./$(BUILD_DIR)/test_core

# 运行 Redis 测试
# Run Redis tests
test-redis: configure-redis-tests
	@echo "========================================="
	@echo "  编译 Redis 测试套件..."
	@echo "========================================="
	$(CMAKE) --build $(BUILD_DIR) --target test_redis -- -j
	@echo ""
	@echo "========================================="
	@echo "  运行 Redis 测试套件..."
	@echo "========================================="
	./scripts/tests/run_redis_suite.sh ./$(BUILD_DIR)/test_redis_cache_contract ./$(BUILD_DIR)/rsync-node-dev

# 清理
# Clean
clean:
	$(CMAKE) --build $(BUILD_DIR) --target clean -- -j || true

distclean:
	rm -rf $(BUILD_DIR)

# ============================================================================
# 兼容旧命令
# Compatibility aliases for old commands
# ============================================================================
demo: router

run: router
	./$(BUILD_DIR)/rsync-router

# 兼容旧的 redis_test 命令
redis_test: test-redis

run_redis_test: test-redis

run_test_unix_socket: test-redis

# 8x8 mesh 拓扑 demo 快捷命令
# 8x8 mesh demo shortcuts
mesh: configure
	$(CMAKE) --build $(BUILD_DIR) --target test_mesh_demo -- -j

run_mesh: mesh
	./$(BUILD_DIR)/test_mesh_demo
