// tests/test_core.cpp
// 核心功能测试（无外部依赖）
//
// 包含：
// - Value 序列化测试
// - InMemoryCache 测试
// - BloomFilter 基础测试
//
// 编译：cmake --build build --target test_core
// 运行：./build/test_core

#include <iostream>
#include <cassert>
#include <stdexcept>
#include "value.hpp"
#include "cache/in_memory_cache.hpp"
#include "bloomfilter.hpp"

// ============================================================================
// 简单测试框架
// ============================================================================

#define TEST(name) void test_##name()
#define RUN_TEST(name, description) do { \
    std::cout << "运行测试: " #name << "\n"; \
    std::cout << "  说明: " << description << "\n"; \
    std::cout << "  结果: "; \
    try { \
        test_##name(); \
        std::cout << "✓ 通过\n"; \
    } catch (const std::exception& e) { \
        std::cout << "✗ 失败: " << e.what() << "\n"; \
        failed++; \
    } \
    std::cout << "\n"; \
} while(0)

static int failed = 0;

// ============================================================================
// 辅助函数
// ============================================================================

// 从 string 创建 Value 对象
static Value make_value(const std::string& str) {
    return Value(std::vector<uint8_t>(str.begin(), str.end()));
}

// 从 Value 提取 string（用于比较）
static std::string value_to_string(const Value& v) {
    return std::string(v.data().begin(), v.data().end());
}

// ============================================================================

// ============================================================================
// InMemoryCache 测试
// ============================================================================

TEST(inmemory_basic_put_get) {
    InMemoryCache cache;

    cache.put("key1", make_value("value1"));
    auto result = cache.get("key1");

    assert(result.has_value());
    assert(value_to_string(result.value()) == "value1");
}

TEST(inmemory_get_nonexistent) {
    InMemoryCache cache;

    auto result = cache.get("nonexistent_key");
    assert(!result.has_value());
}

TEST(inmemory_exists) {
    InMemoryCache cache;

    cache.put("exists_key", make_value("some_value"));

    assert(cache.exists("exists_key") == true);
    assert(cache.exists("not_exists_key") == false);
}

TEST(inmemory_del) {
    InMemoryCache cache;

    cache.put("del_key", make_value("value"));
    assert(cache.exists("del_key"));

    cache.del("del_key");
    assert(!cache.exists("del_key"));
}

TEST(inmemory_overwrite) {
    InMemoryCache cache;

    cache.put("key1", make_value("value1"));
    cache.put("key1", make_value("value2"));

    auto result = cache.get("key1");
    assert(result.has_value());
    assert(value_to_string(result.value()) == "value2");
}

TEST(inmemory_flush) {
    InMemoryCache cache;

    // 添加一些数据
    cache.put("key1", make_value("value1"));
    cache.put("key2", make_value("value2"));

    // 验证数据存在
    assert(cache.exists("key1"));
    assert(cache.exists("key2"));

    // 清空缓存
    cache.flush();

    // 验证数据已清空
    assert(!cache.exists("key1"));
    assert(!cache.exists("key2"));
}

TEST(inmemory_binary_data) {
    InMemoryCache cache;

    // 测试二进制数据存储
    std::vector<uint8_t> binary_data = {0x00, 0xFF, 0x7F, 0x80, 0x01};
    cache.put("binary_key", Value(binary_data));

    auto result = cache.get("binary_key");
    assert(result.has_value());
    assert(result->size() == binary_data.size());
    assert(std::equal(result->data().begin(), result->data().end(), binary_data.begin()));
}

// ============================================================================
// BloomFilter 基础测试
// ============================================================================

TEST(bloomfilter_basic) {
    BloomFilter bf(1024);

    bf.add("key1");
    bf.add("key2");

    assert(bf.contains("key1"));
    assert(bf.contains("key2"));
    assert(!bf.contains("key3"));
}

TEST(bloomfilter_batch_add) {
    BloomFilter bf(1024);

    std::vector<std::string> keys = {"key1", "key2", "key3"};
    bf.batch_add(keys);

    assert(bf.contains("key1"));
    assert(bf.contains("key2"));
    assert(bf.contains("key3"));
    assert(!bf.contains("key4"));
}

TEST(bloomfilter_merge) {
    BloomFilter bf1(1024);
    BloomFilter bf2(1024);

    bf1.add("key1");
    bf2.add("key2");

    bf1.merge(bf2);

    assert(bf1.contains("key1"));
    assert(bf1.contains("key2"));
}

// ============================================================================
// 主函数
// ============================================================================

int main() {
    std::cout << "========================================\n";
    std::cout << "       核心功能测试（无外部依赖）       \n";
    std::cout << "========================================\n\n";

    // InMemoryCache 测试
    std::cout << "--- InMemoryCache 测试 ---\n\n";
    RUN_TEST(inmemory_basic_put_get, "基本 put/get 操作");
    RUN_TEST(inmemory_get_nonexistent, "get 不存在的 key");
    RUN_TEST(inmemory_exists, "exists 方法");
    RUN_TEST(inmemory_del, "del 方法");
    RUN_TEST(inmemory_overwrite, "覆盖写入");
    RUN_TEST(inmemory_flush, "flush 清空所有数据");
    RUN_TEST(inmemory_binary_data, "二进制数据存储");

    // BloomFilter 测试
    std::cout << "--- BloomFilter 测试 ---\n\n";
    RUN_TEST(bloomfilter_basic, "基本 add/contains 操作");
    RUN_TEST(bloomfilter_batch_add, "批量添加");
    RUN_TEST(bloomfilter_merge, "合并操作");

    // 总结
    std::cout << "========================================\n";
    if (failed == 0) {
        std::cout << "🎉 所有测试通过！\n";
    } else {
        std::cout << "❌ " << failed << " 个测试失败\n";
    }
    std::cout << "========================================\n";

    return failed > 0 ? 1 : 0;
}
