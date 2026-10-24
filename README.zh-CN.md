<div align="center">

<img src="docs/assets/lumina-banner.svg" alt="LUMINA Guide：RGB-D感知、场景理解与简洁提示；配合白手杖使用的可穿戴辅助原型" width="100%" />

# LUMINA Guide · 灵犀导盲背心

**看见环境，理解场景，让辅助提示更清晰。**

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · [English](README.en.md)

[![Static checks](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml/badge.svg)](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml)
![Stage: Public demo](https://img.shields.io/badge/stage-public_demo-137c72?style=flat-square)
![Platform: Jetson](https://img.shields.io/badge/platform-Jetson_Orin_Nano_Super-273c52?style=flat-square)
![Sensor: RGB-D](https://img.shields.io/badge/sensor-Gemini_336L-273c52?style=flat-square)

[项目能力](#项目能力) · [系统架构](#系统架构) · [快速开始](#快速开始) · [文档导航](#文档导航) · [合作联系](#合作联系)

</div>

---

LUMINA Guide 是面向视障人群的**可穿戴 RGB-D 场景理解与通行提示原型**。项目以 NVIDIA Jetson Orin Nano Super 与 Orbbec Gemini 336L 为基础，将本地感知、简洁语音、虚拟振动和按需云端理解组合在一起，当前定位是**配合白手杖的辅助设备**。

> **公开演示版** · 本仓库展示公开架构、界面、部分工具与测试；商业核心版本闭源，公开版不定期更新。它是研究与工程验证原型，尚未完成自主导航或真实佩戴行走验收，默认 `movement_allowed=false`。

## 项目能力

| 感知环境 | 理解场景 | 输出提示 |
| :--- | :--- | :--- |
| RGB-D 对齐与深度区域诊断 | IMU 站立 / 步行状态 | 结论优先的短句语音 |
| CUDA YOLO11n 目标检测 | 地面、障碍高度与通道分析 | 六通道虚拟振动 |
| 台阶、路缘与近物候选 | 用户授权的图片与问题审阅 | 实时画面、框选与状态可视化 |

### 围绕两种使用状态设计

| 使用状态 | 交互重点 | 示例 |
| :--- | :--- | :--- |
| **站立观察** | 描述方向、近物与环境；按需提出问题 | “前方有什么？” |
| **步行提示** | 保持一句简洁的行动提示；风险优先 | “停，有台阶。” |
| **按需理解** | 经授权发送图片与问题，返回候选答案供审阅 | “帮我理解这个场景。” |

示例用于说明交互设计，不代表具体场景已通过通行验收。云端答案不会直接控制移动或马达。

### 在同一个页面观察整个过程

- **实时感知台**：RGB、对齐深度、检测框、三维诊断、语音与虚拟振动。
- **地面线调节**：拖动水平线调整地面拟合区域，障碍检测仍覆盖完整画面。
- **提示词反馈闭环**：CSV 修订 → 候选版本 → 授权试答 → 人工评测 → 启用 / 回滚。
- **状态可追溯**：显示实际后端、帧龄和运行状态，感知失效时撤回旧方向。

## 系统架构

![公开系统架构：相机经C++帧服务和共享内存进入本地感知与引导，输出语音、虚拟振动和页面；云端理解需明确授权](docs/assets/system-overview.svg)

**本地路径**负责持续感知与提示；**在线路径**服务于用户主动提出的场景问题。相机由 C++ 帧服务独占，Python 消费共享内存；站立和步行策略共用可追溯的引导状态。

| 层级 | 主要技术 | 作用 |
| :--- | :--- | :--- |
| 设备与采集 | Jetson · Gemini 336L · C++ | RGB-D、IMU、共享内存 |
| 感知与策略 | Python · OpenCV · ONNX Runtime | 目标检测、几何诊断、引导状态 |
| 语音与反馈 | Piper · eSpeak NG · 虚拟振动 | 本地语音、六通道反馈模拟 |
| 可视化与审阅 | HTML / JavaScript · 可选云端视觉 | 实时感知台、问答与人工评测 |

详细进程与数据流见 [架构文档](docs/architecture.md)。图中背心为系统形态示意，振动为软件模拟。

## 快速开始

**准备条件：** Jetson Orin Nano Super、Gemini 336L、USB 3.0、匹配的 JetPack / CUDA、独立 Python 环境、Orbbec SDK，以及自行准备的模型。模型、SDK、API Key 和运行数据不随仓库分发。

1. 阅读 [Jetson 安装说明](docs/setup-jetson.md)，完成设备与依赖配置。
2. 按安装文档构建 C++ 帧服务，并配置设备序列号、模型和 SDK 路径。
3. 在 Jetson 的配置环境中启动预览：

```bash
python3 tools/preview_control.py start --lan
python3 tools/preview_control.py status
```

在同一局域网打开 **`http://<JETSON_IP>:8765/`**。在线视觉功能可选，仅在明确授权后发送图片。详见 [安全与隐私](docs/safety-and-privacy.md)。

<details>
<summary><strong>目录与验证入口</strong></summary>

```text
core/          C++采集、共享内存与几何处理
prompts/       场景提示词与JSON Schema
tools/         感知、语音、页面与诊断工具
tests/         单元、接口与回归测试
deployment/    可选部署模板
docs/          架构、安装与安全说明
```

```bash
python3 -m unittest tests.test_live_view_js tests.test_ground_line -v
node --check tools/live_view.js
```

上方 CI 徽章仅对应无硬件静态检查，不表示实机行走或产品安全验收。完整测试和环境要求见 [详细使用说明](docs/public-demo-guide.zh-CN.md)。

</details>

## 文档导航

| 你想了解什么 | 从这里开始 |
| :--- | :--- |
| 设备配置与启动 | [Jetson 安装](docs/setup-jetson.md) |
| 进程、共享内存与数据流 | [系统架构](docs/architecture.md) |
| 完整功能、命令与测试 | [详细使用说明](docs/public-demo-guide.zh-CN.md) |
| 数据处理与使用边界 | [安全与隐私](docs/safety-and-privacy.md) |
| 公开内容与商业版本 | [公开与商业说明](NOTICE.zh-CN.md) |
| 报告安全问题 | [安全策略](SECURITY.zh-CN.md) |

## 当前边界

当前为研究与工程验证原型，**不替代白手杖、导盲犬、陪同人员或交通判断**。实体振动板、真实佩戴行走、距离真值、长期稳定性和用户可用性仍需验收。玻璃、镜面、细杆、悬空物体和楼梯等场景需要专项验证；云端结果也可能超时或出错。

## 资助与公开范围

本项目为 **香港数码港（Cyberport Hong Kong）资助项目**。资助不代表数码港对产品功能、安全性、医疗器械合规性或商业可行性作出认证或背书。

本仓库是公开演示版，**不等同于完整商业版本，也尚未声明开源许可证**。商业核心的关键算法、参数、模型集成、部署配置和现场适配细节不公开。授权范围以 [NOTICE](NOTICE.zh-CN.md) 为准；第三方模型和 SDK 另受各自许可证约束。

## 合作联系

商业授权、产品合作与商业版本咨询：**[wzr0411@gmail.com](mailto:wzr0411@gmail.com)**

---

<div align="center">

**LUMINA Guide** · Wearable perception. Human-centered assistance.

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · [English](README.en.md)

</div>
