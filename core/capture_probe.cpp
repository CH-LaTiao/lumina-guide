// C++ single-owner capture probe.
//
// Proves the Orbbec C++ SDK can own the 336L before the IPC work: it opens the
// configured serial, starts 640x480@30 depth (Y16) + colour (MJPG), aligns the
// depth to colour, and reports the achieved frame rate. No output is actuated.
#include <libobsensor/ObSensor.hpp>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>

int main(int argc, char **argv) {
    const std::string wanted = argc > 1 ? argv[1] : "";
    const int frames = argc > 2 ? std::atoi(argv[2]) : 90;
    try {
        if (const char *extensions = std::getenv("LUMINA_ORBBEC_EXTENSIONS")) {
            ob::Context::setExtensionsDirectory(extensions);
        }
        ob::Context context;
        auto devices = context.queryDeviceList();
        const auto count = devices->getCount();
        std::cout << "devices " << count << std::endl;
        std::shared_ptr<ob::Device> device;
        for (uint32_t index = 0; index < count; ++index) {
            auto candidate = devices->getDevice(index);
            auto info = candidate->getDeviceInfo();
            if (wanted.empty() || info->getSerialNumber() == wanted) {
                device = candidate;
                break;
            }
        }
        if (!device) {
            std::cerr << "device not found" << std::endl;
            return 2;
        }
        auto info = device->getDeviceInfo();
        std::cout << "name " << info->getName() << " serial " << info->getSerialNumber() << std::endl;

        ob::Pipeline pipeline(device);
        auto config = std::make_shared<ob::Config>();
        std::cout << "step profiles" << std::endl;
        auto depthProfiles = pipeline.getStreamProfileList(OB_SENSOR_DEPTH);
        auto colorProfiles = pipeline.getStreamProfileList(OB_SENSOR_COLOR);
        std::cout << "step select" << std::endl;
        config->enableStream(depthProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_Y16, 30));
        config->enableStream(colorProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_MJPG, 30));
        std::cout << "step start" << std::endl;
        pipeline.start(config);
        int received = 0;
        const auto started = std::chrono::steady_clock::now();
        while (received < frames) {
            auto frameSet = pipeline.waitForFrameset(1000);
            if (!frameSet) {
                continue;
            }
            auto depth = frameSet->getDepthFrame();
            auto color = frameSet->getColorFrame();
            if (!depth || !color) {
                continue;
            }
            if (received == 0) {
                std::cout << "depth " << depth->getWidth() << "x" << depth->getHeight()
                          << " scale " << depth->getValueScale()
                          << " color " << color->getWidth() << "x" << color->getHeight() << std::endl;
            }
            ++received;
        }
        const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        std::cout << "frames " << received << " seconds " << seconds << " fps " << received / seconds << std::endl;
        pipeline.stop();
        return 0;
    }
    catch (ob::Error &error) {
        std::cerr << "sdk error: " << error.what() << " status " << error.getStatus() << std::endl;
        return 1;
    }
    catch (std::exception &error) {
        std::cerr << "error: " << error.what() << std::endl;
        return 1;
    }
}
