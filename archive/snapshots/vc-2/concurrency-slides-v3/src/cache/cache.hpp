#pragma once

#include <optional>
#include <string>
#include <vector>
#include "../value.hpp"

// 纯 KV 抽象接口，供节点内部存取使用
// 设计原则：操作成功则正常返回，失败则抛出异常（不降级）
class LocalCache {
public:
    virtual ~LocalCache() = default;

    // 写入/更新，失败抛异常
    virtual void put(const std::string& key, const Value& value) = 0;

    // 精确读取，不存在返回 nullopt，后端错误抛异常
    virtual std::optional<Value> get(const std::string& key) const = 0;

    // 删除指定 key，失败抛异常
    virtual void del(const std::string& key) = 0;

    // 判断 key 是否存在，后端错误抛异常
    virtual bool exists(const std::string& key) const = 0;

    // 清空缓存所有数据，失败抛异常
    // InMemoryCache: 清空整个 store_
    // RedisCache: 执行 FLUSHDB（清空当前 DB 的所有 key，需要显式允许）
    virtual void flush() = 0;

protected:
    LocalCache() = default;
};
