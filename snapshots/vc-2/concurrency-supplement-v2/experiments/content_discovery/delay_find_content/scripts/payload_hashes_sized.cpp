#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    const auto size = static_cast<std::size_t>(std::stoull(argv[1]));
    for (int index = 2; index < argc; ++index) {
        const auto seed = static_cast<std::uint64_t>(std::stoull(argv[index]));
        std::uint64_t state = seed == 0 ? 0x9e3779b97f4a7c15ULL : seed;
        std::uint64_t hash = 14695981039346656037ULL;
        for (std::size_t byte = 0; byte < size; ++byte) {
            state ^= state << 13U;
            state ^= state >> 7U;
            state ^= state << 17U;
            hash ^= static_cast<std::uint8_t>(state & 0xffU);
            hash *= 1099511628211ULL;
        }
        std::cout << seed << '\t' << std::hex << std::setw(16) << std::setfill('0')
                  << hash << std::dec << '\n';
    }
}
