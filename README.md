# LUMINA Guide / 灵犀导盲背心

**简体中文** | [繁體中文](README.zh-TW.md) | [English](README.en.md)

可穿戴 RGB-D 场景理解与通行提示原型，面向 **Jetson Orin Nano + Orbbec Gemini 336L**。

> **公开演示版 / Public demo**  
> 本仓库仅公开可展示的架构、界面和部分实现。商业化核心版本闭源，关键算法、参数、模型集成、部署配置和现场适配细节未公开，保留技术细节。公开版本不定期更新，不承诺固定周期或完整支持。  
> This is a public demo. The commercial core is closed source; key algorithms, parameters, model integration and deployment details are withheld. Public updates are provided from time to time.

> **资助 / Funding**  
> 本项目为香港数码港（Cyberport Hong Kong）资助项目。资助不代表数码港对产品功能、安全性、医疗器械合规性或商业可行性作出认证或背书。  
> This project is funded by Cyberport Hong Kong. Funding does not constitute certification or endorsement of product functionality, safety, medical-device compliance or commercial viability.

> 这是研究与工程验证原型，不是医疗器械，也不是已经验收的自主导航设备。默认保持 `movement_allowed=false`。

## 语言 / Languages

- [简体中文 README](README.zh-CN.md)
- [繁體中文 README](README.zh-TW.md)
- [English README](README.en.md)

## 简要能力

- RGB-D 采集、共享内存和相机恢复
- CUDA YOLO11n 检测
- IMU 站立/步行识别
- 三维地面平面与水平地面线
- 台阶、路缘和近物提示
- 完全本地的 Piper 语音和虚拟振动
- 可选、需授权的云端视觉审阅
- 统一实时感知台

## 商业版本

商业核心版本闭源，包含更完整的核心算法、参数、模型集成、部署配置和现场适配。公开演示版不定期更新。

商业授权、合作和商业版本咨询：**wzr0411@gmail.com**

## 文档

- [架构](docs/architecture.md)
- [Jetson 安装](docs/setup-jetson.md)
- [安全与隐私](docs/safety-and-privacy.md)
- [三语版公开与商业说明](NOTICE.md)
- [安全策略](SECURITY.md)

模型、相机 SDK、运行数据、设备序列号和 API Key 不随仓库分发。使用第三方模型和 SDK 时请遵守各自许可证。
