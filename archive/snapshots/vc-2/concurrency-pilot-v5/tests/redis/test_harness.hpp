#pragma once

#include "value.hpp"

#include <chrono>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unistd.h>
#include <utility>
#include <vector>

namespace redis_test {

class TestFailure : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct TestCase {
    const char* name;
    const char* description;
    std::function<void()> run;
};

inline void require(bool condition, std::string message) {
    if (!condition) {
        throw TestFailure(std::move(message));
    }
}

inline void require_contains(std::string_view haystack,
                             std::string_view needle,
                             const std::string& context) {
    if (haystack.find(needle) == std::string_view::npos) {
        std::ostringstream stream;
        stream << context << " | expected to find [" << needle
               << "] in [" << haystack << "]";
        throw TestFailure(stream.str());
    }
}

inline void require_not_contains(std::string_view haystack,
                                 std::string_view needle,
                                 const std::string& context) {
    if (haystack.find(needle) != std::string_view::npos) {
        std::ostringstream stream;
        stream << context << " | did not expect to find [" << needle
               << "] in [" << haystack << "]";
        throw TestFailure(stream.str());
    }
}

inline std::string require_env(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || value[0] == '\0') {
        throw TestFailure(std::string("required environment variable is missing: ") + name);
    }
    return std::string(value);
}

class ScopedEnvVar {
public:
    explicit ScopedEnvVar(std::string name)
        : name_(std::move(name)) {
        const char* value = std::getenv(name_.c_str());
        if (value != nullptr) {
            original_ = std::string(value);
        }
    }

    ~ScopedEnvVar() {
        if (original_.has_value()) {
            setenv(name_.c_str(), original_->c_str(), 1);
        } else {
            unsetenv(name_.c_str());
        }
    }

    void set(const std::string& value) {
        setenv(name_.c_str(), value.c_str(), 1);
    }

    void unset() {
        unsetenv(name_.c_str());
    }

private:
    std::string name_;
    std::optional<std::string> original_;
};

inline Value make_value(const std::string& text) {
    return Value(std::vector<uint8_t>(text.begin(), text.end()));
}

inline std::string value_to_string(const Value& value) {
    return std::string(value.data().begin(), value.data().end());
}

inline std::string make_unique_key(const std::string& label) {
    const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
    std::ostringstream stream;
    stream << "phase5d:" << label << ":" << static_cast<long long>(::getpid())
           << ":" << static_cast<long long>(now);
    return stream.str();
}

inline int run_suite(const std::string& suite_name,
                     const std::vector<TestCase>& tests) {
    std::cout << "========================================\n";
    std::cout << suite_name << "\n";
    std::cout << "========================================\n";

    int failed = 0;
    for (const auto& test : tests) {
        std::cout << "\n[" << test.name << "] " << test.description << "\n";
        try {
            test.run();
            std::cout << "  PASS\n";
        } catch (const std::exception& e) {
            ++failed;
            std::cout << "  FAIL: " << e.what() << "\n";
        }
    }

    std::cout << "\n========================================\n";
    if (failed == 0) {
        std::cout << "suite result: PASS\n";
    } else {
        std::cout << "suite result: FAIL (" << failed << " test(s))\n";
    }
    std::cout << "========================================\n";

    return failed == 0 ? 0 : 1;
}

}  // namespace redis_test
