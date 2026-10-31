#pragma once
#include <vector>
#include <cstdint>

class Value {
public:
    Value() = default;
    explicit Value(std::vector<uint8_t> data) : data_(std::move(data)) {}

    const std::vector<uint8_t>& data() const { return data_; }
    std::vector<uint8_t>& data() { return data_; }

    size_t size() const { return data_.size(); }
    bool empty() const { return data_.empty(); }

private:
    std::vector<uint8_t> data_;
};
