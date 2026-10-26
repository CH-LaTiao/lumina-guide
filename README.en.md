<p align="center"><img src="docs/assets/lumina-banner.svg" alt="LUMINA Guide — wearable RGB-D assistance prototype; vest schematic" width="100%" /></p>

# LUMINA Guide

[![Static checks](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml/badge.svg)](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml)

[简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [English](README.en.md)

A wearable RGB-D scene-understanding and navigation-cue prototype built for **Jetson Orin Nano + Orbbec Gemini 336L**.

> **Public demo notice**  
> This repository is a public demo. It exposes only the public architecture, interface, selected tools and tests. The commercial core is closed source; key algorithms, parameters, model integration, deployment configuration and field adaptation are withheld. Public updates are provided from time to time without a fixed schedule or support commitment.

> **Funding**  
> This project is funded by **Cyberport Hong Kong**. Funding does not constitute certification or endorsement of product functionality, safety, medical-device compliance or commercial viability.

> This is a research and engineering prototype, not a medical device or a validated autonomous navigation system. `movement_allowed` remains `false` by default.

**Public update: v0.11.3 (2026-10-27).** Near-wall cues now compare measured space and revoke center hints when the center is near. Perception publishes independently of preview encoding, stale speech is interrupted, and cloud questions explicitly start or continue a task. This sync passed 108 hardware-free regressions and adds a public-content credential guard. See the [update and validation record (Chinese)](docs/public-updates.zh-CN.md). Workspace endpoints and keys stay in local environment configuration.

## Capabilities

- Real-time 640×480 RGB-D capture, shared-memory publishing, freshness checks and camera recovery
- CUDA YOLO11n detection through ONNX Runtime
- IMU-based standing/walking state and turn awareness
- 3D ground-plane fitting, obstacle height ranges and an adjustable horizontal ground line
- Stair, curb and near-hazard cues
- Fully local Piper speech with eSpeak fallback
- Six-channel virtual haptic simulation
- Optional, explicitly authorized cloud review
- Unified perception dashboard

## Architecture

![Animated system data flow](docs/assets/system-overview.gif)

*Animated data-flow illustration, not live device telemetry. [Static alternative](docs/assets/system-overview.svg).*

```text
Gemini 336L
  -> C++ frame-server (exclusive camera owner)
  -> POSIX shared memory (RGB-D + IMU)
  -> Python live view
       -> YOLO / geometry / guidance
       -> local Piper speech
       -> virtual haptics
       -> dashboard and optional cloud review
```

The frame server owns the camera, Python consumers read shared memory, walking mode emits one concise action cue, and cloud results never directly control motors or movement. Stale perception or a disconnected camera clears the previous route.

See [architecture](docs/architecture.md), [Jetson setup](docs/setup-jetson.md) and [safety/privacy](docs/safety-and-privacy.md).

## How the modules work together

The system follows a capture → perception → guidance → feedback → review loop. Depth geometry supplies spatial evidence, object detection adds class information, and IMU measurements inform motion state. Local guidance combines this evidence into concise cues. Optional cloud review addresses user questions about complex scenes through a separate, explicitly authorized path.

| Module | Responsibility | Engineering approach |
| :--- | :--- | :--- |
| Capture | Supply RGB-D and IMU measurements | Exclusive C++ camera ownership, shared-memory publishing, frame identity and timestamps |
| Perception | Combine object classes and spatial evidence | CUDA-preferred ONNX inference, frame-associated detections, depth geometry and ground fitting |
| Guidance | Adapt information to standing or walking | Scene summaries, concise walking cues, multi-frame confirmation, risk priority and stale-route withdrawal |
| Feedback | Keep speech and virtual haptics consistent | Shared guidance state, local Piper speech and priority handling |
| Cloud review | Answer questions about an authorized snapshot | Scene prompts, structured output validation and review rules; no direct movement control |
| Prompt iteration | Turn human feedback into versioned configuration | CSV review, trial answers, human assessment, activation and rollback; no claim of model-weight retraining |

## Engineering foundations

The project includes more than the perception pipeline: a unified diagnostic dashboard, frame/session freshness checks, regression tests, deterministic algorithm scenarios, runtime probes, trace recording, release identity, rollback and runtime-data backup tools. These provide observability and reproducible investigation across development stages. Hardware-free CI and tabletop diagnostics remain distinct from field acceptance.

The next validation areas are real wearing and walking latency, cue clarity, difficult surfaces and obstacles, physical haptic integration, power and endurance, and scenario-specific human evaluation. Combining navigation direction with local perception is a development direction; metro routing and station-level guidance are not presented as completed capabilities. The scope of the public demo remains governed by [NOTICE](NOTICE.en.md).

## Repository layout

```text
core/          C++ capture, shared memory, depth regions and geometry
prompts/       cloud scene prompts and JSON Schema
deployment/    optional systemd/udev templates
tools/         Jetson services, perception, TTS and diagnostics
tests/         Python unit/interface/regression tests
docs/          architecture, installation and safety notes
```

## Requirements

Validated environment:

- Jetson Orin Nano Super, Ubuntu 22.04 / L4T 36.x
- Orbbec Gemini 336L over USB 3.0
- CUDA and ONNX Runtime GPU
- Python 3.10 and CMake 3.16+
- OpenCV, NumPy, SciPy, jsonschema
- A separate `pyorbbecsdk` environment
- Piper Chinese model and eSpeak NG

Models, camera SDK and model weights are not distributed with this repository. YOLO11n / Ultralytics is AGPL-3.0; Piper and the Orbbec SDK have their own upstream licenses.

## Quick start

```bash
export LUMINA_SERIAL="YOUR_DEVICE_SERIAL"
export LUMINA_YOLO_MODEL="$HOME/ultralytics/ultralytics/yolo11n.onnx"
export LUMINA_SDK_PYTHON="$HOME/lumina-sdk-env/bin/python"
export LUMINA_FRAME_SOURCE=shm
export LUMINA_ORBBEC_EXTENSIONS="$HOME/orbbec-sdk/<sdk-version>/lib/extensions"

cmake -S . -B build -DLUMINA_ORBBEC_SDK="$HOME/orbbec-sdk/<sdk-dir>"
cmake --build build -j2
python3 tools/preview_control.py start --lan
```

Open `http://<JETSON_IP>:8765/`. See [Jetson setup](docs/setup-jetson.md) for the full procedure.

Inject an optional cloud API key through stdin only. Never store it in `.env`, SQLite, logs or Git:

```bash
python3 tools/preview_control.py start --lan --key-stdin
```

## Tests

```bash
python3 -m unittest tests/test_live_view_js.py tests/test_ground_line.py -v
node --check tools/live_view.js
python3 -m pytest -q tests
ctest --test-dir build --output-on-failure
```

GitHub Actions only runs hardware-free static checks. Jetson real-time acceptance cannot be replaced by generic CI.

## Safety boundaries

- Not a medical device and not a replacement for a white cane, guide dog, human assistant or traffic judgment.
- Physical movement permission is disabled by default; the real haptic board has not completed hardware acceptance.
- The ground line affects ground fitting only. It does not hide obstacles.
- Glass, mirrors, thin poles, suspended objects, stair ground truth and indoor/outdoor maps require dedicated validation.
- Real wearing, walking, distance truth, long-term stability and user studies remain to be completed.
- Cloud model output may time out or be wrong; it is a candidate only.

## Public vs commercial

- **Public demo:** public architecture, interface, selected tools and tests.
- **Commercial core:** closed source, with more complete algorithms, parameters, model integration, deployment configuration and field adaptation.
- **Public updates:** from time to time, without a fixed schedule.
- **Funding:** funded by Cyberport Hong Kong. Funding is not product certification or safety endorsement.
- **Contact:** for commercial licensing, collaboration or the commercial version, email **wzr0411@gmail.com**.

See the [public/commercial notice](NOTICE.en.md) and [security policy](SECURITY.en.md).

## License

This repository is a public demo and does not currently declare an open-source license. Until a license is added, the code is publicly viewable but no right to copy, modify, redistribute or use it commercially is granted. The commercial core is closed source; licensing inquiries: **wzr0411@gmail.com**.
