// Single-owner camera frame server.
//
// Opens the Orbbec camera in this process only and publishes the newest aligned
// payload into a small POSIX shared-memory ring. Consumers (Python) read the
// slot with the highest published sequence number and re-check it after copying.
// Depth stays raw Y16; colour stays the camera's MJPG payload.
#include <libobsensor/ObSensor.hpp>

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <sys/mman.h>
#include <sys/file.h>
#include <thread>
#include <unistd.h>

namespace {

constexpr uint32_t kMagic = 0x314E4D4C;  // "LMN1"
constexpr uint32_t kVersion = 2;
constexpr uint32_t kSlots = 3;
constexpr uint32_t kDepthCapacity = 640 * 480 * 2;
constexpr uint32_t kColorCapacity = 512 * 1024;
constexpr uint32_t kHeaderSize = 64;
constexpr uint32_t kSlotHeader = 96;
constexpr uint32_t kSlotSize = kSlotHeader + kDepthCapacity + kColorCapacity;
constexpr uint32_t kRingSize = kHeaderSize + kSlots * kSlotSize;
constexpr uint32_t kImuMagic = 0x554D494C;  // "LIMU"
constexpr uint32_t kImuVersion = 1;
constexpr uint32_t kImuSize = 128;

struct Header {
    uint32_t magic;
    uint32_t version;
    uint32_t slot_count;
    uint32_t slot_size;
    uint32_t depth_capacity;
    uint32_t color_capacity;
    float fx;
    float fy;
    float cx;
    float cy;
    uint32_t model_width;
    uint32_t model_height;
    uint64_t epoch;
    uint64_t ready;
};

struct SlotHeader {
    uint64_t seq;
    uint64_t frame_id;
    uint64_t depth_ts_us;
    uint64_t color_ts_us;
    uint64_t received_ns;
    uint64_t published_ns;
    uint32_t width;
    uint32_t height;
    uint32_t depth_size;
    uint32_t color_size;
    float depth_scale;
    uint32_t pad[7];
};

struct ImuSnapshot {
    uint32_t magic;
    uint32_t version;
    uint64_t epoch;
    uint64_t seq;
    uint64_t accel_ts_us;
    uint64_t gyro_ts_us;
    uint64_t received_ns;
    float ax;
    float ay;
    float az;
    float gx;
    float gy;
    float gz;
    float accel_temperature;
    float gyro_temperature;
    uint32_t flags;
    uint32_t reserved[11];
};

static_assert(sizeof(Header) == kHeaderSize, "header size");
static_assert(sizeof(SlotHeader) == kSlotHeader, "slot header size");
static_assert(sizeof(ImuSnapshot) == kImuSize, "imu snapshot size");

volatile std::sig_atomic_t running = 1;

void stop(int) {
    running = 0;
}

uint64_t monotonic_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

}  // namespace

