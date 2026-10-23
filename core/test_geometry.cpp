#include <cassert>
#include <cstdint>
#include <limits>
extern "C" int lumina_geometry_clusters(const float*, const std::uint8_t*, int, int*);
int main() {
    float xyz[45] = {};
    std::uint8_t eligible[15];
    int labels[15];
    for (int i=0; i<15; ++i) {
        xyz[i*3] = (i<6 ? -.5f : i<12 ? .5f : 1.f) + .001f*i;
        xyz[i*3+2] = 1.f;
        eligible[i] = 1;
    }
    xyz[14*3] = std::numeric_limits<float>::quiet_NaN();
    assert(lumina_geometry_clusters(xyz, eligible, 15, labels) == 2);
    for (int i=0; i<6; ++i) assert(labels[i] == 0);
    for (int i=6; i<12; ++i) assert(labels[i] == 1);
    for (int i=12; i<15; ++i) assert(labels[i] == -1);
    eligible[0] = 0;
    assert(lumina_geometry_clusters(xyz, eligible, 15, labels) == 1);
    for (int i=0; i<6; ++i) assert(labels[i] == -1);
    assert(lumina_geometry_clusters(nullptr, eligible, 15, labels) == -1);
}
