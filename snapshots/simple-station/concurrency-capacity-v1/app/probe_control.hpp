#pragma once
#include <cstdint>
#include <string>
#include <stdexcept>
namespace experiment_probe {
inline bool reserved(const std::string& id) { return id.rfind("900000",0)==0; }
inline bool identity(uint32_t node,const std::string& id,const std::string& key) {
 return (node==2 && id=="900000001" && key=="federation_v4h3_probe_B") ||
        (node==34 && id=="900000002" && key=="federation_v4h3_probe_A");
}
inline void retirement(bool registered,bool before_post,std::size_t pending,
 uint64_t start,uint64_t expiry,uint64_t now,uint64_t ttl) {
 if (!registered || !before_post || pending!=1 || expiry<start || expiry-start<ttl || expiry>now)
  throw std::runtime_error("invalid isolation probe retirement");
}
}