int main(int argc, char **argv) {
    const std::string serial = argc > 1 ? argv[1] : "";
    const std::string name = argc > 2 ? argv[2] : "/lumina-frames-v1";
    try {
        if (const char *extensions = std::getenv("LUMINA_ORBBEC_EXTENSIONS")) {
            ob::Context::setExtensionsDirectory(extensions);
        }
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);

        const int owner = shm_open((name + "-owner").c_str(), O_CREAT | O_RDWR, 0600);
        if (owner < 0 || flock(owner, LOCK_EX | LOCK_NB) != 0) {
            std::cerr << "frame server already owns this ring" << std::endl;
            return 2;
        }
        // Restart uses a new inode: old readers expire rather than see reused IDs.
        shm_unlink(name.c_str());
        const int fd = shm_open(name.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
        if (fd < 0) {
            std::cerr << "shm_open failed" << std::endl;
            return 2;
        }
        if (ftruncate(fd, kRingSize) != 0) {
            std::cerr << "ftruncate failed" << std::endl;
            return 2;
        }
        void *base = mmap(nullptr, kRingSize, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (base == MAP_FAILED) {
            std::cerr << "mmap failed" << std::endl;
            return 2;
        }
        flock(fd, LOCK_EX);
        std::memset(base, 0, kRingSize);
        auto *header = static_cast<Header *>(base);
        header->magic = kMagic;
        header->version = kVersion;
        header->slot_count = kSlots;
        header->slot_size = kSlotSize;
        header->depth_capacity = kDepthCapacity;
        header->color_capacity = kColorCapacity;
        header->epoch = monotonic_ns();
        flock(fd, LOCK_UN);

        const std::string imuName = name + "-imu";
        shm_unlink(imuName.c_str());
        const int imuFd = shm_open(imuName.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
        if (imuFd < 0 || ftruncate(imuFd, kImuSize) != 0) {
            std::cerr << "imu shm_open/ftruncate failed" << std::endl;
            return 2;
        }
        void *imuBase = mmap(nullptr, kImuSize, PROT_READ | PROT_WRITE, MAP_SHARED, imuFd, 0);
        if (imuBase == MAP_FAILED) {
            std::cerr << "imu mmap failed" << std::endl;
            return 2;
        }
        auto *imu = static_cast<ImuSnapshot *>(imuBase);
        std::memset(imuBase, 0, kImuSize);
        imu->magic = kImuMagic;
        imu->version = kImuVersion;
        imu->epoch = header->epoch;

        ob::Context context;
        std::shared_ptr<ob::Device> device;
        // USB re-enumeration after a restarted owner can take a few seconds.
        for (int attempt = 0; attempt < 20 && running && !device; ++attempt) {
            auto devices = context.queryDeviceList();
            for (uint32_t index = 0; index < devices->getCount(); ++index) {
                auto candidate = devices->getDevice(index);
                if (serial.empty() || candidate->getDeviceInfo()->getSerialNumber() == serial) {
                    device = candidate;
                    break;
                }
            }
            if (!device) {
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
        }
        if (!device) {
            std::cerr << "device not found" << std::endl;
            return 3;
        }
        ob::Pipeline pipeline(device);
        auto config = std::make_shared<ob::Config>();
        auto depthProfiles = pipeline.getStreamProfileList(OB_SENSOR_DEPTH);
        auto colorProfiles = pipeline.getStreamProfileList(OB_SENSOR_COLOR);
        auto depthProfile = depthProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_Y16, 30);
        auto colorProfile = colorProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_MJPG, 30);
        config->enableStream(depthProfile);
        config->enableStream(colorProfile);
        // Aligned depth shares the colour pixel grid, so publish colour intrinsics.
        const auto intrinsic = colorProfile->getIntrinsic();
        flock(fd, LOCK_EX);
        header->fx = intrinsic.fx;
        header->fy = intrinsic.fy;
        header->cx = intrinsic.cx;
        header->cy = intrinsic.cy;
        header->model_width = intrinsic.width;
        header->model_height = intrinsic.height;
        header->ready = 1;
        flock(fd, LOCK_UN);
        pipeline.start(config);
        std::cout << "frame server ready: " << name << " slots " << kSlots << std::endl;

        std::mutex imuMutex;
        uint64_t imuSeq = 0;
        bool hasAccel = false, hasGyro = false;
        uint64_t accelTs = 0, gyroTs = 0;
        float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
        float accelTemp = 0, gyroTemp = 0;
        auto publishImu = [&]() {
            const uint64_t next = imuSeq + 1;
            __atomic_store_n(&imu->seq, next, __ATOMIC_RELEASE);
            imu->accel_ts_us = accelTs;
            imu->gyro_ts_us = gyroTs;
            imu->received_ns = monotonic_ns();
            imu->ax = ax;
            imu->ay = ay;
            imu->az = az;
            imu->gx = gx;
            imu->gy = gy;
            imu->gz = gz;
            imu->accel_temperature = accelTemp;
            imu->gyro_temperature = gyroTemp;
            imu->flags = (hasAccel ? 1u : 0u) | (hasGyro ? 2u : 0u);
            __atomic_store_n(&imu->seq, next + 1, __ATOMIC_RELEASE);
            imuSeq += 2;
        };

        bool accelStarted = false, gyroStarted = false;
        std::shared_ptr<ob::Sensor> accelSensor, gyroSensor;
        try {
            accelSensor = device->getSensor(OB_SENSOR_ACCEL);
            gyroSensor = device->getSensor(OB_SENSOR_GYRO);
            if (accelSensor) {
                auto profiles = accelSensor->getStreamProfileList();
                std::shared_ptr<ob::AccelStreamProfile> profile;
                try {
                    profile = profiles->getAccelStreamProfile(OB_ACCEL_FS_4g, OB_SAMPLE_RATE_100_HZ);
                } catch (ob::Error &) {
                    profile = profiles->getAccelStreamProfile(OB_ACCEL_FS_UNKNOWN, OB_SAMPLE_RATE_UNKNOWN);
                }
                accelSensor->start(profile, [&](std::shared_ptr<ob::Frame> frame) {
                    auto value = frame->as<ob::AccelFrame>();
                    if (!value) return;
                    auto sample = value->getValue();
                    std::lock_guard<std::mutex> guard(imuMutex);
                    ax = sample.x;
                    ay = sample.y;
                    az = sample.z;
                    accelTemp = value->getTemperature();
                    accelTs = value->getTimeStampUs();
                    hasAccel = true;
                    publishImu();
                });
                accelStarted = true;
            }
            if (gyroSensor) {
                auto profiles = gyroSensor->getStreamProfileList();
                std::shared_ptr<ob::GyroStreamProfile> profile;
                try {
                    profile = profiles->getGyroStreamProfile(OB_GYRO_FS_250dps, OB_SAMPLE_RATE_100_HZ);
                } catch (ob::Error &) {
                    profile = profiles->getGyroStreamProfile(OB_GYRO_FS_UNKNOWN, OB_SAMPLE_RATE_UNKNOWN);
                }
                gyroSensor->start(profile, [&](std::shared_ptr<ob::Frame> frame) {
                    auto value = frame->as<ob::GyroFrame>();
                    if (!value) return;
                    auto sample = value->getValue();
                    std::lock_guard<std::mutex> guard(imuMutex);
                    gx = sample.x;
                    gy = sample.y;
                    gz = sample.z;
                    gyroTemp = value->getTemperature();
                    gyroTs = value->getTimeStampUs();
                    hasGyro = true;
                    publishImu();
                });
                gyroStarted = true;
            }
            std::cout << "imu accel " << (accelStarted ? "ready" : "unavailable")
                      << " gyro " << (gyroStarted ? "ready" : "unavailable")
                      << " rates 100Hz" << std::endl;
        } catch (ob::Error &error) {
            std::cerr << "imu unavailable: " << error.what() << std::endl;
        }

        uint32_t counter = 0;
        uint64_t published = 0;
        auto statStart = std::chrono::steady_clock::now();
        uint32_t statFrames = 0;
        std::mutex arrivalMutex, publishMutex;
        std::map<uint64_t, uint64_t> arrivals;
        auto align = std::make_shared<ob::Align>(OB_STREAM_COLOR);
        align->setCallBack([&](std::shared_ptr<ob::Frame> frame) {
            auto frameSet = frame->as<ob::FrameSet>();
            if (!frameSet) {
                return;
            }
            auto depth = frameSet->getDepthFrame();
            auto color = frameSet->getColorFrame();
            if (!depth || !color) {
                return;
            }
            const uint32_t depthSize = static_cast<uint32_t>(depth->getDataSize());
            const uint32_t colorSize = static_cast<uint32_t>(color->getDataSize());
            if (depthSize == 0 || depthSize > kDepthCapacity || colorSize > kColorCapacity) {
                return;
            }
            uint64_t received = 0;
            {
                std::lock_guard<std::mutex> guard(arrivalMutex);
                auto it = arrivals.find(color->getTimeStampUs());
                if (it == arrivals.end()) return;
                received = it->second;
                arrivals.erase(arrivals.begin(), std::next(it));
            }
            std::lock_guard<std::mutex> publishGuard(publishMutex);
            // Shared readers lock the whole copy. Drop instead of blocking capture.
            if (flock(fd, LOCK_EX | LOCK_NB) != 0) return;
            auto *slot = static_cast<uint8_t *>(base) + kHeaderSize + (counter % kSlots) * kSlotSize;
            auto *meta = reinterpret_cast<SlotHeader *>(slot);
            meta->frame_id = counter;
            meta->depth_ts_us = depth->getTimeStampUs();
            meta->color_ts_us = color->getTimeStampUs();
            meta->received_ns = received;
            meta->published_ns = monotonic_ns();
            meta->width = depth->getWidth();
            meta->height = depth->getHeight();
            meta->depth_size = depthSize;
            meta->color_size = colorSize;
            meta->depth_scale = depth->getValueScale();
            std::memcpy(slot + kSlotHeader, depth->getData(), depthSize);
            std::memcpy(slot + kSlotHeader + kDepthCapacity, color->getData(), colorSize);
            std::atomic_thread_fence(std::memory_order_release);
            __atomic_store_n(&meta->seq, ++published, __ATOMIC_RELEASE);
            flock(fd, LOCK_UN);
            ++counter;
            ++statFrames;
            const auto now = std::chrono::steady_clock::now();
            const double elapsed = std::chrono::duration<double>(now - statStart).count();
            if (elapsed >= 5.0) {
                std::cout << "frames " << statFrames << " fps " << statFrames / elapsed << std::endl;
                statFrames = 0;
                statStart = now;
            }
        });
        while (running) {
            auto frameSet = pipeline.waitForFrameset(500);
            if (frameSet) {
                auto color = frameSet->getColorFrame();
                if (!color) continue;
                {
                    std::lock_guard<std::mutex> guard(arrivalMutex);
                    arrivals[color->getTimeStampUs()] = monotonic_ns();
                    while (arrivals.size() > 64) arrivals.erase(arrivals.begin());
                }
                align->pushFrame(frameSet);
            }
        }
        try {
            if (gyroStarted && gyroSensor) gyroSensor->stop();
            if (accelStarted && accelSensor) accelSensor->stop();
        } catch (ob::Error &error) {
            std::cerr << "imu stop: " << error.what() << std::endl;
        }
        pipeline.stop();
        align.reset();
        munmap(imuBase, kImuSize);
        close(imuFd);
        munmap(base, kRingSize);
        close(fd);
        close(owner);
        return 0;
    }
    catch (ob::Error &error) {
        std::cerr << "sdk error: " << error.what() << std::endl;
        return 1;
    }
    catch (std::exception &error) {
        std::cerr << "error: " << error.what() << std::endl;
        return 1;
    }
}
