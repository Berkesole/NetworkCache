#pragma once

#include "cache.hpp"
#include <unordered_map>

class InMemoryCache : public LocalCache {
public:
    InMemoryCache() = default;
    ~InMemoryCache() override = default;

    void put(const std::string& key, const Value& value) override;
    std::optional<Value> get(const std::string& key) const override;
    void del(const std::string& key) override;
    bool exists(const std::string& key) const override;
    void flush() override;

private:
    // 以字节流为主存储，避免额外序列化开销
    std::unordered_map<std::string, std::vector<uint8_t>> store_;
};
