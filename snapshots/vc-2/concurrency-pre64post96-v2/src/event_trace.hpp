#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace rsync_trace {

using FieldList = std::vector<std::pair<std::string, std::string>>;

bool enabled();

void record(uint32_t node_id, const std::string& event, const FieldList& fields = {});

void flush();

}  // namespace rsync_trace
