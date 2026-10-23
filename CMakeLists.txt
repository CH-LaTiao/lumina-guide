cmake_minimum_required(VERSION 3.16)
project(lumina_guide VERSION 0.7.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(lumina-core core/main.cpp)
target_compile_options(lumina-core PRIVATE -Wall -Wextra -Werror)
enable_testing()
add_test(NAME expired_frame COMMAND lumina-core --self-test)
add_library(lumina-depth SHARED core/depth_regions.cpp core/geometry_clusters.cpp)
target_compile_options(lumina-depth PRIVATE -Wall -Wextra -Werror -O2)
add_executable(test-depth core/test_depth.cpp)
target_link_libraries(test-depth PRIVATE lumina-depth)
add_test(NAME depth_unknown_stale_and_outliers COMMAND test-depth)
add_executable(test-geometry core/test_geometry.cpp)
target_link_libraries(test-geometry PRIVATE lumina-depth)
add_test(NAME geometry_clusters_and_invalid_points COMMAND test-geometry)

# Optional C++ single-owner capture probe. Build with:
#   cmake -S . -B build -DLUMINA_ORBBEC_SDK=$HOME/orbbec-sdk/<sdk-dir>
set(LUMINA_ORBBEC_SDK "" CACHE PATH "Orbbec SDK root containing include/ and lib/")
if(LUMINA_ORBBEC_SDK)
    add_executable(capture-probe core/capture_probe.cpp)
    target_include_directories(capture-probe PRIVATE ${LUMINA_ORBBEC_SDK}/include)
    target_link_directories(capture-probe PRIVATE ${LUMINA_ORBBEC_SDK}/lib)
    target_link_libraries(capture-probe PRIVATE OrbbecSDK)
    set_target_properties(capture-probe PROPERTIES
        BUILD_RPATH ${LUMINA_ORBBEC_SDK}/lib
        INSTALL_RPATH ${LUMINA_ORBBEC_SDK}/lib)
    add_executable(frame-server core/frame_server.cpp)
    target_link_libraries(frame-server PRIVATE OrbbecSDK rt)
    target_include_directories(frame-server PRIVATE ${LUMINA_ORBBEC_SDK}/include)
    target_link_directories(frame-server PRIVATE ${LUMINA_ORBBEC_SDK}/lib)
    set_target_properties(frame-server PROPERTIES
        BUILD_RPATH ${LUMINA_ORBBEC_SDK}/lib
        INSTALL_RPATH ${LUMINA_ORBBEC_SDK}/lib)
endif()
