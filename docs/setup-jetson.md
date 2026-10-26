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

## 可选在线问答与语音识别

`LUMINA_API_HOST` 是 HTTPS 服务根地址，不含 `/compatible-mode/v1` 后缀。公开默认值为 `https://dashscope.aliyuncs.com`；专属工作空间地址只在本机环境中配置，不写进源码、提交或截图。地址、API Key 的地域与计费方案必须匹配，详见 [阿里云官方地址说明](https://www.alibabacloud.com/help/en/model-studio/base-url)。

```bash
export LUMINA_API_HOST="https://dashscope.aliyuncs.com"
export ONLINE_MODEL="qwen3.8-flash"
export ASR_MODEL="qwen3-asr-flash"
read -rsp "DashScope API Key: " DASHSCOPE_API_KEY
export DASHSCOPE_API_KEY
python3 tools/preview_control.py start --lan
unset DASHSCOPE_API_KEY
```

示例模型名需与账号当前可用模型一致。也可用启动器的 `--key-stdin` 入口由可信进程注入密钥；禁止将真实值放进命令参数、Shell 历史或版本库。`.env.example` 仅是配置字段示例，程序不会自动加载它。

本地 Piper 提示不需要在线密钥。配置密钥不代表授权上传：在线图片理解与录音仍需在页面中分别授权。麦克风与扬声器可通过 NoMachine 路由接入，实际设备、路由和权限需按本机配置验证。

公开仓库通过环境变量指定相机编号、SDK 和模型路径，不能直接使用私人机器上的固定部署配置。启动后的访问地址为 `http://<JETSON_IP>:8765/`，以页面和终端显示的实际地址为准。
