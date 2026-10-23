# Jetson 安装

1. 安装 JetPack/L4T、CUDA 和 Python 3.10。
2. 创建独立 Python 环境并安装相机 SDK、OpenCV、ONNX Runtime GPU、NumPy、SciPy、jsonschema 和 Piper。
3. 准备模型：
   - YOLO11n ONNX；
   - Piper 中文模型和配置；
   - 可选 eSpeak NG。
4. 构建 C++ 部分，设置 Orbbec SDK 路径。
5. 设置 `LUMINA_SERIAL`、`LUMINA_YOLO_MODEL`、`LUMINA_SDK_PYTHON`、`LUMINA_FRAME_SOURCE=shm`。
6. 启动预览并检查 `/status` 中的实际 release、CUDA 后端、帧龄和音频路由。

示例：

```bash
export LUMINA_SERIAL="YOUR_DEVICE_SERIAL"
export LUMINA_YOLO_MODEL="$HOME/ultralytics/ultralytics/yolo11n.onnx"
export LUMINA_SDK_PYTHON="$HOME/lumina-sdk-env/bin/python"
export LUMINA_FRAME_SOURCE=shm
cmake -S . -B build -DLUMINA_ORBBEC_SDK="$HOME/orbbec-sdk/<sdk-dir>"
cmake --build build -j2
python3 tools/preview_control.py start --lan
```

真实相机、模型和 API Key 不进入仓库。在线功能必须显式授权；不要把密钥写入文件。
