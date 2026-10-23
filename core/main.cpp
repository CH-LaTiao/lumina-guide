#include <iostream>
#include <string>
#include <cstdint>
// M0-only freshness gate. No camera geometry or physical output is implemented.
bool fresh(std::int64_t captured, std::int64_t now) {
    return captured >= 0 && now >= captured && now-captured <= 200000000;
}
int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        if (!fresh(0,200000000) || fresh(0,200000001) || fresh(5,4) || fresh(-1,0)) return 1;
        std::cout << "freshness boundaries PASS\n"; return 0;
    }
    if (argc != 2 || std::string(argv[1]) != "--simulate") {
        std::cerr << "Usage: lumina-core --simulate | --self-test; hardware output unavailable\n";return 2;
    }
    for (const auto* state : {"BOOT", "SELF_TEST", "READY", "STOPPING"})
        std::cout << "{\"mode\":\"simulation\",\"state\":\"" << state
                  << "\",\"real_output\":false,\"camera\":\"not_opened\"}\n";
}
