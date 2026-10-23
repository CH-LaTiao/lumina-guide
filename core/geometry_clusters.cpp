#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <vector>

namespace {
struct Cell {
    int x, y, z;
    bool operator==(const Cell& b) const { return x == b.x && y == b.y && z == b.z; }
};
struct Hash {
    std::size_t operator()(const Cell& c) const {
        return (std::hash<int>{}(c.x) * 73856093u) ^
               (std::hash<int>{}(c.y) * 19349663u) ^ (std::hash<int>{}(c.z) * 83492791u);
    }
};
}

// Diagnostic 5cm voxels, 26-neighbour components. Labels -1 mean unsupported.
extern "C" int lumina_geometry_clusters(const float* xyz, const std::uint8_t* eligible,
    int count, int* labels) {
    if (!xyz || !eligible || !labels || count < 0 || count > 100000) return -1;
    std::unordered_map<Cell, std::vector<int>, Hash> cells;
    for (int i = 0; i < count; ++i) {
        labels[i] = -1;
        if (!eligible[i]) continue;
        const float* p = xyz + i * 3;
        if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2]) ||
            std::abs(p[0]) > 20 || std::abs(p[1]) > 20 || p[2] < .3f || p[2] > 4.f) continue;
        cells[{int(std::floor(p[0] / .05f)), int(std::floor(p[1] / .05f)),
               int(std::floor(p[2] / .05f))}].push_back(i);
    }
    int label = 0;
    // Iterate input order for deterministic labels, independent of hash iteration order.
    for (int i = 0; i < count; ++i) {
        if (!eligible[i]) continue;
        const float* p = xyz + i * 3;
        if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2]) ||
            std::abs(p[0]) > 20 || std::abs(p[1]) > 20 || p[2] < .3f || p[2] > 4.f) continue;
        Cell first{int(std::floor(p[0] / .05f)), int(std::floor(p[1] / .05f)), int(std::floor(p[2] / .05f))};
        if (!cells.count(first)) continue;
        std::vector<Cell> queue{first};
        std::vector<int> members;
        for (std::size_t q = 0; q < queue.size(); ++q) {
            auto it = cells.find(queue[q]);
            if (it == cells.end()) continue;
            members.insert(members.end(), it->second.begin(), it->second.end());
            cells.erase(it);
            for (int x = -1; x <= 1; ++x)
                for (int y = -1; y <= 1; ++y)
                    for (int z = -1; z <= 1; ++z) {
                        Cell next{queue[q].x+x, queue[q].y+y, queue[q].z+z};
                        if (cells.count(next)) queue.push_back(next);
                    }
        }
        if (members.size() < 6) continue;
        for (int m : members) labels[m] = label;
        ++label;
    }
    return label;
}
