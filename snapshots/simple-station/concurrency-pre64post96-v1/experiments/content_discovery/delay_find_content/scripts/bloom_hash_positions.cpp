#include "bloomfilter.hpp"

#include <iostream>
#include <string>

int main(int argc, char** argv) {
    BloomFilter filter(2048, 3);
    for (int index = 1; index < argc; ++index) {
        const std::string key(argv[index]);
        const auto positions = filter.positions(key);
        std::cout << key;
        for (const auto position : positions) std::cout << '\t' << position;
        std::cout << '\n';
    }
    return 0;
}
