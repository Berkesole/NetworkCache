#include <functional>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    for (int index = 1; index < argc; ++index) {
        const std::string key(argv[index]);
        std::cout << key << '\t' << (std::hash<std::string>{}(key) % 1024) << '\n';
    }
    return 0;
}
